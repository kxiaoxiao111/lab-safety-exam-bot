# -*- coding: utf-8 -*-
"""
实验室安全考试 · 图形界面
双击 一键考试.bat 启动，或直接 python exam_gui.py
"""
import os, sys, time, json, threading, queue, subprocess, traceback

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

SUMMARY = os.path.join(HERE, "attempts_summary.json")
BANK = os.path.join(HERE, "bank.json")


class QueueWriter:
    """把 print 输出转到界面，同时写入日志文件（方便排查）"""
    def __init__(self, q, logfile=None):
        self.q = q
        self.logfile = logfile

    def write(self, s):
        if not s:
            return
        self.q.put(s)
        if self.logfile:
            try:
                with open(self.logfile, "a", encoding="utf-8") as f:
                    f.write(s)
            except Exception:
                pass

    def flush(self):
        pass


def make_logfile():
    """按时间生成日志文件路径，返回路径"""
    try:
        d = os.path.join(HERE, "日志")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, time.strftime("运行_%Y%m%d_%H%M%S.log"))
    except Exception:
        return None


class App:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.worker = None
        self.stop_flag = False

        root.title("实验室安全考试 · 自动答题")
        root.geometry("900x640")
        root.minsize(760, 520)

        # 顶部信息
        top = ttk.Frame(root, padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="江苏科技大学 · 安全教育培训考试系统",
                  font=("Microsoft YaHei", 13, "bold")).pack(anchor="w")

        self.bank_info = tk.StringVar(value=self.bank_text())
        ttk.Label(top, textvariable=self.bank_info, foreground="#555").pack(anchor="w", pady=(2, 0))

        # 控制区
        ctrl = ttk.LabelFrame(root, text="操作", padding=10)
        ctrl.pack(fill="x", padx=10, pady=6)

        row1 = ttk.Frame(ctrl)
        row1.pack(fill="x")

        self.btn_login = ttk.Button(row1, text="① 打开登录窗口", command=self.do_login, width=18)
        self.btn_login.pack(side="left", padx=(0, 8))

        self.btn_exam = ttk.Button(row1, text="② 开始考试", command=self.do_exam, width=16)
        self.btn_exam.pack(side="left", padx=(0, 8))

        self.btn_practice = ttk.Button(row1, text="练习模式", command=self.do_practice, width=12)
        self.btn_practice.pack(side="left", padx=(0, 8))

        self.btn_open = ttk.Button(row1, text="打开文件夹", command=self.open_dir, width=12)
        self.btn_open.pack(side="left", padx=(0, 8))

        self.btn_clean = ttk.Button(row1, text="清理缓存", command=self.do_clean, width=11)
        self.btn_clean.pack(side="left")

        row2 = ttk.Frame(ctrl)
        row2.pack(fill="x", pady=(8, 0))

        ttk.Label(row2, text="最多考").pack(side="left")
        self.attempts = tk.StringVar(value="10")
        ttk.Spinbox(row2, from_=1, to=20, width=5, textvariable=self.attempts).pack(side="left", padx=(4, 4))
        ttk.Label(row2, text="次").pack(side="left", padx=(0, 16))

        ttk.Label(row2, text="大模型Key(可留空)").pack(side="left")
        self.apikey = tk.StringVar(value=os.environ.get("DASHSCOPE_API_KEY", ""))
        ttk.Entry(row2, textvariable=self.apikey, width=34, show="*").pack(side="left", padx=4)

        # 状态
        self.status = tk.StringVar(value="就绪")
        ttk.Label(root, textvariable=self.status, foreground="#0a6", padding=(12, 2)).pack(anchor="w")

        # 日志
        logf = ttk.LabelFrame(root, text="运行日志", padding=6)
        logf.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        self.log = scrolledtext.ScrolledText(logf, height=20, font=("Consolas", 9))
        self.log.pack(fill="both", expand=True)

        # 历史
        hist = ttk.LabelFrame(root, text="历史成绩", padding=6)
        hist.pack(fill="x", padx=10, pady=(0, 10))
        self.hist_label = tk.StringVar(value=self.hist_text())
        ttk.Label(hist, textvariable=self.hist_label).pack(anchor="w")

        self.root.after(200, self.pump)

    # ---------- 信息 ----------
    def bank_text(self):
        try:
            with open(BANK, encoding="utf-8") as f:
                n = len(json.load(f))
            return f"题库：{n} 题 · 已就绪"
        except Exception:
            return "题库：未找到 bank.json"

    def hist_text(self):
        try:
            with open(SUMMARY, encoding="utf-8") as f:
                data = json.load(f)
            return "  ".join(f"第{d['attempt']}次 {d['score']}分" for d in data) or "暂无"
        except Exception:
            return "暂无"

    def refresh_info(self):
        self.bank_info.set(self.bank_text())
        self.hist_label.set(self.hist_text())

    # ---------- 日志 ----------
    def write_log(self, s):
        self.log.insert("end", s)
        self.log.see("end")

    def pump(self):
        try:
            while True:
                s = self.q.get_nowait()
                self.write_log(s)
        except queue.Empty:
            pass
        self.root.after(200, self.pump)

    # ---------- 动作 ----------
    def set_busy(self, busy, text=""):
        state = "disabled" if busy else "normal"
        for b in (self.btn_login, self.btn_exam, self.btn_practice):
            b.configure(state=state)
        if text:
            self.status.set(text)

    def do_login(self):
        self.set_busy(True, "等待登录...")
        self.write_log("\n=== 打开浏览器，请登录学校账号 ===\n")

        def run():
            try:
                p = subprocess.Popen([sys.executable, os.path.join(HERE, "login_once.py")],
                                     cwd=HERE,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, encoding="utf-8", errors="replace")
                for line in p.stdout:
                    self.q.put(line)
                p.wait()
            except Exception as e:
                self.q.put(f"登录出错: {e}\n")
            finally:
                self.q.put("\n=== 登录流程结束 ===\n")
                self.root.after(0, lambda: self.set_busy(False, "就绪"))

        threading.Thread(target=run, daemon=True).start()

    def do_practice(self):
        self.write_log("\n=== 练习模式（不消耗考试次数）===\n")
        self.set_busy(True, "练习中...")
        key = self.apikey.get().strip()

        def run():
            try:
                import just_auto_answer as J
                if key and getattr(J, "HAS_LLM", False):
                    try:
                        import dashscope
                        dashscope.api_key = key
                    except Exception:
                        self.q.put("[提示] 未安装 dashscope，大模型不可用，将只用题库作答。\n")
                sys.stdout = QueueWriter(self.q, make_logfile())
                J.main()
            except Exception:
                self.q.put(traceback.format_exc())
            finally:
                sys.stdout = sys.__stdout__
                self.q.put("\n=== 练习结束 ===\n")
                self.root.after(0, lambda: self.set_busy(False, "就绪"))

        threading.Thread(target=run, daemon=True).start()

    def do_exam(self):
        n = self.attempts.get().strip() or "10"
        try:
            n = max(1, min(20, int(n)))
        except Exception:
            n = 10
        if not messagebox.askokcancel("确认", f"将开始正式考试，最多考 {n} 次。\n\n确定继续吗？"):
            return
        self.write_log(f"\n=== 开始考试（最多 {n} 次）===\n")
        self.set_busy(True, "考试进行中...")
        key = self.apikey.get().strip()

        def run():
            try:
                import run_exam_loop as R
                R.MAX_ATTEMPTS = n
                if key:
                    R.API_KEY = key
                    if getattr(R, "HAS_LLM", False):
                        try:
                            import dashscope
                            dashscope.api_key = key
                        except Exception:
                            self.q.put("[提示] 未安装 dashscope，大模型不可用，将只用题库作答。\n")
                    else:
                        self.q.put("[提示] 未安装 dashscope，大模型不可用，将只用题库作答。\n")
                sys.stdout = QueueWriter(self.q, make_logfile())
                R.main()
            except Exception:
                self.q.put(traceback.format_exc())
            finally:
                sys.stdout = sys.__stdout__
                self.q.put("\n=== 考试流程结束 ===\n")
                self.root.after(0, lambda: self.set_busy(False, "就绪"))
                self.root.after(500, self.refresh_info)

        threading.Thread(target=run, daemon=True).start()

    def open_dir(self):
        try:
            os.startfile(HERE)
        except Exception as e:
            messagebox.showerror("错误", str(e))

    def do_clean(self):
        try:
            import clean_profile_cache as C
            b, a = C.clean(verbose=False) or (0, 0)
            msg = f"缓存已清理：{b:.1f} MB → {a:.1f} MB\n登录状态保留。"
            self.write_log("\n" + msg + "\n")
            messagebox.showinfo("完成", msg)
        except Exception as e:
            messagebox.showerror("错误", str(e))


def main():
    root = tk.Tk()
    try:
        root.iconbitmap(default="")
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
