"""
Phone-friendly test page — accessible at /test in any browser.
No curl or terminal needed.
"""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Raina AI — Test Panel</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, sans-serif; background: #0f0f0f; color: #e0e0e0; padding: 16px; }
  h1 { color: #7c3aed; font-size: 1.4rem; margin-bottom: 4px; }
  .sub { color: #888; font-size: 0.85rem; margin-bottom: 20px; }
  .card { background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 12px; padding: 16px; margin-bottom: 16px; }
  h2 { font-size: 1rem; color: #a78bfa; margin-bottom: 12px; }
  label { display: block; font-size: 0.8rem; color: #aaa; margin-bottom: 4px; margin-top: 10px; }
  input { width: 100%; padding: 10px 12px; background: #111; border: 1px solid #333; border-radius: 8px;
          color: #fff; font-size: 0.95rem; outline: none; }
  input:focus { border-color: #7c3aed; }
  button { width: 100%; margin-top: 14px; padding: 12px; background: #7c3aed; color: #fff;
           border: none; border-radius: 8px; font-size: 1rem; font-weight: 600; cursor: pointer; }
  button:active { background: #6d28d9; }
  button.sec { background: #1e3a5f; margin-top: 8px; }
  button.sec:active { background: #1e3a8a; }
  pre { background: #111; border: 1px solid #2a2a2a; border-radius: 8px; padding: 12px;
        font-size: 0.78rem; overflow-x: auto; white-space: pre-wrap; word-break: break-all;
        margin-top: 12px; min-height: 48px; color: #4ade80; }
  .status { font-size: 0.8rem; color: #888; margin-top: 6px; }
  .ok { color: #4ade80; } .err { color: #f87171; }
  .sep { border-top: 1px solid #2a2a2a; margin: 16px 0; }
</style>
</head>
<body>
<h1>⚡ Raina AI</h1>
<p class="sub">Test Panel — works from any phone browser</p>

<!-- Health -->
<div class="card">
  <h2>🟢 Health Check</h2>
  <button onclick="doHealth()">Check /health</button>
  <pre id="health-out">tap to check</pre>
</div>

<!-- MT5 Connect -->
<div class="card">
  <h2>🔗 MT5 Connect (MetaAPI)</h2>
  <label>MT5 Login (account number)</label>
  <input id="c-login" type="text" placeholder="81690308" inputmode="numeric">
  <label>MT5 Password</label>
  <input id="c-pass" type="password" placeholder="your broker password">
  <label>MT5 Server</label>
  <input id="c-server" type="text" placeholder="Exness-MT5Trial10" value="Exness-MT5Trial10">
  <button onclick="doConnect()">Connect Account</button>
  <pre id="connect-out">fill in and tap</pre>
</div>

<!-- MT5 Account Status -->
<div class="card">
  <h2>📊 Account Status</h2>
  <label>User ID (your MT5 login number)</label>
  <input id="s-uid" type="text" placeholder="81690308" inputmode="numeric">
  <button onclick="doStatus()">Get Account</button>
  <button class="sec" onclick="doSettings()">Get Settings</button>
  <button class="sec" onclick="doTrades()">Get Trades</button>
  <pre id="status-out">fill in and tap</pre>
</div>

<!-- MT5 Settings -->
<div class="card">
  <h2>⚙️ Save Settings</h2>
  <label>User ID</label>
  <input id="set-uid" type="text" placeholder="81690308" inputmode="numeric">
  <label>Risk % per trade</label>
  <input id="set-risk" type="number" placeholder="1" value="1" min="0.1" max="10" step="0.1">
  <label>Max open trades</label>
  <input id="set-max" type="number" placeholder="3" value="3" min="1" max="20">
  <label>Max daily loss %</label>
  <input id="set-loss" type="number" placeholder="5" value="5" min="1" max="50">
  <button onclick="doSaveSettings()">Save Settings + Enable Scalping</button>
  <pre id="settings-out">fill in and tap</pre>
</div>

<script>
const BASE = "";  // same origin

async function call(method, path, body, outId) {
  const el = document.getElementById(outId);
  el.textContent = "loading…";
  el.className = "";
  try {
    const opts = { method, headers: {"Content-Type":"application/json"} };
    if (body) opts.body = JSON.stringify(body);
    const r = await fetch(BASE + path, opts);
    const txt = await r.text();
    let pretty;
    try { pretty = JSON.stringify(JSON.parse(txt), null, 2); } catch { pretty = txt; }
    el.textContent = "HTTP " + r.status + "\\n\\n" + pretty;
    el.className = r.ok ? "ok" : "err";
  } catch(e) {
    el.textContent = "Error: " + e.message;
    el.className = "err";
  }
}

function doHealth() { call("GET", "/health", null, "health-out"); }

function doConnect() {
  const login = document.getElementById("c-login").value.trim();
  const pass  = document.getElementById("c-pass").value.trim();
  const srv   = document.getElementById("c-server").value.trim();
  if (!login || !pass || !srv) { document.getElementById("connect-out").textContent = "Fill in all fields first."; return; }
  call("POST", "/mt5/connect/metaapi", {mt5_login: login, mt5_password: pass, mt5_server: srv}, "connect-out");
}

function doStatus() {
  const uid = document.getElementById("s-uid").value.trim();
  if (!uid) { document.getElementById("status-out").textContent = "Enter a User ID first."; return; }
  call("GET", "/mt5/account/"+uid, null, "status-out");
}

function doSettings() {
  const uid = document.getElementById("s-uid").value.trim();
  if (!uid) { document.getElementById("status-out").textContent = "Enter a User ID first."; return; }
  call("GET", "/mt5/settings/"+uid, null, "status-out");
}

function doTrades() {
  const uid = document.getElementById("s-uid").value.trim();
  if (!uid) { document.getElementById("status-out").textContent = "Enter a User ID first."; return; }
  call("GET", "/mt5/trades/"+uid, null, "status-out");
}

function doSaveSettings() {
  const uid  = document.getElementById("set-uid").value.trim();
  const risk = parseFloat(document.getElementById("set-risk").value) || 1;
  const max  = parseInt(document.getElementById("set-max").value) || 3;
  const loss = parseFloat(document.getElementById("set-loss").value) || 5;
  if (!uid) { document.getElementById("settings-out").textContent = "Enter a User ID first."; return; }
  call("POST", "/mt5/settings", {
    user_id: uid, risk_percent: risk, max_open_trades: max,
    max_daily_loss_percent: loss, scalping_enabled: true,
    account_mode: "demo", min_confidence: 70
  }, "settings-out");
}
</script>
</body>
</html>"""


@router.get("/test", response_class=HTMLResponse, include_in_schema=False)
async def test_panel():
    return HTMLResponse(content=_HTML)
