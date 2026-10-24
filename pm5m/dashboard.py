"""Dashboard: the graphs.

Everything before this was terminal tables. This serves a live page instead, and
the centrepiece chart is the one that actually decides these markets:

    UP  iff  TWAP60(close) >= TWAP60(open)

A trailing 60-second mean at each end -- not the last tick, and not the average
of the whole path. Both ends are drawn: the spot path, the rolling TWAP60 that
settles it, and the strike. You can see window after window where spot finishes
on one side of the strike and the market resolves on the other, because the mean
of the last minute is what counts.

That single fact is invisible on a normal price chart and is the entire basis of
the strategy. No amount of table-reading conveys it as quickly as one picture.

Everything is stdlib + canvas. No CDN, no build step, no external assets, so it
works offline and inside a preview iframe.
"""

from __future__ import annotations

import json
import math
import random
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .config import Settings
from .expectancy import analyze


# ---------------------------------------------------------------------------
# snapshot building
# ---------------------------------------------------------------------------
def _synthetic_windows(n: int = 8, seed: int = 7, sigma: float = 2.2e-4) -> list[dict[str, Any]]:
    """Windows where the real settlement rule and the last tick disagree.

    Under `Up iff TWAP60(close) >= TWAP60(open)`, the resolving quantity is a
    trailing 60s mean at each end -- not the whole-path average, and not the last
    print. These windows are selected so that a spot-watcher would call at least
    some of them wrong.

    The path starts 60s before the window so the *open* TWAP has real history;
    that pre-window stretch is what makes the strike differ from the first tick.
    """
    rng = random.Random(seed)
    out: list[dict[str, Any]] = []
    tries = 0
    step = 5.0
    pre = 12          # 60s of pre-window history
    post = 60         # 300s of window
    while len(out) < n and tries < n * 400:
        tries += 1
        spot0 = 100_000.0
        path = [spot0]
        for _ in range(pre + post):
            path.append(
                path[-1] * math.exp(-0.5 * sigma**2 * step + sigma * math.sqrt(step) * rng.gauss(0, 1))
            )

        def twap_at(idx: int, window: int = 12) -> float:
            lo = max(0, idx - window + 1)
            return sum(path[lo:idx + 1]) / (idx + 1 - lo)

        # t=0 is index `pre`. Strike = TWAP60 at the open; settle = TWAP60 at the close.
        strike = twap_at(pre)
        settle = twap_at(pre + post)
        last = path[pre + post]
        resolved_up = settle >= strike
        naive_up = last >= strike
        if resolved_up == naive_up:
            continue  # keep only the disagreements; they are the teaching cases

        twap_line = [twap_at(i) for i in range(pre, pre + post + 1)]
        out.append(
            {
                "slug": f"btc-updown-5m-{1750000000 + tries * 300}",
                "path": [round(p, 2) for p in path[pre:]],
                "twap": [round(v, 2) for v in twap_line],
                "open": round(strike, 2),
                "last_tick": round(path[pre], 2),
                "close": round(last, 2),
                "settle": round(settle, 2),
                "outcome": "up" if resolved_up else "down",
                "naive_would_say": "up" if naive_up else "down",
            }
        )
    return out


def _demo_session(n_trades: int = 240, win_rate: float = 0.605, price: float = 0.541,
                  shares: float = 40.0, seed: int = 11) -> dict[str, Any]:
    """A synthetic paper session, used when no recorded trades exist."""
    from .expectancy import TradeRecord

    rng = random.Random(seed)
    trades = []
    equity = []
    cum = 0.0
    for i in range(n_trades):
        won = rng.random() < win_rate
        pnl = (shares if won else 0.0) - shares * price
        cum += pnl
        ts = 1.75e9 + i * 300
        trades.append(
            TradeRecord(
                ts=ts, slug=f"w{i}", side="up", price=price, shares=shares,
                fee=0.0, won=won, pnl=pnl,
                edge=rng.uniform(0.005, 0.05),
                z=rng.uniform(0.5, 4.0),
                remaining_s=rng.uniform(25.0, 270.0),
            )
        )
        equity.append({"t": ts, "v": round(cum, 2)})
    return {"trades": trades, "equity": equity, "source": "demo"}


def build_snapshot(store=None, demo: bool = True, bankroll: float = 1000.0) -> dict[str, Any]:
    """Everything the page needs, in one JSON-serialisable dict."""
    settings = Settings()
    trades = []
    source = "demo"

    if store is not None:
        try:
            trades = store.trade_records()
            source = "store" if trades else "demo"
        except Exception:
            trades = []

    equity = []
    if trades:
        cum = 0.0
        for t in trades:
            cum += t.pnl
            equity.append({"t": t.ts, "v": round(cum, 2)})
        session = {"trades": trades, "equity": equity, "source": source}
    else:
        session = _demo_session()

    exp = analyze(session["trades"])

    hist_edges = [-80, -60, -40, -20, 0, 20, 40, 60]
    hist = [0] * (len(hist_edges) - 1)
    for t in session["trades"]:
        for i in range(len(hist_edges) - 1):
            if hist_edges[i] <= t.pnl < hist_edges[i + 1]:
                hist[i] += 1
                break

    windows = _synthetic_windows()
    if store is not None:
        try:
            rows = store.conn.execute(
                "SELECT slug, outcome, open_price, avg_price FROM settlements "
                "WHERE outcome IS NOT NULL AND open_price > 0 LIMIT 8"
            ).fetchall()
            if rows:
                from .storage import Store  # noqa: F401

                real_windows = []
                for slug, outcome, open_p, avg_p in rows:
                    ticks = store.spot_window(
                        store.market(slug)["start_ts"], store.market(slug)["end_ts"]
                    ) if store.market(slug) else []
                    if len(ticks) < 5:
                        continue
                    path = [round(p, 2) for _, p in ticks]
                    real_windows.append(
                        {
                            "slug": slug,
                            "path": path,
                            "open": round(open_p, 2),
                            "close": path[-1],
                            "average": round(avg_p, 2),
                            "outcome": outcome,
                            "naive_would_say": "up" if path[-1] >= open_p else "down",
                        }
                    )
                if real_windows:
                    windows = real_windows
        except Exception:
            pass

    return {
        "generated_at": time.time(),
        "source": session["source"],
        "settings": {
            "bankroll": bankroll,
            "maker_shares": settings.maker_quote_shares,
            "fee_rate": settings.taker_fee_rate,
        },
        "metrics": {
            "trades": exp.n,
            "win_rate": round(exp.win_rate, 4),
            "win_rate_ci": [round(exp.win_rate_ci[0], 4), round(exp.win_rate_ci[1], 4)],
            "breakeven": round(exp.breakeven_win_rate, 4),
            "expectancy": round(exp.mean_pnl, 4),
            "expectancy_ci": [round(exp.ci_low, 4), round(exp.ci_high, 4)],
            "expectancy_lower": round(exp.ci_low_1s, 4),
            "p_value": round(exp.p_value, 6),
            "total_pnl": round(exp.total_pnl, 2),
            "profit_factor": round(exp.profit_factor, 3) if math.isfinite(exp.profit_factor) else None,
            "avg_price": round(exp.avg_price, 4),
            "required_n": exp.required_n,
            "significant": exp.significant,
            "verdict": exp.verdict(),
            "notes": exp.notes,
        },
        "equity": session["equity"],
        "histogram": {"edges": hist_edges, "counts": hist},
        "windows": windows,
    }


# ---------------------------------------------------------------------------
# page
# ---------------------------------------------------------------------------
PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>pm5m — live cockpit</title>
<style>
  :root{
    --bg:#0b0e14; --panel:#121722; --line:#1e2635; --ink:#dbe3f0;
    --dim:#7d8aa0; --good:#3fb950; --bad:#f85149; --accent:#58a6ff;
    --warn:#d29922;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
       font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}
  header{padding:16px 22px;border-bottom:1px solid var(--line);
         display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}
  h1{font-size:15px;margin:0;letter-spacing:.08em;text-transform:uppercase}
  .tag{font-size:11px;padding:2px 8px;border-radius:10px;border:1px solid var(--line);color:var(--dim)}
  .tag.demo{border-color:var(--warn);color:var(--warn)}
  .tag.store{border-color:var(--good);color:var(--good)}
  main{padding:18px 22px 60px;max-width:1400px;margin:0 auto}
  .grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));margin-bottom:16px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
  .card .k{font-size:11px;color:var(--dim);text-transform:uppercase;letter-spacing:.07em}
  .card .v{font-size:24px;margin-top:6px;font-variant-numeric:tabular-nums}
  .card .s{font-size:11px;color:var(--dim);margin-top:4px}
  .good{color:var(--good)} .bad{color:var(--bad)} .warn{color:var(--warn)}
  .panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;
         padding:14px 16px;margin-bottom:16px}
  .panel h2{font-size:12px;margin:0 0 4px;letter-spacing:.08em;text-transform:uppercase;color:var(--ink)}
  .panel p.cap{margin:0 0 12px;color:var(--dim);font-size:12px;max-width:90ch}
  canvas{width:100%;display:block;border-radius:6px;background:#0d121b}
  .wide{display:grid;gap:16px;grid-template-columns:1fr}
  .verdict{padding:12px 14px;border-radius:8px;font-size:13px;margin-top:4px;
           border:1px solid var(--line);background:#0d121b}
  .notes{margin:10px 0 0;padding-left:18px;color:var(--dim);font-size:12px}
  .legend{display:flex;gap:16px;flex-wrap:wrap;font-size:11px;color:var(--dim);margin-top:8px}
  .legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:middle}
  .wgrid{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(320px,1fr))}
  .wcell{background:#0d121b;border:1px solid var(--line);border-radius:8px;padding:10px 12px}
  .wcell .wh{font-size:11px;color:var(--dim);display:flex;justify-content:space-between;gap:8px;margin-bottom:6px}
  .wcell .out{font-weight:700}
</style>
</head>
<body>
<header>
  <h1>pm5m cockpit</h1>
  <span class="tag" id="src">—</span>
  <span class="tag" id="clock">—</span>
</header>
<main>
  <div class="grid" id="cards"></div>

  <div class="panel">
    <h2>The graph that decides these markets</h2>
    <p class="cap">Settlement is <b>TWAP60(close) vs TWAP60(open)</b> — a trailing 60-second mean
    at each end, not the last tick. In each window below the two disagree, so the market resolves
    against what a price-chart watcher would have called. Green = spot path; white = rolling TWAP60;
    dashed = the strike.</p>
    <div class="wgrid" id="windows"></div>
  </div>

  <div class="panel">
    <h2>Equity curve</h2>
    <p class="cap">Cumulative P&amp;L. The shape that matters is the slope, not any single spike.</p>
    <canvas id="equity" height="260"></canvas>
    <div class="legend"><span><i style="background:#58a6ff"></i>cumulative pnl</span>
      <span><i style="background:#7d8aa0"></i>zero</span></div>
  </div>

  <div class="panel">
    <h2>Win rate vs break-even</h2>
    <p class="cap">The bar must clear the line before the strategy earns anything. The shaded band is the
    95% confidence interval — if it covers the line, you have not proven an edge.</p>
    <canvas id="winrate" height="150"></canvas>
  </div>

  <div class="panel">
    <h2>Outcome distribution</h2>
    <p class="cap">Per-trade P&amp;L. Note how few bars sit on the left compared with the right — that
    asymmetry is the whole reason a 60% win rate can work, and why one bad stretch hurts.</p>
    <canvas id="hist" height="200"></canvas>
  </div>

  <div class="panel">
    <h2>Verdict</h2>
    <div class="verdict" id="verdict">—</div>
    <ul class="notes" id="notes"></ul>
  </div>
</main>
<script>
const COL={good:'#3fb950',bad:'#f85149',accent:'#58a6ff',dim:'#7d8aa0',
           line:'#1e2635',ink:'#dbe3f0',warn:'#d29922'};
let SNAP=null;

function fmt(n,d=2){return (n>=0?'':'-')+Math.abs(n).toFixed(d);}
function money(n){const s=n<0?'-':'';return s+'$'+Math.abs(n).toLocaleString(undefined,{maximumFractionDigits:2});}

function fitCanvas(c){
  const r=window.devicePixelRatio||1;
  const w=c.clientWidth, h=parseInt(c.getAttribute('height'))||200;
  c.width=w*r; c.height=h*r; c.style.height=h+'px';
  const x=c.getContext('2d'); x.setTransform(r,0,0,r,0,0);
  return {x,w,h};
}

function drawCards(m){
  const cards=[
    {k:'trades', v:m.trades, s:'completed trades'},
    {k:'win rate', v:(m.win_rate*100).toFixed(1)+'%',
     s:'95% CI '+(m.win_rate_ci[0]*100).toFixed(1)+'–'+(m.win_rate_ci[1]*100).toFixed(1)+'%',
     cls:(m.win_rate>m.breakeven)?'good':'bad'},
    {k:'break-even', v:(m.breakeven*100).toFixed(1)+'%', s:'cost per share × 100'},
    {k:'expectancy / trade', v:money(m.expectancy),
     s:'95% lower bound '+money(m.expectancy_lower)+' — must clear $0',
     cls:(m.expectancy_lower>0)?'good':'bad'},
    {k:'p-value', v:m.p_value<0.0001?'<0.0001':m.p_value.toFixed(4),
     s:m.significant?'significant':'not significant', cls:m.significant?'good':'warn'},
    {k:'total pnl', v:money(m.total_pnl), s:'net of fees', cls:(m.total_pnl>0)?'good':'bad'},
    {k:'avg fill', v:(m.avg_price*100).toFixed(1)+'c', s:'average entry price'},
    {k:'trades needed', v:(m.required_n>=1e8?'∞':m.required_n.toLocaleString()),
     s:'for 80% power', cls:'warn'},
  ];
  document.getElementById('cards').innerHTML=cards.map(c=>
    '<div class="card"><div class="k">'+c.k+'</div><div class="v '+(c.cls||'')+'">'+c.v+
    '</div><div class="s">'+c.s+'</div></div>').join('');
}

function drawEquity(){
  const c=document.getElementById('equity'); const {x,w,h}=fitCanvas(c);
  const e=SNAP.equity||[]; if(e.length<2) return;
  const pad={l:56,r:12,t:12,b:22};
  const vals=e.map(p=>p.v);
  let lo=Math.min(0,...vals), hi=Math.max(0,...vals);
  if(hi===lo){hi=lo+1;}
  const X=i=>pad.l+(w-pad.l-pad.r)*(i/(e.length-1));
  const Y=v=>pad.t+(h-pad.t-pad.b)*(1-(v-lo)/(hi-lo));

  x.strokeStyle=COL.line; x.lineWidth=1;
  for(let i=0;i<=4;i++){
    const v=lo+(hi-lo)*i/4, y=Y(v);
    x.beginPath(); x.moveTo(pad.l,y); x.lineTo(w-pad.r,y); x.stroke();
    x.fillStyle=COL.dim; x.font='10px ui-monospace'; x.textAlign='right';
    x.fillText(money(v), pad.l-6, y+3);
  }
  x.setLineDash([4,4]); x.strokeStyle=COL.dim; x.beginPath();
  x.moveTo(pad.l,Y(0)); x.lineTo(w-pad.r,Y(0)); x.stroke(); x.setLineDash([]);

  x.beginPath(); x.moveTo(X(0),Y(e[0].v));
  for(let i=1;i<e.length;i++) x.lineTo(X(i),Y(e[i].v));
  x.strokeStyle=COL.accent; x.lineWidth=2; x.stroke();
  x.lineTo(X(e.length-1),Y(0)); x.lineTo(X(0),Y(0)); x.closePath();
  x.fillStyle='rgba(88,166,255,0.10)'; x.fill();
}

function drawWinRate(){
  const c=document.getElementById('winrate'); const {x,w,h}=fitCanvas(c);
  const m=SNAP.metrics;
  const pad={l:54,r:16,t:22,b:26};
  const lo=0.35, hi=0.85;
  const X=v=>pad.l+(w-pad.l-pad.r)*((v-lo)/(hi-lo));
  const midY=pad.t+ (h-pad.t-pad.b)/2;

  x.strokeStyle=COL.line;
  for(let v=lo;v<=hi+1e-9;v+=0.1){
    x.beginPath(); x.moveTo(X(v),pad.t-6); x.lineTo(X(v),h-pad.b); x.stroke();
    x.fillStyle=COL.dim; x.font='10px ui-monospace'; x.textAlign='center';
    x.fillText((v*100).toFixed(0)+'%', X(v), h-pad.b+14);
  }
  // CI band
  const a=X(m.win_rate_ci[0]), b=X(m.win_rate_ci[1]);
  x.fillStyle='rgba(88,166,255,0.16)';
  x.fillRect(a, midY-16, Math.max(2,b-a), 32);
  // point
  const px=X(m.win_rate);
  x.fillStyle=(m.win_rate>m.breakeven)?COL.good:COL.bad;
  x.fillRect(px-3, midY-22, 6, 44);
  // break-even line
  const bx=X(m.breakeven);
  x.strokeStyle=COL.warn; x.lineWidth=2; x.setLineDash([5,4]);
  x.beginPath(); x.moveTo(bx,pad.t-8); x.lineTo(bx,h-pad.b); x.stroke(); x.setLineDash([]);
  x.fillStyle=COL.warn; x.font='10px ui-monospace'; x.textAlign='left';
  x.fillText('break-even '+(m.breakeven*100).toFixed(1)+'%', bx+6, pad.t+2);
  x.fillStyle=COL.ink; x.textAlign='center';
  x.fillText('you '+(m.win_rate*100).toFixed(1)+'%', px, midY-30);
}

function drawHist(){
  const c=document.getElementById('hist'); const {x,w,h}=fitCanvas(c);
  const H=SNAP.histogram, n=H.counts.length;
  const pad={l:48,r:12,t:14,b:30};
  const maxC=Math.max(1,...H.counts);
  const bw=(w-pad.l-pad.r)/n;
  for(let i=0;i<n;i++){
    const cval=H.counts[i];
    const bh=(h-pad.t-pad.b)*(cval/maxC);
    const bx=pad.l+i*bw+2, by=h-pad.b-bh;
    const mid=(H.edges[i]+H.edges[i+1])/2;
    x.fillStyle = mid<0? 'rgba(248,81,73,0.75)' : 'rgba(63,185,80,0.75)';
    x.fillRect(bx,by,bw-4,bh);
    if(cval){ x.fillStyle=COL.ink; x.font='10px ui-monospace'; x.textAlign='center';
      x.fillText(cval, bx+(bw-4)/2, by-4); }
  }
  x.fillStyle=COL.dim; x.font='10px ui-monospace'; x.textAlign='center';
  for(let i=0;i<n;i++){
    x.fillText(money((H.edges[i]+H.edges[i+1])/2,0), pad.l+i*bw+bw/2, h-pad.b+14);
  }
  x.strokeStyle=COL.dim; x.beginPath();
  x.moveTo(pad.l,h-pad.b); x.lineTo(w-pad.r,h-pad.b); x.stroke();
}

function drawWindow(cell, win, idx){
  const c=cell.querySelector('canvas');
  const {x,w,h}=fitCanvas(c);
  const p=win.path; if(p.length<2) return;
  const pad={l:8,r:8,t:8,b:8};
  const all=p.concat([win.open]);
  let lo=Math.min(...all), hi=Math.max(...all);
  if(hi===lo) hi=lo+1;
  const X=i=>pad.l+(w-pad.l-pad.r)*(i/(p.length-1));
  const Y=v=>pad.t+(h-pad.t-pad.b)*(1-(v-lo)/(hi-lo));

  x.strokeStyle='rgba(125,138,160,0.35)'; x.lineWidth=1; x.setLineDash([4,4]);
  x.beginPath(); x.moveTo(pad.l,Y(win.open)); x.lineTo(w-pad.r,Y(win.open)); x.stroke();
  x.setLineDash([]);

  x.beginPath(); x.moveTo(X(0),Y(p[0]));
  for(let i=1;i<p.length;i++) x.lineTo(X(i),Y(p[i]));
  x.strokeStyle=(win.outcome==='up')?COL.good:COL.bad; x.lineWidth=1.5; x.stroke();

  const tw = win.twap || p.map(()=>win.open);
  x.beginPath();
  for(let i=0;i<tw.length;i++){ const y=Y(tw[i]); i?x.lineTo(X(i),y):x.moveTo(X(i),y); }
  x.strokeStyle='#ffffff'; x.globalAlpha=0.75; x.lineWidth=1.6; x.stroke(); x.globalAlpha=1;

  x.fillStyle=COL.ink; x.font='10px ui-monospace'; x.textAlign='left';
  x.fillText('strike '+win.open.toFixed(0), pad.l, pad.t+9);
  x.fillText('twap60 '+win.settle.toFixed(0), pad.l, pad.t+21);
  x.fillText('last   '+win.close.toFixed(0), pad.l, pad.t+33);
  x.fillText('t='+idx, w-pad.r-26, pad.t+9);
}

function drawWindows(){
  const host=document.getElementById('windows');
  host.innerHTML=(SNAP.windows||[]).map((win,i)=>
    '<div class="wcell" data-i="'+i+'">'+
      '<div class="wh"><span>'+win.slug+'</span>'+
      '<span class="out '+(win.outcome==='up'?'good':'bad')+'">resolved '+win.outcome.toUpperCase()+
      ' · chart says '+win.naive_would_say.toUpperCase()+'</span></div>'+
      '<canvas height="120"></canvas></div>').join('');
  host.querySelectorAll('.wcell').forEach((cell,i)=>drawWindow(cell, SNAP.windows[i], i+1));
}

function render(){
  if(!SNAP) return;
  const m=SNAP.metrics;
  document.getElementById('src').textContent='data: '+SNAP.source;
  document.getElementById('src').className='tag '+SNAP.source;
  document.getElementById('clock').textContent=new Date().toLocaleTimeString();
  drawCards(m);
  drawWindows(); drawEquity(); drawWinRate(); drawHist();
  const v=document.getElementById('verdict');
  v.textContent=m.verdict;
  v.className='verdict '+(m.significant?'good':(m.expectancy>0?'warn':'bad'));
  document.getElementById('notes').innerHTML=(m.notes||[]).map(n=>'<li>'+n+'</li>').join('');
}

async function pull(){
  try{
    const r=await fetch('api/snapshot',{cache:'no-store'});
    SNAP=await r.json(); render();
  }catch(e){ document.getElementById('clock').textContent='disconnected'; }
}
window.addEventListener('resize',()=>{ if(SNAP) render(); });
pull(); setInterval(pull, 3000);
</script>
</body>
</html>
"""


def render_html() -> str:
    return PAGE


# ---------------------------------------------------------------------------
# server
# ---------------------------------------------------------------------------
@dataclass
class DashboardServer:
    host: str = "0.0.0.0"
    port: int = 8000
    store: Any = None
    demo: bool = True
    _srv: Any = field(default=None, repr=False)

    def snapshot(self) -> dict[str, Any]:
        return build_snapshot(self.store, self.demo)

    def serve_forever(self) -> None:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep the console readable
                return

            def do_GET(self):
                if self.path.startswith("/api/snapshot"):
                    body = json.dumps(outer.snapshot()).encode()
                    ctype = "application/json"
                elif self.path in ("/", "/index.html"):
                    body = render_html().encode()
                    ctype = "text/html; charset=utf-8"
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

        self._srv = ThreadingHTTPServer((self.host, self.port), Handler)
        self._srv.daemon_threads = True
        print(f"pm5m cockpit on http://{self.host}:{self.port}")
        self._srv.serve_forever()

    def stop(self) -> None:
        if self._srv is not None:
            self._srv.shutdown()
