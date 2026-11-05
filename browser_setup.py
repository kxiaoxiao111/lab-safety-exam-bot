# -*- coding: utf-8 -*-
"""
浏览器启动器（统一入口）

优先使用本目录下打包的 Chrome：
    chrome/chrome.exe
    chrome/chromedriver.exe

若没有打包浏览器，则回退到系统 Edge。

日志与登录态都放在本目录，整个文件夹可整体复制。
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CHROME_DIR = os.path.join(HERE, "chrome")
CHROME_EXE = os.path.join(CHROME_DIR, "chrome.exe")
CHROME_DRIVER = os.path.join(CHROME_DIR, "chromedriver.exe")

# 浏览器配置目录（保存登录态）
PROFILE = os.environ.get("LAB_PROFILE") or os.path.join(HERE, ".browser-profile")

# 兼容：若外面已有旧配置目录，沿用
_LEGACY = r"C:\Users\a\Documents\deepseek-harness\default-workspace\.edge-lab-profile"


def has_bundled_chrome():
    return os.path.exists(CHROME_EXE)


def profile_dir():
    os.makedirs(PROFILE, exist_ok=True)
    return PROFILE


def make_driver(headless=False, extra_args=None):
    """
    创建 WebDriver。优先打包 Chrome，失败则自动回退系统 Edge。
    """
    if has_bundled_chrome():
        try:
            return _make_chrome(headless, extra_args)
        except Exception as e:
            print(f"[提示] 打包 Chrome 启动失败（{type(e).__name__}），改用系统 Edge。", flush=True)
            print(f"       原因：{str(e).splitlines()[0][:120]}", flush=True)
    return _make_edge(headless, extra_args)


def _make_chrome(headless=False, extra_args=None):
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.chrome.service import Service as ChromeService
    from selenium import webdriver

    args = list(extra_args or [])
    prof = profile_dir()

    o = ChromeOptions()
    o.binary_location = CHROME_EXE
    o.add_argument(f"--user-data-dir={prof}")
    o.add_argument("--no-first-run")
    o.add_argument("--no-default-browser-check")
    o.add_argument("--ignore-certificate-errors")
    o.add_argument("--window-size=1400,1000")
    o.add_argument("--disable-features=Translate,MediaRouter")
    o.add_argument("--disable-background-networking")
    o.add_argument("--no-sandbox")
    o.add_argument("--disable-dev-shm-usage")
    o.add_argument("--disable-gpu")
    for a in args:
        o.add_argument(a)
    if headless:
        o.add_argument("--headless=new")

    svc = None
    if os.path.exists(CHROME_DRIVER):
        svc = ChromeService(executable_path=CHROME_DRIVER)
    return webdriver.Chrome(options=o, service=svc) if svc else webdriver.Chrome(options=o)


def _make_edge(headless=False, extra_args=None):
    from selenium.webdriver.edge.options import Options as EdgeOptions
    from selenium import webdriver

    args = list(extra_args or [])
    prof = profile_dir()

    o = EdgeOptions()
    o.add_argument(f"--user-data-dir={prof}")
    o.add_argument("--no-first-run")
    o.add_argument("--no-default-browser-check")
    o.add_argument("--ignore-certificate-errors")
    o.add_argument("--window-size=1400,1000")
    for a in args:
        o.add_argument(a)
    if headless:
        o.add_argument("--headless=new")
    return webdriver.Edge(options=o)


def browser_name():
    return "Chrome（打包）" if has_bundled_chrome() else "Edge（系统）"
