# Orihost 自动续期

由 `hostship-renew` 一比一移植（同为 Pelican/Pterodactyl 系面板，接口一致）。
纯 HTTP，无浏览器——CF 验证盾、广告弹窗天然绕过，无需处理。

## Secrets（Settings → Secrets and variables → Actions）

| 名称 | 必填 | 说明 |
|---|---|---|
| `API_KEY` | ✅ | 面板 Account → API Credentials 新建的 Client key（`ptlc_`开头） |
| `SERVER_ID` | ✅ | 服务器 UUID（面板服务器 URL 里那串，如 `8651e616…`） |
| `COOKIE` | ❌ | 备链用（`a=b; c=d` 格式，至少含 `remember_web`） |
| `EMAIL` | ❌ | 通知备注名 |
| `TG_BOT_TOKEN` / `TG_CHAT_ID` | ❌ | Telegram 通知 |

## 逻辑

主链 API key `POST /api/client/servers/{uuid}/renew`（204/200/201/202 算成功）
→ 主链 401/403 且配了 COOKIE 则切备链（Cookie+XSRF 头）
→ 再 GET 一次验到期变化 → TG 通知（Hiden 风格）。

## 定时

默认每天 UTC 7 点（北京时间 15 点），`renew.yml` 里改 `cron`。
`workflow_dispatch` 可手动跑。旧运行记录只保留最近 1 条。
