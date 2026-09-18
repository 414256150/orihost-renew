#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# ============================================================
# Orihost 免费服务器自动续期
#
# 改进版：
# 1. 使用 requests.Session() 保持完整续期会话
# 2. 自动接收 begin 阶段 Set-Cookie
# 3. begin -> 等待 -> complete 使用同一个 Session
# 4. complete 500/502/503/504 自动有限重试
# 5. Telegram 显示 Orihost 原始错误
# 6. 保留原有环境变量和多账号/多服务器功能
# ============================================================

import os
import sys
import time
import json
import requests

from urllib.parse import unquote
from datetime import datetime, timezone, timedelta


# ============================================================
# 配置
# ============================================================

BASE_URL = "https://panel.orihost.com"
PANEL_URL = "https://orihost.com"

# complete 临时服务器错误最大重试次数
COMPLETE_MAX_RETRIES = 3

# 每次重试间隔
COMPLETE_RETRY_DELAY = 5

# HTTP 请求超时
REQUEST_TIMEOUT = 30


# ============================================================
# 代理配置
# ============================================================

ORIHOST_PROXY = os.environ.get("ORIHOST_PROXY") or ""

PROXIES = {}

if ORIHOST_PROXY:
    PROXIES = {
        "http": ORIHOST_PROXY,
        "https": ORIHOST_PROXY,
    }

    print(f"🔗 尝试使用 ORIHOST_PROXY: {ORIHOST_PROXY}")

    try:
        requests.get(
            "http://www.gstatic.com/generate_204",
            proxies=PROXIES,
            timeout=5,
        )

        print("✅ 代理可用")

    except Exception as e:
        print(f"⚠️ 代理不可达 ({e})，回退直连")
        PROXIES = {}

else:
    http_proxy = (
        os.environ.get("HTTP_PROXY")
        or os.environ.get("http_proxy")
        or ""
    )

    https_proxy = (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or ""
    )

    if http_proxy or https_proxy:
        PROXIES = {
            "http": http_proxy,
            "https": https_proxy or http_proxy,
        }

        print(f"🔗 使用 HTTP_PROXY: {http_proxy}")


# ============================================================
# Telegram
# ============================================================

TG_CHAT_ID = os.environ.get("TG_CHAT_ID") or ""
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN") or ""


# ============================================================
# 多账号配置
#
# 账号1:
# ORIHOST_COOKIE_1
# ORIHOST_SERVER_IDS_1
#
# 账号2:
# ORIHOST_COOKIE_2
# ORIHOST_SERVER_IDS_2
#
# 向下兼容：
# ORIHOST_COOKIE
# ORIHOST_SERVER_IDS
# ============================================================

ACCOUNTS = []

for i in range(1, 100):

    cookie = os.environ.get(f"ORIHOST_COOKIE_{i}")

    if cookie:

        server_ids_raw = (
            os.environ.get(f"ORIHOST_SERVER_IDS_{i}") or ""
        )

        server_ids = [
            s.strip()
            for s in server_ids_raw.split(",")
            if s.strip()
        ]

        if not server_ids:
            print(
                f"⚠️ 账号{i} "
                f"ORIHOST_SERVER_IDS_{i} 为空，跳过"
            )
            continue

        ACCOUNTS.append({
            "cookie": cookie,
            "server_ids": server_ids,
            "label": f"账号{i}",
        })

    else:
        break


# ============================================================
# 向下兼容旧版单账号变量
# ============================================================

if not ACCOUNTS:

    legacy_cookie = (
        os.environ.get("ORIHOST_COOKIE") or ""
    )

    if legacy_cookie:

        server_ids_raw = (
            os.environ.get("ORIHOST_SERVER_IDS") or ""
        )

        server_ids = [
            s.strip()
            for s in server_ids_raw.split(",")
            if s.strip()
        ]

        if server_ids:

            ACCOUNTS.append({
                "cookie": legacy_cookie,
                "server_ids": server_ids,
                "label": "默认账号",
            })


# ============================================================
# 没有账号直接退出
# ============================================================

if not ACCOUNTS:

    print("❌ 未配置任何 Cookie，脚本终止。")

    print(
        "单账号：设置 "
        "ORIHOST_COOKIE + ORIHOST_SERVER_IDS"
    )

    print(
        "多账号：设置 "
        "ORIHOST_COOKIE_1 + ORIHOST_SERVER_IDS_1"
    )

    print(
        "       ORIHOST_COOKIE_2 + ORIHOST_SERVER_IDS_2"
    )

    sys.exit(1)


print(f"📋 检测到 {len(ACCOUNTS)} 个账号")

for acc in ACCOUNTS:

    print(
        f"  {acc['label']}: "
        f"{', '.join(acc['server_ids'])}"
    )


# ============================================================
# Telegram
# ============================================================

def send_telegram(message: str):
    """发送 Telegram 通知"""

    if not TG_BOT_TOKEN or not TG_CHAT_ID:

        print("⚠️ Telegram 未配置，跳过通知")
        return

    url = (
        f"https://api.telegram.org/"
        f"bot{TG_BOT_TOKEN}/sendMessage"
    )

    try:

        requests.post(
            url,
            json={
                "chat_id": TG_CHAT_ID,
                "text": message,
            },
            timeout=10,
            proxies=PROXIES or None,
        )

        print("✅ Telegram 通知已发送")

    except Exception as e:

        print(f"❌ Telegram 发送失败: {e}")


# ============================================================
# Cookie
# ============================================================

def parse_cookies(cookie_str: str) -> dict:
    """
    将 Cookie 字符串解析成 dict。

    XSRF-TOKEN / jexactyl_session
    等 Cookie 可能经过 URL 编码。
    """

    cookies = {}

    for item in cookie_str.split(";"):

        item = item.strip()

        if "=" not in item:
            continue

        key, value = item.split("=", 1)

        cookies[key.strip()] = unquote(
            value.strip()
        )

    return cookies


def get_xsrf_token(cookie_str: str) -> str:
    """从 Cookie 获取 XSRF-TOKEN"""

    cookies = parse_cookies(cookie_str)

    return cookies.get("XSRF-TOKEN", "")


# ============================================================
# Headers
# ============================================================

def build_headers(
    cookie_str: str,
    referer: str = "",
) -> dict:

    xsrf = get_xsrf_token(cookie_str)

    headers = {
        "accept": "application/json",
        "accept-language": "zh-CN,zh;q=0.9",
        "x-requested-with": "XMLHttpRequest",

        "user-agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/151.0.0.0 "
            "Safari/537.36"
        ),

        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
    }

    if xsrf:
        headers["x-xsrf-token"] = xsrf

    if referer:
        headers["referer"] = referer

    return headers


# ============================================================
# 响应内容提取
# ============================================================

def response_detail(resp, max_length=800) -> str:
    """
    提取服务器返回内容。

    优先 JSON，无法解析时使用文本。
    """

    try:

        data = resp.json()

        if isinstance(data, dict):

            # 常见错误字段
            for key in (
                "message",
                "error",
                "detail",
                "errors",
            ):

                if key in data:

                    value = data[key]

                    if isinstance(value, (dict, list)):
                        text = json.dumps(
                            value,
                            ensure_ascii=False,
                        )
                    else:
                        text = str(value)

                    return text[:max_length]

            return json.dumps(
                data,
                ensure_ascii=False,
            )[:max_length]

    except Exception:
        pass

    text = resp.text or ""

    text = " ".join(
        text.split()
    )

    return text[:max_length]


# ============================================================
# 安全打印响应
# ============================================================

def print_response_debug(
    name: str,
    resp,
):

    print(
        f"   {name}: HTTP {resp.status_code}"
    )

    detail = response_detail(resp)

    if detail:
        print(
            f"   📄 返回: {detail}"
        )

    # 只打印有价值的诊断头
    for key in (
        "server",
        "cf-ray",
        "retry-after",
        "content-type",
    ):

        value = resp.headers.get(key)

        if value:
            print(
                f"   🔎 {key}: {value}"
            )


# ============================================================
# Telegram 消息
# ============================================================

def format_notification(
    status: str,
    label: str,
    server_id: str,
    detail: str,
) -> str:

    now = (
        datetime.now(timezone.utc)
        + timedelta(hours=8)
    ).strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        "🖥 Orihost 免费服务器续期",
        "",
        status,
        f"👤 {label}",
        f"🆔 服务器: {server_id}",
        f"📌 结果: {detail}",
        f"⏰ 执行时间: {now}",
    ]

    return "\n".join(lines)


# ============================================================
# 核心续期
# ============================================================

def renew_server(
    cookie: str,
    server_id: str,
) -> dict:

    """
    Orihost 续期：

    1. 创建 Session
    2. 注入用户 Cookie
    3. POST renew/begin
    4. 保存服务器下发的新 Cookie
    5. 等待 dwell_seconds
    6. GET renewal/complete
    7. 500/502/503/504 有限重试
    """

    referer = (
        f"{PANEL_URL}/server/"
        f"{server_id[:8]}"
    )

    # --------------------------------------------------------
    # 创建 Session
    # --------------------------------------------------------

    session = requests.Session()

    session.proxies.update(
        PROXIES
    )

    # --------------------------------------------------------
    # 注入原始 Cookie
    # --------------------------------------------------------

    original_cookies = parse_cookies(
        cookie
    )

    session.cookies.update(
        original_cookies
    )

    # --------------------------------------------------------
    # Headers
    # --------------------------------------------------------

    headers = build_headers(
        cookie,
        referer=referer,
    )

    # --------------------------------------------------------
    # Step 1: begin
    # --------------------------------------------------------

    begin_url = (
        f"{BASE_URL}/api/client/"
        f"servers/{server_id}/renew/begin"
    )

    print("")
    print(
        f"🔄 [{server_id[:8]}] "
        f"开始续期..."
    )

    print(
        f"🌐 POST {begin_url}"
    )

    try:

        resp = session.post(
            begin_url,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )

    except Exception as e:

        print(
            f"❌ begin 请求失败: {e}"
        )

        return {
            "status": "error",
            "message": f"begin 请求失败: {e}",
        }

    print_response_debug(
        "begin",
        resp,
    )

    # --------------------------------------------------------
    # 认证错误
    # --------------------------------------------------------

    if resp.status_code == 419:

        return {
            "status": "error",
            "message": (
                "CSRF token mismatch (419) "
                "- Cookie 可能已过期"
            ),
        }

    if resp.status_code == 401:

        return {
            "status": "error",
            "message": (
                "401 Unauthenticated "
                "- Cookie 已失效"
            ),
        }

    # --------------------------------------------------------
    # begin 其它错误
    # --------------------------------------------------------

    if resp.status_code != 200:

        detail = response_detail(resp)

        return {
            "status": "error",
            "message": (
                f"begin HTTP "
                f"{resp.status_code}"
                f": {detail}"
            ),
        }

    # --------------------------------------------------------
    # 解析 begin JSON
    # --------------------------------------------------------

    try:

        data = resp.json()

    except Exception:

        detail = response_detail(resp)

        return {
            "status": "error",
            "message": (
                "begin 响应解析失败: "
                f"{detail}"
            ),
        }

    article_url = data.get(
        "url",
        "",
    )

    dwell_seconds = data.get(
        "dwell_seconds",
        30,
    )

    try:

        dwell_seconds = int(
            dwell_seconds
        )

    except Exception:

        dwell_seconds = 30

    # 防止异常返回导致等待时间异常
    dwell_seconds = max(
        0,
        min(dwell_seconds, 300),
    )

    print(
        f"📰 文章: {article_url}"
    )

    print(
        f"⏳ 等待 {dwell_seconds} 秒..."
    )

    # --------------------------------------------------------
    # 非常关键：
    #
    # Session 会自动接收 begin 返回的 Set-Cookie。
    #
    # 原版本使用 requests.post/get + cookies=固定字典，
    # 不会自然继承服务器新下发的 Session Cookie。
    # --------------------------------------------------------

    print(
        "🍪 当前 Session Cookie 数量: "
        f"{len(session.cookies)}"
    )

    # --------------------------------------------------------
    # 等待
    # --------------------------------------------------------

    time.sleep(
        dwell_seconds + 1
    )

    # --------------------------------------------------------
    # Step 2: complete
    # --------------------------------------------------------

    complete_url = (
        f"{BASE_URL}/api/client/"
        f"renewal/complete"
    )

    print("")
    print(
        "🚀 开始提交 complete..."
    )

    print(
        f"🌐 GET {complete_url}"
    )

    # --------------------------------------------------------
    # complete 有限重试
    # --------------------------------------------------------

    retryable_statuses = {
        500,
        502,
        503,
        504,
    }

    resp2 = None

    for attempt in range(
        1,
        COMPLETE_MAX_RETRIES + 1,
    ):

        try:

            resp2 = session.get(
                complete_url,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )

        except Exception as e:

            print(
                f"❌ complete 请求异常 "
                f"(第 {attempt} 次): {e}"
            )

            if attempt >= COMPLETE_MAX_RETRIES:

                return {
                    "status": "error",
                    "message": (
                        "complete 请求失败: "
                        f"{e}"
                    ),
                }

            time.sleep(
                COMPLETE_RETRY_DELAY
            )

            continue

        print(
            f"📡 complete 第 {attempt}/"
            f"{COMPLETE_MAX_RETRIES} 次"
        )

        print_response_debug(
            "complete",
            resp2,
        )

        # ----------------------------------------------------
        # 成功
        # ----------------------------------------------------

        if resp2.status_code == 200:
            break

        # ----------------------------------------------------
        # Cookie / CSRF 错误
        # 不重试
        # ----------------------------------------------------

        if resp2.status_code == 419:

            detail = response_detail(
                resp2
            )

            return {
                "status": "error",
                "message": (
                    "complete HTTP 419 "
                    f"CSRF: {detail}"
                ),
            }

        if resp2.status_code == 401:

            detail = response_detail(
                resp2
            )

            return {
                "status": "error",
                "message": (
                    "complete HTTP 401 "
                    f"认证失败: {detail}"
                ),
            }

        # ----------------------------------------------------
        # Orihost 临时服务器错误
        # 允许重试
        # ----------------------------------------------------

        if (
            resp2.status_code
            in retryable_statuses
        ):

            detail = response_detail(
                resp2
            )

            print(
                f"⚠️ Orihost 返回 "
                f"HTTP {resp2.status_code}"
            )

            if detail:
                print(
                    f"📄 原始错误: {detail}"
                )

            if attempt < COMPLETE_MAX_RETRIES:

                print(
                    f"⏳ {COMPLETE_RETRY_DELAY} 秒后重试..."
                )

                time.sleep(
                    COMPLETE_RETRY_DELAY
                )

                continue

        # ----------------------------------------------------
        # 其它 HTTP 错误
        # ----------------------------------------------------

        detail = response_detail(
            resp2
        )

        return {
            "status": "error",
            "message": (
                f"complete HTTP "
                f"{resp2.status_code}"
                f": {detail}"
            ),
        }

    # --------------------------------------------------------
    # complete 最终仍失败
    # --------------------------------------------------------

    if (
        resp2 is None
        or resp2.status_code != 200
    ):

        if resp2 is None:

            detail = (
                "没有收到 complete 响应"
            )

            status_code = "N/A"

        else:

            detail = response_detail(
                resp2
            )

            status_code = resp2.status_code

        return {
            "status": "error",
            "message": (
                f"complete HTTP "
                f"{status_code}: "
                f"{detail}"
            ),
        }

    # --------------------------------------------------------
    # 解析成功响应
    # --------------------------------------------------------

    try:

        result = resp2.json()

    except Exception:

        detail = response_detail(
            resp2
        )

        return {
            "status": "error",
            "message": (
                "complete 响应解析失败: "
                f"{detail}"
            ),
        }

    print(
        f"📦 complete JSON: "
        f"{json.dumps(result, ensure_ascii=False)}"
    )

    renewed = result.get(
        "renewed_count",
        0,
    )

    skipped = result.get(
        "skipped_count",
        0,
    )

    # --------------------------------------------------------
    # 成功
    # --------------------------------------------------------

    if renewed > 0:

        print(
            f"✅ 续期成功！"
            f" renewed_count={renewed}"
        )

        return {
            "status": "success",
            "message": (
                f"续期成功 (+{renewed})"
            ),
        }

    # --------------------------------------------------------
    # 已达到续期上限
    # --------------------------------------------------------

    if skipped > 0:

        print(
            f"⏭️ 服务器被跳过 "
            f"(skipped={skipped})"
        )

        return {
            "status": "skipped",
            "message": (
                "服务器被跳过 "
                "(可能已达续期上限)"
            ),
        }

    # --------------------------------------------------------
    # 未知结果
    # --------------------------------------------------------

    print(
        f"⚠️ 未预期响应: {result}"
    )

    return {
        "status": "unknown",
        "message": (
            "未预期响应: "
            f"{json.dumps(result, ensure_ascii=False)[:500]}"
        ),
    }


# ============================================================
# 主程序
# ============================================================

def main():

    print("=" * 50)
    print(" Orihost 免费服务器自动续期")
    print(" Session + 500诊断 + 自动重试版")
    print("=" * 50)

    all_results = []

    for acc in ACCOUNTS:

        label = acc["label"]
        cookie = acc["cookie"]
        server_ids = acc["server_ids"]

        print("")
        print("=" * 50)
        print(f"👤 {label}")
        print(
            f"🖥 服务器: "
            f"{', '.join(server_ids)}"
        )
        print("=" * 50)

        for server_id in server_ids:

            try:

                result = renew_server(
                    cookie,
                    server_id,
                )

                status_map = {
                    "success": "✅ 续期成功",
                    "skipped": "⏭️ 已到上限",
                    "error": "❌ 续期失败",
                    "unknown": "⚠️ 未知结果",
                }

                info = {
                    "label": label,
                    "server_id": server_id,
                    "status": status_map.get(
                        result["status"],
                        "❌ 续期失败",
                    ),
                    "message": result.get(
                        "message",
                        "",
                    ),
                }

            except Exception as e:

                print(
                    f"❌ 服务器 "
                    f"{server_id} "
                    f"续期异常: {e}"
                )

                info = {
                    "label": label,
                    "server_id": server_id,
                    "status": "❌ 续期失败",
                    "message": str(e)[:500],
                }

            all_results.append(
                info
            )

            # ------------------------------------------------
            # Telegram
            # ------------------------------------------------

            msg = format_notification(
                info["status"],
                info["label"],
                info["server_id"][:8],
                info["message"],
            )

            send_telegram(msg)

    # ========================================================
    # 汇总
    # ========================================================

    success = sum(
        1
        for r in all_results
        if "成功" in r["status"]
    )

    fail = sum(
        1
        for r in all_results
        if "失败" in r["status"]
    )

    skipped = sum(
        1
        for r in all_results
        if "上限" in r["status"]
    )

    unknown = sum(
        1
        for r in all_results
        if "未知" in r["status"]
    )

    accounts = len(
        set(
            r["label"]
            for r in all_results
        )
    )

    print("")
    print("=" * 50)

    print(
        f"📊 汇总: "
        f"{accounts} 个账号, "
        f"{success} 成功, "
        f"{skipped} 跳过, "
        f"{fail} 失败, "
        f"{unknown} 未知, "
        f"共 {len(all_results)} 个服务器"
    )

    print("=" * 50)


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":
    main()
