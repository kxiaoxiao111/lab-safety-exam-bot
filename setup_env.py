# -*- coding: utf-8 -*-
"""
环境自检（对应「1 首次使用·准备环境」）

自动适配两种版本：
  - 完整版：带 chrome\\ 目录，用打包的 Chrome
  - 轻量版：没有 chrome\\ 目录，用系统自带的 Edge

任何一项失败都会明确提示，并停住不关窗口。
"""
import os, sys, socket

HERE = os.path.dirname(os.path.abspath(__file__))
CHROME_EXE = os.path.join(HERE, "chrome", "chrome.exe")
CHROME_DRIVER = os.path.join(HERE, "chrome", "chromedriver.exe")
LINE = "=" * 52

EDGE_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

BUNDLED = os.path.exists(CHROME_EXE)


def ok(msg):
    print(f"  [通过] {msg}", flush=True)
    return True


def bad(msg, hint=""):
    print(f"  [失败] {msg}", flush=True)
    if hint:
        print(f"         {hint}", flush=True)
    return False


def check_python():
    v = sys.version.split()[0]
    if sys.version_info >= (3, 10):
        return ok(f"Python {v}")
    return bad(f"Python 版本过低（{v}）", "请重新解压完整包")


def check_selenium():
    try:
        import selenium
        return ok(f"selenium {selenium.__version__}")
    except Exception as e:
        return bad("selenium 依赖缺失", f"{e}；请重新解压")


def check_browser():
    if BUNDLED:
        if os.path.exists(CHROME_EXE):
            return ok("Chrome 浏览器（已打包）")
        return bad("缺少 chrome\\chrome.exe", "请确认压缩包完整解压")
    # 轻量版：找系统 Edge
    for p in EDGE_PATHS:
        if os.path.exists(p):
            return ok("Microsoft Edge（系统自带）")
    return bad("未找到 Edge 浏览器",
               "本版本需要系统 Edge；Win10/11 一般自带，若已被卸载请装回")


def check_driver():
    if BUNDLED:
        if os.path.exists(CHROME_DRIVER):
            return ok("chromedriver（已打包）")
        return bad("缺少 chrome\\chromedriver.exe", "请重新解压完整包")
    return ok("驱动由系统自动匹配（Edge）")


def check_network():
    try:
        socket.setdefaulttimeout(8)
        ip = socket.gethostbyname("aqjy.just.edu.cn")
        return ok(f"网络可达考试系统（{ip}）")
    except Exception as e:
        return bad("无法解析考试系统域名",
                   f"{type(e).__name__}；请检查网络或关闭代理后重试")


def check_browser_launch():
    try:
        import browser_setup
        d = browser_setup.make_driver(headless=True)
        try:
            d.get("about:blank")
            ver = d.capabilities.get("browserVersion", "?")
            name = d.capabilities.get("browserName", "?")
            return ok(f"浏览器可正常启动（{name} {ver}）")
        finally:
            d.quit()
    except Exception as e:
        first = str(e).splitlines()
        detail = first[1] if len(first) > 1 else first[0]
        return bad("浏览器启动失败", f"{detail[:160]}")


def main():
    print("", flush=True)
    print(LINE, flush=True)
    print("  实验室安全考试 · 环境自检", flush=True)
    print(f"  版本：{'完整版（自带 Chrome）' if BUNDLED else '轻量版（用系统 Edge）'}", flush=True)
    print(LINE, flush=True)
    print("", flush=True)

    checks = [
        ("Python 运行时", check_python),
        ("依赖库", check_selenium),
        ("浏览器", check_browser),
        ("驱动", check_driver),
        ("网络", check_network),
        ("启动测试", check_browser_launch),
    ]

    passed = 0
    for name, fn in checks:
        print(f"[{passed+1}/{len(checks)}] {name}", flush=True)
        try:
            good = fn()
        except Exception as e:
            good = bad("检查过程出错", f"{type(e).__name__}: {e}")
        if not good:
            print("", flush=True)
            print(LINE, flush=True)
            print("  ! 环境未就绪，请按上面的提示处理后重试。", flush=True)
            print(LINE, flush=True)
            input("\n按回车键退出...")
            return 1
        passed += 1
        print("", flush=True)

    print(LINE, flush=True)
    print("  全部通过，环境已就绪。", flush=True)
    print("", flush=True)
    print("  以后请双击「2 启动程序」开始使用。", flush=True)
    print("  第一次使用需要登录一次学校账号（约 1 分钟）。", flush=True)
    print(LINE, flush=True)
    input("\n按回车键退出...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
