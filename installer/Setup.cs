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
        internal const string VERSION = "0.3.6-26.11.15-Fxied";
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

    /// <summary>深色渐变头部：品牌 + 版本 + 副标题。</summary>
    internal class HeaderPanel : Panel
    {
        public HeaderPanel()
        {
            DoubleBuffered = true;
            ResizeRedraw = true;
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics;
            g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
            var rect = new Rectangle(0, 0, Width, Height);
            using (var brush = new System.Drawing.Drawing2D.LinearGradientBrush(
                rect, Color.FromArgb(23, 32, 52), Color.FromArgb(38, 84, 150), 25f))
            {
                g.FillRectangle(brush, rect);
            }
            // 右上角光晕
            using (var glow = new SolidBrush(Color.FromArgb(26, 120, 200, 255)))
            {
                g.FillEllipse(glow, Width - 260, -90, 380, 260);
            }
            using (var accent = new SolidBrush(Color.FromArgb(88, 166, 255)))
            {
                g.FillRectangle(accent, 0, Height - 3, Width, 3);
            }
            using (var titleFont = new Font("Microsoft YaHei UI", 20f, FontStyle.Bold))
            using (var subFont = new Font("Microsoft YaHei UI", 9f))
            using (var verFont = new Font("Microsoft YaHei UI", 8.5f))
            using (var white = new SolidBrush(Color.White))
            using (var soft = new SolidBrush(Color.FromArgb(198, 214, 232)))
            using (var verBrush = new SolidBrush(Color.FromArgb(140, 210, 255)))
            {
                g.DrawString("AI Helper In Minecraft", titleFont, white, 24f, 18f);
                g.DrawString("无客户端 Minecraft AI 玩家 · 点歌 Mod · QQ 转述 · 全部功能内置",
                    subFont, soft, 26f, 58f);
                string ver = "v" + Program.VERSION;
                var size = g.MeasureString(ver, verFont);
                g.DrawString(ver, verFont, verBrush, Width - size.Width - 26f, 20f);
                g.DrawString("一键完全安装", verFont, verBrush, Width - g.MeasureString("一键完全安装", verFont).Width - 26f, 40f);
            }
        }
    }

    /// <summary>自绘的细进度条（比系统 ProgressBar 好看且能显示在深色面板上）。</summary>
    internal class SlimProgress : Control
    {
        int value;
        public SlimProgress()
        {
            DoubleBuffered = true;
            SetStyle(ControlStyles.SupportsTransparentBackColor, true);
        }

        public int Value
        {
            get { return value; }
            set
            {
                this.value = Math.Max(0, Math.Min(100, value));
                Invalidate();
            }
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics;
            g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
            var track = new Rectangle(0, 0, Width - 1, Height - 1);
            using (var bg = new SolidBrush(Color.FromArgb(238, 242, 247)))
            using (var border = new Pen(Color.FromArgb(205, 216, 230)))
            {
                g.FillRectangle(bg, track);
                g.DrawRectangle(border, track);
            }
            if (value > 0)
            {
                int w = (int)((Width - 2) * value / 100.0);
                if (w < 6) w = 6;
                var fill = new Rectangle(1, 1, w, Height - 2);
                using (var brush = new System.Drawing.Drawing2D.LinearGradientBrush(
                    new Rectangle(0, 0, Math.Max(2, Width - 2), Height),
                    Color.FromArgb(46, 134, 255), Color.FromArgb(88, 166, 255), 0f))
                {
                    g.FillRectangle(brush, fill);
                }
            }
        }
    }

    internal class SetupForm : Form
    {
        readonly TextBox pathBox = new TextBox();
        readonly CheckBox napcatBox = new CheckBox();
        readonly SlimProgress bar = new SlimProgress();
        readonly Label percentLabel = new Label();
        readonly Label status = new Label();
        readonly TextBox logBox = new TextBox();
        readonly Button installButton = new Button();
        readonly Button openButton = new Button();
        readonly Button exitButton = new Button();
        readonly Button browseButton = new Button();
        readonly Label sizeHint = new Label();
        readonly StringBuilder logText = new StringBuilder();
        string installedDir = "";

        public SetupForm()
        {
            Text = "AI Helper In Minecraft 安装程序 v" + Program.VERSION;
            ClientSize = new Size(660, 540);
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedSingle;
            MaximizeBox = false;
            BackColor = Color.FromArgb(246, 248, 252);
            Font = new Font("Microsoft YaHei UI", 9f);

            var header = new HeaderPanel();
            header.SetBounds(0, 0, 660, 96);
            Controls.Add(header);

            var features = new Label();
            features.Text = "✓ 内置 Python 3.9 + Node.js 运行时与全部依赖，目标机器无需另装环境\n"
                + "✓ 15 个版本点歌 Mod（1.21.1 ~ 26.2）、服务端插件、源码/测试/文档全部在这一个 exe 里";
            features.ForeColor = Color.FromArgb(72, 90, 112);
            features.SetBounds(24, 112, 612, 40);
            Controls.Add(features);

            var pathLabel = new Label();
            pathLabel.Text = "安装到：";
            pathLabel.SetBounds(24, 166, 70, 22);
            Controls.Add(pathLabel);

            pathBox.SetBounds(96, 164, 446, 25);
            pathBox.Text = defaultInstallDir();
            Controls.Add(pathBox);

            StyleButton(browseButton, Color.White, Color.FromArgb(46, 78, 112));
            browseButton.Text = "浏览…";
            browseButton.SetBounds(548, 163, 88, 27);
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

            napcatBox.Text = "同时安装 NapCat（QQ 转述，可选）";
            napcatBox.SetBounds(96, 198, 300, 22);
            napcatBox.Checked = true;
            napcatBox.CheckedChanged += delegate { UpdateSizeHint(); };
            Controls.Add(napcatBox);

            sizeHint.ForeColor = Color.FromArgb(122, 138, 158);
            sizeHint.SetBounds(96, 222, 540, 20);
            Controls.Add(sizeHint);
            UpdateSizeHint();

            var progLabel = new Label();
            progLabel.Text = "安装进度";
            progLabel.ForeColor = Color.FromArgb(72, 90, 112);
            progLabel.SetBounds(24, 254, 90, 20);
            Controls.Add(progLabel);

            bar.SetBounds(24, 276, 552, 16);
            Controls.Add(bar);

            percentLabel.Text = "0%";
            percentLabel.ForeColor = Color.FromArgb(46, 134, 255);
            percentLabel.SetBounds(586, 272, 52, 22);
            percentLabel.TextAlign = ContentAlignment.MiddleRight;
            Controls.Add(percentLabel);

            status.Text = "点「开始安装」把程序、运行环境与全部功能释放到上面的目录。";
            status.ForeColor = Color.FromArgb(72, 90, 112);
            status.SetBounds(24, 300, 612, 38);
            Controls.Add(status);

            logBox.SetBounds(24, 340, 612, 110);
            logBox.Multiline = true;
            logBox.ReadOnly = true;
            logBox.ScrollBars = ScrollBars.Vertical;
            logBox.BackColor = Color.White;
            logBox.ForeColor = Color.FromArgb(60, 76, 96);
            logBox.BorderStyle = BorderStyle.FixedSingle;
            Controls.Add(logBox);

            StyleButton(installButton, Color.FromArgb(46, 134, 255), Color.White);
            installButton.Text = "开始安装";
            installButton.Font = new Font("Microsoft YaHei UI", 10f, FontStyle.Bold);
            installButton.SetBounds(24, 470, 160, 42);
            installButton.Click += delegate { StartInstall(); };
            Controls.Add(installButton);

            StyleButton(openButton, Color.White, Color.FromArgb(46, 78, 112));
            openButton.Text = "打开安装目录";
            openButton.Enabled = false;
            openButton.SetBounds(196, 470, 140, 42);
            openButton.Click += delegate { OpenFolder(); };
            Controls.Add(openButton);

            StyleButton(exitButton, Color.White, Color.FromArgb(46, 78, 112));
            exitButton.Text = "退出";
            exitButton.SetBounds(544, 470, 92, 42);
            exitButton.Click += delegate { Close(); };
            Controls.Add(exitButton);

            AcceptButton = installButton;
        }

        static void StyleButton(Button button, Color back, Color fore)
        {
            button.FlatStyle = FlatStyle.Flat;
            button.FlatAppearance.BorderSize = 0;
            button.BackColor = back;
            button.ForeColor = fore;
            button.Cursor = Cursors.Hand;
            button.UseVisualStyleBackColor = false;
        }

        void UpdateSizeHint()
        {
            sizeHint.Text = napcatBox.Checked
                ? "预计释放约 330 MB（含 NapCat 约 110 MB）· 全部功能已内置，装完即用"
                : "预计释放约 220 MB（不含 NapCat）· QQ 转述需要 NapCat，可之后单独安装";
        }

        static string defaultInstallDir()
        {
            return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "AIHelperInMinecraft");
        }

        void SetStatus(string text)
        {
            if (InvokeRequired) { BeginInvoke(new Action<string>(SetStatus), text); return; }
            status.Text = text;
            logText.AppendLine("[" + DateTime.Now.ToString("HH:mm:ss") + "] " + text);
            logBox.Text = logText.ToString();
            logBox.SelectionStart = logBox.TextLength;
            logBox.ScrollToCaret();
        }

        void SetPercent(int value)
        {
            if (InvokeRequired) { BeginInvoke(new Action<int>(SetPercent), value); return; }
            if (value < 0) value = 0;
            if (value > 100) value = 100;
            bar.Value = value;
            percentLabel.Text = value + "%";
        }

        void OpenFolder()
        {
            try { Process.Start("explorer.exe", installedDir); }
            catch (Exception e) { MessageBox.Show("打开失败：" + e.Message, Text); }
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
            openButton.Enabled = false;
            exitButton.Enabled = false;
            browseButton.Enabled = false;
            pathBox.Enabled = false;
            napcatBox.Enabled = false;
            installedDir = target;
            bar.Value = 0;
            percentLabel.Text = "0%";
            logText.Clear();
            logBox.Clear();
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
                        openButton.Enabled = true;
                        SetPercent(100);
                        status.Text = "安装完成！双击桌面「AI Helper」或点「打开安装目录」开始使用。";
                        if (MessageBox.Show("安装完成，要现在启动 AI Helper 吗？", Text,
                                MessageBoxButtons.YesNo, MessageBoxIcon.Information) == DialogResult.Yes)
                        {
                            string exe = Path.Combine(target, "AIHelper.exe");
                            try { Process.Start(new ProcessStartInfo(exe) { WorkingDirectory = target }); }
                            catch (Exception e) { MessageBox.Show("启动失败：" + e.Message, Text); }
                        }
                    }
                    else
                    {
                        status.Text = "安装失败（退出码 " + code + "），请查看上方日志。";
                    }
                }));
            });
            thread.IsBackground = true;
            thread.Start();
        }
    }
}
