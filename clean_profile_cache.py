# -*- coding: utf-8 -*-
"""
清理浏览器配置目录里的缓存，保留登录态（Cookies / Local State）。
可单独运行：python clean_profile_cache.py
"""
import os, shutil, sys

CACHE_DIRS = [
    "Default/Cache", "Default/Code Cache", "Default/GPUCache",
    "Default/DawnWebGPUCache", "Default/DawnGraphiteCache",
    "Default/Service Worker", "Default/CacheStorage",
    "Default/GrShaderCache", "Default/ShaderCache",
    "Default/Edge Entity Extraction", "Default/Edge Wallet",
    "Default/Subresource Filter",
    "GrShaderCache", "ShaderCache", "component_crx_cache",
    "WidevineCdm", "Edge Entity Extraction", "Edge Wallet",
    "Subresource Filter", "Edge Shopping", "BrowserMetrics",
    "EdgeLanguageDetectionModel", "Speech Recognition",
]

KEEP = ["Local State", "Default/Network/Cookies", "Default/Preferences"]


def profile_path():
    here = os.path.dirname(os.path.abspath(__file__))
    # 优先用 browser_setup 认定的配置目录（打包版在文件夹内）
    try:
        import browser_setup
        p = browser_setup.PROFILE
        if os.path.isdir(p):
            return p
    except Exception:
        pass
    for p in [os.environ.get("LAB_PROFILE"),
              r"C:\Users\a\Documents\deepseek-harness\default-workspace\.edge-lab-profile",
              os.path.join(here, ".edge-lab-profile")]:
        if p and os.path.isdir(p):
            return p
    return None


def size_of(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def clean(verbose=True):
    """返回 (清理前MB, 清理后MB)。登录态文件不会被删除。"""
    prof = profile_path()
    if not prof:
        if verbose:
            print("未找到浏览器配置目录")
        return None
    before = size_of(prof)
    for rel in CACHE_DIRS:
        target = os.path.join(prof, rel.replace("/", os.sep))
        if os.path.isdir(target):
            shutil.rmtree(target, ignore_errors=True)
    after = size_of(prof)
    if verbose:
        print(f"缓存清理完成：{before/1024/1024:.1f} MB -> {after/1024/1024:.1f} MB")
        for k in KEEP:
            f = os.path.join(prof, k.replace("/", os.sep))
            print(f"  {'✓' if os.path.exists(f) else '✗'} {k}")
    return (before / 1024 / 1024, after / 1024 / 1024)


if __name__ == "__main__":
    clean()
