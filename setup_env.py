# -*- coding: utf-8 -*-
"""环境自检（对应「1 首次使用·准备环境」）"""
import os, sys, socket

HERE = os.path.dirname(os.path.abspath(__file__))
CHROME_EXE = os.path.join(HERE, "chrome", "chrome.exe")
CHROME_DRIVER = os.path.join(HERE, "chrome", "chromedriver.exe")
LINE = "=" * 52


def ok(m):
    print(f"  [通过] {m}", flush=True)
    return True


def bad(m, hint=""):
    print(f"  [失败] {m}", flush=True)
    if hint:
        print(f"         {hint}", flush=True)
    return False


def c_python():
    v = sys.version.split()[0]
    return ok(f"Python {v}") if sys.version_info >= (3, 10) else bad(f"Python 版本过低（{v}）")


def c_selenium():
    try:
        import selenium
        return ok(f"selenium {selenium.__version__}")
    except Exception as e:
        return bad("selenium 缺失", f"{e}；请重新解压")


def c_chrome():
    return ok("Chrome 浏览器（已打包）") if os.path.exists(CHROME_EXE) \
        else bad("缺少 chrome\\chrome.exe", "请确认完整解压")


def c_driver():
    return ok("chromedriver（已打包）") if os.path.exists(CHROME_DRIVER) \
        else bad("缺少 chrome\\chromedriver.exe", "请重新解压")


def c_net():
    socket.setdefaulttimeout(8)
    try:
        ip = socket.gethostbyname("aqjy.just.edu.cn")
        return ok(f"网络可达考试系统（{ip}）")
    except Exception:
        return bad("无法连接考试系统", "请检查网络或关闭代理")


def c_launch():
    try:
        import browser_setup
        d = browser_setup.make_driver(headless=True)
        try:
            d.get("about:blank")
            return ok(f"浏览器可正常启动（Chrome {d.capabilities.get('browserVersion','?')}）")
        finally:
            d.quit()
    except Exception as e:
        lines = str(e).splitlines()
        return bad("浏览器启动失败", (lines[1] if len(lines) > 1 else lines[0])[:160])


def main():
    print("", flush=True)
    print(LINE, flush=True)
    print("  安全考试自动答题 · 环境自检", flush=True)
    print(LINE, flush=True)
    print("", flush=True)

    checks = [("Python", c_python), ("依赖库", c_selenium), ("浏览器", c_chrome),
              ("驱动", c_driver), ("网络", c_net), ("启动测试", c_launch)]

    for i, (name, fn) in enumerate(checks, 1):
        print(f"[{i}/{len(checks)}] {name}", flush=True)
        try:
            good = fn()
        except Exception as e:
            good = bad("检查出错", f"{type(e).__name__}: {e}")
        if not good:
            print("", flush=True)
            print(LINE, flush=True)
            print("  ! 环境未就绪，请按提示处理后重试。", flush=True)
            print(LINE, flush=True)
            input("\n按回车键退出...")
            return 1
        print("", flush=True)

    print(LINE, flush=True)
    print("  全部通过，环境已就绪。", flush=True)
    print("", flush=True)
    print("  以后请双击「2 开始考试.cmd」。", flush=True)
    print("  运行后会打开浏览器，你登录一次即可，答题全自动。", flush=True)
    print(LINE, flush=True)
    input("\n按回车键退出...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
