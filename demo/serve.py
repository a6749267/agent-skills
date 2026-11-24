#!/usr/bin/env python3
"""Demo dashboard —— 一行命令起一个本地合成数据后台。

这个 demo 存在的意义: 让人 30 秒看懂这套 skill 在干什么。
它是一个**完全虚构**的订单后台, 数据全部由 skill 自带的合成引擎生成,
不含任何真实系统。

用法:
    python demo/serve.py            # 起服务, 默认 http://127.0.0.1:8899
    python demo/serve.py --port 9000

然后用浏览器打开 / 看界面, 或者:
    curl http://127.0.0.1:8899/api/market/quotes | head
"""
import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
SKILL = HERE.parent / "skills" / "tutorial-video-recorder"
sys.path.insert(0, str(SKILL / "scripts"))

import synthetic  # noqa: E402
from adapters.ecommerce_demo.adapter import Adapter  # noqa: E402

CFG = {
    "synthetic": {
        "code_prefixes": ["QA", "QB", "QC", "QD", "QE", "QF", "QG", "QH"],
        "code_year": 27,
        "code_months": [1, 5, 10],
        "name_template": "合成对象{LETTER}",
    }
}


class Cfg(dict):
    def get(self, k, default=None):
        return CFG.get(k, default)


def build_adapter():
    a = Adapter(Cfg(), HERE / ".work")
    return a


# ── 极简 HTTP 服务 ──────────────────────────────────────────────
PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>合成订单后台 · demo</title>
<style>
  :root { --bg:#0b0f16; --card:#141b26; --line:#223046; --fg:#e6edf6;
          --mut:#8494ab; --acc:#00e5ff; --pos:#ff4d4f; --neg:#22c55e; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font:14px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif; }
  header { padding:18px 28px; border-bottom:1px solid var(--line);
           display:flex; align-items:center; gap:14px; }
  .logo { width:30px;height:30px;border-radius:8px;
          background:linear-gradient(135deg,var(--acc),#ff2d55); }
  h1 { font-size:17px; margin:0; font-weight:600; }
  .tag { font-size:11px; color:var(--acc); border:1px solid var(--acc);
         border-radius:20px; padding:2px 9px; opacity:.8; }
  .wrap { padding:22px 28px; max-width:1200px; }
  .cards { display:grid; grid-template-columns:repeat(4,1fr); gap:14px; margin-bottom:20px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:15px 17px; }
  .card .k { color:var(--mut); font-size:12px; }
  .card .v { font-size:25px; font-weight:650; margin-top:5px;
             font-variant-numeric:tabular-nums; }
  .up { color:var(--pos); } .dn { color:var(--neg); }
  table { width:100%; border-collapse:collapse; background:var(--card);
          border:1px solid var(--line); border-radius:12px; overflow:hidden; }
  th,td { padding:11px 15px; text-align:left; border-bottom:1px solid var(--line);
          font-variant-numeric:tabular-nums; }
  th { color:var(--mut); font-weight:500; font-size:12px; background:#111825; }
  tr:last-child td { border-bottom:none; }
  .mono { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
  .note { margin-top:18px; color:var(--mut); font-size:12.5px; }
  code { background:#1a2333; padding:1px 6px; border-radius:5px; font-size:12px; }
</style></head><body>
<header><div class="logo"></div><h1>合成订单后台</h1>
  <span class="tag">synthetic · demo</span></header>
<div class="wrap">
  <div class="cards" id="cards"></div>
  <table><thead><tr><th>编号</th><th>名称</th><th>单价</th><th>数量</th>
    <th>方向</th><th>金额</th></tr></thead><tbody id="rows"></tbody></table>
  <p class="note">全部数据由 <code>skills/tutorial-video-recorder/scripts/synthetic.py</code>
    确定性生成 —— 同一编号永远同一价格，可直接用于录屏演示。</p>
</div>
<script>
const fmt = n => n.toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2});
const cls = n => n > 0 ? 'up' : (n < 0 ? 'dn' : '');
(async () => {
  const q = await (await fetch('/api/market/quotes')).json();
  const items = await (await fetch('/api/market/items')).json();
  const nameOf = {}; items.items.forEach(i => nameOf[i.code] = i.name);
  const qm = {}; q.items.forEach(x => qm[x.code] = x);
  const codes = Object.keys(qm);
  const orders = [
    {code: codes[0], side:'sell', quantity:2},
    {code: codes[2], side:'buy',  quantity:1},
    {code: codes[4], side:'sell', quantity:3},
  ];
  let total = 0, count = 0;
  const rows = [];
  for (const o of orders) {
    const price = qm[o.code].unit_price;
    const sign = o.side === 'sell' ? 1 : -1;
    const amt = sign * price * o.quantity;
    total += amt; count += o.quantity;
    rows.push(`<tr><td class="mono">${o.code}</td><td>${nameOf[o.code]||''}</td>
      <td>${fmt(price)}</td><td>${o.quantity}</td>
      <td>${o.side==='sell'?'卖出':'买入'}</td>
      <td class="${cls(amt)}">${amt>0?'+':''}${fmt(amt)}</td></tr>`);
  }
  document.getElementById('rows').innerHTML = rows.join('');
  const c = document.getElementById('cards');
  const chg = q.items.reduce((s,x)=>s+x.change,0);
  c.innerHTML = `
    <div class="card"><div class="k">订单净额</div>
      <div class="v ${cls(total)}">${total>0?'+':''}${fmt(total)}</div></div>
    <div class="card"><div class="k">成交数量</div><div class="v">${count}</div></div>
    <div class="card"><div class="k">标的数</div><div class="v">${codes.length}</div></div>
    <div class="card"><div class="k">涨跌合计</div>
      <div class="v ${cls(chg)}">${chg>0?'+':''}${fmt(chg)}</div></div>`;
})();
</script></body></html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()

    adapter = build_adapter()
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                b = PAGE.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
                return
            r = adapter.route(self.path)
            if r is not None:
                status, payload = r
                self._send(payload, status)
                return
            self._send({"error": "not found"}, 404)

    srv = HTTPServer((a.host, a.port), H)
    print(f"demo dashboard → http://{a.host}:{a.port}   (Ctrl-C 停止)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
