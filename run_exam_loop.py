# -*- coding: utf-8 -*-
"""
自动考试循环：题库作答 -> 交卷 -> 解析分数 -> 未满100分则抓标准答案入库 -> 重考
最多 10 次。
"""
import os, sys, time, json, re
from selenium import webdriver
from selenium.webdriver.edge.options import Options
from selenium.webdriver.common.by import By

# 大模型是可选功能：没装 dashscope 也能正常运行（题库优先）
try:
    import dashscope
    from dashscope import Generation
    HAS_LLM = True
except Exception:
    dashscope = None
    Generation = None
    HAS_LLM = False

HERE = os.path.dirname(os.path.abspath(__file__))

# 输出目录：脚本所在目录（跟着脚本走，可整体复制）
OUTDIR = os.environ.get("LAB_OUTDIR") or HERE

# 浏览器配置目录：必须与登录模块完全一致，否则登录成功但开考时是未登录状态
try:
    import browser_setup
    PROFILE = browser_setup.PROFILE
except Exception:
    PROFILE = os.environ.get("LAB_PROFILE") or os.path.join(HERE, ".browser-profile")

BANKFILE = os.path.join(OUTDIR, "bank.json")
LIST_URL = "https://aqjy.just.edu.cn/h5/p/exam/exam?factoryCode=100000"
MAX_ATTEMPTS = int(os.environ.get("MAX_ATTEMPTS", "10"))

API_KEY = os.environ.get("DASHSCOPE_API_KEY", "").strip()
MODEL = os.environ.get("QWEN_MODEL", "qwen-plus")
if API_KEY and HAS_LLM:
    dashscope.api_key = API_KEY
if API_KEY and not HAS_LLM:
    print("[提示] 未安装 dashscope，大模型功能不可用（题库仍可正常作答）。", flush=True)


def norm(s):
    return re.sub(r'[\s（）()、，,。．.？?！!：:；;“”\"\'\']', '', s or '')


def load_bank():
    if os.path.exists(BANKFILE):
        return json.load(open(BANKFILE, encoding="utf-8"))
    return {}


def save_bank(b):
    json.dump(b, open(BANKFILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def build_index(bank):
    idx = {}
    for v in bank.values():
        k = norm(v.get("q", ""))[:32]
        if k:
            idx.setdefault(k, v)
    return idx


def lookup(idx, q):
    n = norm(q)
    for ln in (32, 28, 24, 20):
        v = idx.get(n[:ln])
        if v:
            return v
    return None


def T(d):
    try:
        return d.find_element(By.TAG_NAME, "body").text or ""
    except Exception:
        return ""


def read_q(d):
    qtext, opts, labels = "", [], []
    try:
        qtext = (d.find_element(By.CSS_SELECTOR, ".question-content").text or "").strip()
    except Exception:
        pass
    for sel in ["label.el-radio.options", "label.el-checkbox.options",
                "label.el-radio", "label.el-checkbox"]:
        labels = [x for x in d.find_elements(By.CSS_SELECTOR, sel) if x.is_displayed()]
        if labels:
            break
    for i, lab in enumerate(labels):
        raw = re.sub(r"\s+", " ", (lab.text or "").strip())
        raw = re.sub(r"^[A-D]\s*[、.]\s*", "", raw)
        opts.append(raw)
    return qtext, opts, labels


def wait_mask(d, timeout=8):
    """等待 el-loading 遮罩消失"""
    end = time.time() + timeout
    while time.time() < end:
        try:
            masks = [m for m in d.find_elements(By.CSS_SELECTOR, ".el-loading-mask") if m.is_displayed()]
            if not masks:
                return True
        except Exception:
            return True
        time.sleep(0.3)
    return False


def js_click(d, el):
    try:
        d.execute_script("arguments[0].scrollIntoView({block:'center'}); arguments[0].click();", el)
        return True
    except Exception:
        return False


def wait_question_change(d, prev, timeout=10):
    """等题干真正变化（避免旧 DOM 被误读）"""
    end = time.time() + timeout
    while time.time() < end:
        try:
            cur = (d.find_element(By.CSS_SELECTOR, ".question-content").text or "").strip()
        except Exception:
            cur = ""
        if cur and cur != prev:
            time.sleep(0.4)   # 再留一点渲染时间给选项
            return cur
        time.sleep(0.25)
    return None


def pick_option(opts, target):
    """按文本精确匹配选项，返回下标；找不到返回 None"""
    t = norm(target)
    if not t:
        return None
    # 1) 完全相等
    for j, o in enumerate(opts):
        if norm(o) == t:
            return j
    # 2) 互为前缀且长度接近（防止「水」命中「无水乙醇」）
    for j, o in enumerate(opts):
        no = norm(o)
        if not no:
            continue
        if (no.startswith(t) or t.startswith(no)) and min(len(no), len(t)) >= 4 \
           and abs(len(no) - len(t)) <= max(4, int(0.35 * max(len(no), len(t)))):
            return j
    return None


def is_checked(d, lab):
    try:
        return d.execute_script(
            "return arguments[0].classList.contains('is-checked') || "
            "(arguments[0].querySelector('input') && arguments[0].querySelector('input').checked);",
            lab)
    except Exception:
        return False


def click_label(d, lab):
    wait_mask(d)
    try:
        lab.click()
    except Exception:
        try:
            lab.find_element(By.CSS_SELECTOR, ".el-radio__inner, .el-checkbox__inner").click()
        except Exception:
            js_click(d, lab)
    time.sleep(0.35)
    if not is_checked(d, lab):
        js_click(d, lab)
        time.sleep(0.35)
    return is_checked(d, lab)


def click_next(d):
    wait_mask(d)
    for attempt in range(3):
        for b in d.find_elements(By.CSS_SELECTOR, ".btn-box button, button"):
            try:
                if "下一题" in (b.text or "") and not b.get_attribute("disabled"):
                    try:
                        b.click()
                        return True
                    except Exception:
                        if js_click(d, b):
                            return True
            except Exception:
                pass
        time.sleep(0.8)
        wait_mask(d)
    return False


def ask_llm(q, opts):
    """大模型兜底（可选）。未配置 Key 或未安装依赖时直接返回空。"""
    if not API_KEY or not HAS_LLM:
        print("    [提示] 大模型不可用（未配置 Key 或未安装依赖），本题按题库结果处理", flush=True)
        return []
    lines = "\n".join(f"{chr(65+i)}、{t}" for i, t in enumerate(opts))
    try:
        r = Generation.call(model=MODEL,
                            messages=[{"role": "system", "content": "你是实验室安全专家，只输出答案字母。"},
                                      {"role": "user", "content": f"题目：{q}\n选项：\n{lines}\n选出正确选项，多选输出多个字母，只输出字母。"}],
                            result_format="message")
        if r.status_code == 200:
            txt = (r.output.choices[0]["message"]["content"] or "").upper()
            return list(dict.fromkeys(re.findall(r"[A-D]", txt)))
    except Exception:
        pass
    return []


def has_result(d):
    """结果页判定：出现「答对 N 题」或「分数」"""
    t = T(d)
    return bool(re.search(r"答对\s*\d+\s*题", t) or re.search(r"\d{1,3}\s*\n?\s*分数", t))


def is_result_url(d):
    u = d.current_url
    return ("/exam/answer" in u) or ("/exam/plan" in u) or ("result" in u)


def submit(d, timeout=120):
    """
    状态驱动的交卷：
    1. 点「交卷」
    2. 之后每一轮先判断是否已经到结果页；是就结束
    3. 否则若出现确认弹窗，优先点「继续交卷」
    4. 直到出现成绩或超时；超时截图留证
    """
    print("  交卷中...", flush=True)
    start_url = d.current_url

    # 第 1 步：点交卷按钮（优先精确按钮，避免命中"答题卡"里的文字）
    clicked = False
    for el in d.find_elements(By.XPATH, "//button[contains(.,'交卷')]"):
        try:
            if el.is_displayed():
                js_click(d, el)
                clicked = True
                print("  点击交卷按钮", flush=True)
                break
        except Exception:
            pass
    if not clicked:
        for el in d.find_elements(By.XPATH, "//*[contains(text(),'交卷')]"):
            try:
                if el.is_displayed():
                    js_click(d, el)
                    clicked = True
                    print("  点击交卷(文字)", flush=True)
                    break
            except Exception:
                pass

    # 第 2 步：状态轮询
    end = time.time() + timeout
    stage = 0
    while time.time() < end:
        if has_result(d):
            print("  已到成绩页", flush=True)
            return True

        dlg_btn = None
        for sel in [".el-message-box__btns button", ".el-dialog__footer button", "button"]:
            for b in d.find_elements(By.CSS_SELECTOR, sel):
                try:
                    txt = (b.text or "").strip()
                    if b.is_displayed() and txt in ("继续交卷", "确定", "确认"):
                        dlg_btn = b
                        break
                except Exception:
                    pass
            if dlg_btn:
                break

        if dlg_btn is not None:
            print(f"  弹窗确认: {dlg_btn.text.strip()}", flush=True)
            js_click(d, dlg_btn)
            stage += 1
            time.sleep(1.5)
            continue

        if is_result_url(d) and d.current_url != start_url:
            # URL 已跳转，等渲染
            time.sleep(2)
            if has_result(d):
                return True

        time.sleep(1.5)

    # 超时：截图 + 保存现场
    try:
        shot = os.path.join(OUTDIR, "submit_timeout.png")
        d.save_screenshot(shot)
        print(f"  !! 交卷超时，截图: {shot}", flush=True)
        open(os.path.join(OUTDIR, "submit_timeout.txt"), "w", encoding="utf-8").write(
            d.current_url + "\n" + T(d)[:3000])
    except Exception:
        pass
    return False


def parse_score(t):
    m = re.search(r"答对\s*(\d{1,3})\s*题", t)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d{1,3})\s*\n?\s*分数", t)
    if m:
        return int(m.group(1))
    m = re.search(r"分数\D{0,6}(\d{1,3})", t)
    return int(m.group(1)) if m else None


def harvest_answers(d, aid):
    """抓取答题结果页全部标准答案，入库"""
    bank = load_bank()
    before = len(bank)
    d.get(f"https://aqjy.just.edu.cn/h5/p/exam/answer?answerId={aid}&factoryCode=100000")
    time.sleep(10)
    # 回第1题
    for _ in range(120):
        hit = False
        for b in d.find_elements(By.TAG_NAME, "button"):
            try:
                if "上一题" in (b.text or "") and not b.get_attribute("disabled"):
                    b.click(); hit = True; break
            except Exception:
                pass
        if not hit:
            break
        time.sleep(0.25)
    time.sleep(1.5)

    wrong = []
    for i in range(100):
        t = T(d)
        mt = re.search(r"(\d+)\s*、\s*(单选题|多选题|判断题)", t)
        # 多选题答案可能写成 "A、C、D" / "ACD"，必须全部抓出
        m = re.search(r"正确答案[：:]\s*([A-D](?:\s*[、,，/\s]\s*[A-D])*)", t)
        if mt and m:
            key = "".join(re.findall(r"[A-D]", m.group(1)))
            seg = t[mt.end():]
            ai = seg.find("A、")
            if ai > 0:
                qtext = re.sub(r"\s+", " ", re.sub(r"^纠错\s*", "", seg[:ai])).strip()
                rest = seg[ai:]
                e = re.search(r"正确答案", rest)
                if e:
                    rest = rest[:e.start()]
                parts = re.split(r"([A-D])、\s*", rest)
                opts = []
                for j in range(1, len(parts) - 1, 2):
                    txt = re.sub(r"\s+", " ", parts[j + 1]).strip()
                    # 去掉判分文字污染
                    txt = re.sub(r"\s*(答对了|答错了|得分[：:].*)$", "", txt).strip()
                    opts.append((parts[j], txt))
                ctext = "、".join(x for L, x in opts if L in key)
                bank[norm(qtext)[:80]] = {"q": qtext, "type": mt.group(2), "key": key,
                                          "correct_text": ctext, "opts": opts}
                if "答对了" not in t:
                    wrong.append({"q": qtext, "correct": key + "、" + ctext})
        ok = False
        for b in d.find_elements(By.TAG_NAME, "button"):
            try:
                if "下一题" in (b.text or "") and not b.get_attribute("disabled"):
                    b.click(); ok = True; break
            except Exception:
                pass
        if not ok:
            break
        time.sleep(0.5)
    save_bank(bank)
    print(f"  题库入库 {before} -> {len(bank)}；本轮错 {len(wrong)} 题", flush=True)
    for w in wrong:
        print("    错:", w["q"][:40], "->", w["correct"][:24], flush=True)
    return bank, wrong


def start_exam(d):
    """从列表进入考试并开始"""
    d.get(LIST_URL)
    time.sleep(6)
    for el in d.find_elements(By.XPATH, "//*[contains(text(),'材料学院实验室安全考试')]"):
        try:
            if el.is_displayed():
                el.click(); break
        except Exception:
            pass
    time.sleep(8)
    if len(d.window_handles) > 1:
        d.switch_to.window(d.window_handles[-1]); time.sleep(4)
    for el in d.find_elements(By.XPATH, "//*[contains(text(),'开始考试')]"):
        try:
            if el.is_displayed():
                el.click(); break
        except Exception:
            pass
    time.sleep(10)
    for t in ["确定", "确认"]:
        for b in d.find_elements(By.XPATH, f"//button[contains(.,'{t}')]"):
            try:
                if b.is_displayed() and b.is_enabled():
                    b.click(); time.sleep(5); break
            except Exception:
                pass
    time.sleep(4)
    m = re.search(r"answerId=([0-9a-f]+)", d.current_url)
    return m.group(1) if m else None


def run_attempt(d, idx, n):
    resume = os.environ.get("RESUME_AID", "").strip()
    if resume:
        d.get(f"https://aqjy.just.edu.cn/h5/p/strat/exam?answerId={resume}&factoryCode=100000")
        time.sleep(10)
        # 回到第 1 题
        for _ in range(120):
            hitb = False
            for b in d.find_elements(By.TAG_NAME, "button"):
                try:
                    if "上一题" in (b.text or "") and not b.get_attribute("disabled"):
                        b.click(); hitb = True; break
                except Exception:
                    pass
            if not hitb:
                break
            time.sleep(0.25)
        time.sleep(2)
        aid = resume
        print(f"\n===== 续做第 {n} 次考试 answerId={aid} =====", flush=True)
    else:
        aid = start_exam(d)
        print(f"\n===== 第 {n} 次考试 开始 answerId={aid} =====", flush=True)
    if not aid:
        print("  开考失败", flush=True)
        return None, None

    log = []
    for i in range(100):
        try:
            q, opts, labels = read_q(d)
        except Exception as e:
            print(f"  第{i+1}题读取异常: {type(e).__name__}", flush=True)
            break
        if not q or not labels:
            print(f"  第{i+1}题读取失败（q={bool(q)}, labels={len(labels)}）", flush=True)
            time.sleep(1.5)
            q, opts, labels = read_q(d)
            if not q or not labels:
                break
        hit = lookup(idx, q)
        targets = []      # 要点击的选项下标
        src = "bank"
        if hit:
            key = (hit.get("key") or "").strip()
            is_multi = (hit.get("type") == "多选题") or len(key) > 1
            # 多选题却只存了一个字母：题库数据不可靠，交给大模型多选判断
            if is_multi and len(key) == 1 and len(opts) >= 3:
                llm = ask_llm("（这是多选题，可能有多项正确）" + q, opts)
                if len(llm) > 1:
                    key = "".join(llm)
                    src = "llm(多选补全)"
                    hit = {**hit, "opts": [(chr(65 + i), t) for i, t in enumerate(opts)],
                           "key": key}
            if is_multi:
                # 多选题：按每个字母对应的选项原文逐一匹配，全部点选
                for L in key:
                    txt = ""
                    for bl, bt in (hit.get("opts") or []):
                        if bl == L:
                            txt = bt
                            break
                    jj = pick_option(opts, txt) if txt else None
                    if jj is None:
                        k = ord(L) - 65
                        if 0 <= k < len(labels):
                            jj = k
                    if jj is not None and jj not in targets:
                        targets.append(jj)
            else:
                j = pick_option(opts, hit.get("correct_text", ""))
                if j is not None:
                    targets = [j]
                elif key:
                    k = ord(key[0]) - 65
                    if 0 <= k < len(labels):
                        targets = [k]
        if not targets:
            src = "llm"
            for L in ask_llm(q, opts):
                k = ord(L) - 65
                if 0 <= k < len(labels):
                    targets.append(k)

        wait_mask(d)
        clicked = []
        for j in targets:
            if 0 <= j < len(labels):
                if click_label(d, labels[j]):
                    clicked.append(chr(65 + j))
                else:
                    print(f"     !! 选项 {chr(65+j)} 点击未生效", flush=True)
        print(f"  [{i+1}] {q[:34]} | 源={src} 填={clicked}", flush=True)
        log.append({"i": i+1, "q": q, "src": src, "ans": clicked})
        time.sleep(0.4)
        if i < 99:
            prev = q
            if not click_next(d):
                print("  翻页提前结束", flush=True)
                break
            if not wait_question_change(d, prev):
                print("  题干未变化，重试翻页", flush=True)
                if not click_next(d):
                    break
                wait_question_change(d, prev)
            time.sleep(0.3)

    json.dump(log, open(os.path.join(OUTDIR, f"attempt_{n}.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    ok = submit(d)
    score = None
    for _ in range(10):
        t = T(d)
        score = parse_score(t)
        if score is not None:
            break
        time.sleep(2)
    if not ok and score is None:
        print("  !! 交卷结果未知，请看 submit_timeout.png / .txt", flush=True)
    print(f"  >>> 第 {n} 次成绩: {score}", flush=True)
    print("  " + T(d)[:300].replace("\n", " | "), flush=True)
    return score, aid


def main():
    import browser_setup
    d = browser_setup.make_driver()
    results = []
    try:
        bank = load_bank()
        idx = build_index(bank)
        print(f"题库 {len(bank)} 条，索引 {len(idx)} 条", flush=True)

        for n in range(1, MAX_ATTEMPTS + 1):
            score, aid = run_attempt(d, idx, n)
            results.append({"attempt": n, "score": score, "answerId": aid})
            json.dump(results, open(os.path.join(OUTDIR, "attempts_summary.json"), "w", encoding="utf-8"),
                      ensure_ascii=False, indent=2)
            if score == 100:
                print(f"\n*** 第 {n} 次满分 100，结束 ***", flush=True)
                break
            if aid:
                bank, wrong = harvest_answers(d, aid)
                idx = build_index(bank)
            time.sleep(3)
        else:
            print("\n已达最大次数", flush=True)

        print("\n汇总:", flush=True)
        for r in results:
            print("  ", r["attempt"], "->", r["score"], flush=True)

        # 收尾：清理浏览器缓存，保持目录小巧（不影响登录态）
        try:
            import clean_profile_cache
            b, a = clean_profile_cache.clean(verbose=False)
            print(f"\n已清理浏览器缓存：{b:.1f} MB -> {a:.1f} MB", flush=True)
        except Exception as e:
            print("缓存清理跳过:", e, flush=True)
    finally:
        time.sleep(20)
        d.quit()


if __name__ == "__main__":
    main()
