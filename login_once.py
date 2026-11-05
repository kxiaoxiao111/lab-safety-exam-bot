# -*- coding: utf-8 -*-
"""
登录助手（手动模式，供图形界面调用）

重要：可见的浏览器窗口【只会打开到学校统一身份认证】，
不会先跳到考试系统（那里登录不可用）。

登录态检查用无头（不可见）浏览器完成，检查完立即关闭。
"""
import os, sys, time
from selenium import webdriver
from selenium.webdriver.edge.options import Options
from selenium.webdriver.common.by import By

HERE = os.path.dirname(os.path.abspath(__file__))

_CANDIDATES = [
    os.environ.get("LAB_PROFILE"),
    r"C:\Users\a\Documents\deepseek-harness\default-workspace\.edge-lab-profile",
    os.path.join(HERE, ".edge-lab-profile"),
]
PROFILE = next((p for p in _CANDIDATES if p and os.path.isdir(p)),
               os.path.join(HERE, ".edge-lab-profile"))
os.makedirs(PROFILE, exist_ok=True)

PORTAL = "https://my.just.edu.cn"
EXAM_HOME = "https://aqjy.just.edu.cn/h5/p/custom-home?factoryCode=100000"
TIMEOUT = int(os.environ.get("LAB_LOGIN_TIMEOUT", "600"))


# ---------- 浏览器创建（统一走 browser_setup，优先打包 Chrome） ----------
def new_driver(headless=False):
    import browser_setup
    return browser_setup.make_driver(headless=headless)


# ---------- 登录态判定（必须看内容，不能只看元素是否存在） ----------
def check_logged_in(d):
    """返回 (是否登录, 读到的名字)"""
    try:
        logouts = [e for e in d.find_elements(By.CSS_SELECTOR, ".app_logout") if e.is_displayed()]
    except Exception:
        logouts = []
    name = ""
    try:
        for e in d.find_elements(By.CSS_SELECTOR, ".person_info_card"):
            if e.is_displayed():
                name = (e.text or "").strip()[:20]
                break
    except Exception:
        pass
    # 未登录时 .person_info_card 存在但内容是「登 录」
    if logouts:
        return True, name
    if name and name not in ("登 录", "登录", "登  录"):
        return True, name
    return False, name


def probe_logged_in():
    """用无头浏览器检查考试系统是否已登录（不打扰用户）"""
    try:
        d = new_driver(headless=True)
    except Exception as e:
        print(f"（检查登录态失败：{type(e).__name__}，继续走登录流程）", flush=True)
        return False, ""
    try:
        d.get(EXAM_HOME)
        time.sleep(7)
        return check_logged_in(d)
    except Exception:
        return False, ""
    finally:
        try:
            d.quit()
        except Exception:
            pass


# ---------- 主流程 ----------
print("检查当前登录状态...", flush=True)
ok, who = probe_logged_in()
if ok:
    print(f"✓ 当前已是登录状态（{who}），无需重复登录。", flush=True)
    print("  可以直接点「② 开始考试」。", flush=True)
    sys.exit(0)

print("当前未登录，正在打开学校统一身份认证...", flush=True)

d = new_driver(headless=False)


def handles():
    try:
        return list(d.window_handles)
    except Exception:
        return []


def current_url():
    try:
        return d.current_url or ""
    except Exception:
        return ""


def scan_all_tabs():
    """遍历所有标签页，返回 ('logged_in'|'logged_out'|'none', url, name)"""
    entered = ""
    for h in handles():
        try:
            d.switch_to.window(h)
        except Exception:
            continue
        u = current_url()
        if "aqjy.just.edu.cn" not in u:
            continue
        entered = u
        logged, name = check_logged_in(d)
        if logged:
            return "logged_in", u, name
    if entered:
        return "logged_out", entered, ""
    return "none", "", ""


try:
    d.get(PORTAL)
    time.sleep(5)

    # 关闭浏览器恢复出来的其它标签，避免旧页面干扰
    try:
        main_h = d.current_window_handle
        for h in list(d.window_handles):
            if h != main_h:
                try:
                    d.switch_to.window(h)
                    d.close()
                except Exception:
                    pass
        d.switch_to.window(main_h)
    except Exception:
        pass

    print("", flush=True)
    print("=" * 52, flush=True)
    print("请按以下步骤手动操作：", flush=True)
    print("  1. 在当前页面输入学号密码（统一身份认证）", flush=True)
    print("  2. 进入大厅后，找到并点击「安全教育培训考试」", flush=True)
    print("  3. 进去后不用管，我会自动检测并关闭窗口", flush=True)
    print("=" * 52, flush=True)
    print("正在等待你进入考试系统...", flush=True)

    start = time.time()
    last_state = None
    last_note = 0.0
    result = None
    found_url = ""
    found_name = ""

    while time.time() - start < TIMEOUT:
        if not handles():
            print("浏览器窗口被关闭，登录流程结束。", flush=True)
            sys.exit(0)

        state, where, name = scan_all_tabs()

        if state == "logged_in":
            result, found_url, found_name = "ok", where, name
            break

        if state != last_state:
            if state == "logged_out":
                print("", flush=True)
                print("检测到你已打开考试系统，但显示未登录。", flush=True)
                print("请回到大厅（门户首页），重新点击「安全教育培训考试」。", flush=True)
                print("注意：直接在考试系统页面登录是无效的。", flush=True)
            elif state == "none":
                print("尚未检测到考试系统页面，继续等待...", flush=True)
            last_state = state

        if time.time() - last_note > 20:
            print(f"  ...仍在等待（已等待 {int(time.time()-start)} 秒）", flush=True)
            last_note = time.time()

        time.sleep(3)

    print("", flush=True)
    if result == "ok":
        print(f"✓ 已登录（耗时 {int(time.time()-start)} 秒）", flush=True)
        print(f"  检测页面: {found_url[:70]}", flush=True)
        print(f"  登录账号: {found_name or '(未读到名字)'}", flush=True)
        print("  5 秒后关闭窗口（保存登录状态）。", flush=True)
        time.sleep(5)
    else:
        print(f"! 等待超时（{TIMEOUT} 秒），未检测到进入考试系统。", flush=True)
finally:
    try:
        d.quit()
    except Exception:
        pass
