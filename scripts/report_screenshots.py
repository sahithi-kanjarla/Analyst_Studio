"""Open a finished run's Report tab in headless Edge and save scripts/report_top.png (the top of the report) and scripts/report_card.png (the first full answer card).
Test tooling only. Needs the app serving on 127.0.0.1:8000 and websocket-client.   python scripts/report_screenshots.py <run_id>"""
import base64, json, subprocess, sys, time, urllib.request
from pathlib import Path

import websocket

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
OUT = Path(__file__).parent
run_id = sys.argv[1]
proc = subprocess.Popen([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--remote-debugging-port=9333", "--window-size=1100,1400", "--user-data-dir=" + str(OUT / "_edge_profile"),
                         f"http://127.0.0.1:8000/#/run/{run_id}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(40):
        try:
            tabs = json.load(urllib.request.urlopen("http://127.0.0.1:9333/json"))
            page = next(t for t in tabs if t["type"] == "page")
            break
        except Exception:
            time.sleep(0.5)
    ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=30)
    n = 0

    def call(method, **params):
        global n
        n += 1
        ws.send(json.dumps({"id": n, "method": method, "params": params}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == n:
                return msg.get("result", {})

    def js(expr):
        return call("Runtime.evaluate", expression=expr, returnByValue=True).get("result", {}).get("value")

    for _ in range(60):          # wait for the run to load, then open the Report tab
        if js("[...document.querySelectorAll('button.tab')].some(b => b.textContent.startsWith('Report'))"):
            break
        time.sleep(0.5)
    js("[...document.querySelectorAll('button.tab')].find(b => b.textContent.startsWith('Report')).click()")
    time.sleep(3)

    def shot(name, clip):
        data = call("Page.captureScreenshot", format="png", captureBeyondViewport=True, clip={**clip, "scale": 1})["data"]
        (OUT / name).write_bytes(base64.b64decode(data))

    box = json.loads(js("JSON.stringify((() => { const r = document.querySelector('.tabs').getBoundingClientRect(); return {y: r.top + scrollY}; })())"))
    shot("report_top.png", {"x": 0, "y": 0, "width": 1100, "height": 1100})
    card = js("""JSON.stringify((() => { const h = [...document.querySelectorAll('strong,p')].find(e => /^1\\. /.test(e.textContent.trim()));
        if (!h) return null; const p = h.closest('p') || h; let el = p, bottom = 0; const top = p.getBoundingClientRect().top + scrollY;
        for (let e = p.nextElementSibling; e; e = e.nextElementSibling) { const r = e.getBoundingClientRect(); if (e.tagName === 'DETAILS' || e.tagName === 'H3') { bottom = r.bottom + scrollY; break; } }
        return {top, bottom}; })())""")
    c = json.loads(card)
    shot("report_card.png", {"x": 0, "y": max(0, c["top"] - 20), "width": 1100, "height": min(1400, c["bottom"] - c["top"] + 60)})
    print("saved", card)
finally:
    proc.terminate()
