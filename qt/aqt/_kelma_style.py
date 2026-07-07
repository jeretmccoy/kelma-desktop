# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
"""KelmaDesktop theme — a modern, warm, gold-accented look in both light and dark.

Design language (shared with KelmaMobile):
  * Warm charcoal (dark) / warm cream (light) canvas — never neutral gray.
  * Gold is the single interactive accent; semantic card-state colors
    (new / learning / review) are left untouched.
  * Generous rounding, soft shadows, refined system typography, thin gold
    scrollbars, and a signature gold hairline under the toolbar.

The palette is applied by overriding Anki's CSS custom properties (with
``!important`` so they beat the generated theme vars) plus a native ``QSS``
layer via ``style_did_init``. Structural polish is scoped per screen (deck
list, overview, toolbar) by webview *context* so it never leaks into card
content, the editor, or the browser. Everything is best-effort — a failure
must never break rendering.
"""

from __future__ import annotations

import json

from aqt import gui_hooks, mw
from aqt.theme import theme_manager

# KelmaMobile-inspired dark palette: warm gold-charcoal, without the olive cast.
DARK = {
    "canvas": "#11100c",
    "inset": "#0c0b08",
    "surface": "#1d1a12",
    "elevated": "#292315",
    "border": "#4a3e28",
    "border_subtle": "#332c1d",
    "fg": "#f4f1e7",
    "fg_subtle": "#b6aa92",
    "fg_faint": "#857860",
}
# Matching warm-cream light variant.
LIGHT = {
    "canvas": "#f4f1e7",
    "inset": "#eae4d6",
    "surface": "#fdfbf4",
    "elevated": "#ffffff",
    "border": "#ddd6c4",
    "border_subtle": "#ece6d7",
    "fg": "#26231d",
    "fg_subtle": "#6b655a",
    "fg_faint": "#99937f",
}

GOLD = "#c9ac6b"
GOLD_SOFT = "#dcc48f"
GOLD_BRIGHT = "#ecd49a"
ON_GOLD = "#17150f"

# Default UI font — a modern system sans stack (cards, deck list, toolbar,
# reviewer). SF on macOS, Segoe on Windows, Roboto on Linux.
FONT_STACK = (
    '-apple-system, "SF Pro Display", "SF Pro Text", system-ui, '
    '"Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif'
)

# Computer Modern (the classic TeX/LaTeX face), bundled under data/web/kelma —
# used ONLY on the statistics page. Falls back to other serifs if unavailable.
STATS_FONT = '"Computer Modern Serif", Georgia, "Times New Roman", serif'

_FONT_DIR = "/_anki/kelma/fonts"
_FONT_FACES = f"""
@font-face {{
  font-family: 'Computer Modern Serif';
  src: url('{_FONT_DIR}/cmunrm.woff') format('woff');
  font-weight: normal; font-style: normal; font-display: swap;
}}
@font-face {{
  font-family: 'Computer Modern Serif';
  src: url('{_FONT_DIR}/cmunbx.woff') format('woff');
  font-weight: bold; font-style: normal; font-display: swap;
}}
@font-face {{
  font-family: 'Computer Modern Serif';
  src: url('{_FONT_DIR}/cmunti.woff') format('woff');
  font-weight: normal; font-style: italic; font-display: swap;
}}
@font-face {{
  font-family: 'Computer Modern Serif';
  src: url('{_FONT_DIR}/cmunbi.woff') format('woff');
  font-weight: bold; font-style: italic; font-display: swap;
}}
"""


def _font_face_css() -> str:
    return _FONT_FACES


def _pal() -> dict:
    return DARK if theme_manager.night_mode else LIGHT


def _accent() -> tuple[str, str, str, str]:
    """(accent, accent-bright, on-accent, gold-tint-rgba) for the current mode.

    Dark mode uses a slightly brighter gold so it reads against charcoal, and a
    stronger tint for hovers; light mode keeps the softer gold.
    """
    if theme_manager.night_mode:
        return GOLD_SOFT, GOLD_BRIGHT, ON_GOLD, "rgba(236, 212, 154, 0.12)"
    return GOLD, GOLD_BRIGHT, ON_GOLD, "rgba(201, 172, 107, 0.16)"


def _vars_css() -> str:
    """Palette + accent CSS-variable overrides — safe for every webview."""
    p = _pal()
    accent, bright, on_accent, _tint = _accent()
    return f"""
:root {{
  --canvas: {p['canvas']} !important;
  --canvas-inset: {p['inset']} !important;
  --canvas-elevated: {p['surface']} !important;
  --canvas-overlay: {p['elevated']} !important;
  --canvas-code: {p['inset']} !important;
  --canvas-glass: {p['surface']} !important;
  --fg: {p['fg']} !important;
  --fg-subtle: {p['fg_subtle']} !important;
  --fg-faint: {p['fg_faint']} !important;
  --fg-disabled: {p['fg_faint']} !important;
  --fg-link: {accent} !important;
  --border: {p['border']} !important;
  --border-subtle: {p['border_subtle']} !important;
  --border-strong: {p['border']} !important;
  --border-focus: {accent} !important;
  --button-primary-bg: {accent} !important;
  --button-primary-gradient-start: {bright} !important;
  --button-primary-gradient-end: {accent} !important;
  --button-primary-disabled: {p['border']} !important;
  --highlight-bg: {accent} !important;
  --highlight-fg: {on_accent} !important;
}}
"""


def _scrollbar_css() -> str:
    p = _pal()
    accent, _bright, _on, _tint = _accent()
    return f"""
::-webkit-scrollbar {{ width: 11px; height: 11px; }}
::-webkit-scrollbar-track {{ background: transparent; }}
::-webkit-scrollbar-thumb {{
  background: {p['border']};
  border-radius: 999px;
  border: 3px solid transparent;
  background-clip: content-box;
}}
::-webkit-scrollbar-thumb:hover {{ background: {accent}; background-clip: content-box; }}
"""


def _base_css() -> str:
    """Var overrides + scrollbars + link accent, injected into every webview."""
    accent, _bright, _on, _tint = _accent()
    return (
        '<style id="kelma-theme">'
        + _vars_css()
        + _scrollbar_css()
        + f"a:hover {{ color: {accent} !important; }}"
        + "</style>"
    )


def _deckbrowser_css() -> str:
    """Modern deck list: elevated rounded card, airy rows, refined counts."""
    p = _pal()
    accent, bright, _on, tint = _accent()
    shadow = (
        "0 1px 2px rgba(0,0,0,0.35), 0 10px 30px rgba(0,0,0,0.30)"
        if theme_manager.night_mode
        else "0 1px 2px rgba(60,50,20,0.05), 0 12px 34px rgba(120,100,40,0.11)"
    )
    return f"""
<style id="kelma-deckbrowser">
body {{ font-family: {FONT_STACK}; margin: 2.4em 1em 1em 1em; }}
.fancy table {{
  border: 1px solid {p['border_subtle']} !important;
  border-radius: 20px !important;
  box-shadow: {shadow} !important;
  background: {p['surface']} !important;
  padding: 1.1rem 1rem !important;
  border-collapse: separate !important;
  /* Vertical gap so adjacent row highlights never touch — each stays a
     separate rounded pill. */
  border-spacing: 0 5px !important;
}}
.fancy table:hover {{ box-shadow: {shadow} !important; }}
th {{
  font-size: 0.7rem;
  letter-spacing: 0.11em;
  text-transform: uppercase;
  color: {p['fg_faint']} !important;
  font-weight: 700;
  padding: 4px 14px 12px 14px !important;
}}
th.count {{ padding-right: 16px !important; }}
/* Airy rows — noticeably more breathing room between decks. */
tr.deck td {{
  padding: 12px 14px !important;
  transition: background 0.14s ease;
}}
tr.deck td.decktd {{ padding-left: 18px !important; }}
tr.deck td[align=end] {{ padding-right: 18px !important; }}
a.deck {{ font-weight: 600; font-size: 1.04em; letter-spacing: 0.01em; }}
a.deck:hover {{ color: {accent} !important; text-decoration: none; }}
/* Counts: tabular figures, aligned, a touch heavier. */
.new-count, .learn-count, .review-count, .zero-count {{
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  font-size: 1.02em;
}}
/* Rounded pill highlight on the current / hovered deck. */
.current td, tr:hover:not(.top-level-drag-row) td {{
  background: {tint} !important;
}}
.current td:first-child, tr:hover:not(.top-level-drag-row) td:first-child {{
  border-top-left-radius: 12px; border-bottom-left-radius: 12px;
}}
.current td:last-child, tr:hover:not(.top-level-drag-row) td:last-child {{
  border-top-right-radius: 12px; border-bottom-right-radius: 12px;
}}
.gears {{ transition: opacity 0.14s ease, filter 0.14s ease; }}
.gears:hover {{ filter: drop-shadow(0 0 4px {accent}); }}
</style>
"""


def _overview_css() -> str:
    """Modern Overview: prominent, readable, gradient Study Now button."""
    p = _pal()
    accent, bright, on_accent, _tint = _accent()
    grad = f"linear-gradient(135deg, {bright}, {accent})"
    glow = (
        f"0 8px 22px rgba(201, 172, 107, 0.30)"
        if theme_manager.night_mode
        else f"0 8px 22px rgba(201, 172, 107, 0.38)"
    )
    return f"""
<style id="kelma-overview">
body {{ font-family: {FONT_STACK}; }}
.descfont {{ line-height: 1.6; }}
h3 {{ letter-spacing: 0.01em; }}
/* Study Now — dark text on gold (was low-contrast white), pill + lift. */
#study, button#study.but {{
  color: {on_accent} !important;
  background: {grad} !important;
  border: none !important;
  border-radius: 999px !important;
  padding: 13px 44px !important;
  margin-top: 0.4em;
  font-size: 1.06rem !important;
  font-weight: 700 !important;
  letter-spacing: 0.015em;
  box-shadow: {glow} !important;
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}}
#study:hover, button#study.but:hover {{
  color: {on_accent} !important;
  background: {grad} !important;
  transform: translateY(-2px);
  box-shadow: 0 12px 30px rgba(201, 172, 107, 0.48) !important;
}}
#study:active, button#study.but:active {{ transform: translateY(0); }}
</style>
"""


def _toolbar_css() -> str:
    """Pill nav with gold hovers + a signature gold hairline under the bar."""
    p = _pal()
    accent, bright, on_accent, tint = _accent()
    return f"""
<style id="kelma-toolbar">
body {{
  font-family: {FONT_STACK};
  border-bottom: 1px solid {accent}55 !important;
}}
.fancy .toolbar {{
  background: {p['surface']} !important;
  border: 1px solid {p['border_subtle']} !important;
  border-radius: 999px !important;
  padding: 4px 6px !important;
}}
.hitem {{
  color: {p['fg']} !important;
  border-radius: 999px !important;
  padding: 6px 15px !important;
  letter-spacing: 0.02em;
  transition: background 0.14s ease, color 0.14s ease;
}}
.hitem:hover {{
  text-decoration: none !important;
  background: {tint} !important;
  color: {accent} !important;
}}
</style>
"""


# Semantic rating colors for the answer buttons (kept close to Anki's), shown
# as a coloured accent bar so the four choices stay instantly recognizable.
EASE_COLOR = {
    "1": "#e0555f",  # Again  — red
    "2": "#e0913f",  # Hard   — amber
    "3": "#5bbf83",  # Good   — green
    "4": "#5a9fe0",  # Easy   — blue
}


def _reviewer_bottom_css() -> str:
    """Modern study controls: pill answer buttons with a per-rating accent bar,
    and a prominent gold Show Answer button."""
    p = _pal()
    accent, bright, on_accent, tint = _accent()
    grad = f"linear-gradient(135deg, {bright}, {accent})"
    ease_rules = "".join(
        f"""
#middle button[data-ease="{e}"] {{ border-bottom: 3px solid {c} !important; }}
#middle button[data-ease="{e}"] .nobold {{ color: {c} !important; opacity: 0.9; }}
#middle button[data-ease="{e}"]:hover {{ border-color: {c} !important; }}
"""
        for e, c in EASE_COLOR.items()
    )
    return f"""
<style id="kelma-reviewer-bottom">
body {{ font-family: {FONT_STACK}; }}
#middle button {{
  border: 1px solid {p['border_subtle']} !important;
  border-radius: 12px !important;
  background: {p['elevated']} !important;
  color: {p['fg']} !important;
  padding: 9px 20px !important;
  min-width: 82px !important;
  margin: 8px 7px !important;
  font-weight: 600;
  transition: transform 0.12s ease, box-shadow 0.12s ease, border-color 0.12s ease;
}}
#middle button:hover {{
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(0,0,0,0.18);
}}
{ease_rules}
/* Show Answer — the primary action, a gold pill. Selector must out-specify
   `#middle button` above (which also carries !important), so qualify it. */
#middle button#ansbut, button#ansbut {{
  background: {grad} !important;
  color: {on_accent} !important;
  border: none !important;
  border-radius: 999px !important;
  padding: 11px 44px !important;
  min-width: 200px !important;
  font-weight: 700 !important;
  letter-spacing: 0.01em;
  box-shadow: 0 6px 18px rgba(201, 172, 107, 0.34) !important;
}}
#middle button#ansbut:hover, button#ansbut:hover {{ transform: translateY(-2px); }}
.nobold, .stattxt {{ color: {p['fg_faint']} !important; }}
</style>
"""


def _reviewer_css() -> str:
    """Study card polish: refined default typography, smaller default images,
    and a soft gold Q/A divider. Uses gentle defaults (no !important on type)
    so custom note styling still wins, and caps image size sensibly."""
    p = _pal()
    accent, bright, on_accent, _tint = _accent()
    return f"""
<style id="kelma-reviewer">
/* Refined default typography — a note type's own CSS still overrides this. */
.card {{
  font-family: {FONT_STACK};
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
  letter-spacing: 0.005em;
}}
/* Comfortable reading measure so long text isn't edge-to-edge. */
#qa, .card {{ max-width: 780px; margin-left: auto; margin-right: auto; }}
/* Smaller images by default — capped height, centered, softly rounded.
   A note that sets its own image size overrides these. */
.card img {{
  max-width: 88%;
  max-height: 42vh;
  height: auto;
  border-radius: 10px;
}}
/* Audio replay button: a centered gold circular control on its own row,
   instead of a stock white circle stranded wherever the [sound:] tag sits. */
.replay-button {{
  display: flex !important;
  width: -webkit-fit-content !important;
  width: fit-content !important;
  margin: 16px auto !important;
  cursor: pointer;
}}
.replay-button svg {{
  width: 50px !important;
  height: 50px !important;
  border-radius: 50%;
  box-shadow: 0 4px 12px rgba(201, 172, 107, 0.30);
  transition: transform 0.12s ease, box-shadow 0.12s ease;
}}
.replay-button svg circle {{ fill: {accent} !important; stroke: none !important; }}
.replay-button svg path {{ fill: {on_accent} !important; }}
.replay-button:hover svg {{
  transform: scale(1.08);
  box-shadow: 0 6px 18px rgba(201, 172, 107, 0.45);
}}
.replay-button:active svg {{ transform: scale(0.96); }}
/* Q/A divider as a soft gold gradient hairline instead of a hard rule. */
hr#answer {{
  border: none !important;
  height: 2px !important;
  background: linear-gradient(90deg, transparent, {accent}, transparent) !important;
  opacity: 0.75;
  margin: 1.1em auto !important;
  max-width: 620px;
}}
</style>
"""


def _stats_css() -> str:
    """Graph cards: rounded, elevated, subtly bordered — a modern dashboard."""
    p = _pal()
    accent, _bright, _on, _tint = _accent()
    shadow = (
        "0 1px 2px rgba(0,0,0,0.30), 0 8px 22px rgba(0,0,0,0.22)"
        if theme_manager.night_mode
        else "0 1px 2px rgba(60,50,20,0.05), 0 10px 26px rgba(120,100,40,0.09)"
    )
    return f"""
.graph {{
  background: {p['surface']} !important;
  border: 1px solid {p['border_subtle']} !important;
  border-radius: 16px !important;
  box-shadow: {shadow} !important;
  padding: 1.1em 1.2em !important;
  margin: 0.7em auto !important;
}}
.graph h1, .graph .title {{ letter-spacing: 0.01em; }}
.range-box, .range-box-inner {{ border-radius: 12px !important; }}
"""


def _revlog_daily() -> list[dict]:
    """Per-day review aggregates from the revlog: date (ordinal), total review
    time (minutes), and review count. Used by the correlation scatter."""
    import datetime
    import time as _time
    from collections import defaultdict

    try:
        rows = mw.col.db.all("select id, time from revlog")
    except Exception:  # noqa: BLE001
        return []
    agg: dict = defaultdict(lambda: [0, 0])
    for id_ms, t_ms in rows:
        lt = _time.localtime((id_ms or 0) / 1000)
        key = (lt.tm_year, lt.tm_mon, lt.tm_mday)
        a = agg[key]
        a[0] += 1
        a[1] += t_ms or 0
    out = []
    for (y, m, d), (cnt, tms) in sorted(agg.items()):
        dt = datetime.date(y, m, d)
        out.append(
            {"d": dt.toordinal(), "t": round(tms / 60000.0, 2), "n": cnt,
             "label": dt.isoformat()}
        )
    return out


def _scatter_js() -> str:
    """Self-contained interactive correlation scatter injected into the stats
    page: pick any of Date / Review time / Review total for each axis, see the
    points and the Pearson correlation coefficient r."""
    p = _pal()
    accent, _bright, on_accent, _tint = _accent()
    data_json = json.dumps(_revlog_daily())
    # Colours passed through to the drawing code.
    cfg = json.dumps({
        "accent": accent, "onAccent": on_accent, "surface": p["surface"],
        "fg": p["fg"], "faint": p["fg_faint"], "border": p["border"],
        "grid": p["border_subtle"],
    })
    return (
        "(function(){"
        "if(!/graphs/.test(location.pathname))return;"
        "if(document.getElementById('kelma-scatter'))return;"
        "var DATA=" + data_json + ";var C=" + cfg + ";"
        + _SCATTER_BODY +
        "})();"
    )


# The DOM-building + drawing routine (kept as a constant so the f-string above
# stays readable). Uses only DATA and C from the closure.
_SCATTER_BODY = r"""
var AX={
  date:{label:'Date',get:function(p){return p.d;},tick:function(v){
     var dt=new Date(0);dt.setUTCDate(Math.round(v)-719162);
     return dt.toISOString().slice(0,10);}},
  time:{label:'Review time (min)',get:function(p){return p.t;},tick:function(v){return (Math.round(v*10)/10);}},
  total:{label:'Reviews',get:function(p){return p.n;},tick:function(v){return Math.round(v);}}
};
function pearson(xs,ys){
  var n=xs.length;if(n<2)return NaN;
  var sx=0,sy=0,sxx=0,syy=0,sxy=0;
  for(var i=0;i<n;i++){var x=xs[i],y=ys[i];sx+=x;sy+=y;sxx+=x*x;syy+=y*y;sxy+=x*y;}
  var cov=sxy-sx*sy/n,vx=sxx-sx*sx/n,vy=syy-sy*sy/n;
  if(vx<=0||vy<=0)return NaN;
  return cov/Math.sqrt(vx*vy);
}
var card=document.createElement('div');
card.id='kelma-scatter';card.className='graph';
card.style.cssText='background:'+C.surface+';border:1px solid '+C.border+
 ';border-radius:16px;padding:1.1em 1.2em;margin:0.7em auto;max-width:60em;';
var h=document.createElement('h1');h.textContent='Correlations';
h.style.cssText='letter-spacing:.01em;margin:0 0 .2em;';
card.appendChild(h);
function mkSel(sel){var s=document.createElement('select');
 s.style.cssText='margin:0 .5em;padding:4px 8px;border-radius:8px;border:1px solid '+C.border+
  ';background:'+C.surface+';color:'+C.fg+';font:inherit;';
 ['date','time','total'].forEach(function(k){var o=document.createElement('option');
  o.value=k;o.textContent=AX[k].label;if(k===sel)o.selected=true;s.appendChild(o);});
 return s;}
var ctrl=document.createElement('div');
ctrl.style.cssText='margin:.2em 0 .6em;color:'+C.faint+';font-size:.95em;';
var xSel=mkSel('date'),ySel=mkSel('time');
ctrl.appendChild(document.createTextNode('X: '));ctrl.appendChild(xSel);
ctrl.appendChild(document.createTextNode('  Y: '));ctrl.appendChild(ySel);
card.appendChild(ctrl);
var W=680,H=380,PADL=64,PADB=48,PADT=16,PADR=20;
var ns='http://www.w3.org/2000/svg';
var svg=document.createElementNS(ns,'svg');
svg.setAttribute('viewBox','0 0 '+W+' '+H);
svg.style.cssText='width:100%;height:auto;max-width:'+W+'px;display:block;margin:0 auto;';
card.appendChild(svg);
var rLbl=document.createElement('div');
rLbl.style.cssText='text-align:center;margin-top:.4em;font-size:1.05em;color:'+C.fg+';';
card.appendChild(rLbl);
function draw(){
  while(svg.firstChild)svg.removeChild(svg.firstChild);
  var ax=AX[xSel.value],ay=AX[ySel.value];
  var xs=DATA.map(ax.get),ys=DATA.map(ay.get);
  if(DATA.length<2){rLbl.textContent='Not enough review data yet.';return;}
  var xmin=Math.min.apply(null,xs),xmax=Math.max.apply(null,xs);
  var ymin=Math.min.apply(null,ys),ymax=Math.max.apply(null,ys);
  if(xmax===xmin)xmax=xmin+1; if(ymax===ymin)ymax=ymin+1;
  function sx(v){return PADL+(v-xmin)/(xmax-xmin)*(W-PADL-PADR);}
  function sy(v){return H-PADB-(v-ymin)/(ymax-ymin)*(H-PADT-PADB);}
  function line(x1,y1,x2,y2,col,w){var l=document.createElementNS(ns,'line');
   l.setAttribute('x1',x1);l.setAttribute('y1',y1);l.setAttribute('x2',x2);l.setAttribute('y2',y2);
   l.setAttribute('stroke',col);l.setAttribute('stroke-width',w||1);svg.appendChild(l);}
  function txt(x,y,s,col,anchor){var t=document.createElementNS(ns,'text');
   t.setAttribute('x',x);t.setAttribute('y',y);t.setAttribute('fill',col);
   t.setAttribute('font-size','12');t.setAttribute('text-anchor',anchor||'middle');
   t.textContent=s;svg.appendChild(t);}
  // axes + gridlines/ticks
  var i,frac,gv;
  for(i=0;i<=4;i++){frac=i/4;
    gv=xmin+frac*(xmax-xmin);line(sx(gv),PADT,sx(gv),H-PADB,C.grid,1);
    txt(sx(gv),H-PADB+18,''+ax.tick(gv),C.faint,'middle');
    gv=ymin+frac*(ymax-ymin);line(PADL,sy(gv),W-PADR,sy(gv),C.grid,1);
    txt(PADL-8,sy(gv)+4,''+ay.tick(gv),C.faint,'end');
  }
  line(PADL,PADT,PADL,H-PADB,C.border,1.5);
  line(PADL,H-PADB,W-PADR,H-PADB,C.border,1.5);
  txt(W/2,H-8,ax.label,C.fg,'middle');
  var yl=document.createElementNS(ns,'text');
  yl.setAttribute('transform','translate(16,'+(H/2)+') rotate(-90)');
  yl.setAttribute('fill',C.fg);yl.setAttribute('font-size','12');
  yl.setAttribute('text-anchor','middle');yl.textContent=ay.label;svg.appendChild(yl);
  // regression line
  var r=pearson(xs,ys);
  if(!isNaN(r)){
    var n=xs.length,mx=xs.reduce(function(a,b){return a+b;},0)/n,
        my=ys.reduce(function(a,b){return a+b;},0)/n,num=0,den=0;
    for(i=0;i<n;i++){num+=(xs[i]-mx)*(ys[i]-my);den+=(xs[i]-mx)*(xs[i]-mx);}
    if(den>0){var slope=num/den,b0=my-slope*mx;
      var lx1=xmin,lx2=xmax,ly1=b0+slope*lx1,ly2=b0+slope*lx2;
      ly1=Math.max(ymin,Math.min(ymax,ly1));ly2=Math.max(ymin,Math.min(ymax,ly2));
      line(sx(lx1),sy(b0+slope*lx1),sx(lx2),sy(b0+slope*lx2),C.accent,2);}
  }
  // points
  for(i=0;i<DATA.length;i++){var c=document.createElementNS(ns,'circle');
   c.setAttribute('cx',sx(xs[i]));c.setAttribute('cy',sy(ys[i]));c.setAttribute('r',4);
   c.setAttribute('fill',C.accent);c.setAttribute('fill-opacity','0.72');
   var tt=document.createElementNS(ns,'title');
   tt.textContent=DATA[i].label+'  ('+ax.label+': '+ax.tick(xs[i])+', '+ay.label+': '+ay.tick(ys[i])+')';
   c.appendChild(tt);svg.appendChild(c);}
  rLbl.innerHTML='Pearson correlation coefficient  <b style="color:'+C.accent+
    '">r = '+(isNaN(r)?'—':r.toFixed(3))+'</b>  (n='+DATA.length+' days)';
}
xSel.addEventListener('change',draw);ySel.addEventListener('change',draw);
var host=document.querySelector('.graphs-container,main,.container,body')||document.body;
host.appendChild(card);
draw();
"""


def _qss() -> str:
    """Native Qt chrome: menus, buttons, tabs, scrollbars, progress bars."""
    p = _pal()
    accent, bright, on_accent, _tint = _accent()
    return f"""
/* --- KelmaDesktop native chrome --- */
QMenuBar {{ background-color: {p['surface']}; }}
QMenuBar::item {{ padding: 5px 10px; border-radius: 6px; }}
QMenuBar::item:selected {{ background-color: {accent}; color: {on_accent}; }}
QMenu {{
  background-color: {p['surface']};
  color: {p['fg']};
  border: 1px solid {p['border']};
  border-radius: 10px;
  padding: 6px;
}}
QMenu::item {{ padding: 6px 22px; border-radius: 6px; }}
QMenu::item:selected {{ background-color: {accent}; color: {on_accent}; }}
QMenu::separator {{ height: 1px; background: {p['border_subtle']}; margin: 5px 8px; }}
QPushButton {{
  border: 1px solid {p['border']};
  border-radius: 8px;
  padding: 5px 14px;
  background-color: {p['elevated']};
  color: {p['fg']};
}}
QPushButton:hover {{ border-color: {accent}; }}
QPushButton:default {{
  background-color: {accent};
  color: {on_accent};
  border: none;
  font-weight: 600;
}}
QPushButton:default:hover {{ background-color: {bright}; }}
QTabBar::tab {{ padding: 6px 14px; }}
QTabBar::tab:selected {{ color: {accent}; border-bottom: 2px solid {accent}; }}
QProgressBar {{ border-radius: 6px; }}
QProgressBar::chunk {{ background-color: {accent}; border-radius: 6px; }}
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 0; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
  background: {p['border']};
  border-radius: 6px;
  min-height: 28px;
  min-width: 28px;
}}
QScrollBar::handle:hover {{ background: {accent}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit {{
  border: 1px solid {p['border']};
  border-radius: 8px;
  padding: 4px 8px;
  selection-background-color: {accent};
  selection-color: {on_accent};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {{ border-color: {accent}; }}

/* --- Browser: card table + sidebar tree --- */
QTableView, QTreeView {{
  border: none;
  background-color: {p['canvas']};
  alternate-background-color: {p['surface']};
  selection-background-color: {accent};
  selection-color: {on_accent};
  outline: 0;
}}
QTableView::item, QTreeView::item {{ padding: 3px 4px; }}
QTableView::item:selected, QTreeView::item:selected {{
  background-color: {accent}; color: {on_accent};
}}
QTreeView::item:hover, QTableView::item:hover {{ background-color: {_tint}; }}
QHeaderView::section {{
  background-color: {p['surface']};
  color: {p['fg_faint']};
  border: none;
  border-bottom: 1px solid {p['border']};
  padding: 6px 8px;
  font-weight: 600;
}}
"""


def _on_webview(web_content, context) -> None:
    try:
        web_content.head += _base_css()
        name = type(context).__name__
        if name == "DeckBrowser":
            web_content.head += _deckbrowser_css()
        elif name == "Overview":
            web_content.head += _overview_css()
        elif name in ("Toolbar", "TopToolbar"):
            web_content.head += _toolbar_css()
        elif name == "ReviewerBottomBar":
            web_content.head += _reviewer_bottom_css()
        elif name == "Reviewer":
            web_content.head += _reviewer_css()
    except Exception:  # noqa: BLE001
        pass


def _on_page_style(webview) -> None:
    """Theme load_url pages (stats graphs, deck options, import …) that don't go
    through webview_will_set_content — inject the palette + graph-card polish,
    and on the stats page add the Kelma correlation scatter."""
    try:
        css = _vars_css() + _scrollbar_css() + _stats_css()
        payload = json.dumps(css)
        webview.eval(
            "(function(){var s=document.createElement('style');"
            "s.id='kelma-page';s.textContent=" + payload + ";"
            "document.head.appendChild(s);})();"
        )
        try:
            url = webview.page().url().toString()
        except Exception:  # noqa: BLE001
            url = ""
        if "graphs" in url:
            # Statistics page only: Computer Modern typography + the scatter.
            font_css = json.dumps(
                _font_face_css() + f"body {{ font-family: {STATS_FONT}; }}"
            )
            webview.eval(
                "(function(){var s=document.createElement('style');"
                "s.id='kelma-stats-font';s.textContent=" + font_css + ";"
                "document.head.appendChild(s);})();"
            )
            webview.eval(_scatter_js())
    except Exception:  # noqa: BLE001
        pass


def _on_style(buf: str) -> str:
    try:
        return buf + _qss()
    except Exception:  # noqa: BLE001
        return buf


def _on_theme_change() -> None:
    # Re-render webviews so they pick up the new light/dark palette.
    try:
        mw.reset()
    except Exception:  # noqa: BLE001
        pass


def setup() -> None:
    gui_hooks.style_did_init.append(_on_style)
    gui_hooks.webview_will_set_content.append(_on_webview)
    gui_hooks.webview_did_inject_style_into_page.append(_on_page_style)
    gui_hooks.theme_did_change.append(_on_theme_change)
    try:
        theme_manager.apply_style()
        mw.reset()
    except Exception:  # noqa: BLE001
        pass
