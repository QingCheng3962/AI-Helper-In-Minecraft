using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace AafmSetup
{
    /// <summary>AI Helper 安装程序：把 exe 里附带的 payload 解压到用户选择的目录。</summary>
    static class Program
    {
        const string MAGIC = "AAFMINS1";
        const int FOOTER = 48;   // magic(8) + appOff(8) + appLen(8) + napOff(8) + napLen(8) + crc-ish(8)

        [STAThread]
        static void Main(string[] args)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            var opts = ParseArgs(args);
            if (opts.Silent)
            {
                int code = Install(opts.TargetDir, opts.WithNapcat, null, null);
                Environment.Exit(code);
                return;
            }
            Application.Run(new SetupForm());
        }

        internal class Options
        {
            public bool Silent;
            public bool WithNapcat;
            public string TargetDir = "";
        }

        internal static Options ParseArgs(string[] args)
        {
            var o = new Options();
            for (int i = 0; i < args.Length; i++)
            {
                string a = args[i] ?? "";
                if (a.Equals("/S", StringComparison.OrdinalIgnoreCase) ||
                    a.Equals("--silent", StringComparison.OrdinalIgnoreCase)) o.Silent = true;
                else if (a.StartsWith("/D=", StringComparison.OrdinalIgnoreCase)) o.TargetDir = a.Substring(3).Trim('"');
                else if (a.StartsWith("--dir=", StringComparison.OrdinalIgnoreCase)) o.TargetDir = a.Substring(6).Trim('"');
                else if (a.Equals("/NAPCAT", StringComparison.OrdinalIgnoreCase) ||
                         a.Equals("/NAPCAT=1", StringComparison.OrdinalIgnoreCase) ||
                         a.Equals("--napcat", StringComparison.OrdinalIgnoreCase)) o.WithNapcat = true;
            }
            if (string.IsNullOrEmpty(o.TargetDir))
                o.TargetDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "AIHelperInMinecraft");
            return o;
        }

        /// <summary>读取 exe 末尾的 footer，拿到两个 payload 的偏移/长度。</summary>
        internal static bool ReadFooter(string exePath, out long appOff, out long appLen, out long napOff, out long napLen)
        {
            appOff = appLen = napOff = napLen = 0;
            try
            {
                using (var fs = new FileStream(exePath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
                {
                    if (fs.Length < FOOTER) return false;
                    fs.Seek(-FOOTER, SeekOrigin.End);
                    var buf = new byte[FOOTER];
                    int read = 0;
                    while (read < FOOTER)
                    {
                        int n = fs.Read(buf, read, FOOTER - read);
                        if (n <= 0) return false;
                        read += n;
                    }
                    string magic = Encoding.ASCII.GetString(buf, 0, 8);
                    if (magic != MAGIC) return false;
                    appOff = BitConverter.ToInt64(buf, 8);
                    appLen = BitConverter.ToInt64(buf, 16);
                    napOff = BitConverter.ToInt64(buf, 24);
                    napLen = BitConverter.ToInt64(buf, 32);
                    return appLen > 0;
                }
            }
            catch
            {
                return false;
            }
        }

        /// <summary>把 payload 里指定区间解压到目标目录（先解到临时目录再合并，兼容 .NET 4.0）。</summary>
        internal static long ExtractRange(string exePath, long offset, long length, string targetDir,
                                          Action<long, long> progress)
        {
            string tempZip = Path.Combine(Path.GetTempPath(), "aafm-payload-" + Guid.NewGuid().ToString("N") + ".zip");
            try
            {
                using (var src = new FileStream(exePath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
                using (var dst = new FileStream(tempZip, FileMode.Create, FileAccess.Write, FileShare.None))
                {
                    src.Seek(offset, SeekOrigin.Begin);
                    var buf = new byte[1 << 20];
                    long remaining = length;
                    while (remaining > 0)
                    {
                        int want = (int)Math.Min(buf.Length, remaining);
                        int n = src.Read(buf, 0, want);
                        if (n <= 0) break;
                        dst.Write(buf, 0, n);
                        remaining -= n;
                    }
                }
                string tempDir = Path.Combine(Path.GetTempPath(), "aafm-extract-" + Guid.NewGuid().ToString("N"));
                Directory.CreateDirectory(tempDir);
                try
                {
                    ZipFile.ExtractToDirectory(tempZip, tempDir);
                    long files = MergeDirectory(tempDir, targetDir, progress);
                    return files;
                }
                finally
                {
                    try { Directory.Delete(tempDir, true); } catch { }
                }
            }
            finally
            {
                try { File.Delete(tempZip); } catch { }
            }
        }

        internal static long MergeDirectory(string sourceDir, string targetDir, Action<long, long> progress)
        {
            Directory.CreateDirectory(targetDir);
            long copied = 0;
            foreach (string dir in Directory.GetDirectories(sourceDir, "*", SearchOption.AllDirectories))
            {
                string rel = dir.Substring(sourceDir.Length).TrimStart('\\');
                Directory.CreateDirectory(Path.Combine(targetDir, rel));
            }
            foreach (string file in Directory.GetFiles(sourceDir, "*", SearchOption.AllDirectories))
            {
                string rel = file.Substring(sourceDir.Length).TrimStart('\\');
                string dest = Path.Combine(targetDir, rel);
                string parent = Path.GetDirectoryName(dest);
                if (!string.IsNullOrEmpty(parent)) Directory.CreateDirectory(parent);
                File.Copy(file, dest, true);
                copied++;
                if (progress != null) progress(copied, 0);
            }
            return copied;
        }

        /// <summary>真正安装。返回进程退出码（0 成功）。</summary>
        internal static int Install(string targetDir, bool withNapcat, Action<string> log, Action<int> percent)
        {
            try
            {
                string exePath = Application.ExecutablePath;
                Directory.CreateDirectory(targetDir);
                long appOff, appLen, napOff, napLen;
                if (!ReadFooter(exePath, out appOff, out appLen, out napOff, out napLen))
                {
                    Fail(log, "安装包数据损坏（找不到内置 payload），请重新下载。");
                    return 2;
                }
                if (log != null) log("正在释放程序文件…");
                long total = 0;
                total += ExtractRange(exePath, appOff, appLen, targetDir, delegate(long done, long _)
                {
                    if (percent != null) percent(done > 0 ? Math.Min(95, (int)(done % 100)) : 0);
                });
                if (withNapcat && napLen > 0)
                {
                    if (log != null) log("正在释放 NapCat（QQ 转述）…");
                    total += ExtractRange(exePath, napOff, napLen, Path.Combine(targetDir, "NapCat"), null);
                }
                // 可写的运行目录
                foreach (string sub in new string[] { "logs", "pic_http", "db_backup", "preview", "qq_data", "other_qq_pic_sent\\relay_data" })
                {
                    try { Directory.CreateDirectory(Path.Combine(targetDir, sub)); } catch { }
                }
                if (log != null) log("正在创建快捷方式…");
                CreateShortcuts(targetDir);
                WriteInstallInfo(targetDir, withNapcat);
                if (log != null) log("安装完成，共 " + total + " 个文件。");
                if (percent != null) percent(100);
                return 0;
            }
            catch (Exception e)
            {
                Fail(log, "安装失败：" + e.Message);
                return 1;
            }
        }

        internal static void Fail(Action<string> log, string message)
        {
            if (log != null) log(message);
            MessageBox.Show(message, "AI Helper 安装程序", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }

        internal static void WriteInstallInfo(string targetDir, bool withNapcat)
        {
            var sb = new StringBuilder();
            sb.AppendLine("AI Helper In Minecraft");
            sb.AppendLine("安装目录：" + targetDir);
            sb.AppendLine("安装时间：" + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss"));
            sb.AppendLine("NapCat：" + (withNapcat ? "已安装" : "未安装"));
            sb.AppendLine();
            sb.AppendLine("启动方式：双击安装目录里的 AIHelper.exe（或桌面快捷方式）");
            sb.AppendLine("数据文件：data.db（好感度/写书进度/提示词模板/QQ 账号）、daily.db（每日数据）");
            sb.AppendLine("配置文件：config.json / mcai_chat.json（首次运行自动生成，含 API Key，注意备份）");
            File.WriteAllText(Path.Combine(targetDir, "安装信息.txt"), sb.ToString(), new UTF8Encoding(false));
        }

        /// <summary>桌面 + 开始菜单快捷方式（用 WScript.Shell COM，失败不影响安装）。</summary>
        internal static void CreateShortcuts(string targetDir)
        {
            string exe = Path.Combine(targetDir, "AIHelper.exe");
            if (!File.Exists(exe)) return;
            string desktop = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
            string programs = Environment.GetFolderPath(Environment.SpecialFolder.Programs);
            TryCreateShortcut(Path.Combine(desktop, "AI Helper.lnk"), exe, targetDir);
            TryCreateShortcut(Path.Combine(programs, "AI Helper.lnk"), exe, targetDir);
        }

        internal static void TryCreateShortcut(string linkPath, string targetExe, string workDir)
        {
            try
            {
                Type shellType = Type.GetTypeFromProgID("WScript.Shell");
                if (shellType == null) return;
                object shell = Activator.CreateInstance(shellType);
                object shortcut = shellType.InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, shell, new object[] { linkPath });
                Type shortcutType = shortcut.GetType();
                shortcutType.InvokeMember("TargetPath", BindingFlags.SetProperty, null, shortcut, new object[] { targetExe });
                shortcutType.InvokeMember("WorkingDirectory", BindingFlags.SetProperty, null, shortcut, new object[] { workDir });
                shortcutType.InvokeMember("IconLocation", BindingFlags.SetProperty, null, shortcut, new object[] { targetExe + ",0" });
                shortcutType.InvokeMember("Description", BindingFlags.SetProperty, null, shortcut, new object[] { "AI Helper In Minecraft" });
                shortcutType.InvokeMember("Save", BindingFlags.InvokeMethod, null, shortcut, new object[0]);
            }
            catch { }
        }

        internal static long DirSize(string path)
        {
            try
            {
                long total = 0;
                foreach (string f in Directory.GetFiles(path, "*", SearchOption.AllDirectories))
                    total += new FileInfo(f).Length;
                return total;
            }
            catch { return 0; }
        }
    }

    internal class SetupForm : Form
    {
        readonly TextBox pathBox = new TextBox();
        readonly CheckBox napcatBox = new CheckBox();
        readonly ProgressBar bar = new ProgressBar();
        readonly Label status = new Label();
        readonly Button installButton = new Button();
        readonly Button exitButton = new Button();
        readonly Button browseButton = new Button();
        readonly StringBuilder logText = new StringBuilder();

        public SetupForm()
        {
            Text = "AI Helper In Minecraft 安装程序";
            ClientSize = new Size(560, 340);
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedSingle;
            MaximizeBox = false;
            Font = new Font("Microsoft YaHei UI", 9f);

            var title = new Label();
            title.Text = "AI Helper In Minecraft";
            title.Font = new Font("Microsoft YaHei UI", 16f, FontStyle.Bold);
            title.SetBounds(24, 18, 500, 34);
            Controls.Add(title);

            var subtitle = new Label();
            subtitle.Text = "无客户端的 Minecraft AI 玩家 · 游戏内点歌 Mod · QQ 转述（内置 Python / Node 运行时，无需另装）";
            subtitle.ForeColor = Color.DimGray;
            subtitle.SetBounds(26, 54, 500, 20);
            Controls.Add(subtitle);

            var pathLabel = new Label();
            pathLabel.Text = "安装到：";
            pathLabel.SetBounds(26, 92, 70, 22);
            Controls.Add(pathLabel);

            pathBox.SetBounds(96, 90, 350, 24);
            pathBox.Text = defaultInstallDir();
            Controls.Add(pathBox);

            browseButton.Text = "浏览…";
            browseButton.SetBounds(452, 89, 80, 26);
            browseButton.Click += delegate
            {
                using (var dlg = new FolderBrowserDialog())
                {
                    dlg.Description = "选择安装目录";
                    if (Directory.Exists(pathBox.Text)) dlg.SelectedPath = pathBox.Text;
                    if (dlg.ShowDialog() == DialogResult.OK) pathBox.Text = dlg.SelectedPath;
                }
            };
            Controls.Add(browseButton);

            napcatBox.Text = "同时安装 NapCat（QQ 转述功能，可选，体积较大）";
            napcatBox.SetBounds(96, 124, 430, 22);
            napcatBox.Checked = true;
            Controls.Add(napcatBox);

            bar.SetBounds(26, 162, 506, 22);
            bar.Minimum = 0; bar.Maximum = 100;
            Controls.Add(bar);

            status.Text = "点「开始安装」把程序和运行环境释放到上面的目录。";
            status.ForeColor = Color.DimGray;
            status.SetBounds(26, 190, 506, 40);
            Controls.Add(status);

            installButton.Text = "开始安装";
            installButton.SetBounds(300, 280, 110, 34);
            installButton.Click += delegate { StartInstall(); };
            Controls.Add(installButton);

            exitButton.Text = "退出";
            exitButton.SetBounds(422, 280, 110, 34);
            exitButton.Click += delegate { Close(); };
            Controls.Add(exitButton);
        }

        static string defaultInstallDir()
        {
            return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "AIHelperInMinecraft");
        }

        void SetStatus(string text)
        {
            if (InvokeRequired) { BeginInvoke(new Action<string>(SetStatus), text); return; }
            status.Text = text;
            logText.AppendLine(text);
        }

        void SetPercent(int value)
        {
            if (InvokeRequired) { BeginInvoke(new Action<int>(SetPercent), value); return; }
            if (value < 0) value = 0;
            if (value > 100) value = 100;
            bar.Value = value;
        }

        void StartInstall()
        {
            string target = pathBox.Text.Trim();
            if (target.Length == 0)
            {
                MessageBox.Show("请先选一个安装目录。", Text, MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }
            try { Directory.CreateDirectory(target); }
            catch (Exception e)
            {
                MessageBox.Show("目录不可写：" + e.Message, Text, MessageBoxButtons.OK, MessageBoxIcon.Error);
                return;
            }
            installButton.Enabled = false;
            exitButton.Enabled = false;
            browseButton.Enabled = false;
            pathBox.Enabled = false;
            napcatBox.Enabled = false;
            bool withNapcat = napcatBox.Checked;
            var thread = new Thread(delegate()
            {
                int code = Program.Install(target, withNapcat, SetStatus, SetPercent);
                Invoke(new Action(delegate
                {
                    installButton.Enabled = true;
                    exitButton.Enabled = true;
                    browseButton.Enabled = true;
                    pathBox.Enabled = true;
                    napcatBox.Enabled = true;
                    if (code == 0)
                    {
                        status.Text = "安装完成！可双击桌面「AI Helper」启动。";
                        if (MessageBox.Show("安装完成，要现在启动 AI Helper 吗？", Text,
                                MessageBoxButtons.YesNo, MessageBoxIcon.Information) == DialogResult.Yes)
                        {
                            string exe = Path.Combine(target, "AIHelper.exe");
                            try { Process.Start(new ProcessStartInfo(exe) { WorkingDirectory = target }); }
                            catch (Exception e) { MessageBox.Show("启动失败：" + e.Message, Text); }
                        }
                        Close();
                    }
                }));
            });
            thread.IsBackground = true;
            thread.Start();
        }
    }
}
