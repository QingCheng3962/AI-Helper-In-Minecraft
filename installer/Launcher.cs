using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows.Forms;

namespace AafmLauncher
{
    /// <summary>启动器：用安装目录里内置的 Python + Node 运行 main.py。</summary>
    static class Program
    {
        [STAThread]
        static void Main(string[] args)
        {
            string root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
            string pythonw = Path.Combine(root, "runtime", "python", "pythonw.exe");
            string python = Path.Combine(root, "runtime", "python", "python.exe");
            string main = Path.Combine(root, "main.py");
            string nodeDir = Path.Combine(root, "runtime", "node");
            if (!File.Exists(main))
            {
                MessageBox.Show("找不到 main.py，安装不完整？请重新安装。", "AI Helper",
                    MessageBoxButtons.OK, MessageBoxIcon.Error);
                return;
            }
            if (!File.Exists(pythonw)) pythonw = python;
            if (!File.Exists(pythonw))
            {
                MessageBox.Show("找不到内置 Python 运行时（runtime\\python）。", "AI Helper",
                    MessageBoxButtons.OK, MessageBoxIcon.Error);
                return;
            }
            // 让 .js 引擎和 node 命令优先用随包带的 Node
            string path = Environment.GetEnvironmentVariable("PATH") ?? "";
            if (Directory.Exists(nodeDir)) path = nodeDir + ";" + Path.Combine(root, "runtime", "python") + ";" + path;
            Environment.SetEnvironmentVariable("PATH", path);
            Environment.SetEnvironmentVariable("PYTHONIOENCODING", "utf-8");
            Environment.SetEnvironmentVariable("AAF_INSTALL_DIR", root);

            var sb = new StringBuilder();
            sb.Append('"').Append(main).Append('"');
            foreach (string a in args) sb.Append(' ').Append('"').Append(a.Replace("\"", "\\\"")).Append('"');
            var info = new ProcessStartInfo(pythonw, sb.ToString());
            info.WorkingDirectory = root;
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            try
            {
                Process.Start(info);
            }
            catch (Exception e)
            {
                MessageBox.Show("启动失败：" + e.Message, "AI Helper",
                    MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }
    }
}
