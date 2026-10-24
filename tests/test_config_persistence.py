"""設定持久化與無視窗 GUI 回歸；僅使用臨時檔案及假元件。"""
import contextlib
import ctypes
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from aafm_py.config import AiPlayerConfig, AppConfig, PromptTemplateManager
from aafm_py.controller import Controller
from aafm_py.gui import AiPlayerGUI


class FaultFile:
    def __init__(self, real, stage):
        self.real, self.stage, self.name = real, stage, real.name

    def __enter__(self):
        return self

    def write(self, text):
        if self.stage == 'write':
            self.real.write(text[:1])
            raise OSError('sensitive-test-value')
        return self.real.write(text)

    def flush(self):
        if self.stage == 'flush':
            raise OSError('sensitive-test-value')
        return self.real.flush()

    def fileno(self):
        return self.real.fileno()

    def __exit__(self, *args):
        self.real.close()
        if self.stage == 'close':
            raise OSError('sensitive-test-value')


class ConfigPersistenceTests(unittest.TestCase):
    def test_failures_preserve_original_clean_temporary_files_and_allow_retry(self):
        for model in (AppConfig, AiPlayerConfig):
            for stage in ('serialize', 'create', 'write', 'flush', 'sync', 'close', 'replace'):
                with self.subTest(model=model.__name__, stage=stage), tempfile.TemporaryDirectory() as folder:
                    path = Path(folder) / 'settings.json'
                    original = b'{"original": true}\n'
                    path.write_bytes(original)
                    cfg = model()
                    real_factory = tempfile.NamedTemporaryFile
                    with contextlib.ExitStack() as stack:
                        if stage == 'serialize':
                            stack.enter_context(patch('aafm_py.config.json.dump', side_effect=TypeError('sensitive-test-value')))
                        elif stage in ('create', 'sync', 'replace'):
                            target = {'create': 'tempfile.NamedTemporaryFile', 'sync': 'os.fsync', 'replace': 'os.replace'}[stage]
                            stack.enter_context(patch('aafm_py.config.' + target, side_effect=OSError('sensitive-test-value')))
                        else:
                            stack.enter_context(patch('aafm_py.config.tempfile.NamedTemporaryFile',
                                side_effect=lambda **kwargs: FaultFile(real_factory(**kwargs), stage)))
                        output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                        self.assertIs(cfg.save(str(path)), False)
                        self.assertNotIn('sensitive-test-value', output.getvalue())
                    self.assertEqual(path.read_bytes(), original)
                    self.assertEqual(list(Path(folder).iterdir()), [path])
                    self.assertIs(cfg.save(str(path)), True)
                    self.assertEqual(model.load(str(path)).to_dict(), cfg.to_dict())

    def test_nonserializable_values_preserve_original(self):
        for cfg, field in ((AppConfig(), 'server'), (AiPlayerConfig(), 'systemPrompt')):
            with self.subTest(model=type(cfg).__name__), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'settings.json'
                path.write_text('{"old": 1}', encoding='utf-8')
                setattr(cfg, field, object())
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertIs(cfg.save(path), False)
                self.assertEqual(json.loads(path.read_text(encoding='utf-8')), {'old': 1})
                self.assertEqual(list(Path(folder).iterdir()), [path])

    def test_normal_first_save_unicode_reload_and_relative_path(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as folder:
            for model in (AppConfig, AiPlayerConfig):
                cfg = model()
                if isinstance(cfg, AppConfig):
                    cfg.server.brand = '中文 😀\nsecond line'
                    cfg.llm.apiKey = 'fake-key-for-test'
                else:
                    cfg.systemPrompt = '中文 😀\nsecond line'
                    cfg.apiKey = 'fake-key-for-test'
                path = Path(os.path.relpath(Path(folder) / (model.__name__ + '.json')))
                self.assertIs(cfg.save(path), True)
                self.assertEqual(model.load(path).to_dict(), cfg.to_dict())

    def test_failure_on_new_file_does_not_leave_a_partial_config(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'new.json'
            with patch('aafm_py.config.os.replace', side_effect=PermissionError), contextlib.redirect_stdout(io.StringIO()):
                self.assertIs(AppConfig().save(path), False)
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_snapshot_failure_returns_false_without_exposing_setting_values(self):
        for model in (AppConfig, AiPlayerConfig):
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'config.json'
                path.write_bytes(b'{"old": true}')
                cfg = model()
                cfg.to_dict = Mock(side_effect=ValueError('fake-sensitive-setting'))
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertIs(cfg.save(path), False)
                self.assertNotIn('fake-sensitive-setting', output.getvalue())
                self.assertIn('ValueError', output.getvalue())
                self.assertEqual(path.read_bytes(), b'{"old": true}')

    @unittest.skipUnless(os.name == 'nt', 'Windows file-sharing lock')
    def test_real_windows_file_lock_keeps_original_and_recovers_after_unlock(self):
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'locked.json'
            original = b'{"old": true}'
            path.write_bytes(original)
            handle = kernel.CreateFileW(str(path), 0x80000000, 1, None, 3, 0, None)
            self.assertNotEqual(handle, wintypes.HANDLE(-1).value)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertIs(AppConfig().save(path), False)
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(list(Path(folder).iterdir()), [path])
            finally:
                kernel.CloseHandle(handle)
            self.assertIs(AppConfig().save(path), True)


class ConfigGuiTests(unittest.TestCase):
    def setUp(self):
        self.gui = AiPlayerGUI.__new__(AiPlayerGUI)
        self.gui._append_log = Mock()
        self.gui.controller = SimpleNamespace(config=AppConfig(), ai_config=AiPlayerConfig(),
            sync_roster_runtime=Mock(), connect=Mock(), shutdown=Mock())

    def test_failed_save_does_not_reload_or_report_success(self):
        self.gui._gather_config = Mock(return_value=False)
        with patch('aafm_py.gui.messagebox.showinfo') as info, patch.object(AiPlayerConfig, 'load') as load:
            self.gui._on_save()
        info.assert_not_called()
        load.assert_not_called()
        self.gui.controller.sync_roster_runtime.assert_not_called()

    def test_successful_save_reloads_and_reports_success(self):
        self.gui._gather_config = Mock(return_value=True)
        with patch('aafm_py.gui.messagebox.showinfo') as info, patch.object(AiPlayerConfig, 'load') as load:
            self.gui._on_save()
        info.assert_called_once()
        load.assert_called_once()
        self.gui.controller.sync_roster_runtime.assert_called_once()

    def test_partial_failure_attempts_both_files_and_reports_error(self):
        for app_ok, ai_ok in ((False, True), (True, False), (False, False), (True, True)):
            self.gui.controller.config.save = Mock(return_value=app_ok)
            self.gui.controller.ai_config.save = Mock(return_value=ai_ok)
            with patch('aafm_py.gui.messagebox.showerror') as error:
                self.assertIs(self.gui._save_configs(self.gui.controller.config, self.gui.controller.ai_config), app_ok and ai_ok)
            self.gui.controller.config.save.assert_called_once()
            self.gui.controller.ai_config.save.assert_called_once()
            self.assertEqual(error.call_count, int(not (app_ok and ai_ok)))

    def test_failed_appearance_or_connect_does_not_continue(self):
        self.gui._gather_config = Mock(return_value=False)
        self.gui._refresh_background_image = Mock()
        with patch('aafm_py.gui.messagebox.showinfo') as info:
            self.gui._on_apply_appearance()
            self.gui._on_connect()
        info.assert_not_called()
        self.gui._refresh_background_image.assert_not_called()
        self.gui.controller.connect.assert_not_called()

    def test_failed_cookie_save_never_claims_cookie_is_saved(self):
        self.gui.var_song_netease_cookie = Mock()
        self.gui._netease_status = Mock()
        self.gui.controller.config.save = Mock(return_value=False)
        with patch('aafm_py.gui.messagebox.showerror'):
            self.gui._apply_netease_cookie('fake-secret-cookie')
        text = str(self.gui._netease_status.mock_calls) + str(self.gui._append_log.mock_calls)
        self.assertNotIn('已保存会员', text)
        self.assertNotIn('fake-secret-cookie', text)

    def test_close_failure_keeps_window_unless_user_discards_edits(self):
        self.gui._gather_config = Mock(return_value=False)
        self.gui.root = SimpleNamespace(destroy=Mock())
        with patch('aafm_py.gui.messagebox.askyesno', return_value=False):
            self.gui._on_close()
        self.gui.root.destroy.assert_not_called()
        self.gui.controller.shutdown.assert_not_called()
        with patch('aafm_py.gui.messagebox.askyesno', return_value=True):
            self.gui._on_close()
        self.gui.root.destroy.assert_called_once()
        self.gui.controller.shutdown.assert_called_once()

    def test_pause_and_disconnect_remain_available_after_save_failure(self):
        self.gui._gather_config = Mock(return_value=False)
        self.gui.controller.pause_book = Mock()
        self.gui._on_book_control('stop')
        self.gui.controller.pause_book.assert_called_once_with('stop')
        self.gui.var_qq_enabled = Mock()
        self.gui.controller.stop_qq = Mock()
        self.gui.controller.config.save = Mock(return_value=False)
        with patch('aafm_py.gui.messagebox.showerror'):
            self.gui._on_qq_stop()
        self.gui.controller.stop_qq.assert_called_once()

    def test_actual_gather_and_save_handle_partial_failure_then_retry(self):
        class HeadlessGUI(AiPlayerGUI):
            def __init__(self):
                self.controller = SimpleNamespace(config=AppConfig(), ai_config=AiPlayerConfig(),
                    sync_roster_runtime=Mock())
                self._append_log = Mock()
                self._random_rows, self._cmd_rows = [], []
                self._bili_fav_map, self._bili_fav_cur = {}, {}
                self.var_bg_darkness = Mock(get=lambda: '0')
                self.var_api_key = Mock(get=lambda: 'fake-edited-key')
                self.system_prompt_widget = Mock(get=lambda *args: 'edited prompt')
                self.reconnect_triggers_text = Mock(get=lambda *args: '')

            def __getattr__(self, name):
                if name.startswith('var_'):
                    return Mock(get=lambda: '')
                raise AttributeError(name)

        with tempfile.TemporaryDirectory() as folder:
            app_path, ai_path = Path(folder) / 'app.json', Path(folder) / 'ai.json'
            gui = HeadlessGUI()
            self.assertTrue(gui.controller.ai_config.save(ai_path))
            original = ai_path.read_bytes()
            real_replace = os.replace

            def replace(source, destination):
                if os.fspath(destination) == str(ai_path):
                    raise PermissionError('fake-key-not-for-logs')
                real_replace(source, destination)

            with patch('aafm_py.config.APP_CONFIG_FILE', str(app_path)), \
                    patch('aafm_py.config.AI_PLAYER_CONFIG_FILE', str(ai_path)), \
                    patch('aafm_py.gui.messagebox.showerror') as error, \
                    patch('aafm_py.gui.messagebox.showinfo') as info:
                with patch('aafm_py.config.os.replace', side_effect=replace), contextlib.redirect_stdout(io.StringIO()):
                    gui._on_save()
                self.assertTrue(app_path.is_file())
                self.assertEqual(ai_path.read_bytes(), original)
                self.assertEqual(gui.controller.ai_config.apiKey, 'fake-edited-key')
                error.assert_called_once()
                info.assert_not_called()
                gui.controller.sync_roster_runtime.assert_not_called()
                gui._on_save()
                info.assert_called_once()
                self.assertEqual(AiPlayerConfig.load(ai_path).apiKey, 'fake-edited-key')


class RelatedPersistenceTests(unittest.TestCase):
    def test_automatic_config_saves_report_persistence_failure_to_gui(self):
        controller = Controller.__new__(Controller)
        controller._post_ui = Mock()
        for cfg in (AppConfig(), AiPlayerConfig()):
            cfg.save = Mock(return_value=False)
            self.assertIs(controller._persist_config(cfg), False)
            event = controller._post_ui.call_args.args[0]
            self.assertEqual(event['level'], 'error')
            self.assertIn('仅在内存中', event['message'])
        controller._post_ui.reset_mock()
        cfg.save.return_value = True
        self.assertIs(controller._persist_config(cfg), True)
        controller._post_ui.assert_not_called()

    def test_templates_save_update_and_delete_preserve_original_on_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'templates.json'
            with patch('aafm_py.config.PROMPT_TEMPLATES_FILE', str(path)):
                self.assertIs(PromptTemplateManager.save('test', 'original'), True)
                original = path.read_bytes()
                with patch('aafm_py.config.os.replace', side_effect=OSError), contextlib.redirect_stdout(io.StringIO()):
                    self.assertIs(PromptTemplateManager.save('test', 'edited'), False)
                    self.assertIs(PromptTemplateManager.delete('test'), False)
                self.assertEqual(path.read_bytes(), original)
                self.assertIs(PromptTemplateManager.save('test', 'edited'), True)
                self.assertEqual(PromptTemplateManager.get_all(), [{'name': 'test', 'prompt': 'edited'}])
                self.assertIs(PromptTemplateManager.delete('test'), True)
                self.assertEqual(PromptTemplateManager.get_all(), [])

    def test_progress_and_qq_records_keep_original_and_report_failed_writes(self):
        controller = Controller.__new__(Controller)
        controller._post_ui = Mock()
        controller._qq_nicknames = {'10001': 'fake account'}
        with tempfile.TemporaryDirectory() as folder:
            for kind in ('progress', 'accounts'):
                path = Path(folder) / (kind + '.json')
                original = b'{"existing": "retained"}'
                path.write_bytes(original)
                key = 'BOOK_PROGRESS_FILE' if kind == 'progress' else 'QQ_ACCOUNTS_FILE'
                save = (lambda: controller.save_book_progress(startPage=12)) if kind == 'progress' else controller._save_qq_accounts
                with patch('aafm_py.controller.' + key, str(path)):
                    with patch('aafm_py.config.os.replace', side_effect=OSError), contextlib.redirect_stdout(io.StringIO()):
                        self.assertIs(save(), False)
                    self.assertEqual(path.read_bytes(), original)
                    self.assertEqual(controller._post_ui.call_args.args[0]['level'], 'error')
                    self.assertIs(save(), True)
                    data = json.loads(path.read_text(encoding='utf-8'))
                    if kind == 'progress':
                        self.assertEqual(data, {'existing': 'retained', 'startPage': 12})
                    else:
                        self.assertEqual(data, controller._qq_nicknames)


if __name__ == '__main__':
    unittest.main()
