#!/usr/bin/env python3
"""
IceHost.pl 免费服 —— GitHub Actions 续期 + 保活
==============================================
在 GitHub runner 上：
  1. 用真 Chromium（手动起 + --remote-debugging-port，不带自动化标志）挂 CDP
  2. 过 Cloudflare + 面板自带 WAF，登录面板
  3. POST /api/client/freeservers/<uuid>/renew 续期 +6h（撞冷却则视为正常）
  4. 服务器不在 RUNNING 就用 WebSocket 发 start
  5. 可选 Telegram 通知

环境变量:
  必需  ICEHOST_EMAIL / ICEHOST_PW
  可选  TG_BOT_TOKEN / TG_CHAT_ID
  可选  ICEHOST_UUID (默认 7aaf1e7c)
"""
import os, sys, json, time, subprocess, urllib.request, urllib.parse, datetime, tempfile, shutil, re

UUID = os.environ.get("ICEHOST_UUID", "7aaf1e7c").strip()
FULL = os.environ.get("ICEHOST_FULL_UUID", "").strip()
BASE = "https://dash.icehost.pl"
EMAIL = os.environ.get("ICEHOST_EMAIL", "").strip()
PW = os.environ.get("ICEHOST_PW", "").strip()
TG_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT = os.environ.get("TG_CHAT_ID", "").strip()
PROXY = os.environ.get("PROXY", "").strip()          # 例: socks5://127.0.0.1:1080
DEBUG = os.environ.get("DEBUG", "0") == "1"

TZ_CN = datetime.timezone(datetime.timedelta(hours=8))
def bj():
    return datetime.datetime.now(TZ_CN).strftime("%m-%d %H:%M:%S")

LOG = []
def log(*a):
    s = " ".join(str(x) for x in a)
    LOG.append(s)
    print(s, flush=True)

def tg(text):
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        u = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage?chat_id={TG_CHAT}&text=" + urllib.parse.quote(text)
        urllib.request.urlopen(u, timeout=20).read()
    except Exception as e:
        log("TG 通知失败:", e)

def die(msg, code=1):
    log("FATAL:", msg)
    tg(f"❌ IceHost 续期失败: {msg}")
    sys.exit(code)


def main():
    if not EMAIL or not PW:
        die("缺少 ICEHOST_EMAIL / ICEHOST_PW")
    log(f"=== IceHost renew @ {bj()} (BJ) ===")

    # runner 出口 IP（诊断用）
    try:
        ip = urllib.request.urlopen("https://api.ipify.org", timeout=15).read().decode()
        log("runner IP:", ip)
    except Exception:
        log("runner IP: 取不到")
    if PROXY:
        try:
            out = subprocess.run(["curl", "-s", "--max-time", "20", "-x", PROXY, "https://api.ipify.org"],
                                 capture_output=True, text=True, timeout=30)
            log("proxy exit IP:", (out.stdout or "").strip() or f"(失败: {out.stderr.strip()[:80]})")
        except Exception as e:
            log("proxy exit IP: 检查失败", e)

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        exe = p.chromium.executable_path
        log("chromium:", exe)
        prof = tempfile.mkdtemp(prefix="iceprof_")
        port = 9333
        env = dict(os.environ)
        for k in ("http_proxy","https_proxy","HTTP_PROXY","HTTPS_PROXY","all_proxy","ALL_PROXY"):
            env.pop(k, None)
        chrome_args = [
            exe, f"--remote-debugging-port={port}", f"--user-data-dir={prof}",
            "--no-sandbox", "--disable-dev-shm-usage", "--no-first-run", "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled",
            f"--window-size=1440,900", "about:blank",
        ]
        if PROXY:
            # 只让面板走代理；Google reCAPTCHA 等直连（否则 grecaptcha 加载失败，登录提交会抛错）
            chrome_args.insert(-2, f"--proxy-server={PROXY}")
            chrome_args.insert(-2,
                "--proxy-bypass-list=*.google.com,*.gstatic.com,*.googleapis.com,*.recaptcha.net,localhost,127.0.0.1")
            log("browser proxy:", PROXY, "(google/reCAPTCHA 域走直连)")
        chrome = subprocess.Popen(chrome_args,
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        ok = False
        for _ in range(60):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3) as r:
                    log("CDP:", json.load(r).get("Browser")); ok = True; break
            except Exception:
                time.sleep(1)
        if not ok:
            chrome.kill()
            die("Chrome/CDP 起不来")

        try:
            run(p, port)
        finally:
            try: chrome.kill()
            except Exception: pass
            shutil.rmtree(prof, ignore_errors=True)


def run(p, port):
    from playwright.sync_api import sync_playwright
    b = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    ctx = b.contexts[0]
    page = ctx.new_page()
    cdp = ctx.new_cdp_session(page)

    def solve(page, budget=120):
        """穿透 shadow DOM 找 Turnstile iframe 并真点击"""
        def rects():
            try: doc = cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})
            except Exception: return []
            st = [doc["root"]]; out = []
            while st:
                n = st.pop()
                if n.get("nodeName") == "IFRAME":
                    a = dict(zip(n.get("attributes", [])[::2], n.get("attributes", [])[1::2]))
                    if "challenges.cloudflare.com" in (a.get("src") or ""):
                        out.append(n)
                for c in (n.get("children") or []): st.append(c)
                for s in (n.get("shadowRoots") or []): st.append(s)
                if n.get("contentDocument"): st.append(n["contentDocument"])
            res = []
            for n in out:
                try:
                    oid = cdp.send("DOM.resolveNode", {"nodeId": n["nodeId"]})["object"]["objectId"]
                    res.append(json.loads(cdp.send("Runtime.callFunctionOn", {"objectId": oid,
                      "functionDeclaration": "function(){var b=this.getBoundingClientRect();return JSON.stringify({x:b.x,y:b.y,w:b.width,h:b.height});}",
                      "returnByValue": True})["result"]["value"]))
                except Exception: pass
            return res
        t0 = time.time(); c = 0
        while time.time() - t0 < budget:
            page.wait_for_timeout(1500)
            if "Just a moment" not in page.title() and "Cierpliwo" not in page.title():
                return True
            hit = False
            for r in rects():
                if r["w"] > 5 and r["h"] > 5:
                    x, y = r["x"] + 25, r["y"] + r["h"] / 2
                    cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y}); page.wait_for_timeout(150)
                    cdp.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1}); page.wait_for_timeout(150)
                    cdp.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})
                    c += 1; log(f"  cf click #{c}"); page.wait_for_timeout(4000); hit = True; break
            if not hit:
                page.wait_for_timeout(1500)
        return False

    def nav(url, wait=6000):
        page.goto(url, wait_until="domcontentloaded", timeout=90000)
        if "Just a moment" in page.title() or "Cierpliwo" in page.title():
            log("  CF 挑战中...")
            if not solve(page):
                log("  ⚠️ CF 挑战没能解决")
        page.wait_for_timeout(wait)

    def body():
        try: return " ".join((page.evaluate("document.body.innerText") or "").split())
        except Exception: return ""

    # 0) 站点可达性（CF 挑战最多解 3 轮）
    ok_page = False
    for attempt in range(3):
        nav(BASE + "/auth/login", 6000)
        t = page.title()
        log(f"after login-page (try {attempt+1}):", page.url, "|", t)
        if "Just a moment" in t or "Cierpliwo" in t:
            log("  -> 还在 CF 挑战，再解一轮")
            if solve(page, budget=120):
                page.wait_for_timeout(3000)
                t = page.title()
                log("  -> 解盾后 title:", t)
            else:
                log("  -> 本轮解盾失败")
        if "Just a moment" not in t and "Cierpliwo" not in t:
            ok_page = True
            break
    if not ok_page:
        die(f"Cloudflare 挑战未通过（runner 出口被挡）title={page.title()}")
    txt = body()
    if "WAF Challange" in txt or page.title().strip().endswith("- Block"):
        die(f"被面板 WAF 挡住 (title={page.title()})")
    log("login page ok, fields:",
        json.dumps(page.evaluate("()=>[...document.querySelectorAll('input')].map(e=>e.name)"), ensure_ascii=False))

    # 1) 登录
    login_resps = []
    all_reqs = []
    def on_resp(r):
        try:
            if "google" in r.url or "tawk" in r.url:
                return
            if r.request.method in ("POST", "PUT"):
                try: b = r.text()[:300]
                except Exception: b = ""
                login_resps.append((r.status, r.request.method, r.url[:100], b))
        except Exception:
            pass
    def on_req(r):
        try:
            if "google" in r.url or "tawk" in r.url:
                return
            if r.method in ("POST", "PUT"):
                all_reqs.append((r.method, r.url[:100]))
        except Exception:
            pass
    page.on("response", on_resp)
    page.on("request", on_req)
    console = []
    page.on("console", lambda m: console.append(f"{m.type}: {m.text[:150]}"))
    if "/auth/login" in page.url:
        try:
            page.click("input[name=username]")
            page.keyboard.type(EMAIL, delay=40)
            page.click("input[name=password]")
            page.keyboard.type(PW, delay=40)
            page.wait_for_timeout(600)
            log("input values:", json.dumps(page.evaluate(
                "()=>[...document.querySelectorAll('input')].map(e=>e.name+'='+e.value.slice(0,3)+'..')"
            ), ensure_ascii=False)[:200])
            # 先试回车（表单原生提交），再试点按钮
            page.keyboard.press("Enter")
            page.wait_for_timeout(8000)
            if "/auth/login" in page.url:
                log("回车没走，改点按钮")
                for sel in ["button:has-text('Zaloguj się do panelu')", "button[type=submit]",
                            "button:has-text('Zaloguj')"]:
                    try:
                        el = page.query_selector(sel)
                        if el:
                            el.click(force=True); log("clicked:", sel); break
                    except Exception:
                        continue
                page.wait_for_timeout(10000)
        except Exception as e:
            die(f"登录表单填写失败: {str(e)[:120]}")
        log("after login:", page.url)
        log("POST reqs seen:", json.dumps(all_reqs[-5:], ensure_ascii=False)[:400])
        log("POST resps:", json.dumps(login_resps[-4:], ensure_ascii=False)[:600])
        log("console:", json.dumps(console[-8:], ensure_ascii=False)[:500])
        if "/auth/login" in page.url:
            log("page text:", body()[:300])
            die("登录失败（表单没提交成功 / 账号密码不对 / 被盾拦）")

    # 2) 打开服务器页，读续期前有效期
    nav(f"{BASE}/server/{UUID}", 7000)
    t = body()
    m = re.search(r"DATA WAŻNOŚCI:\s*([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9:]{8})", t)
    v0 = m.group(1) if m else ""
    # 取完整 uuid（面板 API 给的是完整 uuid；页面文字里只有短 id）
    full = FULL
    if not full:
        try:
            js_info = page.evaluate("""async (u)=>{
              try{
                const r=await fetch('/api/client/servers/'+u,{headers:{'Accept':'application/json','X-Requested-With':'XMLHttpRequest'},credentials:'same-origin'});
                return await r.text();
              }catch(e){ return 'ERR '+e; }
            }""", UUID)
            mm = re.search(r'"uuid"\s*:\s*"([0-9a-fA-F-]{36})"', js_info)
            if mm:
                full = mm.group(1)
            else:
                log("server API 片段:", js_info[:200])
        except Exception as e:
            log("取完整 uuid 失败:", str(e)[:120])
    log("full uuid:", full or "(没拿到)")

    # 3) 续期
    if not full:
        log("⚠️ 拿不到完整 uuid，尝试用 short uuid 调接口")
        full = UUID
    js = """
    async (full) => {
      const m = document.cookie.match(/(?:^|;\\s*)XSRF-TOKEN=([^;]+)/);
      const tok = m ? decodeURIComponent(m[1]) : '';
      try {
        const r = await fetch('/api/client/freeservers/'+full+'/renew', {
          method:'POST', credentials:'same-origin',
          headers:{'X-XSRF-TOKEN':tok,'Accept':'application/json',
                   'X-Requested-With':'XMLHttpRequest','Content-Type':'application/json'}
        });
        let b=''; try{ b=await r.text(); }catch(e){}
        return JSON.stringify({status:r.status, body:b.slice(0,400)});
      } catch(e) { return JSON.stringify({err:String(e)}); }
    }
    """
    raw = page.evaluate(js, full)
    log("renew raw:", raw)
    try: rr = json.loads(raw)
    except Exception: rr = {"err": raw}
    st = rr.get("status"); bod = rr.get("body", "") or ""
    renewed = (st == 200 and "success" in bod)
    cooling = ("niedawno" in bod) or ("recently" in bod.lower())

    # 4) 读续期后有效期
    try:
        page.reload(wait_until="domcontentloaded", timeout=60000); page.wait_for_timeout(5000)
    except Exception: pass
    t2 = body()
    m3 = re.search(r"DATA WAŻNOŚCI:\s*([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9:]{8})", t2)
    v1 = m3.group(1) if m3 else ""

    # 5) 保活：不在运行就 start
    state = ""
    ms = re.search(r"([A-Z]{4,})\s*\((\d+godz[^)]*)\)", t2)
    if ms:
        state = f"{ms.group(1)}({ms.group(2)})"
    else:
        up = (t2 + " " + t).upper()
        for kw in ("RUNNING", "STARTING", "STOPPING", "STOPPED", "OFFLINE", "SUSPENDED"):
            if kw in up:
                state = kw
                break
    log("state:", state or "(没读到)")

    WSJS = """
    async (sig) => {
      try {
        const r = await fetch('/api/client/servers/UU/websocket'); const j = await r.json();
        if(!j.data||!j.data.socket) return 'no-socket';
        const ws = new WebSocket(j.data.socket); const tok=j.data.token;
        return await new Promise(res=>{
          const t=setTimeout(()=>res('timeout'),25000);
          ws.onopen=()=>ws.send(JSON.stringify({event:'auth',args:[tok]}));
          ws.onmessage=(ev)=>{ try{const mm=JSON.parse(ev.data);
            if(mm.event==='auth success') ws.send(JSON.stringify({event:'set state',args:[sig]}));
            if(mm.event==='status'){clearTimeout(t);res(mm.args[0]);}
          }catch(e){} };
          ws.onerror=()=>{clearTimeout(t);res('err');};
          ws.onclose=()=>{clearTimeout(t);res('closed');};
        });
      } catch(e){ return 'ERR '+e; }
    }
    """.replace("UU", UUID)

    started = ""
    if state and not any(x in state.upper() for x in ("RUNNING", "STARTING")):
        started = page.evaluate(WSJS, "start")
        log("server 不在运行 -> 已发 start:", started)

    # 6) 汇报
    if renewed:
        msg = f"✅ IceHost 续期成功\n{v0} → {v1 or '?'}\n状态: {state or '?'}"
    elif cooling:
        msg = f"ℹ️ IceHost 冷却中（刚续过），未变更\n当前有效期: {v1 or v0 or '?'}\n状态: {state or '?'}"
    else:
        msg = f"❌ IceHost 续期失败\nresp: {raw[:200]}\n状态: {state or '?'}"
    log(msg)
    tg(msg)
    if not renewed and not cooling:
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        import traceback; traceback.print_exc()
        tg(f"❌ IceHost 脚本异常: {e}")
        sys.exit(1)
