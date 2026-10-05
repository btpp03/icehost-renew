# IceHost Renew + Keepalive

IceHost.pl 免费 Minecraft 服的**自动续期 + 保活**（GitHub Actions）。

## 为什么需要
免费服有效期只有 **24h**，面板「DODAJ 6 GODZIN WAŻNOŚCI」每次 **+6h**，且**有冷却**
（刚续过会返回 `niedawno to zrobiłeś`）。所以策略是**每 2 小时试一次**，能续就续。

## 它做什么
1. 真 Chromium（手动起 + CDP，不带自动化标志）过 Cloudflare + 面板自带 WAF
2. 登录面板
3. `POST /api/client/freeservers/<uuid>/renew` 续期（冷却=正常，静默）
4. 服务器不在 RUNNING 就经 WebSocket 发 `start`
5. 可选 Telegram 通知

## Secrets（Settings → Secrets and variables → Actions）
| 名称 | 必需 | 说明 |
|---|---|---|
| `ICEHOST_EMAIL` | ✅ | 面板登录邮箱 |
| `ICEHOST_PW` | ✅ | 面板登录密码 |
| `ICEHOST_UUID` | 可选 | 服务器 uuid（默认 `7aaf1e7c`） |
| `TG_BOT_TOKEN` | 可选 | Telegram 通知 |
| `TG_CHAT_ID` | 可选 | Telegram chat id |

## 手动触发
Actions → IceHost Renew + Keepalive → Run workflow

## 已知风险
Cloudflare 对数据中心 IP（含 GitHub runner）可能给交互式盾；脚本用真浏览器 + 穿透 shadow DOM
点 Turnstile 尽力过。若日志显示 `Cloudflare 挑战未通过`，说明该 runner IP 被挡，需要换出口。
