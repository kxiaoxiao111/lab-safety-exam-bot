# -*- coding: utf-8 -*-
"""
江苏科技大学 安全教育培训考试系统 —— 自动答题脚本
- 复用已登录的 Edge 配置（SSO 会话）
- 可见窗口运行，方便观察
- 题目交给通义千问 qwen-plus 判断，按答案点击选项

用法:
    python just_auto_answer.py            # 练习模式，答 10 题
    python just_auto_answer.py 30         # 答 30 题
"""
import os, sys, re, time, json
from selenium import webdriver
from selenium.webdriver.edge.options import Options
from selenium.webdriver.common.by import By

try:
    import dashscope
    from dashscope import Generation
    HAS_LLM = True
except Exception:
    dashscope = None
    Generation = None
    HAS_LLM = False

# ---------------- 配置 ----------------
API_KEY = os.environ.get("DASHSCOPE_API_KEY", "").strip()
MODEL = os.environ.get("QWEN_MODEL", "qwen-plus")
HERE = os.path.dirname(os.path.abspath(__file__))
OUTDIR = os.environ.get("LAB_OUTDIR") or HERE
# 浏览器配置目录：与登录模块保持一致
try:
    import browser_setup
    PROFILE = browser_setup.PROFILE
except Exception:
    PROFILE = os.environ.get("LAB_PROFILE") or os.path.join(HERE, ".browser-profile")
BASE = "https://aqjy.just.edu.cn/h5/p/exam/exercise?factoryCode=100000"

try:
    N = int(sys.argv[1]) if len(sys.argv) > 1 else 10
except ValueError:
    N = 10

if API_KEY and HAS_LLM:
    dashscope.api_key = API_KEY


# ---------------- 题库 ----------------
BANKFILE = os.path.join(OUTDIR, "bank.json")


def _norm(s):
    return re.sub(r'[\s（）()、，,。．.？?！!：:；;“”\"\'\']', '', s or '')


def load_bank_index():
    """题库前缀索引"""
    try:
        with open(BANKFILE, encoding="utf-8") as f:
            b = json.load(f)
    except Exception:
        return {}
    idx = {}
    for v in b.values():
        k = _norm(v.get("q", ""))[:32]
        if k:
            idx.setdefault(k, v)
    return idx


def lookup_bank(idx, qtext, opts):
    """返回要点击的字母列表；未命中返回 []"""
    if not idx:
        return []
    n = _norm(qtext)
    hit = None
    for ln in (32, 28, 24, 20):
        hit = idx.get(n[:ln])
        if hit:
            break
    if not hit:
        return []
    key = (hit.get("key") or "").strip()
    if not key:
        return []
    ct = _norm(hit.get("correct_text", ""))
    # 单答案：按选项文本精确定位，避免顺序差异
    if len(key) == 1 and ct:
        for j, o in enumerate(opts):
            if _norm(o) == ct:
                return [chr(65 + j)]
    return [L for L in key if ord(L) - 65 < len(opts)]


# ---------------- 问模型 ----------------
SYS_PROMPT = "你是化工与实验室安全专家。只输出答案，不要解释。"

def ask(question, options, qtype):
    """返回选项字母列表，如 ['B'] 或 ['A','C']"""
    if not API_KEY or not HAS_LLM:
        print("    [提示] 大模型不可用（未配置 Key 或未安装依赖），本题跳过", flush=True)
        return []
    if qtype == "多选题":
        rule = "这是多选题，输出所有正确选项的大写字母，连写不要加标点，例如 ABD"
    elif qtype in ("判断题", "是非题"):
        rule = "这是判断题，只输出 对 或 错"
    else:
        rule = "这是单选题，只输出一个大写字母，例如 B"

    opt_lines = "\n".join(options)
    user = f"""{rule}。

题目：{question}

选项：
{opt_lines}

只输出答案本身，不要任何解释。"""

    try:
        r = Generation.call(
            model=MODEL,
            messages=[{"role": "system", "content": SYS_PROMPT},
                      {"role": "user", "content": user}],
            result_format="message",
        )
        if r.status_code != 200:
            print(f"    [API {r.status_code}] {getattr(r,'code',None)}", flush=True)
            return []
        text = r.output.choices[0]["message"]["content"].strip()
    except Exception as e:
        print(f"    [API 异常] {type(e).__name__}: {e}", flush=True)
        return []

    upper = text.upper()
    if "对" in upper and "错" not in upper:
        return ["对"]
    if "错" in upper:
        return ["错"]
    letters = re.findall(r"[A-D]", upper)
    return list(dict.fromkeys(letters))


# ---------------- 页面操作 ----------------
def read_question(d):
    """返回 (题型, 题干, [(字母, 文本), ...])"""
    qtype = ""
    try:
        el = d.find_element(By.CSS_SELECTOR, ".questiontype-box")
        qtype = re.sub(r"纠错", "", el.text or "").strip()
        qtype = re.sub(r"^\d+\s*、\s*", "", qtype).strip()
    except Exception:
        pass

    qtext = ""
    try:
        qtext = (d.find_element(By.CSS_SELECTOR, ".question-content").text or "").strip()
    except Exception:
        pass
    if not qtext:
        return qtype, "", []

    opts = []
    try:
        labels = d.find_elements(By.CSS_SELECTOR, "label.el-radio.options")
        if not labels:
            labels = d.find_elements(By.CSS_SELECTOR, "label.el-checkbox.options")
        for lab in labels:
            txt = (lab.text or "").strip()
            m = re.match(r"^([A-D])\s*、\s*(.*)$", txt, re.S)
            if m:
                opts.append((m.group(1), txt))
            elif txt:
                opts.append((chr(ord("A") + len(opts)), txt))
    except Exception as e:
        print(f"    读取选项失败: {e}", flush=True)

    return qtype, qtext, opts


def click_option(d, letter):
    """点击指定字母的选项"""
    labels = d.find_elements(By.CSS_SELECTOR, "label.el-radio.options")
    if not labels:
        labels = d.find_elements(By.CSS_SELECTOR, "label.el-checkbox.options")
    idx = ord(letter) - ord("A")
    if idx < 0 or idx >= len(labels):
        return False
    try:
        labels[idx].click()
        return True
    except Exception:
        try:
            labels[idx].find_element(By.CSS_SELECTOR, ".el-radio__inner").click()
            return True
        except Exception:
            try:
                labels[idx].find_element(By.TAG_NAME, "input").click()
                return True
            except Exception:
                return False


def click_next(d):
    """点击下一题"""
    try:
        btns = d.find_elements(By.CSS_SELECTOR, ".btn-box button")
        for b in btns:
            if "下一题" in (b.text or ""):
                b.click()
                return True
    except Exception:
        pass
    return False


def feedback_text(d):
    """练习模式可能给出对错提示"""
    for sel in [".el-message-box__message", ".el-message", ".analysis-box", ".answer-analysis"]:
        try:
            els = [e for e in d.find_elements(By.CSS_SELECTOR, sel) if e.is_displayed()]
            if els:
                return (els[0].text or "").strip()[:200]
        except Exception:
            pass
    return ""


# ---------------- 主流程 ----------------
def raise_browser_window():
    """把 Edge 窗口置顶，方便观察"""
    try:
        import ctypes, ctypes.wintypes as wt
        u32 = ctypes.WinDLL("user32", use_last_error=True)

        def title(h):
            n = u32.GetWindowTextLengthW(h)
            b = ctypes.create_unicode_buffer(n + 1)
            u32.GetWindowTextW(h, b, n + 1)
            return b.value

        targets = []
        CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

        def cb(h, l):
            t = title(h)
            if t and u32.IsWindowVisible(h) and ("安全教育培训考试系统" in t or "答题练习" in t):
                targets.append((h, t))
            return True

        u32.EnumWindows(CB(cb), 0)
        for h, t in targets:
            u32.ShowWindow(h, 9)      # SW_RESTORE
            u32.SetForegroundWindow(h)
            u32.BringWindowToTop(h)
            print(f"窗口置顶: {t[:60]}", flush=True)
    except Exception as e:
        print("置顶失败:", e, flush=True)


def main():
    import browser_setup
    d = browser_setup.make_driver()
    transcript = []
    try:
        d.get(BASE)
        time.sleep(5)
        raise_browser_window()
        if "login" in d.current_url.lower():
            print("!! SSO 会话已失效，请先在浏览器里登录学校门户，再运行本脚本")
            return

        # 进入第 3 个题库（通识类）的答题模式
        btns = [b for b in d.find_elements(By.XPATH, "//*[contains(text(),'开始练习')]") if b.is_displayed()]
        if not btns:
            print("!! 没找到练习入口")
            return
        (btns[2] if len(btns) > 2 else btns[0]).click()
        time.sleep(5)
        for el in d.find_elements(By.XPATH, "//*[contains(text(),'答题模式')]"):
            if el.is_displayed():
                el.click()
                break
        time.sleep(6)
        if len(d.window_handles) > 1:
            d.switch_to.window(d.window_handles[-1])
            time.sleep(3)

        # 载入题库，练习模式也优先用标准答案
        bank_idx = load_bank_index()
        print(f"答题页: {d.current_url}", flush=True)
        print(f"准备回答 {N} 题，题库 {len(bank_idx)} 条，模型 {MODEL}\n", flush=True)

        for i in range(N):
            qtype, qtext, opts = read_question(d)
            if not qtext:
                print(f"第 {i+1} 题读取失败，停止", flush=True)
                break

            print(f"── 第 {i+1} 题 [{qtype}] ──", flush=True)
            print(f"  题目: {qtext[:100]}", flush=True)
            for letter, txt in opts:
                print(f"    {txt[:80]}", flush=True)

            ans = lookup_bank(bank_idx, qtext, [t for _, t in opts])
            if ans:
                print(f"  题库答案: {ans}", flush=True)
            else:
                ans = ask(qtext, [t for _, t in opts], qtype)
                print(f"  模型答案: {ans}", flush=True)

            ok = []
            for letter in ans:
                if letter in ("对", "错"):
                    # 判断题：按选项文本匹配
                    for j, (_, txt) in enumerate(opts):
                        if letter in txt:
                            labels = d.find_elements(By.CSS_SELECTOR, "label.el-radio.options")
                            labels[j].click()
                            ok.append(letter)
                            break
                else:
                    if click_option(d, letter):
                        ok.append(letter)
            print(f"  已点击: {ok}", flush=True)

            time.sleep(1.5)
            fb = feedback_text(d)
            if fb:
                print(f"  页面反馈: {fb}", flush=True)

            transcript.append({"n": i + 1, "type": qtype, "q": qtext,
                               "opts": [t for _, t in opts], "ans": ans, "clicked": ok, "fb": fb})

            if i < N - 1:
                if not click_next(d):
                    print("  翻页失败，停止", flush=True)
                    break
                time.sleep(2.5)

        with open(os.path.join(OUTDIR, "answer_transcript.json"), "w", encoding="utf-8") as f:
            json.dump(transcript, f, ensure_ascii=False, indent=2)
        print(f"\n完成，记录 {len(transcript)} 题 -> answer_transcript.json", flush=True)
        print("浏览器保持 5 分钟供查看，之后自动关闭", flush=True)
        time.sleep(300)
    finally:
        d.quit()


if __name__ == "__main__":
    main()
