# -*- coding: utf-8 -*-
"""
江苏科技大学 安全教育培训考试系统 · 自动答题（混合版 v3）

思路：取其精华（API 直连答题）+ 去其糟粕（不再要求考试系统密码）
  - 登录：打开浏览器，你手动走学校统一身份认证（和原来一样）
  - 凭证：从 localStorage['--qsy--member-token'] 自动取出 token
  - 答题：纯 HTTP 接口，全速，100 题约 15-25 秒

用法：
    python auto_exam_hybrid.py
    set EXAM_DURATION=600          # 想放慢到 10 分钟
    set DEEPSEEK_API_KEY=sk-xxx    # 题库未命中时用模型兜底
"""
import html
import http.cookiejar
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

BASE = "https://aqjy.just.edu.cn"
FACTORY = "100000"
MAX_RETRY = 3
DURATION = int(os.environ.get("EXAM_DURATION", "0"))
TOKEN_KEY = "--qsy--member-token"

HERE = os.path.dirname(os.path.abspath(__file__))
BANK_FILE = os.path.join(HERE, "question_bank.json")
LOGDIR = os.path.join(HERE, "日志")
MISS_FILE = os.path.join(HERE, "未匹配题目.txt")
os.makedirs(LOGDIR, exist_ok=True)

STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
AUTH = {"token": None, "cookies": None}
LOGFILE = os.path.join(LOGDIR, f"run_{STAMP}.log")

DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

START_URL = f"{BASE}/h5/p/custom-home?factoryCode={FACTORY}"
# 学校门户（会跳转到统一身份认证）；考试系统不能直接在上面登录
PORTAL_URL = "https://my.just.edu.cn"


def log(msg):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOGFILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ---------------- 题库 ----------------
def norm(s):
    s = html.unescape(s or "")
    return re.sub(r"[\s　:：?？,，.。()（）;；'\"'\"、·\-—]", "", s)


def get_opts(q):
    jd = q.get("jsonData") or {}
    for k in ("single", "multiple", "multi"):
        if k in jd:
            return jd[k].get("options") or []
    return None


def load_bank():
    with open(BANK_FILE, encoding="utf-8") as f:
        banks = json.load(f)
    by_content = {}
    count = 0
    for lib in banks.values():
        if not isinstance(lib, list):
            continue
        for q in lib:
            if not isinstance(q, dict) or "id" not in q:
                continue
            count += 1
            by_content.setdefault(norm(q.get("questionContent")), []).append(q)
    return by_content, count


def answer_for(item, by_content):
    qtype = item["questionType"]
    cands = by_content.get(norm(item.get("questionContent")), [])
    match = None
    if qtype == 4:
        match = cands[0] if cands else None
    else:
        eopts = get_opts(item) or []
        eset = {norm(o.get("optionsContent")) for o in eopts}
        for q in cands:
            qopts = get_opts(q)
            if qopts and len(qopts) == len(eopts) and all(norm(o.get("optionsContent")) in eset for o in qopts):
                match = q
                break
    if not match:
        return None, "题库中未找到该题"
    if qtype == 4:
        return str(match.get("rightAnswers")).lower(), None
    qopts = get_opts(match) or []
    right = {norm(o.get("optionsContent")) for o in qopts if o.get("rightAnswers")}
    eopts = get_opts(item) or []
    idxs = [o["sortIndex"] for o in eopts if norm(o.get("optionsContent")) in right]
    if not idxs:
        return None, "无法确定正确选项"
    if qtype == 2:
        return ",".join(str(i) for i in idxs), None
    return str(idxs[0]), None


def llm_answer(item):
    if not DEEPSEEK_KEY:
        return None
    qtype = item["questionType"]
    qtext = item.get("questionContent", "")
    if qtype == 4:
        user = f"判断题：{qtext}\n只输出 true 或 false（true=正确），不要解释。"
    else:
        opts = get_opts(item) or []
        lines = "\n".join(f"{o['sortIndex']}: {o.get('optionsContent')}" for o in opts)
        rule = "这是多选题，输出所有正确选项序号，英文逗号分隔" if qtype == 2 else "这是单选题，只输出一个序号"
        user = f"题目：{qtext}\n选项：\n{lines}\n{rule}。只输出序号，不要解释。"

    payload = {"model": DEEPSEEK_MODEL,
               "messages": [{"role": "system", "content": "你是实验室安全考试专家，只输出答案。"},
                            {"role": "user", "content": user}],
               "temperature": 0, "max_tokens": 20, "stream": False}
    req = urllib.request.Request("https://api.deepseek.com/chat/completions",
                                 data=json.dumps(payload).encode("utf-8"),
                                 headers={"Authorization": f"Bearer {DEEPSEEK_KEY}",
                                          "Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            obj = json.loads(r.read().decode("utf-8", "replace"))
        txt = (obj["choices"][0]["message"]["content"] or "").strip().lower()
    except Exception as e:
        log(f"    模型调用失败: {type(e).__name__}")
        return None

    if qtype == 4:
        if "true" in txt or "正确" in txt:
            return "true"
        if "false" in txt or "错误" in txt:
            return "false"
        return None
    nums = re.findall(r"\d+", txt)
    valid = {str(o["sortIndex"]) for o in (get_opts(item) or [])}
    nums = [n for n in nums if n in valid]
    if not nums:
        return None
    nums = list(dict.fromkeys(nums))
    return ",".join(nums) if qtype == 2 else nums[0]


# ---------------- 浏览器登录 + 取 token ----------------
def find_token(driver):
    """在所有标签页里找考试系统的 token（localStorage 按域名隔离）"""
    try:
        handles = list(driver.window_handles)
    except Exception:
        return None
    try:
        cur = driver.current_window_handle
    except Exception:
        cur = None

    found = None
    for h in handles:
        try:
            driver.switch_to.window(h)
            u = (driver.current_url or "")
            if "aqjy.just.edu.cn" not in u:
                continue
            tok = driver.execute_script(
                f"return window.localStorage.getItem('{TOKEN_KEY}');")
            if tok:
                found = tok
                break
        except Exception:
            continue
        finally:
            pass

    # 回到原来的标签，避免打断用户
    if cur:
        try:
            driver.switch_to.window(cur)
        except Exception:
            pass
    return found


def list_tabs(driver):
    out = []
    try:
        handles = list(driver.window_handles)
    except Exception:
        return out
    try:
        cur = driver.current_window_handle
    except Exception:
        cur = None
    for h in handles:
        try:
            driver.switch_to.window(h)
            out.append(driver.current_url)
        except Exception:
            out.append("?")
    if cur:
        try:
            driver.switch_to.window(cur)
        except Exception:
            pass
    return out


def read_current_token(driver):
    try:
        return driver.execute_script(
            f"return window.localStorage.getItem('{TOKEN_KEY}');")
    except Exception:
        return None


def scan_iframes(driver, depth=0, max_depth=3):
    """
    有些门户是把应用嵌在 iframe 里打开的（地址栏不变）。
    这里逐层进 iframe 找考试系统，并读它的 localStorage。
    切换 iframe 不会引起窗口闪烁。
    """
    from selenium.webdriver.common.by import By
    if depth > max_depth:
        return None
    found = []
    try:
        frames = driver.find_elements(By.TAG_NAME, "iframe")
    except Exception:
        return None

    for i in range(len(frames)):
        try:
            driver.switch_to.default_content()
            # 重新取，避免 stale
            fs = driver.find_elements(By.TAG_NAME, "iframe")
            if i >= len(fs):
                continue
            src = (fs[i].get_attribute("src") or "")
            driver.switch_to.frame(fs[i])
            try:
                href = driver.execute_script("return location.href") or ""
            except Exception:
                href = ""
            found.append(href or src)
            if "aqjy" in (href + src).lower():
                tok = read_current_token(driver)
                if tok:
                    return tok
            tok = scan_iframes(driver, depth + 1, max_depth)
            if tok:
                return tok
        except Exception:
            continue
        finally:
            try:
                driver.switch_to.default_content()
            except Exception:
                pass

    if depth == 0:
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
    return None


def list_frame_srcs(driver):
    from selenium.webdriver.common.by import By
    out = []
    try:
        for f in driver.find_elements(By.TAG_NAME, "iframe"):
            out.append((f.get_attribute("src") or "")[:90])
    except Exception:
        pass
    return out


def browser_login_and_token():
    """
    登录流程（全自动识别，不闪屏）：
      1. 打开【学校门户】，你登录
      2. 进入大厅后，你手动点「安全教育培训考试」（和平时一样）
      3. 程序自动发现考试系统页面 → 取 token → 开始答题

    关键：只读【当前页地址】做判断，不反复切换标签，所以窗口不会闪。
    只有当你用新标签打开时，才切换一次去看。
    """
    import browser_setup
    driver = browser_setup.make_driver()
    try:
        print("", flush=True)
        print("=" * 60, flush=True)
        print("  你只需要做两步（和平时一样）：", flush=True)
        print("    1) 登录统一身份认证", flush=True)
        print("    2) 进大厅后手动点开「安全教育培训考试」", flush=True)
        print("", flush=True)
        print("  进到考试系统后不用管，我会自动接手开始答题。", flush=True)
        print("=" * 60, flush=True)

        driver.get(PORTAL_URL)
        time.sleep(3)

        end = time.time() + 600
        last_note = 0
        t_start = time.time()
        scanned = set()          # 只用来避免对同一个非考试窗口反复切换
        dumped = False           # 是否已做过诊断输出
        tried_cookie = False     # 是否已尝试 cookie 认证

        while time.time() < end:
            # ---------- 1. 当前窗口是不是考试系统？ ----------
            try:
                u = driver.current_url or ""
            except Exception:
                u = ""

            if "aqjy.just.edu.cn" in u:
                # 已经站在考试系统里了：原地轮询 token，不再切换任何窗口
                tok = read_current_token(driver)
                if not tok:
                    tok = scan_iframes(driver)      # 万一又嵌了一层
                if tok:
                    log(f"✓ 检测到考试系统，取得凭证（长度 {len(tok)}）")
                    AUTH["token"] = tok
                    return True, driver
                elapsed = int(time.time() - t_start)
                if not dumped and elapsed >= 12:
                    log("  [诊断] 还没读到 token，先看看页面里有什么：")
                    dump_storage(driver)
                    dumped = True
                if elapsed - last_note >= 15:
                    log(f"  已在考试系统页，等待凭证初始化（{elapsed}s）：{u[:70]}")
                    last_note = elapsed
                # 25 秒还拿不到 token，就试试用浏览器 Cookie 认证
                if not tried_cookie and elapsed >= 25:
                    tried_cookie = True
                    try:
                        cks = driver.get_cookies() or []
                        log(f"  [尝试] 用浏览器 Cookie 认证（{len(cks)} 个）")
                        api2 = Api(None, cks)
                        who = api2.whoami()
                        log(f"✓ Cookie 认证成功，当前用户: {who}")
                        AUTH["cookies"] = cks
                        return True, driver
                    except Exception as e:
                        log(f"  [尝试] Cookie 认证失败: {type(e).__name__}")
                time.sleep(1)
                continue

            # ---------- 2. 门户页：看看有没有 iframe 内嵌 ----------
            tok = scan_iframes(driver)
            if tok:
                log(f"✓ 在门户内嵌页面里取得凭证（长度 {len(tok)}）")
                AUTH["token"] = tok
                return True, driver

            # ---------- 3. 找新窗口；一旦发现是考试系统就【留住不切回】 ----------
            try:
                handles = list(driver.window_handles)
            except Exception:
                handles = []

            for h in handles:
                if h in scanned:
                    continue
                scanned.add(h)
                try:
                    driver.switch_to.window(h)
                    time.sleep(1)
                    u2 = driver.current_url or ""
                except Exception:
                    continue
                if "aqjy.just.edu.cn" in u2:
                    # 留在该窗口，交给下一轮循环原地轮询
                    log(f"✓ 发现考试系统窗口，切换过去并留在该窗口")
                    break
                # 不是考试系统：切回门户，继续等
                try:
                    for hh in driver.window_handles:
                        driver.switch_to.window(hh)
                        if "aqjy.just.edu.cn" not in (driver.current_url or ""):
                            break
                except Exception:
                    pass

            # ---------- 4. 每 30 秒报状态 ----------
            elapsed = int(time.time() - t_start)
            if elapsed - last_note >= 30:
                log(f"  等待中（{elapsed}s）：{u[:80]}")
                fr = list_frame_srcs(driver)
                if fr:
                    log("    页面内嵌: " + " | ".join(fr[:3]))
                last_note = elapsed
            time.sleep(1.5)

        log("! 等待超时，未能取到凭证")
        dump_storage(driver)
        return False, driver
    except Exception as e:
        log(f"! 浏览器异常: {type(e).__name__}: {e}")
        try:
            driver.quit()
        except Exception:
            pass
        return False, None


def scan_token(driver):
    """扫一遍标签页找 token，返回 (token, 所有标签URL)"""
    tabs, tok = [], None
    try:
        cur = driver.current_window_handle
    except Exception:
        cur = None
    try:
        handles = list(driver.window_handles)
    except Exception:
        return None, []

    for h in handles:
        try:
            driver.switch_to.window(h)
            u = driver.current_url or ""
            tabs.append(u)
            if "aqjy.just.edu.cn" in u:
                t = driver.execute_script(
                    f"return window.localStorage.getItem('{TOKEN_KEY}');")
                if t and not tok:
                    tok = t
        except Exception:
            tabs.append("?")
    if cur:
        try:
            driver.switch_to.window(cur)
        except Exception:
            pass
    return tok, tabs


def dump_storage(driver):
    """把当前页的 storage / cookie 全打出来，用于定位凭证键名"""
    try:
        u = driver.current_url or ""
    except Exception:
        u = "?"
    log(f"  [诊断] 当前页: {u[:90]}")

    # localStorage
    try:
        keys = driver.execute_script(
            "var a=[];for(var i=0;i<localStorage.length;i++)a.push(localStorage.key(i));return a;")
        log(f"  [诊断] localStorage 键({len(keys or [])}): {', '.join((keys or [])[:25])}")
        vals = driver.execute_script(
            "var o={};for(var i=0;i<localStorage.length;i++){var k=localStorage.key(i);"
            "var v=String(localStorage.getItem(k));o[k]=v.length>28?(v.slice(0,20)+'...('+v.length+')'):v;}"
            "return o;")
        if vals:
            for k, v in list(vals.items())[:12]:
                log(f"      {k} = {v}")
    except Exception as e:
        log(f"  [诊断] localStorage 读取失败: {type(e).__name__}")

    # sessionStorage
    try:
        skeys = driver.execute_script(
            "var a=[];for(var i=0;i<sessionStorage.length;i++)a.push(sessionStorage.key(i));return a;")
        log(f"  [诊断] sessionStorage 键({len(skeys or [])}): {', '.join((skeys or [])[:20])}")
    except Exception:
        pass

    # cookies
    try:
        cks = driver.get_cookies() or []
        log(f"  [诊断] cookie({len(cks)}): {', '.join(c['name'] for c in cks[:15])}")
    except Exception:
        pass


def find_clickable_by_text(driver, keywords):
    """找页面上文字匹配的可点击元素"""
    from selenium.webdriver.common.by import By
    tags = ("a", "button", "span", "div", "li", "p")
    for kw in keywords:
        for tag in tags:
            try:
                els = driver.find_elements(By.TAG_NAME, tag)
            except Exception:
                continue
            for e in els:
                try:
                    if not e.is_displayed():
                        continue
                    t = (e.text or "").strip()
                    if t and t == kw:
                        return e, t
                except Exception:
                    continue
    # 退一步：包含匹配
    for kw in keywords:
        for tag in tags:
            try:
                els = driver.find_elements(By.TAG_NAME, tag)
            except Exception:
                continue
            for e in els:
                try:
                    if not e.is_displayed():
                        continue
                    t = (e.text or "").strip()
                    if t and kw in t and len(t) <= 12:
                        return e, t
                except Exception:
                    continue
    return None, None


def click_and_confirm(driver, keywords, desc=""):
    """点击匹配按钮并处理可能出现的确认弹窗"""
    el, txt = find_clickable_by_text(driver, keywords)
    if el is None:
        return False
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        time.sleep(0.3)
        el.click()
        log(f"  已点击「{txt}」{desc}")
    except Exception:
        try:
            driver.execute_script("arguments[0].click();", el)
            log(f"  已用脚本点击「{txt}」{desc}")
        except Exception as e:
            log(f"  点击「{txt}」失败: {type(e).__name__}")
            return False
    time.sleep(1)
    for _ in range(3):
        try:
            driver.switch_to.alert.accept()
            log("  已确认弹窗")
            time.sleep(1)
        except Exception:
            break
    return True


def page_text(driver):
    try:
        from selenium.webdriver.common.by import By
        return driver.find_element(By.TAG_NAME, "body").text or ""
    except Exception:
        return ""


def open_native_result(driver, answer_id):
    """
    交卷后打开考试系统自带的成绩页。
    优先 /exam/finish（点『交卷』后真正的成绩页），
    其次 /exam/answer-grade-own，
    页面报 ERROR 时尝试自动点页面上的『交卷/确定』按钮。
    """
    if driver is None or not answer_id:
        return False

    urls = [
        f"{BASE}/h5/p/exam/finish?answerId={answer_id}&factoryCode={FACTORY}",
        f"{BASE}/h5/p/exam/answer-grade-own?answerId={answer_id}&factoryCode={FACTORY}",
    ]

    for u in urls:
        try:
            driver.get(u)
        except Exception as e:
            log(f"  打开失败 {u[:60]}: {type(e).__name__}")
            continue
        time.sleep(5)

        txt = page_text(driver)
        bad = ("ERROR" in txt.upper()) or ("错误" in txt[:200])
        log(f"  页面状态: {'ERROR' if bad else 'OK'} | {u.split('/h5/p')[-1][:60]}")

        # 如果是 ERROR 页，找「交卷 / 确定 / 查看成绩」点一下
        if bad or not any(k in txt for k in ("分", "成绩", "答对", "对题")):
            for kws in (["交卷"], ["确定"], ["确认"], ["查看成绩"], ["查看详情"], ["返回"]):
                if click_and_confirm(driver, kws, "(错误页兜底)"):
                    time.sleep(3)
                    txt = page_text(driver)
                    if "ERROR" not in txt.upper():
                        break

        txt = page_text(driver)
        if "ERROR" in txt.upper():
            continue
        if any(k in txt for k in ("分", "成绩", "答对", "对题", "用时")):
            log(f"  ✓ 已显示考试系统成绩页")
            return True
        time.sleep(2)

    log("  未能打开考试系统成绩页，改用浮层显示")
    return False

def show_final_score(driver, results):
    """在浏览器页面正中间铺一块大号成绩面板"""
    if driver is None:
        return
    rows = ""
    for n, ok, got, total in results:
        color = "#22c55e" if ok else "#f59e0b"
        rows += (
            f"<div style='margin:14px 0'>"
            f"<div style='color:#94a3b8;font-size:15px'>{n}</div>"
            f"<div style='color:{color};font-size:44px;font-weight:800;line-height:1.1'>"
            f"{got} <span style='font-size:24px;color:#64748b'>/ {total}</span></div>"
            f"</div>"
        )

    js = (
        "(function(){"
        "var old=document.getElementById('__dsh_final');if(old)old.remove();"
        "var d=document.createElement('div');d.id='__dsh_final';"
        "d.style.cssText='position:fixed;z-index:2147483647;left:0;top:0;width:100%;height:100%;"
        "background:rgba(2,6,23,.93);display:flex;align-items:center;justify-content:center;"
        "font-family:system-ui,Segoe UI,Microsoft YaHei,sans-serif;pointer-events:none';"
        "var box=document.createElement('div');"
        "box.style.cssText='text-align:center;color:#e2e8f0;"
        "background:rgba(30,41,59,.85);border:1px solid #334155;border-radius:18px;"
        "padding:34px 60px;box-shadow:0 20px 60px rgba(0,0,0,.6)';"
        "box.innerHTML='<div style=\\'font-size:16px;color:#38bdf8;letter-spacing:2px\\'>考试完成</div>'"
        "+arguments[0];"
        "d.appendChild(box);document.body.appendChild(d);"
        "})();"
    )
    driver.execute_script(js, rows)

# ---------------- API ----------------
class Api:
    def __init__(self, token=None, cookies=None):
        cj = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        self.token = token
        if cookies:
            for c in cookies:
                try:
                    ck = http.cookiejar.Cookie(
                        version=0, name=c.get("name", ""), value=c.get("value", ""),
                        port=None, port_specified=False,
                        domain=c.get("domain", ""), domain_specified=True,
                        domain_initial_dot=False,
                        path=c.get("path", "/"), path_specified=True,
                        secure=bool(c.get("secure")), expires=None, discard=False,
                        comment=None, comment_url=None, rest={}, rfc2109=False)
                    cj.set_cookie(ck)
                except Exception:
                    continue

    def _req(self, method, path, payload=None):
        headers = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
        if self.token:
            headers["Authorization"] = self.token
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
        with self.opener.open(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def get(self, path):
        return self._req("GET", path)

    def post(self, path, payload):
        return self._req("POST", path, payload)

    def whoami(self):
        j = self.get("/h5/api/mine/memberInfo")
        return (j.get("body") or {}).get("nickName", "?")

    def exam_list(self):
        j = self.get("/h5/api/exam/examList?classifyName=&classifyPath=&pageNum=1&pageSize=50")
        return (j.get("body") or {}).get("list") or []

    def start_exam(self, paper_id):
        j = self.get(f"/h5/api/exam/startExam?paperId={paper_id}")
        if str(j.get("code")) != "200":
            raise RuntimeError(f"开考失败（code={j.get('code')}）")
        body = j.get("body")
        if isinstance(body, dict):
            return body.get("answerId") or body.get("id")
        return body

    def examing(self, answer_id):
        j = self.get(f"/h5/api/exam/examing?answerId={answer_id}")
        return (j.get("body") or {}).get("examItems") or []

    def submit_answer(self, answer_id, item_id, content):
        j = self.post("/h5/api/exam/submitAnswer",
                      {"answerId": answer_id, "answerItemId": item_id, "answerContent": content})
        return str(j.get("code")) == "200" and (j.get("body") or {}).get("finish") == 1

    def finish_exam(self, answer_id):
        return self.get(f"/h5/api/exam/finishExam?answerId={answer_id}&finishType=1")

    def score(self, answer_id):
        j = self.get(f"/h5/api/exam/findExam?answerId={answer_id}")
        items = (j.get("body") or {}).get("examItems") or []
        total = sum(i.get("originalScore") or 0 for i in items)
        got = sum(i.get("realScore") or 0 for i in items)
        return got, total


# ---------------- 浏览器内可视化面板 ----------------
OVERLAY_ID = "__dsh_auto_panel"
VISUAL = os.environ.get("EXAM_VISUAL", "1") != "0"


def overlay_ping(driver):
    """确保面板存在（页面重渲染后可能被冲掉）"""
    if not VISUAL or driver is None:
        return
    js = ("(function(){"
          "if(document.getElementById('" + OVERLAY_ID + "'))return;"
          "var d=document.createElement('div');d.id='" + OVERLAY_ID + "';"
          "d.style.cssText='position:fixed;z-index:2147483647;left:14px;bottom:14px;"
          "background:rgba(15,23,42,.94);color:#e2e8f0;border:1px solid #334155;"
          "font:13px/1.7 Consolas,Menlo,monospace;padding:12px 16px;border-radius:12px;"
          "box-shadow:0 10px 30px rgba(0,0,0,.5);min-width:300px;pointer-events:none';"
          "document.body.appendChild(d);})();")
    try:
        driver.execute_script(js)
    except Exception:
        pass


def overlay_update(driver, cur, total, last_q, last_a, ok, llm, miss):
    if not VISUAL or driver is None:
        return
    pct = int(cur * 100 / total) if total else 0
    bar_w = max(2, pct)
    html = (
        f"<div style='font-size:14px;font-weight:700;color:#38bdf8;margin-bottom:6px'>"
        f"自动答题中 &nbsp;{cur}/{total}</div>"
        f"<div style='background:#1e293b;height:8px;border-radius:4px;overflow:hidden;margin-bottom:8px'>"
        f"<div style='background:linear-gradient(90deg,#22c55e,#38bdf8);height:8px;width:{bar_w}%'></div></div>"
        f"<div style='color:#94a3b8'>已提交 <b style='color:#22c55e'>{ok}</b>"
        f" 模型兜底 <b style='color:#fbbf24'>{llm}</b>"
        f" 未匹配 <b style='color:#f87171'>{miss}</b></div>"
        f"<div style='margin-top:6px;color:#cbd5e1;max-width:420px;white-space:nowrap;"
        f"overflow:hidden;text-overflow:ellipsis'>{last_q[:46]}</div>"
        f"<div style='color:#38bdf8'>→ {last_a}</div>"
    )
    try:
        driver.execute_script(
            "var d=document.getElementById(arguments[0]);"
            "if(d){d.innerHTML=arguments[1];return true;}return false;",
            OVERLAY_ID, html)
    except Exception:
        pass


def overlay_done(driver, got, total):
    if not VISUAL or driver is None:
        return
    ok = (got == total)
    color = "#22c55e" if ok else "#f87171"
    html = (f"<div style='font-size:14px;font-weight:700;color:{color}'>"
            f"{'✓ 满分通过' if ok else '本轮结束'}</div>"
            f"<div style='font-size:22px;font-weight:800;color:#e2e8f0;margin-top:4px'>"
            f"{got} / {total}</div>")
    try:
        driver.execute_script(
            "var d=document.getElementById(arguments[0]);"
            "if(d){d.innerHTML=arguments[1];}", OVERLAY_ID, html)
    except Exception:
        pass


# ---------------- 主流程 ----------------
def do_exam(api, paper, by_content, driver=None):
    name = paper.get("name")
    paper_id = paper.get("paperId")
    log(f"=== {name} (paperId={paper_id}) ===")

    for attempt in range(1, MAX_RETRY + 1):
        try:
            answer_id = api.start_exam(paper_id)
        except RuntimeError as e:
            log(f"  {e}")
            return False, 0, 0
        if not answer_id:
            log("  开考失败：未获得 answerId（可能次数已用完）")
            return False, 0, 0

        t0 = time.time()
        items = api.examing(answer_id)
        log(f"  第 {attempt} 次, answerId={answer_id}, {len(items)} 题"
            + ("，全速作答" if DURATION <= 0 else f"，限速 {DURATION}s"))

        delay = max((DURATION - 30) / len(items), 0) if (DURATION > 0 and items) else 0

        unmatched, used_llm, ok = [], 0, 0
        overlay_ping(driver)
        for idx, it in enumerate(items, 1):
            ans, _ = answer_for(it, by_content)
            label = ans
            if ans is None:
                ans = llm_answer(it)
                if ans is not None:
                    used_llm += 1
                    log(f"    第{idx}题未命中，模型兜底 -> {ans}")
                    label = f"{ans}（模型）"
                else:
                    unmatched.append(it.get("questionContent", "")[:80])
                    print(f"\r  进度 {idx}/{len(items)} 未匹配", end="", flush=True)
                    continue
            if api.submit_answer(answer_id, it["id"], ans):
                ok += 1
            # 可视化：每 2 题刷新一次面板，兼顾速度
            if idx % 2 == 0 or idx == len(items):
                overlay_update(driver, idx, len(items),
                               it.get("questionContent", ""), str(label),
                               ok, used_llm, len(unmatched))
            if idx % 5 == 0 or idx == len(items):
                print(f"\r  进度 {idx}/{len(items)}  已答 {ok}  未匹配 {len(unmatched)}",
                      end="", flush=True)
            if delay:
                time.sleep(delay * random.uniform(0.5, 1.5))
        print("", flush=True)

        cost = time.time() - t0
        log(f"  已提交 {ok}/{len(items)}（模型兜底 {used_llm}，用时 {cost:.1f} 秒）")

        if unmatched:
            log(f"  [警告] {len(unmatched)} 题未匹配，已写入 未匹配题目.txt")
            try:
                with open(MISS_FILE, "a", encoding="utf-8") as f:
                    f.write(f"\n# {STAMP} {name}\n")
                    for u in unmatched:
                        f.write(u + "\n")
            except Exception:
                pass

        if DURATION > 0 and cost < DURATION:
            log(f"  补足时长，{DURATION-cost:.0f} 秒后交卷...")
            time.sleep(DURATION - cost)

        api.finish_exam(answer_id)
        got, total = api.score(answer_id)
        if not open_native_result(driver, answer_id):
            overlay_done(driver, got, total)   # 兜底：跳转失败才用浮层
        log(f"  成绩: {got}/{total}（总用时 {time.time()-t0:.1f} 秒）")

        if got == total:
            log("  ✓ 满分通过")
            return True, got, total
        if unmatched:
            log("  有未匹配题目，需补库后才能提分")
            return False, got, total
        time.sleep(1)

    log("  ✗ 达到最大重考次数仍未满分")
    return False, locals().get("got", 0), locals().get("total", 0)


def main():
    if not os.path.exists(BANK_FILE):
        log(f"缺少题库文件: {BANK_FILE}")
        sys.exit(1)

    by_content, total_q = load_bank()
    log("=" * 56)
    log(f"题库: {total_q} 条 / {len(by_content)} 个唯一题干")
    log(f"模式: {'全速' if DURATION <= 0 else f'限速 {DURATION}s'}"
        f" | 模型兜底: {'开' if DEEPSEEK_KEY else '关'}")
    log("=" * 56)

    ok, driver = browser_login_and_token()
    if not ok:
        log("登录失败，退出")
        sys.exit(2)

    api = Api(AUTH.get("token"), AUTH.get("cookies"))
    try:
        who = api.whoami()
        log(f"接口验证通过，当前用户: {who}")
    except Exception as e:
        log(f"! 用该凭证访问接口失败: {type(e).__name__}")
        try:
            j = api.get("/h5/api/exam/examList?pageNum=1&pageSize=5")
            log(f"  examList 返回: {str(j)[:200]}")
        except Exception as e2:
            log(f"  examList 失败: {type(e2).__name__}")
        log("提示：若接口返回 403，可能是凭证字段不对，把日志发我")
        sys.exit(3)

    exams = api.exam_list()
    if not exams:
        log("当前没有可参加的考试")
    else:
        log(f"发现 {len(exams)} 门考试：")
        for i, e in enumerate(exams, 1):
            log(f"  {i}. {e.get('name')}  (有效期至 {e.get('expireTime')})")

        results = []
        for e in exams:
            ok, got, total = do_exam(api, e, by_content, driver)
            results.append((e.get("name"), ok, got, total))

        log("===== 汇总 =====")
        for n, ok, got, total in results:
            log(("✓ " if ok else "✗ ") + f"{n}   {got}/{total}")

        # ---- 成绩单：页面大字 + 文本文件 ----
        # 浏览器已经停在考试系统原生成绩页，不再叠加自制浮层
        pass

        try:
            txt = os.path.join(HERE, "成绩.txt")
            with open(txt, "w", encoding="utf-8") as f:
                f.write(f"考试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                for n, ok, got, total in results:
                    f.write(f"{n}    {got} / {total}    {'满分' if ok else '未满分'}\n")
            log(f"  成绩已保存: {txt}")
        except Exception:
            pass

        print("", flush=True)
        print("=" * 60, flush=True)
        for n, ok, got, total in results:
            print(f"  {'✓' if ok else '✗'}  {n}   {got}/{total}", flush=True)
        print("=" * 60, flush=True)
        print("  浏览器里已用大字显示成绩，看完按回车关闭浏览器。", flush=True)
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass

    log(f"日志: {LOGFILE}")
    try:
        if driver:
            driver.quit()
    except Exception:
        pass


if __name__ == "__main__":
    main()
