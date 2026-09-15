#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# Orihost 免费服务器自动续期脚本 (支持 Cloudflare 验证)
# 基于 Jexactyl 面板 API + SeleniumBase 处理 Cloudflare Turnstile
# 参考: https://github.com/414256150/host-shipRenew
# ============================================================
import os
import sys
import re
import time
import json
import requests
from urllib.parse import unquote
from datetime import datetime, timezone, timedelta
from seleniumbase import SB

# ============================================================
# 配置区域
# ============================================================
BASE_URL = "https://panel.orihost.com"
PANEL_URL = "https://orihost.com"

# ============================================================
# 代理配置（可选）
# ============================================================
ORIHOST_PROXY = os.environ.get("ORIHOST_PROXY") or ""
PROXIES = {}
if ORIHOST_PROXY:
    PROXIES = {"http": ORIHOST_PROXY, "https": ORIHOST_PROXY}
    print(f" 尝试使用 ORIHOST_PROXY: {ORIHOST_PROXY}")
    try:
        requests.get("http://www.gstatic.com/generate_204", proxies=PROXIES, timeout=5)
        print(f" ✅ 代理可用")
    except Exception:
        print(f" ⚠️ 代理不可达 ({ORIHOST_PROXY})，回退直连")
        PROXIES = {}
else:
    http_proxy = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or ""
    https_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or ""
    if http_proxy or https_proxy:
        PROXIES = {"http": http_proxy, "https": https_proxy or http_proxy}
        print(f" 使用 HTTP_PROXY: {http_proxy}")

# ============================================================
# Telegram 配置
# ============================================================
TG_CHAT_ID = os.environ.get("TG_CHAT_ID") or ""
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN") or ""

# ============================================================
# 多账号检测
# ============================================================
ACCOUNTS = []
for i in range(1, 100):
    cookie = os.environ.get(f"ORIHOST_COOKIE_{i}")
    if cookie:
        server_ids_raw = os.environ.get(f"ORIHOST_SERVER_IDS_{i}") or ""
        server_ids = [s.strip() for s in server_ids_raw.split(",") if s.strip()]
        if not server_ids:
            print(f"⚠️ 账号{i} ORIHOST_SERVER_IDS_{i} 为空，跳过")
            continue
        ACCOUNTS.append({
            "cookie": cookie,
            "server_ids": server_ids,
            "label": f"账号{i}"
        })
    else:
        break

# 向下兼容：单账号
if not ACCOUNTS:
    legacy_cookie = os.environ.get("ORIHOST_COOKIE") or ""
    if legacy_cookie:
        server_ids_raw = os.environ.get("ORIHOST_SERVER_IDS") or ""
        server_ids = [s.strip() for s in server_ids_raw.split(",") if s.strip()]
        if server_ids:
            ACCOUNTS.append({
                "cookie": legacy_cookie,
                "server_ids": server_ids,
                "label": "默认账号"
            })

if not ACCOUNTS:
    print("❌ 未配置任何 Cookie，脚本终止。")
    print(" 单账号: 设置 ORIHOST_COOKIE + ORIHOST_SERVER_IDS")
    print(" 多账号: 设置 ORIHOST_COOKIE_1 + ORIHOST_SERVER_IDS_1,")
    print("         ORIHOST_COOKIE_2 + ORIHOST_SERVER_IDS_2, ...")
    sys.exit(1)

print(f" 检测到 {len(ACCOUNTS)} 个账号")
for acc in ACCOUNTS:
    print(f" {acc['label']}: {', '.join(acc['server_ids'])}")

# ============================================================
# 辅助函数
# ============================================================
def send_telegram(message: str):
    """发送 Telegram 通知"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("⚠️ Telegram 未配置，跳过通知")
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TG_CHAT_ID, "text": message}, timeout=10, proxies=PROXIES or None)
        print(" ✅ Telegram 通知已发送")
    except Exception as e:
        print(f" ❌ Telegram 发送失败: {e}")


def parse_cookies(cookie_str: str) -> dict:
    """将 Cookie 字符串解析为字典，并对每个值做 URL 解码"""
    cookies = {}
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" in item:
            key, value = item.split("=", 1)
            cookies[key.strip()] = unquote(value.strip())
    return cookies


def get_xsrf_token(cookie_str: str) -> str:
    """从 Cookie 中提取 XSRF-TOKEN（URL 解码后用作请求头）"""
    cookies = parse_cookies(cookie_str)
    return cookies.get("XSRF-TOKEN", "")


def build_headers(cookie_str: str, referer: str = "") -> dict:
    """构造请求头"""
    xsrf = get_xsrf_token(cookie_str)
    headers = {
        "accept": "application/json",
        "accept-language": "zh-CN,zh;q=0.9",
        "x-requested-with": "XMLHttpRequest",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
    }
    if xsrf:
        headers["x-xsrf-token"] = xsrf
    if referer:
        headers["referer"] = referer
    return headers


def format_notification(status: str, label: str, server_id: str, detail: str) -> str:
    """格式化续期通知消息"""
    now = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "🖥 Orihost 免费服务器续期",
        "",
        f"{status}",
        f"👤 {label}",
        f"🆔 服务器: {server_id}",
        f"📌 结果: {detail}",
        f"⏰ 执行时间: {now}",
    ]
    return "\n".join(lines)


# ============================================================
# Cloudflare Turnstile 处理逻辑（来自 host-shipRenew）
# ============================================================
_EXPAND_JS = """
(function() {
    var ts = document.querySelector('input[name="cf-turnstile-response"]');
    if (!ts) { return 'no-turnstile'; }
    var el = ts;
    for (var i = 0; i < 20; i++) {
        el = el.parentElement;
        if (!el) { break; }
        var s = window.getComputedStyle(el);
        if (s.overflow === 'hidden' || s.overflowX === 'hidden' || s.overflowY === 'hidden') {
            el.style.overflow = 'visible';
        }
        el.style.minWidth = 'max-content';
    }
    document.querySelectorAll('iframe').forEach(function(f) {
        if (f.src && f.src.includes('challenges.cloudflare.com')) {
            f.style.width = '300px';
            f.style.height = '65px';
            f.style.minWidth = '300px';
            f.style.visibility = 'visible';
            f.style.opacity = '1';
        }
    });
    return 'done';
})()
"""

_EXISTS_JS = """
(function() {
    return document.querySelector('input[name="cf-turnstile-response"]') !== null;
})()
"""

_SOLVED_JS = """
(function() {
    var i = document.querySelector('input[name="cf-turnstile-response"]');
    return !!(i && i.value && i.value.length > 20);
})()
"""


def handle_turnstile(sb) -> bool:
    """处理 Cloudflare Turnstile 验证（参考 host-shipRenew）"""
    print(" 处理 Cloudflare Turnstile 验证...")
    time.sleep(2)
    
    # 先检查是否已经静默通过
    if sb.execute_script(_SOLVED_JS):
        print("✅ 已静默通过")
        return True
    
    # 展开 Turnstile iframe / 容器
    for _ in range(3):
        try:
            sb.execute_script(_EXPAND_JS)
        except Exception:
            pass
        time.sleep(0.5)
    
    # 最多尝试 6 次
    for attempt in range(6):
        if sb.execute_script(_SOLVED_JS):
            print(f"✅ Turnstile 通过（第 {attempt} 次尝试）")
            return True
        
        print(f"️ 第 {attempt + 1} 次调用 uc_gui_click_captcha...")
        try:
            sb.uc_gui_click_captcha()
        except Exception as e:
            print(f"⚠️ uc_gui_click_captcha 调用异常: {e}")
        
        # 等待验证结果
        for _ in range(16):
            time.sleep(0.5)
            if sb.execute_script(_SOLVED_JS):
                print(f"✅ Turnstile 通过（第 {attempt + 1} 次尝试）")
                return True
        
        print(f"⚠️ 第 {attempt + 1} 次未通过，重试...")
    
    print("❌ Turnstile 6 次均失败")
    return False


def get_cookie_via_browser(original_cookie: str, proxy_str: str = "") -> str:
    """
    启动浏览器访问面板，处理 Cloudflare Turnstile，提取新的 Cookie。
    返回提取到的 Cookie 字符串；失败则返回原始 Cookie。
    """
    print("🌐 检测到 Cloudflare 拦截，启动浏览器处理...")
    
    sb_kwargs = {
        "uc": True,
        "headless": False,  # 需要 Xvfb 支持
        "proxy": proxy_str if proxy_str else None,
    }
    
    try:
        with SB(**sb_kwargs) as sb:
            # 打开面板首页
            print(f" 打开页面: {BASE_URL}")
            sb.uc_open_with_reconnect(BASE_URL, reconnect_time=8)
            time.sleep(8)
            
            # 等待 Cloudflare / 页面加载
            print("⏳ 等待页面加载 / Cloudflare...")
            cf_passed = False
            for i in range(30):
                page_src = (sb.get_page_source() or "").lower()
                # 检查是否出现了正常的页面元素（而非 CF 挑战页）
                if ("server" in page_src or "dashboard" in page_src or 
                    "login" in page_src or "panel" in page_src):
                    cf_passed = True
                    print(f"✅ 页面已加载（{i + 1}s）")
                    break
                time.sleep(1)
            
            # 检查是否有 Turnstile 需要处理
            if sb.execute_script(_EXISTS_JS):
                print("检测到 Turnstile，开始处理...")
                if not handle_turnstile(sb):
                    print("❌ Turnstile 处理失败")
                    sb.save_screenshot("cf_turnstile_fail.png")
                    return original_cookie
            else:
                print("ℹ️ 未检测到 Turnstile")
            
            time.sleep(3)
            
            # 提取 Cookie
            cookies = sb.get_cookies()
            if cookies:
                cookie_parts = []
                for c in cookies:
                    if isinstance(c, dict) and "name" in c and "value" in c:
                        cookie_parts.append(f"{c['name']}={c['value']}")
                new_cookie = "; ".join(cookie_parts)
                if new_cookie:
                    print(f"✅ 成功提取新 Cookie（{len(cookie_parts)} 个字段）")
                    return new_cookie
            
            print("⚠️ 未能提取到 Cookie，使用原始 Cookie")
            return original_cookie
            
    except Exception as e:
        print(f"❌ 浏览器处理异常: {e}")
        return original_cookie


# ============================================================
# 续期函数
# ============================================================
def renew_server(cookie: str, server_id: str) -> dict:
    """
    通过 Jexactyl 面板 API 续期服务器。
    如果遇到 Cloudflare 拦截，自动启动浏览器处理并重试。
    """
    referer = f"{PANEL_URL}/server/{server_id[:8]}"
    headers = build_headers(cookie, referer=referer)
    cookies = parse_cookies(cookie)
    
    # ==================== Step 1: 开始续期 ====================
    begin_url = f"{BASE_URL}/api/client/servers/{server_id}/renew/begin"
    print(f"  [{server_id[:8]}] 开始续期...")
    
    try:
        resp = requests.post(begin_url, headers=headers, cookies=cookies, timeout=30, proxies=PROXIES or None)
    except Exception as e:
        print(f"  ❌ 请求失败: {e}")
        return {"status": "error", "message": f"请求失败: {e}"}
    
    # 检测 Cloudflare 拦截
    if resp.status_code in (403, 503) or "cloudflare" in resp.text.lower() or "cf-challenge" in resp.text.lower():
        print(f"  ⚠️ 检测到 Cloudflare 拦截 (HTTP {resp.status_code})，尝试通过浏览器绕过...")
        new_cookie = get_cookie_via_browser(cookie, ORIHOST_PROXY)
        if new_cookie != cookie:
            # 使用新 Cookie 重试
            headers = build_headers(new_cookie, referer=referer)
            cookies = parse_cookies(new_cookie)
            try:
                resp = requests.post(begin_url, headers=headers, cookies=cookies, timeout=30, proxies=PROXIES or None)
                print(f"  🔄 使用新 Cookie 重试 begin 请求...")
            except Exception as e:
                print(f"  ❌ 重试请求失败: {e}")
                return {"status": "error", "message": f"重试请求失败: {e}"}
    
    if resp.status_code == 419:
        print(f"  ❌ CSRF token mismatch (419) - Cookie 已过期，需重新登录获取")
        return {"status": "error", "message": "CSRF token mismatch (419) - Cookie 过期"}
    if resp.status_code == 401:
        print(f"  ❌ 401 Unauthenticated - Cookie 已失效")
        return {"status": "error", "message": "401 Unauthenticated - Cookie 失效"}
    if resp.status_code != 200:
        print(f"  ❌ begin 失败 HTTP {resp.status_code}: {resp.text[:200]}")
        return {"status": "error", "message": f"begin HTTP {resp.status_code}"}
    
    try:
        data = resp.json()
    except Exception:
        print(f"  ❌ begin 响应解析失败: {resp.text[:200]}")
        return {"status": "error", "message": "begin 响应解析失败"}
    
    article_url = data.get("url", "")
    dwell_seconds = data.get("dwell_seconds", 30)
    print(f"  文章: {article_url}")
    print(f"  ⏳ 等待 {dwell_seconds} 秒（模拟阅读文章）...")
    time.sleep(dwell_seconds + 1)
    
    # ==================== Step 2: 完成续期 ====================
    complete_url = f"{BASE_URL}/api/client/renewal/complete"
    
    try:
        resp2 = requests.get(complete_url, headers=headers, cookies=cookies, timeout=30, proxies=PROXIES or None)
    except Exception as e:
        print(f"  ❌ complete 请求失败: {e}")
        return {"status": "error", "message": f"complete 请求失败: {e}"}
    
    # 再次检测 Cloudflare 拦截
    if resp2.status_code in (403, 503) or "cloudflare" in resp2.text.lower():
        print(f"  ⚠️ complete 阶段检测到 Cloudflare 拦截，尝试通过浏览器绕过...")
        new_cookie = get_cookie_via_browser(cookie, ORIHOST_PROXY)
        if new_cookie != cookie:
            headers = build_headers(new_cookie, referer=referer)
            cookies = parse_cookies(new_cookie)
            try:
                resp2 = requests.get(complete_url, headers=headers, cookies=cookies, timeout=30, proxies=PROXIES or None)
                print(f"  🔄 使用新 Cookie 重试 complete 请求...")
            except Exception as e:
                print(f"  ❌ 重试请求失败: {e}")
                return {"status": "error", "message": f"重试请求失败: {e}"}
    
    if resp2.status_code != 200:
        print(f"  ❌ complete 失败 HTTP {resp2.status_code}: {resp2.text[:200]}")
        return {"status": "error", "message": f"complete HTTP {resp2.status_code}"}
    
    try:
        result = resp2.json()
    except Exception:
        result = {}
        print(f"  ⚠️ complete 响应解析失败: {resp2.text[:200]}")
    
    renewed = result.get("renewed_count", 0)
    skipped = result.get("skipped_count", 0)
    
    if renewed > 0:
        print(f"  ✅ 续期成功! renewed_count={renewed}")
        return {"status": "success", "message": f"续期成功 (+{renewed})"}
    elif skipped > 0:
        print(f"  ⏭️ 服务器被跳过 (skipped={skipped})，可能已达续期上限")
        return {"status": "skipped", "message": f"服务器被跳过 (已达续期上限)"}
    else:
        print(f"  ⚠️ 未预期响应: {result}")
        return {"status": "unknown", "message": f"未预期响应: {result}"}


# ============================================================
# 主入口
# ============================================================
def main():
    print("=" * 40)
    print(" Orihost 免费服务器自动续期 (CF 验证版)")
    print("=" * 40)
    
    all_results = []
    
    for acc in ACCOUNTS:
        label = acc["label"]
        cookie = acc["cookie"]
        server_ids = acc["server_ids"]
        
        print(f"\n{'=' * 40}")
        print(f" {label}")
        print(f" 服务器: {', '.join(server_ids)}")
        print(f"{'=' * 40}")
        
        for server_id in server_ids:
            try:
                result = renew_server(cookie, server_id)
                status_map = {
                    "success": "✅ 续期成功",
                    "skipped": "⏭️ 已到上限",
                    "error": "❌ 续期失败",
                    "unknown": "⚠️ 未知结果",
                }
                info = {
                    "label": label,
                    "server_id": server_id,
                    "status": status_map.get(result["status"], "❌ 续期失败"),
                    "message": result.get("message", ""),
                }
            except Exception as e:
                print(f"  ❌ 服务器 {server_id} 续期失败: {e}")
                info = {
                    "label": label,
                    "server_id": server_id,
                    "status": "❌ 续期失败",
                    "message": str(e)[:80],
                }
            
            all_results.append(info)
            
            # 每个服务器发一次 Telegram 通知
            msg = format_notification(
                info["status"],
                info["label"],
                info["server_id"][:8],
                info["message"]
            )
            send_telegram(msg)
    
    # 汇总
    success = sum(1 for r in all_results if "成功" in r["status"])
    fail = sum(1 for r in all_results if "失败" in r["status"])
    skipped = sum(1 for r in all_results if "上限" in r["status"])
    accounts = len(set(r["label"] for r in all_results))
    
    print(f"\n{'=' * 40}")
    print(f" 汇总: {accounts} 个账号, {success} 成功, {skipped} 跳过, {fail} 失败, 共 {len(all_results)} 个服务器")
    print(f"{'=' * 40}")


if __name__ == "__main__":
    main()
