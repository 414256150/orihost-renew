#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Orihost 浏览器自动续期（SeleniumBase + 真浏览器）
# v12 2026-10-04：run 37158461182（v11）复盘——看门狗修好后第一次看到真实死亡：
#   Step B 到达、广告 Close 被 CDP 点击后，execute_script 开始返回 None，随后持续抛异常
#   150s。最大嫌疑是 CDP 可信点击打偏/点到广告导致页面导航或上下文失效。
#   本版：① 看门狗打印真实异常信息（之前只打印“无响应”，等于盲飞）；
#   ② 判死时记录当前 URL + 尝试读 page source + 截图；
#   ③ kill_ad_overlay 改为只隐藏不真点（_JS_KILL_AD 几何/文案隐藏），不再用 CDP
#      可信点击去点广告 Close——隐藏足以让出 Turnstile，真点有导航风险。
# v11 2026-10-04：一行修复——js_health 的 repr() 比较 bug（见函数内注释）。
#   之前所有版本的看门狗在通道健康时也判死，Step B/Turnstile/Claim 从未被真正尝试过。
# v10 2026-10-03：在用户版（v8+load_accounts 多账号）上合入 v9 弹窗处理：
#   - 新增 close_popup_windows：关掉续期主窗口之外的所有弹窗/广告标签页并切回
#     panel.orihost.com（点 Read Article 后、Step B 轮询中、通道恢复后、点 Claim 前都调）。
#   - kill_ad_overlay 先找广告弹层上真正的 Close/✕ 按钮坐标再点，找不到才用几何经验坐标。
#     （之前按左下角盲点，点偏会点到 Continue 打开广告新窗口——用户实测反馈。）
# v8 2026-10-03 run 37128070946 复盘追加：
#   - run 37128070946（v7 脚本）两轮都在「倒计时 14→2 走完、进入 Step B 的瞬间」
#     JS 通道冻结，且两次 90 秒都没恢复（v7 的 CDP_RECOVER_WAIT=90 不够）。
#     看门狗放宽到 150s。冻结恰好发生在 Turnstile 挂载时刻，疑似 Cloudflare 在
#     数据中心 IP 上的挑战/重载把主线程占满；根因未实证，仍在排查。
#   - 失败截图实证：Step B 时新的「Download is ready」广告弹层会重新出现、正好
#     压住 Turnstile 区域，且底部有 "We use cookies / Got it" 横幅。v7 只在点
#     Read Article 前清广告，Step B 没清。清理函数现同时点掉 cookie 横幅。
#   - wait_modal_state 也加耐心看门狗：读状态阶段若通道冻结超限，直接返回
#     "cdp-dead"，不再盲轮询 600 秒烧光 Claim 预算。
# v7 2026-10-03 run 37126904215 复盘追加：
#   - Step B 开始时页面 JS 通道会冻结（该次约 1 分钟后自行恢复；但 run 37128070946
#     证明冻结可能超过 90 秒）。疑似 Turnstile 挂载时 Cloudflare 在数据中心 IP 上
#     做挑战/重载——仅为推测，未实证。看门狗改成耐心等（CDP_RECOVER_WAIT，默认 150s），
#     不再 3 次就判死，避免误杀可恢复的抖动。
#   - 主流程改成最多 2 轮完整重试：文章→Step B→Claim 整轮重做；旧恢复逻辑漏了
#     重开后重点 Read Article 的 bug 已修（_article_to_ready 抽成复用函数）。
# v6 2026-10-03 按真实页面重写 Claim 阶段：保留 remember_web Cookie 登录与轮换
# 背景：面板 claim 接口强制要求 Cloudflare Turnstile token（GET /api/client/renewal/complete?cf-turnstile-response=xxx），
#       纯 HTTP 调不通（无 token 直接 500），必须用真浏览器点验证。
# 流程：Cookie 免登 → 服务器页 → Renew → Read Article → 等 "Thanks for reading!"（Step B）
#       → 等 Turnstile 挂载 → 验证 → Claim Renewal 变可用 → 点击 → 读 API 结果
# 2026-10-03 真机实测（面板 built on Jexactyl）：
#   - 对话框 Step A：h2 "Renew your server"，文案 "Click Read Article to open a news article
#     in a new tab. Keep it open and read it for a few seconds, then return here to claim
#     your renewal (+7 days)."，按钮 Cancel / Read Article（可用），无 Turnstile，无数字倒计时。
#   - 点 Read Article 会在新标签打开 albeu.com 文章；文章开 30~60 秒、关闭并回到面板页后，
#     对话框变 Step B："Thanks for reading! Click Claim Renewal to add up to +7 days…"。
#   - Step B 才挂载 Turnstile（iframe + input[name=cf-turnstile-response]），Claim Renewal
#     为 disabled，验证通过后才可用。Step B 之前轮询 Turnstile 是等不到的。
#   - 广告："Download is ready / Tap to proceed" 弹窗 iframe 可能盖住 Renew 入口按钮；
#     kill_ad_overlay 已加 role=dialog 豁免，绝不隐藏续期对话框本身。
#   - 10-02 失败复盘：对话框已到 ready 但 Turnstile 始终未挂载、Claim 查询 600 秒全 js-null
#     （疑似 CDP 通道抖动/中断），旧代码盲轮询。本版加 CDP 健康看门狗：连续 3 次 js_health
#     异常即判定通道中断，尝试关对话框重开恢复一次；仍失败则明确报错，不再盲等。
# 参考：katabump-renew-main（同款 Turnstile 处理 + xvfb 无头方案）

import json
import os
import re
import sys
import time
import random
import requests as tg_lib
from datetime import datetime, timezone, timedelta
from urllib.parse import unquote
from seleniumbase import SB

PANEL = "https://panel.orihost.com"
# Laravel 默认 remember cookie 名（yanyumm1 实测 Orihost 可用）
DEFAULT_REMEMBER_NAME = "remember_web_59ba36addc2b2f9401580f014c7f58ea4e30989d"
# 文章页停留秒数（面板 dwell=15，多留 buffer；“过早关闭文章页会被警告”）
ARTICLE_WAIT = int(os.environ.get("ARTICLE_WAIT") or "30")
# Claim 按钮轮询上限
CLAIM_TIMEOUT = int(os.environ.get("CLAIM_TIMEOUT") or "150")

# ===== Turnstile / Claim 参数 =====
# 强化原因：先完成 Turnstile，再等待 Claim 解锁，避免“先点 Claim 失败→才处理验证码”的竞态。
TURNSTILE_MAX_ATTEMPTS = max(1, int(os.environ.get("TURNSTILE_MAX_ATTEMPTS") or "8"))
TURNSTILE_TOKEN_WAIT = max(3, int(os.environ.get("TURNSTILE_TOKEN_WAIT") or "12"))
TURNSTILE_POLL_INTERVAL = max(0.5, float(os.environ.get("TURNSTILE_POLL_INTERVAL") or "1"))
CLAIM_ENABLE_WAIT = max(5, int(os.environ.get("CLAIM_ENABLE_WAIT") or "25"))
# 验证组件（CF iframe）挂载等待：面板 2026-09 末版可能要等广告被关掉才挂组件；
# 等不到会返回 None 让调用方直接去点 Claim（组件可能在点击后才出现）
TURNSTILE_WIDGET_WAIT = max(10, int(os.environ.get("TURNSTILE_WIDGET_WAIT") or "90"))
# JS 通道抖动恢复等待：Step B 挂载 Turnstile 时 Cloudflare 可能让页面卡住几十秒
# （2026-10-03 run 37126904215 实证：通道卡死约 1 分钟后自行恢复）；
# 通道持续无响应超过此时长才判死，不再 3 次就判死
# （run 37128070946：Step B 切换瞬间冻结，两次 90s 都没恢复，故默认 150s）
CDP_RECOVER_WAIT = max(30, int(os.environ.get("CDP_RECOVER_WAIT") or "150"))

# ---------- 代理 ----------
# 优先级：ORIHOST_PROXY 显式指定 > 工作流 sing-box（IS_PROXY/PROXY_SERVER，由 NODE_LINK 转出）
def _get_proxy():
    explicit = (os.environ.get("ORIHOST_PROXY") or os.environ.get("ORIHOST_GOST_PROXY") or "").strip()
    if explicit:
        scheme = explicit.split("://", 1)[0].lower() if "://" in explicit else ""
        if scheme in ("http", "https", "socks4", "socks5", "socks5h"):
            return explicit
        print(f"  ⚠️ ORIHOST_PROXY 格式不支持 ({scheme}://)，节点链接请填 NODE_LINK")
    if os.environ.get("IS_PROXY", "").lower() == "true":
        srv = (os.environ.get("PROXY_SERVER") or "socks5://127.0.0.1:1080").strip()
        print(f"  🔗 使用 sing-box 代理: {srv}")
        return srv
    return ""

PROXY_STR = _get_proxy()
IS_PROXY = bool(PROXY_STR)

# ---------- Telegram ----------
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN") or ""
TG_CHAT_ID = os.environ.get("TG_CHAT_ID") or ""
if (not TG_BOT_TOKEN or not TG_CHAT_ID) and os.environ.get("TG_BOT"):
    try:
        _cid, _tok = os.environ["TG_BOT"].split(",", 1)
        TG_CHAT_ID = TG_CHAT_ID or _cid.strip()
        TG_BOT_TOKEN = TG_BOT_TOKEN or _tok.strip()
    except Exception:
        pass


def send_tg(msg: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    try:
        r = tg_lib.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "HTML",
                  "link_preview_options": {"is_disabled": True}},
            timeout=15,
        )
        ok = r.status_code == 200 and r.json().get("ok")
        print(f"  📨 TG {'已发送' if ok else '失败: ' + r.text[:80]}")
    except Exception as e:
        print(f"  TG 发送失败: {e}")


def now_bj():
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")


# ---------- 账号解析（与 orihost_renew.py 同一套变量名） ----------
def _split_ids(raw: str):
    return [s.strip() for s in (raw or "").replace(";", ",").split(",") if s.strip()]


def parse_auth_cookies(auth_raw: str):
    """把用户填的 token 还原成 [(name, value)]，支持裸 token / name=value / 完整 Cookie 串"""
    v = (auth_raw or "").strip()
    if "remember_web" in v and (";" in v or "XSRF-TOKEN" in v or "jexactyl_session" in v):
        out = []
        for item in v.split(";"):
            item = item.strip()
            if not item or "=" not in item:
                continue
            k, val = item.split("=", 1)
            k, val = k.strip(), val.strip()
            if not k or k.lower() in ("path", "expires", "domain", "max-age", "samesite", "secure", "httponly"):
                continue
            try:
                val = unquote(val)
            except Exception:
                pass
            out.append((k, val))
        return out
    if "=" in v and "remember_web" in v:
        name, val = v.split("=", 1)
        return [(name.strip(), val.strip())]
    return [(DEFAULT_REMEMBER_NAME, v)]


def load_accounts():
    accounts = []
    for i in range(1, 20):
        token_raw = (os.environ.get(f"ORIHOST_REMEMBER_{i}") or os.environ.get(f"ORIHOST_COOKIE_{i}") or "").strip()
        ids = _split_ids(os.environ.get(f"ORIHOST_SERVER_IDS_{i}") or "")
        if not token_raw and not ids:
            continue
        if not token_raw or not ids:
            print(f"⚠️ 账号{i} 配置不完整，跳过")
            continue
        accounts.append({"label": f"账号{i}", "auth": token_raw, "servers": ids})
    if not accounts:
        single_auth = (os.environ.get("ORIHOST_REMEMBER") or os.environ.get("ORI_COOKIE") or os.environ.get("ORIHOST_COOKIE") or "").strip()
        single_ids = _split_ids(os.environ.get("ORIHOST_SERVER_IDS") or os.environ.get("ORIHOST_SERVER_IDS_1") or "")
        if single_auth and single_ids:
            accounts.append({"label": "默认账号", "auth": single_auth, "servers": single_ids})
    return accounts


# ---------- Turnstile 处理（移植自 katabump，经实测有效） ----------
_EXPAND_JS = """
(function() {
    var ts = document.querySelector('input[name="cf-turnstile-response"]');
    if (!ts) return 'no-turnstile';
    var el = ts;
    for (var i = 0; i < 20; i++) {
        el = el.parentElement;
        if (!el) break;
        var s = window.getComputedStyle(el);
        if (s.overflow === 'hidden' || s.overflowX === 'hidden' || s.overflowY === 'hidden')
            el.style.overflow = 'visible';
        el.style.minWidth = 'max-content';
    }
    document.querySelectorAll('iframe').forEach(function(f){
        if (f.src && f.src.includes('challenges.cloudflare.com')) {
            f.style.width = '300px'; f.style.height = '65px';
            f.style.minWidth = '300px';
            f.style.visibility = 'visible'; f.style.opacity = '1';
        }
    });
    return 'done';
})()
"""

_SOLVED_JS = """
(function(){
    var i = document.querySelector('input[name="cf-turnstile-response"]');
    return !!(i && i.value && i.value.length > 20);
})()
"""

_HAS_TURNSTILE_JS = """
(function(){
    if (document.querySelector('input[name="cf-turnstile-response"]')) return true;
    var fs = document.querySelectorAll('iframe');
    for (var i = 0; i < fs.length; i++) {
        if (fs[i].src && fs[i].src.includes('challenges.cloudflare.com')) return true;
    }
    return false;
})()
"""


# 面板免费方案会插广告：续期对话框弹出时，中间会有个「Download is ready / Tap to proceed」
# 嘅跨域广告 iframe，正好压住 Turnstile 组件 —— 跨域 iframe 嘅文字父页面读唔到，
# 所以除咗原有文案匹配，再加「几何法」：喺组件矩形上采样 elementFromPoint，
# 边个盖住组件就藏边个（run 36215402050 截图实证：广告弹层压住组件只剩顶边露出，
# Claim Renewal 一直 disabled，往组件坐标点嘅 CDP 点击全部点喺广告上面）。
_JS_KILL_AD = """
(function(){
 var out=[];
 function hide(el,why){try{if(el.closest&&el.closest('[role="dialog"]')){out.push('skip-dialog:'+el.tagName);return}el.style.setProperty('display','none','important');el.style.setProperty('visibility','hidden','important');el.style.setProperty('pointer-events','none','important');out.push(why+':'+el.tagName)}catch(e){}}
 var markers=['download is ready','tap to proceed','continue to download','your download is ready'];
 var all=document.querySelectorAll('div,section,aside,iframe,ins');
 for(var i=0;i<all.length;i++){var el=all[i],t=((el.innerText||el.textContent)||'').toLowerCase().slice(0,500);if(!t)continue;for(var j=0;j<markers.length;j++){if(t.indexOf(markers[j])<0)continue;var p=el;for(var k=0;k<10&&p.parentElement;k++){var st=getComputedStyle(p),z=parseInt(st.zIndex||'0',10);if(st.position==='fixed'||st.position==='sticky'||z>=100)break;p=p.parentElement}hide(p,'marker');break}}
 var ts=document.querySelector('input[name="cf-turnstile-response"]');
 if(ts){var tr=null,e2=ts;
  for(var m=0;m<10&&e2.parentElement;m++){e2=e2.parentElement;var r0=e2.getBoundingClientRect();if(r0.width>=200&&r0.height>=40){tr=r0;break}}
  if(tr){var pts=[[tr.left+tr.width/2,tr.top+tr.height/2],[tr.left+24,tr.top+tr.height/2],[tr.left+tr.width-24,tr.top+tr.height/2],[tr.left+tr.width/2,tr.top+12],[tr.left+tr.width/2,tr.bottom-8]];
   for(var q=0;q<pts.length;q++){var top=document.elementFromPoint(pts[q][0],pts[q][1]);if(!top)continue;if(top===ts)continue;if((top.tagName==='IFRAME')&&(top.src||'').indexOf('challenges.cloudflare.com')>=0)continue;if(top.contains(ts)||ts.contains(top))continue;var t2=top;for(var w=0;w<8&&t2.parentElement;w++){var st2=getComputedStyle(t2);if(st2.position==='fixed'||st2.position==='absolute'||st2.position==='sticky'||parseInt(st2.zIndex||'0',10)>=100)break;t2=t2.parentElement}if(t2.closest&&t2.closest('[role="dialog"]'))continue;hide(t2,'cover'+q)}}}
 var els=document.querySelectorAll('body > *,body > * > *,body > * > * > *');
 for(var n=0;n<els.length;n++){var e=els[n],st3=getComputedStyle(e),z2=parseInt(st3.zIndex||'0',10);if(st3.position!=='fixed'&&st3.position!=='absolute'&&st3.position!=='sticky')continue;if(z2<900)continue;var r=e.getBoundingClientRect();if(r.width*r.height<0.12*innerWidth*innerHeight)continue;if(e.closest&&e.closest('[role="dialog"]'))continue;var txt=((e.innerText||e.textContent)+'').toLowerCase(),cls=((e.className||'')+'').toLowerCase();var keep=txt.indexOf('renew your server')>=0||txt.indexOf('claim renewal')>=0||cls.indexOf('turnstile')>=0||cls.indexOf('modal')>=0||e.querySelector('input[name="cf-turnstile-response"]')||e.querySelector('iframe[src*="challenges.cloudflare.com"]');if(!keep)hide(e,'zindex'+z2)}
 try{document.documentElement.style.removeProperty('overflow');document.body.style.removeProperty('overflow')}catch(e){}
 try{var cands=document.querySelectorAll('button,[role="button"],a');for(var ci=0;ci<cands.length;ci++){var cb=cands[ci],bt=((cb.textContent||'').trim().toLowerCase());if(bt!=='got it'&&bt!=='accept'&&bt!=='accept all')continue;var br=cb.getBoundingClientRect();if(br.width<30||br.height<20)continue;if(br.top<innerHeight*0.55)continue;var btxt='';try{var pc=cb.closest('div');btxt=pc?String(pc.textContent||'').toLowerCase():''}catch(e2){}if(btxt.indexOf('cookie')<0)continue;cb.click();out.push('cookie:got-it');break}}catch(e){}
 return out.join(' ')||'none';
})()
"""

# 广告弹层定位：优先以 Turnstile 组件为锚（elementFromPoint 找出盖住佢嘅顶层元素，
# 返回佢哋嘅定位容器矩形俾 Python 用 CDP 点 Close）；
# 组件还没挂载时，退而求其次搵弹窗区域里疑似广告弹层嘅 iframe
# （跨域读唔到文字，只能靠几何尺寸+位置判断）。
_JS_AD_COVERS = """
(function(){
 function container(el){var t=el;for(var k=0;k<8&&t.parentElement;k++){var st=getComputedStyle(t);if(st.position==='fixed'||st.position==='absolute'||st.position==='sticky'||parseInt(st.zIndex||'0',10)>=100)break;t=t.parentElement}return t}
 var out=[],tr=null;
 var ts=document.querySelector('input[name="cf-turnstile-response"]');
 if(ts){
  var e2=ts;
  for(var m=0;m<10&&e2.parentElement;m++){e2=e2.parentElement;var r0=e2.getBoundingClientRect();if(r0.width>=200&&r0.height>=40){tr=r0;break}}
  if(tr){
   var pts=[[tr.left+tr.width/2,tr.top+tr.height/2],[tr.left+24,tr.top+tr.height/2],[tr.left+tr.width-24,tr.top+tr.height/2],[tr.left+tr.width/2,tr.top+12],[tr.left+tr.width/2,tr.bottom-8]];
   var seen={};
   for(var q=0;q<pts.length;q++){
    var top=document.elementFromPoint(pts[q][0],pts[q][1]);
    if(!top)continue;
    if(top===ts)continue;
    if((top.tagName==='IFRAME')&&(top.src||'').indexOf('challenges.cloudflare.com')>=0)continue;
    if(top.contains(ts)||ts.contains(top))continue;
    var c=container(top),r=c.getBoundingClientRect(),key=c.tagName+Math.round(r.left)+','+Math.round(r.top);
    if(seen[key])continue;seen[key]=1;
    out.push([Math.round(r.left),Math.round(r.top),Math.round(r.width),Math.round(r.height)]);
   }
  }
 }else{
  var els=document.querySelectorAll('iframe');
  for(var i=0;i<els.length&&out.length<3;i++){
   var el=els[i],r=el.getBoundingClientRect();
   if(r.width<150||r.height<70||r.width>900||r.height>700)continue;
   if(r.top<innerHeight*0.08||r.top>innerHeight*0.75)continue;
   var src=((el.getAttribute&&el.getAttribute('src'))||'').toLowerCase();
   if(src.indexOf('challenges.cloudflare.com')>=0)continue;
   var c2=container(el),rr=c2.getBoundingClientRect();
   out.push([Math.round(rr.left),Math.round(rr.top),Math.round(rr.width),Math.round(rr.height)]);
  }
 }
 return JSON.stringify(out);
})()
"""

_JS_TS_INFO = """
(function(){
 var inp=document.querySelector('input[name="cf-turnstile-response"]');var out={token:inp?String(inp.value||'').length:-1,rects:[],visible_rects:[],iframe_count:0,visible_iframe_count:0};
 function visible(el){try{var st=getComputedStyle(el),r=el.getBoundingClientRect();return st.display!=='none'&&st.visibility!=='hidden'&&parseFloat(st.opacity||'1')>0&&r.width>2&&r.height>2}catch(e){return false}}
 function walk(root){try{var nodes=root.querySelectorAll?root.querySelectorAll('*'):[];for(var i=0;i<nodes.length;i++){var f=nodes[i];if(f.tagName==='IFRAME'){var src=(f.src||'').toLowerCase(),title=(f.title||'').toLowerCase();if(src.indexOf('challenges.cloudflare.com')>=0||title.indexOf('turnstile')>=0){var r=f.getBoundingClientRect(),a=[Math.round(r.left),Math.round(r.top),Math.round(r.width),Math.round(r.height)];out.rects.push(a);out.iframe_count++;if(visible(f)){out.visible_rects.push(a);out.visible_iframe_count++}}}if(f.shadowRoot)walk(f.shadowRoot)}}catch(e){}}
 walk(document);return JSON.stringify(out);
})()
"""


# 在疑似广告的 fixed/absolute 高 z-index 覆盖层里，找真正的 Close/✕ 按钮坐标。
# （之前 kill_ad_overlay 按「弹层左下角」经验坐标盲点，点偏就可能点到 Continue，
#  打开广告新窗口——用户 2026-10-03 实测反馈。续期对话框自己的 ✕ 用 role=dialog 豁免。）
_JS_FIND_AD_CLOSE = """
(function(){
 var out=[];
 var els=document.querySelectorAll('button,a,[role="button"],span,div');
 for(var i=0;i<els.length;i++){
  var b=els[i],t=((b.textContent||'').trim().toLowerCase());
  if(t!=='close'&&t!=='✕'&&t!=='×'&&t!=='x')continue;
  var r=b.getBoundingClientRect();
  if(r.width<8||r.height<8||r.width>220||r.height>90)continue;
  if(r.left<0||r.top<0||r.right>innerWidth||r.bottom>innerHeight)continue;
  var p=b,ok=false;
  for(var k=0;k<8&&p.parentElement;k++){p=p.parentElement;var st=getComputedStyle(p);var z=parseInt(st.zIndex||'0',10);if((st.position==='fixed'||st.position==='absolute'||st.position==='sticky')&&z>=100){ok=true;break}}
  if(!ok)continue;
  if(p.closest&&p.closest('[role="dialog"]'))continue;
  out.push([Math.round(r.left+r.width/2),Math.round(r.top+r.height/2)]);
  if(out.length>=3)break;
 }
 return JSON.stringify(out);
})()
"""


def close_popup_windows(sb, main_handle):
    """关掉续期主窗口之外的所有弹窗/广告标签页，切回主窗口。

    返回 (关闭数量, 主窗口句柄)。主窗口以 panel.orihost.com 的标签页为准；
    若原 main_handle 已不在，自动在现存标签页里重新定位。
    """
    closed = 0
    try:
        handles = list(sb.driver.window_handles)
    except Exception:
        return 0, main_handle
    if len(handles) <= 1:
        return 0, main_handle
    if main_handle not in handles:
        main_handle = None
        for h in handles:
            try:
                sb.driver.switch_to.window(h)
                if "panel.orihost.com" in (sb.driver.current_url or ""):
                    main_handle = h
                    break
            except Exception:
                continue
        if not main_handle:
            main_handle = handles[0]
    for h in handles:
        if h == main_handle:
            continue
        try:
            sb.driver.switch_to.window(h)
            url = ""
            try:
                url = sb.driver.current_url or ""
            except Exception:
                pass
            sb.driver.close()
            closed += 1
            print(f"    \U0001fa9f 关广告弹窗: {url[:90]}")
        except Exception as e:
            print(f"    \u26a0\ufe0f 关弹窗失败: {str(e)[:60]}")
    try:
        sb.driver.switch_to.window(main_handle)
    except Exception:
        pass
    return closed, main_handle


def kill_ad_overlay(sb):
    """关掉盖住 Turnstile 组件的广告弹层：
    ① 定位覆盖层，用 CDP 点它自己的 Close（面板流程可能要求真关闭才肯挂验证组件）；
    ② 点唔走嘅再按几何位置藏掉（跨域 iframe 读唔到文字，纯文案匹配对佢无效）。
    v12 起只隐藏、不再用 CDP 可信点击去点广告 Close：
    run 37158461182 显示，CDP 点击广告 Close 后通道死亡（疑似点偏导致页面导航/
    上下文失效）。隐藏（display:none）足以让出 Turnstile，且零导航风险。
    _JS_FIND_AD_CLOSE 保留备用，不再调用。"""
    report = []
    try:
        raw = sb.execute_script(_JS_AD_COVERS)
        covers = json.loads(raw) if isinstance(raw, str) else []
    except Exception as e:
        covers = []
        report.append("find:" + str(e)[:50])
    for rect in covers[:3]:
        try:
            x, y, w, h = [float(v) for v in rect[:4]]
        except Exception:
            continue
        if w < 100 or h < 60:
            continue
        cx, cy = x + w * 0.19, y + h * 0.78   # 截图实测：Close 在弹层左下角
        res = ts_click_cdp(sb, cx, cy)
        report.append(f"Close({int(cx)},{int(cy)})→{res}")
        time.sleep(1.2)
    try:
        report.append("清理:" + str(sb.execute_script(_JS_KILL_AD)))
    except Exception as e:
        report.append("清理异常:" + str(e)[:60])
    return " | ".join(report) if report else "无覆盖层"


def ts_info(sb):
    """读 Turnstile 状态：token 长度 + 组件 iframe 视口坐标。"""
    try:
        raw = sb.execute_script(_JS_TS_INFO)
    except Exception as e:
        return None, "err:" + str(e)[:60]

    info = _parse_ts_info(raw)
    if info:
        return info, None

    return None, str(raw)[:120]


def ts_click_cdp(sb, x, y):
    """用 CDP 派发真鼠标事件点 checkbox（唔依赖 X11/pyautogui，坐标係视口坐标）"""
    try:
        for t in ("mouseMoved", "mousePressed", "mouseReleased"):
            sb.driver.execute_cdp_cmd("Input.dispatchMouseEvent", {
                "type": t, "x": int(x), "y": int(y), "button": "left", "clickCount": 1,
            })
            time.sleep(0.08)
        return "cdp-ok"
    except Exception as e:
        return "cdp-err:" + str(e)[:60]


def _parse_ts_info(raw):
    """统一解析 _JS_TS_INFO：execute_script 返回的是 JSON 字符串。"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            obj = json.loads(raw)
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}
    return {}


def _wait_turnstile_token(sb, timeout=TURNSTILE_TOKEN_WAIT):
    """等待 Turnstile token 真正写入页面 DOM。"""
    deadline = time.time() + timeout
    last_len = 0
    while time.time() < deadline:
        try:
            solved = bool(sb.execute_script(_SOLVED_JS))
            info = _parse_ts_info(sb.execute_script(_JS_TS_INFO))

            raw_len = info.get("token_len", info.get("token", 0))
            try:
                last_len = int(raw_len or 0)
            except Exception:
                last_len = 0

            if solved or last_len >= 20:
                print(f"    ✅ Turnstile token 已生成，长度={last_len or '有效'}")
                return True
        except Exception:
            pass
        time.sleep(TURNSTILE_POLL_INTERVAL)

    print(f"    ⚠️ Turnstile token 等待超时，最后长度={last_len}")
    return False


def _turnstile_click_points(rect):
    """根据 iframe 矩形生成几个 checkbox 候选点击坐标。"""
    # ts_info() 的 rect 是 [x, y, width, height]。
    x, y, w, h = [float(v) for v in rect[:4]]
    mid_y = y + max(h / 2.0, 18.0)
    points = [
        (x + min(max(24.0, w * 0.14), 42.0), mid_y),
        (x + 24.0, y + min(max(32.0, h / 2.0), max(h - 8.0, 32.0))),
        (x + 32.0, mid_y),
    ]
    out, seen = [], set()
    for px, py in points:
        key = (round(px, 1), round(py, 1))
        if px > 0 and py > 0 and key not in seen:
            seen.add(key)
            out.append((px, py))
    return out


def _uc_gui_turnstile(sb):
    """优先使用 SeleniumBase/undetected-chromedriver 的 GUI CAPTCHA 点击器。

    这是 Host Ship 项目已经采用的路径。它不依赖我们自己从主文档里定位
    Cloudflare iframe，因此可以覆盖 iframe/shadow DOM/动态渲染导致的定位失败。
    """
    try:
        fn = getattr(sb, "uc_gui_click_captcha", None)
        if not callable(fn):
            return False, "uc_gui_click_captcha 不可用"

        print("  🖱️ 尝试 SeleniumBase uc_gui_click_captcha() ...")
        result = fn()
        print(f"    uc_gui_click_captcha 返回: {result!r}")
        return True, str(result)
    except Exception as e:
        return False, str(e)[:160]


def handle_turnstile(sb):
    """处理续期对话框内的 Cloudflare Turnstile。

    返回值：True=已拿到 token；False=组件有挂载但多轮尝试拿唔到 token（真失败）；
            None=组件一直没挂载（唔算失败，调用方应继续去点 Claim Renewal）。

    处理优先级：
      1. 已有 token 直接通过；
      2. 等待组件挂载（边清广告边等）；
      3. SeleniumBase uc_gui_click_captcha()（不依赖 iframe DOM 定位）；
      4. 原有 CDP iframe 坐标点击作为兜底。
    """
    print("🔍 处理 Cloudflare Turnstile 验证...")
    kill_ad_overlay(sb)
    time.sleep(1.5)

    try:
        if sb.execute_script(_SOLVED_JS):
            print("  ✅ Turnstile 已有有效 token")
            return True
    except Exception:
        pass

    # 先确认验证组件真的挂载了（页面里有 CF iframe）。面板 2026-09 末版可能要
    # 等广告被关掉才挂组件 → 每隔几秒点一次广告 Close + 藏覆盖层，等佢挂载。
    # 等唔到就返回 None：调用方唔会当失败，会继续去点 Claim Renewal
    # （面板若系「点了 Claim 才挂验证」或隐形验证模式，呢一下就係触发条件）。
    def _widget_mounted():
        try:
            return bool(sb.execute_script(
                "(function(){var f=document.querySelectorAll('iframe');"
                "for(var i=0;i<f.length;i++){if((f[i].src||'').indexOf('challenges.cloudflare.com')>=0)return true}"
                "return false})()"))
        except Exception:
            return False

    if not _widget_mounted():
        print(f"  ℹ️ 页面里还没有 Turnstile iframe，边清广告边等它挂载（最多 {TURNSTILE_WIDGET_WAIT}s）...")
        end = time.time() + TURNSTILE_WIDGET_WAIT
        mounted = False
        while time.time() < end:
            try:
                kill_ad_overlay(sb)
            except Exception:
                pass
            time.sleep(3)
            try:
                if sb.execute_script(_SOLVED_JS):
                    print("  ✅ 等待期间 token 已生成")
                    return True
            except Exception:
                pass
            if _widget_mounted():
                print("  ✅ 验证组件已挂载，开始处理")
                mounted = True
                break
        if not mounted:
            print("  ⚠️ 等待超时仍未见验证组件，改为直接尝试点 Claim Renewal")
            return None

    for attempt in range(1, TURNSTILE_MAX_ATTEMPTS + 1):
        print(f"  🔄 Turnstile 第 {attempt}/{TURNSTILE_MAX_ATTEMPTS} 轮")
        try:
            killed = kill_ad_overlay(sb)

            # ===== 第一优先级：SeleniumBase 原生 GUI CAPTCHA 点击 =====
            # 不再要求 iframe 必须被 document.querySelectorAll('iframe') 找到。
            # Cloudflare 动态 iframe / shadow DOM 情况下，这正是原方案失败的原因。
            ok, detail = _uc_gui_turnstile(sb)
            if ok:
                if _wait_turnstile_token(sb, timeout=TURNSTILE_TOKEN_WAIT):
                    return True
                try:
                    diag = sb.execute_script("""
                    (function(){
                        var i=document.querySelector('input[name="cf-turnstile-response"]');
                        var fs=document.querySelectorAll('iframe[src*="challenges.cloudflare.com"]');
                        return {
                          input: !!i,
                          token_len: i ? String(i.value || '').length : 0,
                          iframe_count: fs.length,
                          url: location.href
                        };
                    })()
                    """)
                    print(f"    🔎 GUI 后 Turnstile DOM: {diag}")
                except Exception as e:
                    print(f"    ⚠️ GUI 后 DOM 诊断失败: {str(e)[:100]}")
                print("    ⚠️ GUI 点击已执行，但 token 尚未生成，继续使用 CDP 兜底")

            # ===== 第二优先级：传统 DOM + CDP 坐标点击 =====
            info, err = ts_info(sb)
            if info is None:
                print(f"  ⚠️ 读取 Turnstile 状态失败: {err}")
                time.sleep(1.5)
                continue

            tok = info.get("token")
            rects = info.get("rects") or []
            if isinstance(tok, int) and tok > 20:
                print(f"  ✅ Turnstile 已通过（token 长度 {tok}）")
                return True

            if not rects:
                try:
                    sb.execute_script(_EXPAND_JS)
                except Exception:
                    pass
                time.sleep(1)
                info, _ = ts_info(sb)
                rects = (info or {}).get("rects") or []

            if not rects:
                try:
                    state=sb.execute_script(_JS_POST_ARTICLE_CLEANUP)
                    print(f"    🔎 当前 Turnstile/页面状态: {state}")
                except Exception: pass
                print(f"  ⚠️ 第 {attempt} 轮没有真正的 Turnstile iframe/widget；不把孤立 response input 当验证码，等待页面重新渲染")
                time.sleep(2)
                continue

            rects = sorted(
                rects,
                key=lambda r: float(r[2]) * float(r[3]),
                reverse=True,
            )

            for rect in rects[:2]:
                try:
                    for cx, cy in _turnstile_click_points(rect):
                        res = ts_click_cdp(sb, cx, cy)
                        print(f"  🖱️ CDP 点击 checkbox ({cx:.0f},{cy:.0f}) → {res}")
                        if _wait_turnstile_token(sb, timeout=TURNSTILE_TOKEN_WAIT):
                            return True
                except Exception as e:
                    print(f"  ⚠️ CDP 点击异常: {e}")

            if not rects:
                print(f"  ⚠️ 第 {attempt} 轮没有执行有效点击，清广告={killed}")

            time.sleep(1.5)

        except Exception as e:
            print(f"  ⚠️ Turnstile 第 {attempt} 轮异常: {e}")
            time.sleep(2)

    print(f"  ❌ Turnstile {TURNSTILE_MAX_ATTEMPTS} 轮均未取得有效 token")
    return False

_JS_POST_ARTICLE_CLEANUP = """
(function(){
 var out={url:location.href,state:'',response_inputs:0,token_len:0,iframes:0,visible_iframes:0};
 function visible(el){try{var s=getComputedStyle(el),r=el.getBoundingClientRect();return s.display!=='none'&&s.visibility!=='hidden'&&parseFloat(s.opacity||'1')>0&&r.width>2&&r.height>2}catch(e){return false}}
 function walk(root){try{var ns=root.querySelectorAll?root.querySelectorAll('*'):[];for(var i=0;i<ns.length;i++){var e=ns[i];if(e.tagName==='IFRAME'){var src=(e.src||'').toLowerCase(),title=(e.title||'').toLowerCase();if(src.indexOf('challenges.cloudflare.com')>=0||title.indexOf('turnstile')>=0){out.iframes++;if(visible(e))out.visible_iframes++}}if(e.shadowRoot)walk(e.shadowRoot)}}catch(e){}}
 var ins=document.querySelectorAll('input[name="cf-turnstile-response"]');out.response_inputs=ins.length;if(ins.length)out.token_len=String(ins[0].value||'').length;
 var marks=['download is ready','tap to proceed','continue to download','your download is ready'];var all=document.querySelectorAll('div,section,aside,iframe,ins');
 for(var j=0;j<all.length;j++){var e2=all[j],t=((e2.innerText||e2.textContent)||'').toLowerCase().slice(0,600),hit=false;for(var m=0;m<marks.length;m++){if(t.indexOf(marks[m])>=0){hit=true;break}}if(!hit)continue;var p=e2;for(var k=0;k<10&&p.parentElement;k++){var st=getComputedStyle(p),z=parseInt(st.zIndex||'0',10);if(st.position==='fixed'||z>=100)break;p=p.parentElement}var pt=((p.innerText||p.textContent)||'').toLowerCase(),pc=((p.className||'')+'').toLowerCase();if(pt.indexOf('renew your server')>=0||pt.indexOf('claim renewal')>=0||pc.indexOf('modal')>=0)continue;try{p.style.setProperty('display','none','important');p.style.setProperty('visibility','hidden','important');p.style.setProperty('pointer-events','none','important')}catch(e){}}
 try{document.documentElement.style.removeProperty('overflow');document.body.style.removeProperty('overflow')}catch(e){}walk(document);
 var bt=((document.body&&(document.body.innerText||document.body.textContent))||'').toLowerCase();out.state=bt.indexOf('you renewed recently')>=0?'cooldown':bt.indexOf('thanks for reading')>=0?'ready':bt.indexOf('you can claim your renewal in')>=0?'reading':bt.indexOf('click read article to open')>=0?'confirm':'closed';return JSON.stringify(out);
})()
"""

def post_article_cleanup(sb,sid=''):
    print('  🧹 文章倒计时结束，执行广告层/弹窗收尾清理...')
    for i in range(3):
        try:
            kill_ad_overlay(sb);raw=sb.execute_script(_JS_POST_ARTICLE_CLEANUP);print(f'    清理第 {i+1}/3: {raw}')
        except Exception as e: print(f'    ⚠️ 清理第 {i+1} 次失败: {str(e)[:100]}')
        time.sleep(1)
    try: href=str(sb.execute_script('return location.href') or '')
    except Exception: href=''
    if not href.startswith(PANEL+'/server/'):
        print(f'    ⚠️ 当前页面不是 Orihost server 页面: {href[:160]}');return False
    deadline=time.time()+8
    while time.time()<deadline:
        try:
            info,_=ts_info(sb)
            if info:
                vis=info.get('visible_rects') or [];rects=info.get('rects') or [];token=int(info.get('token') or 0)
                if token>20 or vis or rects:
                    print(f'    ✅ 续期页面已恢复，Turnstile: token={token}, iframe={len(rects)}, visible={len(vis)}');return True
        except Exception: pass
        time.sleep(1)
    try: print(f'    🔎 收尾后 Turnstile 状态: {ts_info(sb)[0]}')
    except Exception: pass
    return True
def page_text(sb) -> str:
    try:
        return (sb.get_page_source() or "").lower()
    except Exception:
        return ""


# 面板入口按钮名字系「Renew」（停权页「Renew Server」），且按钮内可能只有裸文字节点 + SVG 图标
# （run 35450509946 实测：BUTTON[Renew] 存在，但旧逻辑要求「无子元素」→ 误判为冇按钮）。
# 所以改成：先收集所有文案精确匹配的节点，取**树最深**嘅一个做锚点。
_JS_RENEW_PROBE = """
(function () {
    var all = document.querySelectorAll('button,a,div,span,p,strong');
    var match = null, depth = 0;
    for (var i = 0; i < all.length; i++) {
        var el = all[i];
        var t = (el.textContent || '').trim();
        if (!t || t.length > 40) continue;
        var lt = t.toLowerCase();
        if (lt.indexOf('renew limit reached') >= 0) return 'limit';
        if (lt !== 'renew' && lt !== 'renew server') continue;
        var r = el.getBoundingClientRect();
        if (r.width === 0 && r.height === 0 && el.offsetParent === null) continue;
        var a = el.closest('a');
        if (a) {
            var h = a.getAttribute('href') || '';
            if (h.indexOf('/premium') >= 0 || h.indexOf('/services') >= 0) continue;
        }
        var d = 0, n = el;
        while (n.parentElement) { d++; n = n.parentElement; }
        if (d > depth) { depth = d; match = el; }
    }
    return match ? 'ok' : 'none';
})()
"""

# 注意：SeleniumBase 的 CDP 模式（driver 断线后 is_cdp_swap_needed）会用 cdp.evaluate(script)
# 执行，唔支持 arguments[..]；所以文案直接嵌进脚本，唔用 execute_script 传参。
#
# 两段式定位（run 35450777798 血案：子串匹配「read article」会命中说明段里面嘅
# <strong>Read Article</strong> 内联字，佢比真正嘅按钮更深 → 拣错元素、白白点咗空气）：
#   ① 先揀位于互动容器（button/a/[role=button]）内部、树最深嘅候选 → 正路
#   ② 冇先退而求其次揀任意最深候选
_JS_CLICK_BY_TEXT = """
(function () {
    var want = %s;
    var exact = %s;
    var all = document.querySelectorAll('button,a,div,span,p,strong');
    var best = null, bestDepth = -1, fallback = null, fallbackDepth = -1;
    for (var i = 0; i < all.length; i++) {
        var el = all[i];
        var t = (el.textContent || '').trim();
        if (!t || t.length > 60) continue;
        var lt = t.toLowerCase();
        if (exact) { if (lt !== want) continue; }
        else if (lt.indexOf(want) < 0) continue;
        var r = el.getBoundingClientRect();
        if (r.width === 0 && r.height === 0 && el.offsetParent === null) continue;
        var anc = el.closest('a');
        if (anc) {
            var ah = anc.getAttribute('href') || '';
            if (ah.indexOf('/premium') >= 0 || ah.indexOf('/services') >= 0) continue;
        }
        var d = 0, n = el;
        while (n.parentElement) { d++; n = n.parentElement; }
        var inter = el.closest('button,a,[role=button],[role=tab]');
        if (inter) {
            if (d > bestDepth) { bestDepth = d; best = el; }
        } else if (d > fallbackDepth) { fallbackDepth = d; fallback = el; }
    }
    var match = best || fallback;
    if (!match) return 'not-found';
    var tgt = match.closest('button,a,[role=button],[role=tab]') || match;
    var href = (tgt.getAttribute && (tgt.getAttribute('href') || '')) || '';
    if (href.indexOf('/premium') >= 0 || href.indexOf('/services') >= 0) return 'not-found';
    try { tgt.scrollIntoView({block: 'center'}); } catch (e) {}
    if (tgt.disabled) return 'disabled:' + (tgt.textContent || '').trim().slice(0, 30);
    tgt.click();
    return 'clicked:' + tgt.tagName + ':' + (tgt.textContent || '').trim().slice(0, 30);
})()
"""


# 面板「Renew your server」对话框用 window.open('about:blank','_blank') 开文章页。
# JS 合成 click 冇 user activation → Chrome 直接当弹窗拦截 → window.open 返 null →
# 面板弹 danger flash 并停在 confirm 状态，永远到唔到 ready（run 35450777798 实证）。
# 所以先装垫片：返一个假 window，令面板行得落去；真正开文章页由 Python 侧用 CDP 做。
_JS_PATCH_WINDOW_OPEN = """
(function () {
    window.__oriArticleUrl = '';
    window.__oriDummyWin = null;
    if (window.__oriPatched) return 'already';
    window.__oriPatched = true;
    window.open = function (u, n, f) {
        var w = { closed: false, opener: null, name: n || '', __oriDummy: true };
        w.location = {};
        Object.defineProperty(w.location, 'href', {
            get: function () { return window.__oriArticleUrl || 'about:blank'; },
            set: function (v) { window.__oriArticleUrl = v || ''; }
        });
        w.close = function () { w.closed = true; };
        w.focus = function () {};
        window.__oriDummyWin = w;
        if (u && u !== 'about:blank') { window.__oriArticleUrl = u; }
        return w;
    };
    return 'patched';
})()
"""

_JS_GET_ARTICLE_URL = "(function () { return window.__oriArticleUrl || ''; })()"

# 诊断用：钩 XHR，记录面板 /renew/* 请求嘅原始回包（主要想知 dwell_seconds 几多）
_JS_PATCH_XHR = """
(function () {
    if (window.__oriXhrPatched) return 'already';
    window.__oriXhrPatched = true;
    var O = XMLHttpRequest.prototype.open, S = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function (m, u) { this.__oriUrl = u; return O.apply(this, arguments); };
    XMLHttpRequest.prototype.send = function (b) {
        var self = this;
        this.addEventListener('load', function () {
            if ((self.__oriUrl || '').indexOf('/renew') >= 0) {
                window.__oriLastRenew = self.__oriUrl + ' [' + self.status + '] ' + String(self.responseText).slice(0, 300);
            }
        });
        return S.apply(this, arguments);
    };
    return 'patched';
})()
"""

_JS_DIAG = """
(function () {
    var b = document.body;
    var out = {
        href: location.href.slice(0, 90),
        patched: !!window.__oriPatched,
        dummy: !!window.__oriDummyWin,
        closed: !!(window.__oriDummyWin && window.__oriDummyWin.closed),
        article: (window.__oriArticleUrl || '').slice(0, 80),
        renew: (window.__oriLastRenew || '').slice(0, 200),
        modaltxt: '',
        state: ''
    };
    var all = document.querySelectorAll('div');
    for (var i = all.length - 1; i >= 0; i--) {
        var t = all[i].textContent || '';
        if (t.indexOf('Renew your server') >= 0 && t.length < 900) {
            out.modaltxt = t.slice(0, 260).replace(/\s+/g, ' ');
            break;
        }
    }
    if (!out.modaltxt) out.modaltxt = 'no-modal';
    out.state = (function () {
        var t = ((b && (b.innerText || b.textContent)) || '').toLowerCase();
        if (t.indexOf('you renewed recently') >= 0) return 'cooldown';
        if (t.indexOf('thanks for reading') >= 0) return 'ready';
        if (t.indexOf('you can claim your renewal in') >= 0) return 'reading';
        if (t.indexOf('click read article to open') >= 0) return 'confirm';
        return 'closed';
    })();
    return JSON.stringify(out);
})()
"""

_JS_MODAL_STATE = """
(function () {
    var b = document.body;
    var t = ((b && (b.innerText || b.textContent)) || '').toLowerCase();
    if (t.indexOf('you renewed recently') >= 0) return 'cooldown';
    if (t.indexOf('thanks for reading') >= 0) return 'ready';
    if (t.indexOf('you can claim your renewal in') >= 0) return 'reading';
    if (t.indexOf('click read article to open') >= 0) return 'confirm';
    return 'closed';
})()
"""

# 同步 XHR：CDP 模式下 execute_async_script 会走 cdp.evaluate（唔支持 callback）→ 必 timeout，
# 所以读 API 一律用 sync XHR，唔用 async script。
_JS_SYNC_GET_SERVER = """
(function () {
    try {
        var x = new XMLHttpRequest();
        x.open('GET', '/api/client/servers/%s', false);
        x.setRequestHeader('Accept', 'application/json');
        x.send(null);
        var d = JSON.parse(x.responseText);
        var a = (d && d.attributes) || {};
        return JSON.stringify({renewal: a.renewal, renewable: a.renewable, status: a.status});
    } catch (e) { return 'ERR ' + e; }
})()
"""


def _js_click_script(text, exact):
    return _JS_CLICK_BY_TEXT % (json.dumps(text.lower()), "true" if exact else "false")


def click_by_text(sb, text, timeout=10, exact=False):
    """按文案点击（纯 JS 路）。

    面板 UI kit 嘅 button/a 经 WebDriver 读 .text 全返空（实测 34 个 element 全部系空字符串），
    而且 driver 断线后 SeleniumBase 会转 CDP 模式、element 属性访问会抛
    "'NoneType' object is not callable" → 只能用 document.querySelectorAll + click()，
    事件会冒泡到 React handler，效果等同真人点击。
    返回 'clicked:...' / 'disabled:...' / 'not-found' / 'js-err:...'
    """
    script = _js_click_script(text, exact)
    end = time.time() + timeout
    last = "not-found"
    while time.time() < end:
        try:
            raw = sb.execute_script(script)
            last = "js-null" if raw is None or raw == "" else str(raw)
        except Exception as e:
            last = "js-err:" + str(e)[:90]
            time.sleep(1)
            continue
        if str(last).startswith("clicked") or str(last).startswith("disabled"):
            return last
        time.sleep(1)
    return last


def open_renew_dialog(sb, timeout=25):
    """點開续期对话框。

    面板 2026-09 改版：服务器页上的入口按钮文案系「Renew」（停权页系「Renew Server」），
    「Renew Now」/「Read Article」只出现在点击之后弹出嘅对话框里面
    （且「Renew Now」只有 ad-free 帐号先见到）。
    返回 'ok' 已点开 / 'limit' 已达上限 / None 找唔到。
    """
    end = time.time() + timeout
    probe = ""
    while time.time() < end:
        try:
            probe = sb.execute_script(_JS_RENEW_PROBE) or ""
        except Exception as e:
            probe = "err:" + str(e)[:90]
        if probe == "limit":
            return "limit"
        if probe == "ok":
            res = click_by_text(sb, "renew", timeout=6, exact=True)
            print(f"  \U0001f5b1\ufe0f 点续期入口: {res}")
            if str(res).startswith("clicked"):
                return "ok"
        time.sleep(1)
    print("    probe:", probe)
    return None


def dump_page_debug(sb, sid):
    """搵唔到续期入口时嘅现场取证：整页文字 + button/a 文案 + 面板 API 的 renewal 字段"""
    print("  \U0001f9ea 现场诊断：")
    try:
        print("    URL:", sb.execute_script("(function(){return location.href})()"))
    except Exception as e:
        print("    URL 读取失败:", str(e)[:100])
    try:
        txt = sb.execute_script(
            "(function(){var b=document.body;return (b&&(b.innerText||b.textContent))||''})()"
        ) or ""
        print("    --- 整页文字（前 1500 字）---")
        print("    " + txt[:1500].replace("\n", " | "))
    except Exception as e:
        print("    文字读取失败:", str(e)[:120])
    try:
        js = """
        (function () {
            var els = document.querySelectorAll('button,a');
            var out = [];
            for (var i = 0; i < els.length; i++) {
                var e = els[i];
                var t = (e.textContent || '').trim().slice(0, 24);
                out.push(e.tagName + '[' + t + '|vis=' + (e.offsetParent !== null) + ']');
            }
            return out.join(' ');
        })()
        """
        print("    --- button/a 文案 ---")
        print("    " + str(sb.execute_script(js))[:1800])
    except Exception as e:
        print("    按钮枚举失败:", str(e)[:120])
    try:
        print("    --- 面板 API ---", sb.execute_script(_JS_SYNC_GET_SERVER % sid))
    except Exception as e:
        print("    API 诊断失败:", str(e)[:150])


def api_renewal(sb, sid):
    """直接同步读面板 API 嘅 renewal 天数（唔靠页面文字，最可信）"""
    try:
        raw = sb.execute_script(_JS_SYNC_GET_SERVER % sid)
    except Exception as e:
        return None, "err:" + str(e)[:60]
    try:
        return json.loads(raw), None
    except Exception:
        return None, str(raw)[:80]


_RE_COUNTDOWN = re.compile(r"claim your renewal in[^0-9]{0,140}?(\d{1,4})", re.I)


def dialog_countdown(sb):
    """从 page source 抽对话框倒计时剩余秒数（HTML 里係 claim your renewal in <strong>N</strong>）"""
    try:
        src = re.sub(r"\s+", " ", sb.get_page_source() or "")
    except Exception:
        return None
    m = _RE_COUNTDOWN.search(src)
    return int(m.group(1)) if m else None


def js_health(sb):
    """CDP 模式下 createTarget 后 execute_script 可能静默返 None，先探一探。

    2026-10-04 实锤修正：之前这里 return repr(v)，健康时返回的是 "'pong:2'"
    （带引号），而看门狗比较的是 "pong:2"，导致健康也被判死。从 v6 起所有
    "JS 通道无响应" 都是这次误报，页面根本没冻——特此纠正。
    """
    try:
        v = sb.execute_script("(function(){return 'pong:' + (1+1)})()")
    except Exception as e:
        return "err:" + str(e)[:60]
    return "" if v is None else str(v)


def detect_state(sb):
    """读对话框状态；JS 路返空时退而用 page_source 判断（两路互不依赖）"""
    try:
        v = sb.execute_script(_JS_MODAL_STATE) or ""
    except Exception:
        v = ""
    if v:
        return v
    src = page_text(sb)
    if "you renewed recently" in src:
        return "cooldown"
    if "thanks for reading" in src:
        return "ready"
    if "you can claim your renewal in" in src:
        return "reading"
    if "click read article to open" in src:
        return "confirm"
    return ""


def print_diag(sb, tag=""):
    try:
        print(f"    \U0001f9ea 诊断{tag}: {sb.execute_script(_JS_DIAG)}")
    except Exception as e:
        print(f"    \U0001f9ea 诊断{tag} 失败: {str(e)[:120]}")


def wait_modal_state(sb, target, timeout, note="", main_handle=None):
    """等 Renew 对话框走到指定状态（confirm → reading → ready/closed）。

    带耐心看门狗：读状态阶段也可能撞上 Step B 切换瞬间的通道冻结，
    冻结超 CDP_RECOVER_WAIT 直接返回 "cdp-dead"，不再盲轮询烧光预算。
    """
    end = time.time() + timeout
    last = ""
    polls = 0
    bad_since = None
    while time.time() < end:
        polls += 1
        h = js_health(sb)
        if h != "pong:2":
            if bad_since is None:
                bad_since = time.time()
                print(f"    ⚠️ 读状态时 JS 通道无响应，等待恢复…（{h[:90]}）")
            waited = int(time.time() - bad_since)
            if waited >= CDP_RECOVER_WAIT:
                print(f"    ⚠️ JS 通道持续 {waited}s 无响应，判定中断（{h[:90]}）")
                _dump_dead_diag(sb, "modal")
                return "cdp-dead"
            if waited % 15 == 0:
                print(f"    …通道仍无响应（已 {waited}s，{h[:90]}）")
            time.sleep(3)
            continue
        if bad_since is not None:
            print(f"    ✅ JS 通道恢复（中断约 {int(time.time() - bad_since)}s），继续")
        bad_since = None
        if main_handle and polls % 5 == 1:
            try:
                _, main_handle = close_popup_windows(sb, main_handle)
            except Exception:
                pass
        last = detect_state(sb)
        if last == target:
            return last
        cd = dialog_countdown(sb)
        if polls <= 4 or polls % 5 == 0:
            print(f"    （第 {polls} 次轮询 state={last!r} 倒计时={cd}）")
        if polls == 1:
            print(f"    JS 健康检查: {js_health(sb)}")
        time.sleep(3)
    print(f"    （等 {target} 超时{note}，最后状态={last!r}，倒计时={dialog_countdown(sb)}，轮询 {polls} 次）")
    return last


def read_renew_result(sb, sid, days_before=None) -> dict:
    """点完 Claim / Renew Now 之后读结果。

    面板成功后会 window.location.reload()，所以先用 API 对比续期天数最稳，
    页面文字只做辅助（唔再靠 'renewed' 之类模糊关键字）。
    """
    time.sleep(int(os.environ.get("CLAIM_RESULT_WAIT") or "30"))
    src = page_text(sb)
    if "renew limit reached" in src:
        return {"status": "\u23ed\ufe0f 跳过", "message": "已达续期上限（Renew Limit Reached）"}
    info, err = api_renewal(sb, sid)
    if info:
        days = info.get("renewal")
        print(f"    API：renewal={days} renewable={info.get('renewable')} status={info.get('status')}")
        if days_before is not None and isinstance(days, (int, float)) and days > days_before:
            return {"status": "\u2705 续期成功",
                    "message": f"续期天数 {days_before} → {days} 天（+{round(days - days_before)}）"}
        if days_before is not None and days == days_before:
            sb.save_screenshot(f"claim_noadvance_{sid}.png")
            return {"status": "\u26a0\ufe0f 未知结果",
                    "message": f"Claim 已提交，但天数仍系 {days} 天（未后移），请人工确认"}
    if any(k in src for k in ("renewed successfully", "successfully renewed", "extended")):
        return {"status": "\u2705 续期成功", "message": "Claim 成功（页面确认）"}
    if err:
        print(f"    API 读取失败: {err}")
    sb.save_screenshot(f"claim_unknown_{sid}.png")
    return {"status": "\u26a0\ufe0f 未知结果", "message": "已点 Claim，但没读到明确成功提示，请人工看一眼面板"}


# ---------- Cookie 免登 ----------
def cookie_login(sb, auth_raw: str) -> bool:
    print("🍪 Cookie 免登...")
    sb.open(PANEL + "/")
    time.sleep(3)
    try:
        sb.delete_all_cookies()
    except Exception:
        pass
    for name, val in parse_auth_cookies(auth_raw):
        try:
            sb.driver.add_cookie({"name": name, "value": val, "domain": "panel.orihost.com", "path": "/"})
        except Exception as e:
            print(f"  ⚠️ cookie 写入失败 {name}: {e}")
    sb.open(PANEL + "/dashboard")
    time.sleep(6)
    src = page_text(sb)
    if "login" in (sb.get_current_url() or "").lower() and ("sign in" in src or "password" in src and "dashboard" not in src):
        print("  ❌ Cookie 登录失败（仍在登录页），remember 可能失效")
        return False
    print("  ✅ 已登录")
    save_rotated_cookies(sb)
    return True


def save_rotated_cookies(sb):
    """免登成功后，把浏览器内最新 remember_web cookie 写回 GitHub secret（防一次性轮换）。"""
    try:
        import os, base64
        gt = os.environ.get("GH_ROTATE_TOKEN") or os.environ.get("GITHUB_TOKEN")
        repo = os.environ.get("GITHUB_REPOSITORY")  # jardanlau2020/orihost-renew
        if not gt or not repo or "/" not in repo:
            return  # 本地跑冇環境，靜默跳過
        val = None
        cookies = None
        for attempt in range(3):  # driver 重連期間 get_cookies 會斷線，retry 3 次
            try:
                cookies = sb.driver.get_cookies()
                break
            except Exception:
                time.sleep(2)
        if cookies is None:
            try:  # 兜底：driver API 断线时走 CDP 直接读 cookie store
                cookies = (sb.driver.execute_cdp_cmd("Network.getAllCookies", {}) or {}).get("cookies") or []
            except Exception:
                cookies = None
        if not cookies:
            print("  ℹ️ 拿不到浏览器 cookies（driver 断线），跳过写回")
            return
        for c in cookies:
            if c["name"].startswith("remember_web_"):
                val = c["value"]
                break
        import json as _json, base64 as _b64, urllib.parse as _up
        if not val:
            print("  ℹ️ 浏览器内无 remember_web cookie，跳过写回")
            return
        # 格式驗證：確保係正版 Laravel token（防寫壞 secret 害死下一輪）
        try:
            dec = _up.unquote(val)
            payload = _json.loads(_b64.b64decode(dec + "=" * (-len(dec) % 4)))
            assert sorted(payload.keys()) == ["iv", "mac", "tag", "value"], payload.keys()
        except Exception:
            print(f"  ⚠️ remember 格式异常，跳过写回（防寫壞 secret）: {val[:40]}...")
            return
        import requests as _rq
        r = _rq.get(f"https://api.github.com/repos/{repo}/actions/secrets/public-key",
                    headers={"Authorization": f"Bearer {gt}"}, timeout=20)
        kd = r.json()
        from nacl import encoding as _enc, public as _pub
        pk = _pub.PublicKey(kd["key"].encode(), _enc.Base64Encoder())
        enc = _pub.SealedBox(pk).encrypt(val.encode())
        body = {"encrypted_value": base64.b64encode(enc).decode(), "key_id": kd["key_id"]}
        rr = _rq.put(f"https://api.github.com/repos/{repo}/actions/secrets/ORIHOST_REMEMBER",
                     headers={"Authorization": f"Bearer {gt}"}, json=body, timeout=20)
        print(f"  🔁 remember 已轮换写回 secret: HTTP {rr.status_code}")
        # 同步埋 server IDs（其實唔會變，但保險）
    except Exception as e:
        print(f"  ⚠️ 写回 secret 失败（不影响续期）: {str(e)[:120]}")



def _article_to_ready(sb, sid, main_handle=None):
    """点 Read Article → 等对话框进入 Step B（"Thanks for reading!"）。

    返回: "ready" / "cooldown"（刚续期过）/ "cdp-dead"（读状态时通道冻结超限）
          / 其他状态字符串（"confirm"/"not-ready" 等）。
    设计成可复用：主流程第一轮用，CDP 中断恢复的第二轮也调它（2026-10-03 run 37126904215
    教训：恢复时只重开了对话框、没重点 Read Article，导致永远卡在 Step A）。
    """
    # 垫片幂等：JS 合成 click 冇 user activation，Chrome 会拦截 window.open；
    # 垫片返假 window 令面板状态机行得落去（run 35450777798 实证）。
    print("  \U0001fa79 装 window.open 垫片...")
    try:
        print("    ", sb.execute_script(_JS_PATCH_WINDOW_OPEN))
    except Exception as e:
        print("  \u26a0\ufe0f 垫片失败:", str(e)[:80])
    try:
        print("    XHR 诊断钩:", sb.execute_script(_JS_PATCH_XHR))
    except Exception as e:
        print("  \u26a0\ufe0f XHR 钩失败:", str(e)[:80])

    print("  \U0001f5b1\ufe0f 点 Read Article...")
    read_res = click_by_text(sb, "read article", timeout=15)
    print(f"    {read_res}")
    if not str(read_res).startswith("clicked"):
        print(f"  \u26a0\ufe0f Read Article 未点中（{read_res}）")
        return "confirm"
    if main_handle:
        try:
            n_closed, main_handle = close_popup_windows(sb, main_handle)
        except Exception as e:
            print(f"    \u26a0\ufe0f 关弹窗异常: {str(e)[:60]}")
    time.sleep(2)

    # 等面板 POST /renew/begin 返文章 URL，用页面内 fetch 触发一次请求（唔开真标签；
    # Target.createTarget 会搞烂 CDP 连线 —— run 35452477886 实证）。
    art_url = ""
    for _ in range(20):
        try:
            art_url = str(sb.execute_script(_JS_GET_ARTICLE_URL) or "")
        except Exception:
            art_url = ""
        if art_url.startswith("http"):
            break
        time.sleep(1)
    if art_url.startswith("http"):
        try:
            sb.execute_script(
                "(function(){try{fetch(%s,{credentials:'include',mode:'no-cors'})"
                ".catch(function(){})}catch(e){}return 'fetched'})()" % json.dumps(art_url)
            )
            print("    \U0001f4c4 已用页面内 fetch 触发一次文章请求（唔开新标签）")
        except Exception as e:
            print(f"    （fetch 文章页失败，唔影响: {str(e)[:60]}）")
    else:
        print(f"  \u26a0\ufe0f 未拿到文章 URL（面板可能仍在 confirm），read_res={read_res}")

    # 等 Step B：2026-10-03 起新版对话框无数字倒计时，纯文字等待；
    # 旧版数字倒计时（"claim your renewal in N"）若出现仍兼容。
    print(f"  \u23f3 等对话框进入 Step B（最多 {CLAIM_TIMEOUT}s）...")
    cd = dialog_countdown(sb)
    wait_ready = CLAIM_TIMEOUT if not cd else min(max(CLAIM_TIMEOUT, cd + 60), 600)
    print(f"    （数字倒计时 {cd}s → 最多等 {wait_ready}s；无倒计时则纯文字等待）")
    st = wait_modal_state(sb, "ready", wait_ready, main_handle=main_handle)
    if st == "cooldown":
        return "cooldown"
    if st == "ready":
        return "ready"
    return st if st else "not-ready"


def _dump_dead_diag(sb, tag):
    """通道判死时的现场取证：URL + page source 是否可读 + 截图。"""
    try:
        print(f"    🔎 [{tag}] 当前 URL: {sb.driver.current_url}")
    except Exception as e:
        print(f"    🔎 [{tag}] URL 读取失败: {str(e)[:80]}")
    try:
        src = sb.get_page_source() or ""
        print(f"    🔎 [{tag}] page_source 可读，长度 {len(src)}")
    except Exception as e:
        print(f"    🔎 [{tag}] page_source 读取失败: {str(e)[:80]}")
    try:
        sb.save_screenshot(f"cdp_dead_{tag}.png")
        print(f"    🔎 [{tag}] 已截图 cdp_dead_{tag}.png")
    except Exception as e:
        print(f"    🔎 [{tag}] 截图失败: {str(e)[:80]}")


def wait_claim_ready(sb, timeout, main_handle=None):
    """Step B 专用的 Claim 等待：Turnstile 挂载→验证→按钮可用→点击。

    2026-10-03 真机结论：Turnstile 只在 Step B（"Thanks for reading!" 之后）挂载；
    在此之前轮询它永远等不到。Claim Renewal 在验证通过前是 disabled。
    带 CDP 健康看门狗：连续 3 次 js_health 异常即判通道中断，不再盲轮询。
    返回: 'clicked' / 'timeout' / 'cdp-dead' / 'dialog-closed' / 'cooldown' / 'turnstile-fail'
    """
    end = time.time() + timeout
    bad_since = None
    n = 0
    while time.time() < end:
        n += 1
        # —— 看门狗：JS 通道健康（容忍短暂卡死，持续超 CDP_RECOVER_WAIT 才判死） ——
        h = js_health(sb)
        if h != "pong:2":
            if bad_since is None:
                bad_since = time.time()
                print(f"    ⚠️ JS 通道无响应，等待恢复…（{h[:90]}）")
            waited = int(time.time() - bad_since)
            if waited >= CDP_RECOVER_WAIT:
                print(f"    ⚠️ JS 通道持续 {waited}s 无响应，判定中断（{h[:90]}）")
                _dump_dead_diag(sb, "claim")
                return "cdp-dead"
            if waited % 15 == 0:
                print(f"    …通道仍无响应（已 {waited}s，{h[:90]}）")
            time.sleep(3)
            continue
        if bad_since is not None:
            print(f"    ✅ JS 通道恢复（中断约 {int(time.time() - bad_since)}s），继续")
            if main_handle:
                try:
                    n_closed, main_handle = close_popup_windows(sb, main_handle)
                    if n_closed:
                        print(f"    \U0001fa9f 通道恢复后关掉 {n_closed} 个弹窗")
                except Exception as e:
                    print(f"    \u26a0\ufe0f 关弹窗异常: {str(e)[:60]}")
        bad_since = None

        st = detect_state(sb)
        if st == "closed":
            return "dialog-closed"
        if st == "cooldown":
            return "cooldown"

        rep = kill_ad_overlay(sb)
        if n <= 2:
            print(f"    🧹 广告清理: {rep[:160]}")
        if main_handle and n % 4 == 1:
            try:
                n_closed, main_handle = close_popup_windows(sb, main_handle)
            except Exception:
                pass

        # —— Step B 才会有 Turnstile；有就先验证 ——
        try:
            has_ts = bool(sb.execute_script(_HAS_TURNSTILE_JS))
        except Exception:
            has_ts = False
        if has_ts:
            print("    🔐 检测到 Turnstile，先完成验证")
            ts_state = handle_turnstile(sb)
            if ts_state is False:
                return "turnstile-fail"
            # None = 组件闪了一下又没了/没挂载稳，不算失败，继续去探 Claim
            time.sleep(1)

        # —— 探 Claim Renewal ——
        res = str(click_by_text(sb, "claim renewal", timeout=6))
        if res.startswith("clicked"):
            print(f"    🖱️ 点 Claim Renewal: {res[:60]}")
            return "clicked"
        if res.startswith("disabled"):
            if n <= 3 or n % 5 == 0:
                print(f"    Claim 仍 disabled（第 {n} 次），等验证/状态同步…")
        elif res == "js-null":
            if n <= 3 or n % 5 == 0:
                print(f"    ⚠️ Claim 查询返回空（第 {n} 次），通道可能抖动，看门狗会处理")
        elif n <= 3 or n % 5 == 0:
            print(f"    （第 {n} 次 state={st!r} claim={res[:50]}）")
        time.sleep(TURNSTILE_POLL_INTERVAL)
    return "timeout"


# ---------- 单台续期 ----------
def renew_one_server(sb, server_uuid: str) -> dict:
    sid = (server_uuid or "").split("-")[0][:8]
    print(f"\n  🖥 [{sid}] 打开服务器页...")
    # 面板路由用的是 8 位短 ID（如 /server/8651e616），填了完整 UUID 也只取前 8 位
    sb.open(f"{PANEL}/server/{sid}")
    time.sleep(8)
    try:
        main_handle = sb.driver.current_window_handle
    except Exception:
        main_handle = None

    # 先读面板 API 真实状态（最可信）：renewable=False 或 renewal>=18 就係已达上限
    days_before = None
    info, err = api_renewal(sb, sid)
    if info:
        days_before = info.get("renewal")
        print(f"  📊 续期前：renewal={days_before} 天 renewable={info.get('renewable')} status={info.get('status')}")
        d = days_before
        if info.get("renewable") is False or (isinstance(d, (int, float)) and d >= 18):
            return {"status": "\u23ed\ufe0f 跳过",
                    "message": f"已达续期上限（API: renewal={d} 天 renewable={info.get('renewable')}）"}
    elif err:
        print(f"  ⚠️ 读续期天数失败: {err}")

    src = page_text(sb)
    if "renew limit reached" in src:
        return {"status": "⏭️ 跳过", "message": "已达续期上限（Renew Limit Reached）"}
    if "expired renewal" in src or "suspended due" in src:
        print("  ⚠️ 服务器因过期被暂停，走续期流程恢复")

    # 1. 打开续期对话框：页面级入口按钮文案系「Renew」（停权页系「Renew Server」）
    print("  🔍 找 Renew 入口按钮...")
    state = open_renew_dialog(sb, timeout=25)
    if state == "limit":
        return {"status": "⏭️ 跳过", "message": "已达续期上限（Renew Limit Reached）"}
    if state is None:
        dump_page_debug(sb, sid)
        try:
            sb.execute_script("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            pass
        time.sleep(1)
        sb.save_screenshot(f"no_renew_btn_{sid}.png")
        return {"status": "❌ 续期失败", "message": "没找到 Renew 入口按钮（页面结构可能变了）"}
    time.sleep(4)

    # 1b. 对话框里的两种快路
    dlg_src = page_text(sb)
    if "you renewed recently" in dlg_src:
        return {"status": "⏭️ 跳过", "message": "刚续期过，对话框显示冷却中（You renewed recently）"}
    now_res = click_by_text(sb, "renew now", timeout=5)
    if str(now_res).startswith("clicked"):
        print(f"  ⚡ ad-free 帐号：对话框里直接 Renew Now（{now_res}）")
        return read_renew_result(sb, sid, days_before)

    # 2+3. 文章 → Step B → Claim（最多 2 轮；第 2 轮用于通道中断/超时后的完整重试。
    #    2026-10-03 run 37126904215：Step B 开始时 JS 通道卡死约 1 分钟后自行恢复，
    #    看门狗已改为耐心等 90s；若仍失败，第二轮会关对话框、重开、完整重做一次，
    #    而不是只等（旧恢复逻辑漏了重点 Read Article 的 bug 已修）。）
    for attempt in (1, 2):
        if attempt == 2:
            print("  \U0001f504 第二轮：重开对话框，完整重做一次…")
            try:
                click_by_text(sb, "cancel", timeout=5)
            except Exception:
                pass
            time.sleep(3)
            reopen = open_renew_dialog(sb, timeout=20)
            if reopen == "limit":
                return {"status": "\u23ed\ufe0f 跳过", "message": "已达续期上限（Renew Limit Reached）"}
            if reopen != "ok":
                return {"status": "❌ 续期失败", "message": "第二轮重开续期对话框失败"}
            time.sleep(3)
            if "you renewed recently" in page_text(sb):
                return {"status": "\u23ed\ufe0f 跳过", "message": "刚续期过，对话框显示冷却中（You renewed recently）"}

        art = _article_to_ready(sb, sid, main_handle)
        if art == "cooldown":
            return {"status": "\u23ed\ufe0f 跳过", "message": "刚续期过，对话框显示冷却中（You renewed recently）"}
        if art == "cdp-dead":
            if attempt == 1:
                print("  ⚠️ 第一轮读状态时 JS 通道中断，进第二轮重试…")
                continue
            sb.save_screenshot(f"cdp_dead_{sid}.png")
            return {"status": "❌ 续期失败", "message": "页面 JS 通道中断且两轮恢复都失败（CDP 无响应），请人工检查后重跑"}
        if art != "ready":
            if attempt == 1:
                print(f"  \u26a0\ufe0f 第一轮文章步骤未完成（{art}），进第二轮重试…")
                continue
            if art == "confirm":
                sb.save_screenshot(f"stuck_confirm_{sid}.png")
                return {"status": "❌ 续期失败", "message": "对话框卡在 confirm（Read Article 未生效/弹窗被拦）"}
            return {"status": "❌ 续期失败", "message": f"两轮都到不了 Step B（{art}）"}

        cres = wait_claim_ready(sb, CLAIM_TIMEOUT if attempt == 1 else min(CLAIM_TIMEOUT, 240), main_handle)
        try:
            main_handle = sb.driver.current_window_handle
        except Exception:
            pass
        if cres == "clicked":
            return read_renew_result(sb, sid, days_before)
        if cres == "cooldown":
            return {"status": "\u23ed\ufe0f 跳过", "message": "刚续期过，对话框显示冷却中（You renewed recently）"}
        if cres == "turnstile-fail":
            sb.save_screenshot(f"turnstile_fail_{sid}.png")
            return {"status": "❌ 续期失败", "message": f"Turnstile 验证 {TURNSTILE_MAX_ATTEMPTS} 次未通过"}
        if attempt == 1 and cres in ("cdp-dead", "dialog-closed", "timeout"):
            print(f"  \u26a0\ufe0f 第一轮 Claim 阶段异常（{cres}），进第二轮完整重试…")
            continue
        if cres == "cdp-dead":
            sb.save_screenshot(f"cdp_dead_{sid}.png")
            return {"status": "❌ 续期失败", "message": "页面 JS 通道中断且两轮恢复都失败（CDP 无响应），请人工检查后重跑"}
        if cres == "dialog-closed":
            sb.save_screenshot(f"dialog_closed_{sid}.png")
            return {"status": "❌ 续期失败", "message": "等待期间续期对话框被关闭/消失，未能点击 Claim"}
        sb.save_screenshot(f"no_claim_btn_{sid}.png")
        try:
            js = ("(function(){var els=document.querySelectorAll('button,a,[role=button]');"
                  "var o=[];for(var i=0;i<els.length;i++){var t=(els[i].textContent||'')"
                  ".trim().slice(0,30);if(t)o.push(els[i].tagName+'['+t+']')}"
                  "return o.join(' | ')})()")
            print(f"    \U0001f9fe 超时前按钮文案: {str(sb.execute_script(js))[:600]}")
        except Exception as e:
            print(f"    按钮枚举失败: {str(e)[:80]}")
        return {"status": "❌ 续期失败",
                "message": "等唔到可点嘅 Claim Renewal（Turnstile 未挂载或按钮持续 disabled，面板结构可能又变了）"}

    return {"status": "❌ 续期失败", "message": "两轮重试均未完成（未知）"}


def fmt_msg(status, label, server_uuid, detail):
    sid = (server_uuid or "").split("-")[0][:8]
    return f"🖥 Orihost 浏览器续期\n{status}\n👤 {label}\n🆔 {sid}\n📌 {detail}\n⏰ {now_bj()}（北京）"


# ---------- 主入口 ----------
def main():
    print("#" * 42)
    print("   Orihost 浏览器自动续期" + ("（代理开）" if IS_PROXY else "（直连）"))
    print("#" * 42)
    accounts = load_accounts()
    if not accounts:
        print("❌ 未配置账号。请设置 ORIHOST_REMEMBER + ORIHOST_SERVER_IDS ...")
        sys.exit(1)

    sb_kwargs = {"uc": True, "headless": False,
                 "chromium_arg": "--disable-popup-blocking,--disable-notifications"}
    if IS_PROXY:
        print(f"🔗 挂载代理: {PROXY_STR}")
        sb_kwargs["proxy"] = PROXY_STR
    else:
        print("🌐 未使用代理，直连访问")

    results = []
    print("🚀 启动浏览器...")
    with SB(**sb_kwargs) as sb:
        try:
            sb.open("https://api.ip.sb/ip")
            print(f"📍 当前出口IP: {sb.get_text('body')}")
        except Exception:
            pass
        for acc in accounts:
            label = acc["label"]
            print(f"\n{'=' * 42}\n {label}：{len(acc['servers'])} 台\n{'=' * 42}")
            if not cookie_login(sb, acc["auth"]):
                for sv in acc["servers"]:
                    info = {"label": label, "server": sv, "status": "❌ 登录失败", "message": "Cookie 免登失败，remember 可能失效"}
                    results.append(info)
                    send_tg(fmt_msg(info["status"], label, sv, info["message"]))
                continue
            for sv in acc["servers"]:
                try:
                    r = renew_one_server(sb, sv)
                except Exception as e:
                    r = {"status": "❌ 续期失败", "message": f"异常: {str(e)[:120]}"}
                info = {"label": label, "server": sv, "status": r["status"], "message": r.get("message", "")}
                results.append(info)
                print(f"  {info['status']} {info['message']}")
                send_tg(fmt_msg(info["status"], label, sv, info["message"]))
                time.sleep(random.randint(2, 5))

    ok = sum(1 for r in results if "成功" in r["status"])
    skip = sum(1 for r in results if "跳过" in r["status"])
    fail = len(results) - ok - skip
    print(f"\n{'=' * 42}\n📊 汇总：{ok} 成功 / {skip} 跳过 / {fail} 失败，共 {len(results)} 台\n{'=' * 42}")
    if fail:
        sys.exit(2)


if __name__ == "__main__":
    main()
