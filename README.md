# IceHost Renew + Keepalive

IceHost.pl 免费 Minecraft 服的**自动续期 + 保活**（GitHub Actions）。

## 为什么需要
免费服有效期只有 **24h**，面板「DODAJ 6 GODZIN WAŻNOŚCI」每次 **+6h**，且**有冷却**
（刚续过会返回 `niedawno to zrobiłeś`）。所以策略是**每 2 小时试一次**，能续就续。

## 实测踩到的坑（2026-10-06，都已在代码里解决）
1. **GitHub runner 的 Azure IP 被面板自带 WAF 硬封** —— 直接访问拿到 `IceHost - Block`
   （连 CF 挑战页都不给）。**必须先经一个干净出口**。
   → workflow 在 runner 内起 **sing-box**（outbound 来自 `secrets.ICEHOST_NODE`），
     浏览器走 `socks5://127.0.0.1:1080`；此时拿到的是 CF 挑战页（可解）而不是 Block。
2. **CF 交互盾**：穿透 closed shadow root 找到 Turnstile iframe，CDP `Input.dispatchMouseEvent` 真点击
   （普通 `querySelectorAll('iframe')` 看不到它）。实测点 1~2 次即过。
3. **登录表单依赖 Google reCAPTCHA**：如果 Google 域也走代理，`grecaptcha` 加载失败 →
   `submitForm() TypeError: grecaptcha.execute is not a function` → **请求根本不发**。
   → chromium 加 `--proxy-bypass-list=*.google.com,*.gstatic.com,*.googleapis.com,...`
     让 reCAPTCHA 直连。
4. **续期接口要「完整 uuid」**（`xxxxxxxx-xxxx-...`），页面文字里只有 8 位短 id。
   → 脚本用 `/api/client/servers/<short>` 的 JSON 里取 `uuid`（自愈，不写死）。

## 它做什么
1. 真 Chromium（手动起 + `--remote-debugging-port`，不带自动化标志）挂 CDP
2. 过 Cloudflare 挑战（最多 3 轮）→ 登录面板（键盘输入 + 回车/点按钮）
3. `POST /api/client/freeservers/<full-uuid>/renew` 续期（冷却=正常，静默）
4. 服务器不在 RUNNING 就经 WebSocket 发 `start`（面板无 HTTP 电源路由）
5. 可选 Telegram 通知

## Secrets（Settings → Secrets and variables → Actions）
| 名称 | 必需 | 说明 |
|---|---|---|
| `ICEHOST_EMAIL` | ✅ | 面板登录邮箱 |
| `ICEHOST_PW` | ✅ | 面板登录密码 |
| `ICEHOST_NODE` | 强烈建议 | **sing-box 完整配置 JSON**（含 inbounds socks:1080 + outbound vless 出口）。不配则大概率被 WAF Block |
| `ICEHOST_UUID` | 可选 | 服务器短 uuid（默认 `7aaf1e7c`） |
| `TG_BOT_TOKEN` | 可选 | Telegram 通知 |
| `TG_CHAT_ID` | 可选 | Telegram chat id |

## 手动触发
Actions → IceHost Renew + Keepalive → Run workflow

## 状态
2026-10-06 07:20 (BJ) 实测：解盾 ✓ 登录 ✓ 取 uuid ✓ 调续期 ✓（返回冷却，优雅退出）
