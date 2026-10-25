"""Tiny stdlib web UI: GET / shows the last saved cart sorted by aisle,
POST /refresh runs one scrape in the background. The site is only touched
when Refresh is pressed.

Crossing items off is local only: the crossed UPCs live in data/crossed.json
and are never sent to Harris Teeter. A successful refresh deletes the file,
so the list resets to whatever is in the cart on the site."""

import html
import json
import logging
import os
import threading
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from . import parse, scrape

log = logging.getLogger(__name__)

_lock = threading.Lock()
_status = {"running": False, "error": None, "started": None}
_crossed_lock = threading.Lock()

CSS = """
body{font-family:system-ui,sans-serif;margin:0;padding:12px;max-width:720px;
margin:auto;background:#fafafa;color:#222}
header{display:flex;justify-content:space-between;align-items:center;gap:8px}
h1{font-size:1.2rem;margin:0}
button{font-size:1.05rem;font-weight:600;padding:10px 18px;border-radius:8px;
border:0;background:#2a6e3f;color:#fff;cursor:pointer}
button:disabled{background:#999;cursor:default}
.meta{color:#666;font-size:.85rem;margin:6px 0 12px}
.err{background:#fde8e8;border:1px solid #e99;padding:8px;border-radius:8px}
.warn{background:#fff6dc;border:1px solid #e6c766;padding:8px;border-radius:8px;
font-size:.9rem}
h2{font-size:1rem;margin:18px 0 6px;padding:4px 8px;background:#2a6e3f;
color:#fff;border-radius:6px}
.item{display:flex;gap:10px;align-items:center;padding:8px;background:#fff;
border-bottom:1px solid #eee}
.item img{width:48px;height:48px;object-fit:contain}
.name{flex:1}
.sub{color:#666;font-size:.85rem}
.sub b{color:#222}
.qty{font-weight:600;white-space:nowrap;text-align:right}
.was{text-decoration:line-through;color:#888;font-weight:400}
.deal{color:#b45309}
.summary{background:#fff;border:1px solid #ddd;border-radius:8px;padding:6px 10px;
margin-bottom:8px}
.summary div{display:flex;justify-content:space-between;padding:2px 0}
.summary .neg{color:#2a6e3f}
.summary .TOTAL{font-weight:700;border-top:1px solid #ddd;margin-top:4px;
padding-top:4px}
.item form{margin:0}
.check{width:40px;height:40px;padding:0;border-radius:50%;background:#fff;
border:2px solid #2a6e3f;color:#2a6e3f;font-size:1.2rem;line-height:1}
.undo{background:#fff;color:#2a6e3f;border:1px solid #2a6e3f;padding:6px 12px;
font-size:.9rem}
.bar{display:flex;justify-content:space-between;align-items:center;gap:8px}
[hidden]{display:none!important}
"""

# Cross off without a page reload (keeps your scroll position in the store).
# Without JS the same forms still work as plain POST + redirect.
JS = """
document.addEventListener('submit', async e => {
  const f = e.target;
  if (!f.classList.contains('cross')) return;
  e.preventDefault();
  const r = await fetch('/cross', {method: 'POST',
                                   body: new URLSearchParams(new FormData(f))});
  if (!r.ok) return;
  const sec = f.closest('section');
  f.closest('.item').remove();
  const left = sec.querySelectorAll('.item').length;
  if (left) sec.querySelector('h2 small').textContent = '(' + left + ')';
  else sec.remove();
  const n = document.getElementById('left');
  n.textContent = +n.textContent - 1;
  document.getElementById('undo').hidden = false;
  document.body.dataset.crossed = 1;
});
document.addEventListener('submit', e => {
  if (e.target.id === 'refresh' && document.body.dataset.crossed &&
      !confirm('Refresh resets the list and brings back crossed-off items.'))
    e.preventDefault();
});
"""


def _crossed_path():
    return scrape.DATA_DIR / "crossed.json"


def _crossed():
    try:
        return json.loads(_crossed_path().read_text())
    except (OSError, ValueError):
        return []


def _set_crossed(upcs):
    _crossed_path().write_text(json.dumps(upcs))


def _load():
    try:
        return json.loads((scrape.DATA_DIR / "cart.json").read_text())
    except (OSError, ValueError):
        return None


def _esc(v):
    return html.escape(str(v)) if v not in (None, "") else ""


def _render():
    data = _load()
    st = dict(_status)
    out = [
        "<!doctype html><html><head><meta charset=utf-8>",
        "<meta name=viewport content='width=device-width,initial-scale=1'>",
        "<title>Harris Teeter cart by aisle</title>",
        f"<style>{CSS}</style>",
    ]
    if st["running"]:
        out.append("<meta http-equiv=refresh content=5>")
    crossed = _crossed()
    out.append(
        f"</head><body{' data-crossed=1' if crossed else ''}>"
        "<header><h1>Cart by aisle</h1>"
    )
    if st["running"]:
        out.append("<button disabled>Refreshing…</button>")
    else:
        out.append(
            "<form id=refresh method=post action=/refresh>"
            "<button>Refresh</button></form>"
        )
    out.append("</header>")
    if st["error"]:
        out.append(f"<p class=err>Last refresh failed: {_esc(st['error'])}</p>")
    if not data:
        out.append("<p class=meta>No cart loaded yet. Press Refresh.</p>")
        out.append("</body></html>")
        return "".join(out)

    all_items = data.get("items", [])
    items = [i for i in all_items if i.get("upc") not in crossed]
    out.append(
        f"<div class=bar><p class=meta><span id=left>{len(items)}</span> of "
        f"{len(all_items)} items left · fetched {_esc(data.get('fetched_at'))}</p>"
        f"<form id=undo method=post action=/undo{'' if crossed else ' hidden'}>"
        "<button class=undo>Undo</button></form></div>"
    )
    summary = data.get("summary") or []
    if summary:
        out.append("<div class=summary>")
        for ln in summary:
            amt = ln.get("amount") or ""
            cls = f"{_esc(ln.get('type'))}{' neg' if amt.startswith('-') else ''}"
            out.append(
                f"<div class='{cls}'><span>{_esc(ln.get('description'))}</span>"
                f"<span>{_esc(amt)}</span></div>"
            )
        out.append("</div>")
    for w in data.get("warnings", []):
        out.append(f"<p class=warn>{_esc(w)}</p>")

    counts = {}
    for it in items:
        label = parse.aisle_label(it)
        counts[label] = counts.get(label, 0) + 1
    current = None
    for it in items:
        label = parse.aisle_label(it)
        if label != current:
            if current is not None:
                out.append("</section>")
            out.append(
                f"<section><h2>{_esc(label)} <small>({counts[label]})</small></h2>"
            )
            current = label
        loc = it.get("location") or {}
        where = " · ".join(
            f"<b>{k} {_esc(loc[k])}</b>"
            if k in ("side", "bay")
            else f"{k} {_esc(loc[k])}"
            for k in ("side", "bay", "shelf")
            if loc.get(k)
        )
        sub = " · ".join(
            x for x in (_esc(it.get("brand")), _esc(it.get("size")), where) if x
        )
        # Line total after coupons/deals (cart-savings), regular struck through;
        # falls back to the product's own price text if that response was missing.
        total = it.get("line_total") or it.get("price")
        was = (
            it.get("line_regular") if it.get("line_total") else it.get("regular_price")
        )
        price = f"{_esc(total)}" + (
            f" <span class=was>{_esc(was)}</span>" if was else ""
        )
        deal = (
            it.get("price") if it.get("line_total") and it.get("regular_price") else ""
        )
        if deal and deal != total:
            sub = " · ".join(
                x for x in (sub, f"<span class=deal>{_esc(deal)}</span>") if x
            )
        img = f"<img src='{_esc(it['image'])}' alt=''>" if it.get("image") else ""
        check = (
            "<form class=cross method=post action=/cross>"
            f"<input type=hidden name=upc value='{_esc(it.get('upc'))}'>"
            "<button class=check title='Cross off'>✓</button></form>"
        )
        out.append(
            f"<div class=item>{check}{img}<div class=name>{_esc(it.get('name'))}"
            f"<div class=sub>{sub}</div></div>"
            f"<div class=qty>{price}"
            f"<div class=sub>×{_esc(it.get('quantity'))}</div></div></div>"
        )
    if current is not None:
        out.append("</section>")
    if all_items and not items:
        out.append("<p class=meta>Everything is crossed off.</p>")
    out.append(f"<script>{JS}</script></body></html>")
    return "".join(out)


def _refresh_worker():
    try:
        scrape.run()
        with _crossed_lock:
            _crossed_path().unlink(missing_ok=True)
        _status["error"] = None
    except scrape.ScrapeError as e:
        _status["error"] = str(e)
    except Exception as e:  # noqa: BLE001 - surface anything on the page
        log.error("refresh crashed:\n%s", traceback.format_exc())
        _status["error"] = f"{type(e).__name__}: {e}"
    finally:
        _status["running"] = False
        _lock.release()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, '{"status":"ok"}', "application/json")
        if self.path == "/cart.json":
            data = _load() or {}
            return self._send(200, json.dumps(data), "application/json")
        if self.path in ("/", "/index.html"):
            return self._send(200, _render())
        self._send(404, "not found", "text/plain")

    def do_POST(self):
        if self.path == "/refresh":
            if _lock.acquire(blocking=False):
                _status.update(running=True, error=None, started=datetime.now())
                threading.Thread(target=_refresh_worker, daemon=True).start()
        elif self.path == "/cross":
            length = int(self.headers.get("Content-Length") or 0)
            form = parse_qs(self.rfile.read(length).decode())
            upc = (form.get("upc") or [""])[0]
            if upc:
                with _crossed_lock:
                    crossed = _crossed()
                    if upc not in crossed:
                        _set_crossed(crossed + [upc])
        elif self.path == "/undo":
            with _crossed_lock:
                _set_crossed(_crossed()[:-1])
        else:
            return self._send(404, "not found", "text/plain")
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()

    def log_message(self, fmt, *args):
        log.info("%s %s", self.address_string(), fmt % args)


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    port = int(os.environ.get("HT_PORT", "8000"))
    scrape.DATA_DIR.mkdir(parents=True, exist_ok=True)
    log.info("listening on :%d", port)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
