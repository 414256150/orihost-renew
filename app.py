#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Orihost 自动续期（纯 HTTP 版，单账号单服）
# 由 hostship-renew 一比一移植：同为 Pelican/Pterodactyl 系面板，接口一致
# 主链：Client API key（Bearer）调 POST /api/client/servers/{uuid}/renew
# 备链：COOKIE（remember_web 一年串）+ X-XSRF-TOKEN 头调同一接口
# CF 盾 / 广告弹窗只存在于浏览器层，HTTP 路线天然绕过，无需处理
# Secrets（全部经环境变量传入，密钥永不落盘）：
#   API_KEY   必填，面板 Account → API Credentials 新建的 Client key（ptlc_开头）
#   SERVER_ID 必填，服务器 UUID（面板服务器 URL 里那串）
#   COOKIE    可选，备链用（格式 a=b; c=d，至少含 remember_web）
#   EMAIL     可选，通知备注名
#   TG_BOT_TOKEN / TG_CHAT_ID 可选，通知用

import os, sys, time, json, requests
import urllib.parse

API_KEY    = os.environ.get("API_KEY") or ""
SERVER_ID  = os.environ.get("SERVER_ID") or ""
COOKIE_RAW = os.environ.get("COOKIE") or ""
EMAIL      = os.environ.get("EMAIL") or ""
TG_CHAT_ID    = os.environ.get("TG_CHAT_ID") or ""
TG_BOT_TOKEN  = os.environ.get("TG_BOT_TOKEN") or ""

BASE_URL = "https://panel.orihost.com"
PANEL_DOMAIN = "panel.orihost.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
TIMEOUT = 20


def mask(s: str, head: int = 6, tail: int = 4) -> str:
    s = s or ""
    return f"{s[:head]}...{s[-tail:]}" if len(s) > head + tail else "***"


def send_telegram_message(message: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("⚠️ Telegram 未配置，跳过通知")
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
                      json={"chat_id": TG_CHAT_ID, "text": message}, timeout=10)
        print("✅ Telegram 通知已发送")
    except Exception as e:
        print(f"❌ Telegram 发送失败: {e}")


def format_notification(status: str, server_name: str = "", old: str = "",
                        new: str = "", error: str = "") -> str:
    now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + 8 * 3600))
    from_addr = EMAIL if EMAIL else "（未填）"
    lines = ["🖥️ Orihost 续期通知", "", f"{status}",
             f"👤 账户: {from_addr}"]
    if server_name:
        lines.append(f"🖥️ 服务器: {server_name}")
    if new:
        lines.append(f"📅 续期前: {old or '（未获取到）'}")
        lines.append(f"📅 续期后: {new}")
    elif old:
        lines.append(f"📅 当前: {old}")
    if error:
        lines.append(f"⚠️ 错误信息: {error}")
    lines.append(f"⏱️ 执行时间: {now}(北京时间)")
    return "\n".join(lines)


def parse_cookie_str(raw: str):
    pairs = []
    for item in (raw or "").split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        name, value = item.split("=", 1)
        name, value = name.strip(), value.strip()
        if name and value:
            pairs.append((name, value))
    return pairs


def pick_snapshot(attrs: dict):
    """从 GET /servers/{id} 的 attributes 里提取：名字、状态、到期候选字段。"""
    if not isinstance(attrs, dict):
        return "（未知）", "（未知）", "（未获取到）"
    name = attrs.get("name") or attrs.get("server_name") or "（未知）"
    status = attrs.get("status") or attrs.get("state") or ""
    if isinstance(status, dict):
        status = status.get("status") or status.get("state") or ""
    old = ""
    for key in ("renews_at", "renew_at", "expires_at", "expiry",
                "due_date", "renewal", "next_renewal", "suspended_at"):
        val = attrs.get(key)
        if val:
            old = f"{key}={val}"
            break
    if not old:
        old = str(status) if status else "（未获取到）"
    return str(name), str(status or "（未知）"), str(old)


# ---------- 主链：API key ----------

def api_headers():
    return {"Authorization": f"Bearer {API_KEY}", "Accept": "application/json",
            "Content-Type": "application/json", "User-Agent": UA}


def api_get_server():
    try:
        r = requests.get(f"{BASE_URL}/api/client/servers/{SERVER_ID}",
                         headers=api_headers(), timeout=TIMEOUT)
        if r.status_code == 200:
            try:
                attrs = r.json().get("attributes", {})
            except Exception:
                attrs = {}
            return True, pick_snapshot(attrs), ""
        return False, ("（未知）", "（未知）", "（未获取到）"), f"GET {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return False, ("（未知）", "（未知）", "（未获取到）"), f"GET 异常: {e}"


def api_renew():
    """返回 (ok, info)。204/200/201/202 视为请求成功。"""
    try:
        r = requests.post(f"{BASE_URL}/api/client/servers/{SERVER_ID}/renew",
                          headers=api_headers(), timeout=TIMEOUT)
        print(f"📡 POST renew → HTTP {r.status_code}")
        if r.status_code in (200, 201, 202, 204):
            return True, ""
        return False, f"HTTP {r.status_code}: {r.text[:300]}"
    except Exception as e:
        return False, f"POST 异常: {e}"


# ---------- 备链：Cookie + XSRF ----------

def cookie_renew():
    """Laravel 式：带 Cookie GET 一次拿 XSRF-TOKEN，再带 X-XSRF-TOKEN 头 POST。"""
    pairs = parse_cookie_str(COOKIE_RAW)
    if not pairs:
        return False, "COOKIE 为空或格式错误"
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "application/json",
                      "Referer": f"{BASE_URL}/"})
    for name, value in pairs:
        try:
            s.cookies.set(name, value, domain=PANEL_DOMAIN, path="/")
        except Exception as e:
            print(f"⚠️ 装配 Cookie {name} 失败: {e}")
    try:
        g = s.get(f"{BASE_URL}/api/client/servers/{SERVER_ID}", timeout=TIMEOUT)
        print(f"📡 备链 GET → HTTP {g.status_code}")
        if g.status_code in (401, 403):
            return False, f"备链 Cookie 已失效（GET {g.status_code}）"
        xsrf = s.cookies.get("XSRF-TOKEN", domain=PANEL_DOMAIN) or ""
        try:
            xsrf = urllib.parse.unquote(xsrf)
        except Exception:
            pass
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if xsrf:
            headers["X-XSRF-TOKEN"] = xsrf
        r = s.post(f"{BASE_URL}/api/client/servers/{SERVER_ID}/renew",
                   headers=headers, timeout=TIMEOUT)
        print(f"📡 备链 POST renew → HTTP {r.status_code}")
        if r.status_code in (200, 201, 202, 204):
            return True, ""
        if r.status_code in (401, 403, 419):
            return False, f"备链认证失败（HTTP {r.status_code}），请重拷 COOKIE"
        return False, f"备链 HTTP {r.status_code}: {r.text[:300]}"
    except Exception as e:
        return False, f"备链异常: {e}"


def main():
    print("#" * 25)
    print("   Orihost 自动续期（纯 HTTP）")
    print("#" * 25)
    if not API_KEY and not COOKIE_RAW:
        print("ℹ️ 未配置 API_KEY 且无 COOKIE，脚本终止。")
        sys.exit(1)
    if not SERVER_ID:
        print("ℹ️ 未配置 SERVER_ID（服务器 UUID），脚本终止。")
        sys.exit(1)
    print(f"🎯 目标服务器: {mask(SERVER_ID, 8, 4)}")
    print(f"🔑 主链 API_KEY: {'已配置' if API_KEY else '未配置'} / "
          f"备链 COOKIE: {'已配置' if COOKIE_RAW else '未配置'}")

    used_backup = False
    # 1. 主链：先 GET 快照
    name, status, old = "（未知）", "（未知）", "（未获取到）"
    if API_KEY:
        ok, snap, err = api_get_server()
        if ok:
            name, status, old = snap
            print(f"📋 主链快照：{name} / {status} / {old}")
        else:
            print(f"⚠️ 主链 GET 失败: {err}")
    # 2. 主链 POST
    ok, info = (False, "未执行主链（无 API_KEY）")
    if API_KEY:
        ok, info = api_renew()
    # 3. 主链认证失败且有备链 → 切备链（非认证失败不自动切，避免重复续期）
    used_backup = False
    if not ok and COOKIE_RAW and ("401" in info or "403" in info or "未执行" in info):
        print("🔄 主链认证失败，切换备链 Cookie 重试...")
        ok, info = cookie_renew()
        used_backup = True

    if ok:
        # 4. 验成功：再 GET 一次
        new = old
        if API_KEY:
            ok2, snap2, _ = api_get_server()
            if ok2:
                name, status, new = snap2
        via = "备链 Cookie" if used_backup else "主链 API"
        print(f"✅ 续期成功（{via}），{old} → {new}")
        send_telegram_message(format_notification("✅ 续期成功", server_name=name,
                                                  old=old, new=new if new != old else f"{new}（已确认）"))
        print("🏁 执行完毕：续期成功")
        return

    print(f"❌ 续期失败: {info}")
    send_telegram_message(format_notification("❌ 续期失败", server_name=name,
                                              old=old, error=str(info)))
    print("🏁 执行完毕：续期失败")
    sys.exit(1)


if __name__ == "__main__":
    main()
