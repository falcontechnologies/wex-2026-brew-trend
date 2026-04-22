"""
brew_web.py — Flask web interface for brew_analytics.db

Usage:
    python brew_web.py                     # default DB (brew_analytics.db), port 5000
    python brew_web.py --db my.db          # custom DB path
    python brew_web.py --port 8080         # custom port
    python brew_web.py --db my.db --port 8080
"""

import argparse
import csv
import io
import json
import sqlite3
import threading
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, Response, jsonify, render_template_string, request

# ---------------------------------------------------------------------------
# Config / constants
# ---------------------------------------------------------------------------

BASE_URL  = "https://formulae.brew.sh/api/analytics/install/{days}d.json"
VALID_DAYS = (30, 90, 365)
DEFAULT_DB = "brew_analytics.db"

app = Flask(__name__)
DB_PATH: str = DEFAULT_DB
_fetch_lock = threading.Lock()

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS snapshots (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            fetched_at  TEXT    NOT NULL,
            fetch_date  TEXT    NOT NULL DEFAULT '',
            category    TEXT,
            period_days INTEGER,
            start_date  TEXT,
            end_date    TEXT,
            total_items INTEGER,
            total_count INTEGER,
            UNIQUE (fetch_date, period_days)
        );
        UPDATE snapshots SET fetch_date = substr(fetched_at, 1, 10)
        WHERE fetch_date = '' OR fetch_date IS NULL;

        CREATE TABLE IF NOT EXISTS installs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
            rank        INTEGER,
            formula     TEXT    NOT NULL,
            count       INTEGER,
            percent     REAL
        );
        CREATE INDEX IF NOT EXISTS idx_installs_snapshot ON installs(snapshot_id);
        CREATE INDEX IF NOT EXISTS idx_installs_formula  ON installs(formula);
    """)
    conn.commit()


def fetch_and_store(days: int, force: bool = False) -> dict:
    url = BASE_URL.format(days=days)
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            payload = json.loads(resp.read().decode())
    except Exception as exc:
        return {"ok": False, "message": f"Network error: {exc}"}

    fetched_at = datetime.now(timezone.utc).isoformat()
    fetch_date = fetched_at[:10]

    with get_conn() as conn:
        init_db(conn)
        existing = conn.execute(
            "SELECT id FROM snapshots WHERE fetch_date=? AND period_days=?",
            (fetch_date, days),
        ).fetchone()
        if existing:
            if not force:
                return {"ok": False, "message": f"Snapshot for {fetch_date} ({days}d) already exists (id={existing['id']}). Use force=true to replace."}
            conn.execute("DELETE FROM installs  WHERE snapshot_id=?", (existing["id"],))
            conn.execute("DELETE FROM snapshots WHERE id=?",          (existing["id"],))
            conn.commit()

        cur = conn.execute(
            "INSERT INTO snapshots (fetched_at,fetch_date,category,period_days,start_date,end_date,total_items,total_count) VALUES (?,?,?,?,?,?,?,?)",
            (fetched_at, fetch_date, payload.get("category"), days,
             payload.get("start_date"), payload.get("end_date"),
             payload.get("total_items"), payload.get("total_count")),
        )
        snap_id = cur.lastrowid
        rows = []
        for item in payload.get("items", []):
            raw = item.get("count", "0")
            count = int(str(raw).replace(",", "")) if raw else 0
            rows.append((snap_id, item.get("number"), item.get("formula"), count, float(item.get("percent", 0))))
        conn.executemany(
            "INSERT INTO installs (snapshot_id,rank,formula,count,percent) VALUES (?,?,?,?,?)", rows)
        conn.commit()

    return {"ok": True, "message": f"Snapshot #{snap_id} saved — {len(rows):,} formulae ({payload.get('start_date')} → {payload.get('end_date')})", "snapshot_id": snap_id}

# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    with get_conn() as conn:
        init_db(conn)
    return render_template_string(HTML)


@app.route("/api/snapshots")
def api_snapshots():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM snapshots ORDER BY id DESC").fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/installs/<int:snap_id>")
def api_installs(snap_id):
    search  = request.args.get("search", "")
    limit   = int(request.args.get("limit", 100))
    offset  = int(request.args.get("offset", 0))
    min_c   = request.args.get("min_count", type=int, default=None)
    max_c   = request.args.get("max_count", type=int, default=None)
    with get_conn() as conn:
        conds  = ["snapshot_id=?"]
        params = [snap_id]
        if search:
            conds.append("formula LIKE ?"); params.append(f"%{search}%")
        if min_c is not None:
            conds.append("count >= ?"); params.append(min_c)
        if max_c is not None:
            conds.append("count <= ?"); params.append(max_c)
        where = " AND ".join(conds)
        total     = conn.execute(f"SELECT COUNT(*) FROM installs WHERE {where}", params).fetchone()[0]
        max_count = conn.execute(f"SELECT MAX(count) FROM installs WHERE snapshot_id=?", [snap_id]).fetchone()[0] or 1
        rows      = conn.execute(
            f"SELECT rank,formula,count,percent FROM installs WHERE {where} ORDER BY rank LIMIT ? OFFSET ?",
            params + [limit, offset]).fetchall()
    return jsonify({"total": total, "max_count": max_count, "rows": [dict(r) for r in rows]})


@app.route("/api/installs/<int:snap_id>/max_count")
def api_max_count(snap_id):
    with get_conn() as conn:
        row = conn.execute("SELECT MAX(count) as mc FROM installs WHERE snapshot_id=?", [snap_id]).fetchone()
    return jsonify({"max_count": row["mc"] or 0})


@app.route("/api/timeseries")
def api_timeseries():
    """Return install counts over time for a list of formulae across all snapshots."""
    formulae = request.args.getlist("formula")
    period   = request.args.get("period_days", type=int, default=None)
    if not formulae:
        return jsonify({"snapshots": [], "series": []})

    with get_conn() as conn:
        snap_q  = "SELECT id,fetch_date,start_date,end_date,period_days FROM snapshots"
        snap_p  = []
        if period:
            snap_q += " WHERE period_days=?"; snap_p.append(period)
        snap_q += " ORDER BY fetch_date ASC"
        snaps = [dict(r) for r in conn.execute(snap_q, snap_p).fetchall()]

        series = {}
        for f in formulae:
            pts = []
            for s in snaps:
                row = conn.execute(
                    "SELECT count,rank,percent FROM installs WHERE snapshot_id=? AND formula=?",
                    [s["id"], f]).fetchone()
                pts.append({"snap_id": s["id"], "fetch_date": s["fetch_date"],
                            "start_date": s["start_date"], "end_date": s["end_date"],
                            "period_days": s["period_days"],
                            "count": row["count"] if row else None,
                            "rank":  row["rank"]  if row else None,
                            "percent": row["percent"] if row else None})
            series[f] = pts

    return jsonify({"snapshots": snaps, "series": series})


@app.route("/api/top_formulae")
def api_top_formulae():
    """Return top N formulae by average rank across all snapshots (for autocomplete)."""
    limit = int(request.args.get("limit", 200))
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT formula, AVG(rank) as avg_rank, COUNT(*) as appearances
            FROM installs GROUP BY formula ORDER BY avg_rank ASC LIMIT ?
        """, [limit]).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/export/<int:snap_id>")
def api_export(snap_id):
    fmt    = request.args.get("format", "csv")
    search = request.args.get("search", "")
    min_c  = request.args.get("min_count", type=int, default=None)
    max_c  = request.args.get("max_count", type=int, default=None)

    conds  = ["snapshot_id=?"]
    params = [snap_id]
    if search:
        conds.append("formula LIKE ?"); params.append(f"%{search}%")
    if min_c is not None:
        conds.append("count >= ?"); params.append(min_c)
    if max_c is not None:
        conds.append("count <= ?"); params.append(max_c)
    where = " AND ".join(conds)

    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT rank,formula,count,percent FROM installs WHERE {where} ORDER BY rank",
            params).fetchall()

    if fmt == "json":
        return Response(json.dumps([dict(r) for r in rows], indent=2),
                        mimetype="application/json",
                        headers={"Content-Disposition": f"attachment;filename=installs_{snap_id}.json"})
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["rank", "formula", "count", "percent"])
    w.writerows(rows)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment;filename=installs_{snap_id}.csv"})


@app.route("/api/fetch", methods=["POST"])
def api_fetch():
    data  = request.get_json(force=True)
    days  = int(data.get("days", 30))
    force = bool(data.get("force", False))
    if days not in VALID_DAYS:
        return jsonify({"ok": False, "message": f"days must be one of {VALID_DAYS}"}), 400
    with _fetch_lock:
        result = fetch_and_store(days, force)
    return jsonify(result)

# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Brew Analytics</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@300;400;500&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
:root {
  --bg:#0d0f11; --bg2:#151820; --bg3:#1c2028; --border:#2a2f3a;
  --text:#c8cdd8; --muted:#586070; --accent:#4ade80; --accent2:#22d3ee;
  --accent3:#f59e0b; --danger:#f87171;
  --mono:'IBM Plex Mono',monospace; --sans:'IBM Plex Sans',sans-serif;
  --chart-grid:#1c2028; --chart-tick:#586070;
  --tooltip-bg:#1c2028; --tooltip-border:#2a2f3a; --tooltip-text:#c8cdd8;
}
html.light {
  --bg:#f5f5f0; --bg2:#ffffff; --bg3:#eeeee8; --border:#d4d3cb;
  --text:#1a1a18; --muted:#8a8a80; --accent:#16a34a; --accent2:#0891b2;
  --accent3:#b45309; --danger:#dc2626;
  --chart-grid:#e8e8e2; --chart-tick:#8a8a80;
  --tooltip-bg:#ffffff; --tooltip-border:#d4d3cb; --tooltip-text:#1a1a18;
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0;transition:background-color .2s,border-color .2s,color .15s}
html{font-size:14px}
body{background:var(--bg);color:var(--text);font-family:var(--sans);min-height:100vh}

.shell{display:grid;grid-template-columns:220px 1fr;min-height:100vh}
nav{background:var(--bg2);border-right:1px solid var(--border);display:flex;flex-direction:column;position:sticky;top:0;height:100vh;overflow-y:auto}
main{padding:2rem;overflow-x:hidden;min-width:0}

.nav-brand{padding:1.4rem 1.2rem 1rem;border-bottom:1px solid var(--border)}
.nav-brand .logo{font-family:var(--mono);font-size:1rem;font-weight:600;color:var(--accent);letter-spacing:.04em}
.nav-brand .sub{font-family:var(--mono);font-size:.65rem;color:var(--muted);margin-top:2px}
.nav-section{padding:.6rem 1.2rem .3rem;font-family:var(--mono);font-size:.6rem;color:var(--muted);letter-spacing:.12em;text-transform:uppercase}
nav a{display:flex;align-items:center;gap:.5rem;padding:.55rem 1.2rem;font-family:var(--mono);font-size:.75rem;color:var(--muted);text-decoration:none;border-left:2px solid transparent;transition:all .15s;cursor:pointer}
nav a:hover{color:var(--text);background:var(--bg3)}
nav a.active{color:var(--accent);border-left-color:var(--accent);background:rgba(74,222,128,.06)}
nav a .ic{width:14px;flex-shrink:0;opacity:.7}
.nav-snap-list{flex:1;overflow-y:auto;padding-bottom:1rem}
.snap-item{display:flex;flex-direction:column;padding:.5rem 1.2rem;border-left:2px solid transparent;cursor:pointer;transition:all .15s}
.snap-item:hover{background:var(--bg3)}
.snap-item.active{border-left-color:var(--accent2);background:rgba(34,211,238,.05)}
.snap-item .s-id{font-family:var(--mono);font-size:.65rem;color:var(--accent2)}
.snap-item .s-date{font-family:var(--mono);font-size:.6rem;color:var(--muted);margin-top:1px}
.snap-item .s-days{font-family:var(--mono);font-size:.58rem;color:var(--muted)}

.panel{display:none}.panel.active{display:block}

.page-header{margin-bottom:1.8rem}
.page-header h1{font-family:var(--mono);font-size:1.1rem;font-weight:600;color:var(--text);letter-spacing:.02em}
.page-header p{font-size:.8rem;color:var(--muted);margin-top:.3rem;font-family:var(--mono)}

.stat-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:1rem;margin-bottom:2rem}
.stat-card{background:var(--bg2);border:1px solid var(--border);border-radius:6px;padding:1rem 1.2rem}
.stat-card .label{font-family:var(--mono);font-size:.6rem;color:var(--muted);text-transform:uppercase;letter-spacing:.1em}
.stat-card .value{font-family:var(--mono);font-size:1.4rem;font-weight:600;color:var(--accent);margin-top:.3rem}
.stat-card .sub{font-family:var(--mono);font-size:.65rem;color:var(--muted);margin-top:.2rem}

.table-wrap{background:var(--bg2);border:1px solid var(--border);border-radius:6px;overflow:hidden}
.table-toolbar{display:flex;align-items:center;gap:.8rem;padding:.8rem 1rem;border-bottom:1px solid var(--border);flex-wrap:wrap}
.search-box{flex:1;min-width:180px;background:var(--bg3);border:1px solid var(--border);border-radius:4px;padding:.4rem .7rem;font-family:var(--mono);font-size:.75rem;color:var(--text);outline:none;transition:border-color .15s}
.search-box:focus{border-color:var(--accent2)}
.search-box::placeholder{color:var(--muted)}
.btn{font-family:var(--mono);font-size:.7rem;padding:.4rem .9rem;border-radius:4px;border:1px solid var(--border);background:var(--bg3);color:var(--text);cursor:pointer;transition:all .15s;white-space:nowrap}
.btn:hover{border-color:var(--accent2);color:var(--accent2)}
.btn.primary{border-color:var(--accent);color:var(--accent)}
.btn.primary:hover{background:rgba(74,222,128,.1)}
.btn.danger-btn{border-color:var(--danger);color:var(--danger)}
.btn:disabled{opacity:.4;cursor:not-allowed}
table{width:100%;border-collapse:collapse}
th{font-family:var(--mono);font-size:.65rem;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;padding:.6rem 1rem;text-align:left;border-bottom:1px solid var(--border);background:var(--bg2)}
td{font-family:var(--mono);font-size:.75rem;padding:.55rem 1rem;border-bottom:1px solid rgba(42,47,58,.6);vertical-align:middle}
tr:last-child td{border-bottom:none}
tr:hover td{background:var(--bg3)}
.rank-badge{display:inline-block;width:28px;text-align:right;color:var(--muted)}
.formula-name{color:var(--accent2)}
.count-bar{display:flex;align-items:center;gap:.6rem}
.bar-bg{flex:1;max-width:120px;height:4px;background:var(--bg3);border-radius:2px;overflow:hidden}
.bar-fill{height:100%;background:var(--accent);border-radius:2px}
.pct{color:var(--muted);min-width:38px;text-align:right}
.pagination{display:flex;align-items:center;gap:.5rem;padding:.8rem 1rem;border-top:1px solid var(--border);justify-content:flex-end;flex-wrap:wrap}
.page-info{font-family:var(--mono);font-size:.65rem;color:var(--muted);flex:1}

/* ── Time-series panel ── */
.ts-layout{display:flex;flex-direction:column;gap:1.2rem}
.ts-controls{background:var(--bg2);border:1px solid var(--border);border-radius:6px;padding:1.2rem}
.ts-controls-row{display:flex;gap:1rem;flex-wrap:wrap;align-items:flex-end}
.form-group{display:flex;flex-direction:column;gap:.35rem}
.form-group label{font-family:var(--mono);font-size:.6rem;color:var(--muted);text-transform:uppercase;letter-spacing:.1em}
select{background:var(--bg2);border:1px solid var(--border);border-radius:4px;padding:.4rem .7rem;font-family:var(--mono);font-size:.75rem;color:var(--text);outline:none;cursor:pointer}
select:focus{border-color:var(--accent2)}
.preset-btns{display:flex;gap:.4rem;flex-wrap:wrap}
.preset-btn{font-family:var(--mono);font-size:.68rem;padding:.35rem .75rem;border:1px solid var(--border);border-radius:4px;background:var(--bg3);color:var(--muted);cursor:pointer;transition:all .15s}
.preset-btn:hover{border-color:var(--accent2);color:var(--accent2)}
.preset-btn.active{border-color:var(--accent);color:var(--accent);background:rgba(74,222,128,.08)}

/* Formula tag input */
.tag-input-wrap{position:relative;min-width:260px;flex:1}
.tag-input{width:100%;background:var(--bg3);border:1px solid var(--border);border-radius:4px;padding:.4rem .7rem;font-family:var(--mono);font-size:.75rem;color:var(--text);outline:none}
.tag-input:focus{border-color:var(--accent2)}
.tag-input::placeholder{color:var(--muted)}
.autocomplete-list{position:absolute;top:calc(100% + 4px);left:0;right:0;background:var(--bg3);border:1px solid var(--border);border-radius:4px;max-height:200px;overflow-y:auto;z-index:100;display:none}
.autocomplete-list.open{display:block}
.ac-item{padding:.4rem .8rem;font-family:var(--mono);font-size:.72rem;color:var(--text);cursor:pointer;transition:background .1s}
.ac-item:hover,.ac-item.selected{background:var(--bg2);color:var(--accent2)}
.formula-tags{display:flex;flex-wrap:wrap;gap:.4rem;margin-top:.5rem;min-height:24px}
.tag{display:inline-flex;align-items:center;gap:.4rem;background:rgba(34,211,238,.1);border:1px solid rgba(34,211,238,.3);border-radius:3px;padding:.2rem .55rem;font-family:var(--mono);font-size:.68rem;color:var(--accent2)}
.tag .remove{cursor:pointer;opacity:.6;font-size:.8rem;line-height:1}
.tag .remove:hover{opacity:1;color:var(--danger)}

/* Range slider */
.range-wrap{display:flex;flex-direction:column;gap:.4rem;min-width:240px;flex:1}
.range-row{display:flex;align-items:center;gap:.6rem}
.range-row input[type=range]{flex:1;accent-color:var(--accent);cursor:pointer}
.range-val{font-family:var(--mono);font-size:.68rem;color:var(--accent3);min-width:60px;text-align:right}
.range-labels{display:flex;justify-content:space-between;font-family:var(--mono);font-size:.6rem;color:var(--muted)}

/* Chart card */
.chart-card{background:var(--bg2);border:1px solid var(--border);border-radius:6px;padding:1.2rem}
.chart-card-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:1rem;flex-wrap:wrap;gap:.5rem}
.chart-card-header h3{font-family:var(--mono);font-size:.7rem;color:var(--muted);text-transform:uppercase;letter-spacing:.1em}
.chart-canvas-wrap{position:relative}

/* Raw data table below chart */
.raw-data-wrap{background:var(--bg2);border:1px solid var(--border);border-radius:6px;overflow:hidden}
.raw-data-header{display:flex;align-items:center;justify-content:space-between;padding:.7rem 1rem;border-bottom:1px solid var(--border);flex-wrap:wrap;gap:.5rem}
.raw-data-header h3{font-family:var(--mono);font-size:.7rem;color:var(--muted);text-transform:uppercase;letter-spacing:.1em}
.raw-scroll{overflow-x:auto;max-height:340px;overflow-y:auto}

/* Compare */
.compare-controls{display:flex;gap:1rem;margin-bottom:1.5rem;flex-wrap:wrap;align-items:flex-end}
.compare-table-wrap{background:var(--bg2);border:1px solid var(--border);border-radius:6px;overflow:hidden;margin-bottom:1.5rem}
.diff-up{color:var(--accent)} .diff-down{color:var(--danger)} .diff-new{color:var(--accent3)} .diff-gone{color:var(--muted)}

/* Fetch */
.fetch-card{background:var(--bg2);border:1px solid var(--border);border-radius:6px;padding:1.5rem;max-width:520px}
.fetch-card h3{font-family:var(--mono);font-size:.75rem;color:var(--text);margin-bottom:1.2rem;letter-spacing:.02em}
.fetch-options{display:flex;gap:.6rem;flex-wrap:wrap;margin-bottom:1rem}
.days-btn{font-family:var(--mono);font-size:.75rem;padding:.4rem .9rem;border:1px solid var(--border);border-radius:4px;background:var(--bg3);color:var(--muted);cursor:pointer;transition:all .15s}
.days-btn.selected{border-color:var(--accent);color:var(--accent);background:rgba(74,222,128,.08)}
.force-row{display:flex;align-items:center;gap:.6rem;margin-bottom:1.2rem;font-family:var(--mono);font-size:.75rem;color:var(--muted)}
.force-row input[type=checkbox]{accent-color:var(--accent);width:14px;height:14px}
.log-box{background:var(--bg);border:1px solid var(--border);border-radius:4px;padding:.8rem 1rem;font-family:var(--mono);font-size:.72rem;color:var(--muted);min-height:56px;margin-top:1rem;white-space:pre-wrap;line-height:1.6}
.log-ok{color:var(--accent)} .log-err{color:var(--danger)} .log-warn{color:var(--accent3)}

/* Chart grid (overview charts tab) */
.chart-grid{display:grid;grid-template-columns:1fr 1fr;gap:1.2rem;margin-bottom:1.5rem}
.chart-grid .chart-card.wide{grid-column:1/-1}

.empty{padding:3rem;text-align:center;font-family:var(--mono);font-size:.8rem;color:var(--muted)}
.empty span{display:block;font-size:1.8rem;margin-bottom:.7rem;opacity:.4}
.spinner{display:inline-block;width:10px;height:10px;border:1.5px solid var(--muted);border-top-color:var(--accent);border-radius:50%;animation:spin .6s linear infinite;margin-right:.4rem;vertical-align:middle}
@keyframes spin{to{transform:rotate(360deg)}}

::-webkit-scrollbar{width:6px;height:6px}
::-webkit-scrollbar-track{background:var(--bg2)}
::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}

@media(max-width:768px){
  .shell{grid-template-columns:1fr}
  nav{position:static;height:auto}
  .chart-grid{grid-template-columns:1fr}
}
</style>
</head>
<body>
<div class="shell">

<!-- ── Sidebar ── -->
<nav>
  <div class="nav-brand">
    <div style="display:flex;align-items:flex-start;justify-content:space-between">
      <div>
        <div class="logo">⬡ brew/analytics</div>
        <div class="sub">install data explorer</div>
      </div>
      <button id="theme-btn" onclick="toggleTheme()" title="Toggle light/dark mode"
        style="background:none;border:1px solid var(--border);border-radius:4px;cursor:pointer;padding:.3rem .45rem;color:var(--muted);font-size:.85rem;line-height:1;transition:all .15s;margin-top:.1rem;flex-shrink:0"
        onmouseover="this.style.borderColor='var(--accent2)';this.style.color='var(--accent2)'"
        onmouseout="this.style.borderColor='var(--border)';this.style.color='var(--muted)'">
        🌙
      </button>
    </div>
  </div>
  <div class="nav-section">Views</div>
  <a class="active" data-panel="overview" onclick="showPanel('overview',this)">
    <svg class="ic" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="1" y="1" width="6" height="6" rx="1"/><rect x="9" y="1" width="6" height="6" rx="1"/><rect x="1" y="9" width="6" height="6" rx="1"/><rect x="9" y="9" width="6" height="6" rx="1"/></svg>
    Overview
  </a>
  <a data-panel="browse" onclick="showPanel('browse',this)">
    <svg class="ic" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M2 4h12M2 8h9M2 12h6"/></svg>
    Browse
  </a>
  <a data-panel="timeseries" onclick="showPanel('timeseries',this)">
    <svg class="ic" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M2 13L5 8l3 2 3-5 3 3"/><path d="M2 13h12"/></svg>
    Time Series
  </a>
  <a data-panel="charts" onclick="showPanel('charts',this)">
    <svg class="ic" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="8" width="3" height="6" rx="1"/><rect x="6.5" y="5" width="3" height="9" rx="1"/><rect x="11" y="2" width="3" height="12" rx="1"/></svg>
    Charts
  </a>
  <a data-panel="compare" onclick="showPanel('compare',this)">
    <svg class="ic" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M4 2v12M12 2v12M1 7h6M9 9h6"/></svg>
    Compare
  </a>
  <a data-panel="fetch" onclick="showPanel('fetch',this)">
    <svg class="ic" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M8 2v8M5 7l3 3 3-3"/><path d="M3 12h10"/></svg>
    Fetch
  </a>
  <div class="nav-section">Snapshots</div>
  <div class="nav-snap-list" id="snap-list"></div>
</nav>

<!-- ── Main ── -->
<main>

  <!-- Overview -->
  <div class="panel active" id="panel-overview">
    <div class="page-header">
      <h1>// overview</h1>
      <p id="ov-subtitle">select a snapshot from the sidebar</p>
    </div>
    <div class="stat-grid" id="ov-stats"></div>
    <div id="ov-empty" class="empty" style="display:none"><span>◈</span>No snapshots yet — use Fetch to pull data.</div>
  </div>

  <!-- Browse -->
  <div class="panel" id="panel-browse">
    <div class="page-header">
      <h1>// browse installs</h1>
      <p id="br-subtitle">—</p>
    </div>
    <div class="table-wrap">
      <div class="table-toolbar">
        <input class="search-box" id="search-input" placeholder="filter formula…" oninput="debouncedSearch()">
        <button class="btn" onclick="exportData('csv')">↓ CSV</button>
        <button class="btn" onclick="exportData('json')">↓ JSON</button>
        <span id="result-count" style="font-family:var(--mono);font-size:.65rem;color:var(--muted)"></span>
      </div>
      <div id="table-container"></div>
      <div class="pagination" id="pagination"></div>
    </div>
  </div>

  <!-- Time Series -->
  <div class="panel" id="panel-timeseries">
    <div class="page-header">
      <h1>// install counts over time</h1>
      <p>track formula install counts across all snapshots</p>
    </div>
    <div class="ts-layout">

      <!-- Controls -->
      <div class="ts-controls">
        <div class="ts-controls-row" style="margin-bottom:1rem">

          <!-- Presets -->
          <div class="form-group">
            <label>Quick select</label>
            <div class="preset-btns">
              <button class="preset-btn active" data-preset="top10"    onclick="applyPreset(this)">Top 1–10</button>
              <button class="preset-btn"        data-preset="next10"   onclick="applyPreset(this)">Top 11–20</button>
              <button class="preset-btn"        data-preset="next20"   onclick="applyPreset(this)">Top 21–30</button>
              <button class="preset-btn"        data-preset="next30"   onclick="applyPreset(this)">Top 31–40</button>
              <button class="preset-btn"        data-preset="custom"   onclick="applyPreset(this)">Custom</button>
            </div>
          </div>

          <!-- Period filter -->
          <div class="form-group">
            <label>Period window</label>
            <select id="ts-period" onchange="refreshTimeSeries()">
              <option value="">All windows</option>
              <option value="30">30 days</option>
              <option value="90">90 days</option>
              <option value="365">365 days</option>
            </select>
          </div>

          <!-- Reset -->
          <div class="form-group" style="justify-content:flex-end">
            <label>&nbsp;</label>
            <button class="btn primary" onclick="resetTimeSeries()">↺ Reset to default</button>
          </div>
        </div>

        <!-- Custom formula input (shown when preset=custom) -->
        <div id="custom-formula-row" style="display:none;margin-bottom:1rem">
          <div class="form-group">
            <label>Add formula</label>
            <div class="tag-input-wrap">
              <input class="tag-input" id="formula-input" placeholder="type to search formulae…" autocomplete="off"
                     oninput="onFormulaInput()" onkeydown="onFormulaKeydown(event)">
              <div class="autocomplete-list" id="ac-list"></div>
            </div>
            <div class="formula-tags" id="formula-tags"></div>
          </div>
        </div>

        <!-- Count range filter -->
        <div class="ts-controls-row">
          <div class="range-wrap">
            <label style="font-family:var(--mono);font-size:.6rem;color:var(--muted);text-transform:uppercase;letter-spacing:.1em">Min install count</label>
            <div class="range-row">
              <input type="range" id="range-min" min="0" max="1000000" step="1000" value="0" oninput="onRangeChange()">
              <span class="range-val" id="range-min-val">0</span>
            </div>
            <div class="range-labels"><span>0</span><span id="range-max-label">max</span></div>
          </div>
          <div class="range-wrap">
            <label style="font-family:var(--mono);font-size:.6rem;color:var(--muted);text-transform:uppercase;letter-spacing:.1em">Max install count</label>
            <div class="range-row">
              <input type="range" id="range-max" min="0" max="1000000" step="1000" value="1000000" oninput="onRangeChange()">
              <span class="range-val" id="range-max-val">∞</span>
            </div>
            <div class="range-labels"><span>0</span><span id="range-max-label2">max</span></div>
          </div>
          <div class="form-group" style="justify-content:flex-end">
            <label>&nbsp;</label>
            <button class="btn" onclick="clearCountFilter()">Clear filter</button>
          </div>
        </div>
      </div>

      <!-- Chart -->
      <div class="chart-card">
        <div class="chart-card-header">
          <h3 id="ts-chart-title">install counts — top 10</h3>
          <div style="display:flex;gap:.5rem">
            <button class="btn" onclick="exportTsData('csv')">↓ CSV</button>
            <button class="btn" onclick="exportTsData('json')">↓ JSON</button>
          </div>
        </div>
        <div class="chart-canvas-wrap">
          <canvas id="ts-chart"></canvas>
        </div>
        <div id="ts-empty" class="empty" style="display:none"><span>◈</span>No data — fetch at least two snapshots to see trends.</div>
      </div>

      <!-- Raw data table -->
      <div class="raw-data-wrap">
        <div class="raw-data-header">
          <h3>raw data</h3>
          <span id="ts-raw-info" style="font-family:var(--mono);font-size:.65rem;color:var(--muted)"></span>
        </div>
        <div class="raw-scroll">
          <div id="ts-raw-table"></div>
        </div>
      </div>

    </div>
  </div>

  <!-- Charts (snapshot bar/pie) -->
  <div class="panel" id="panel-charts">
    <div class="page-header">
      <h1>// snapshot charts</h1>
      <p id="ch-subtitle">—</p>
    </div>
    <div class="chart-grid">
      <div class="chart-card wide">
        <div class="chart-card-header"><h3>Top 20 formulae — install count</h3></div>
        <canvas id="chart-bar" height="90"></canvas>
      </div>
      <div class="chart-card">
        <div class="chart-card-header"><h3>Top 10 share breakdown</h3></div>
        <canvas id="chart-pie" height="200"></canvas>
      </div>
      <div class="chart-card">
        <div class="chart-card-header"><h3>Snapshot total installs (all snapshots)</h3></div>
        <canvas id="chart-trend" height="200"></canvas>
      </div>
    </div>
  </div>

  <!-- Compare -->
  <div class="panel" id="panel-compare">
    <div class="page-header">
      <h1>// compare snapshots</h1>
      <p>rank and count changes between two snapshots</p>
    </div>
    <div class="compare-controls">
      <div class="form-group">
        <label>Snapshot A (baseline)</label>
        <select id="cmp-a" onchange="runCompare()"></select>
      </div>
      <div class="form-group">
        <label>Snapshot B (compare)</label>
        <select id="cmp-b" onchange="runCompare()"></select>
      </div>
      <div class="form-group">
        <label>Show</label>
        <select id="cmp-filter" onchange="renderCompareTable()">
          <option value="all">All formulae</option>
          <option value="up">Moved up</option>
          <option value="down">Moved down</option>
          <option value="new">New entries</option>
          <option value="gone">Dropped out</option>
        </select>
      </div>
    </div>
    <div class="compare-table-wrap" id="cmp-container">
      <div class="empty"><span>◈</span>Select two snapshots to compare.</div>
    </div>
  </div>

  <!-- Fetch -->
  <div class="panel" id="panel-fetch">
    <div class="page-header">
      <h1>// fetch new data</h1>
      <p>pull latest analytics from formulae.brew.sh</p>
    </div>
    <div class="fetch-card">
      <h3>Analytics window</h3>
      <div class="fetch-options">
        <button class="days-btn selected" data-days="30"  onclick="selectDays(this)">30 days</button>
        <button class="days-btn"          data-days="90"  onclick="selectDays(this)">90 days</button>
        <button class="days-btn"          data-days="365" onclick="selectDays(this)">365 days</button>
      </div>
      <div class="force-row">
        <input type="checkbox" id="force-cb">
        <label for="force-cb">Force replace today's snapshot if it exists</label>
      </div>
      <button class="btn primary" id="fetch-btn" onclick="triggerFetch()">▶ Fetch now</button>
      <div class="log-box" id="fetch-log">ready.</div>
    </div>
  </div>

</main>
</div>
<script>
// ── State ──────────────────────────────────────────────────────────────────
let snapshots    = [];
let activeSnap   = null;
let browseOffset = 0;
const PAGE       = 50;
let searchVal    = '';
let compareData  = null;
let barChart = null, pieChart = null, trendChart = null, tsChart = null;

// Time-series state
let tsFormulae   = [];   // active formula list
let tsPreset     = 'top10';
let tsAllFormulae = [];  // for autocomplete
let tsData       = null; // last API response
let tsMinCount   = 0;
let tsMaxCount   = Infinity;
let tsGlobalMax  = 1000000;
let acIndex      = -1;

// ── Palette ────────────────────────────────────────────────────────────────
const PALETTE = [
  '#4ade80','#22d3ee','#f59e0b','#f87171','#a78bfa',
  '#fb923c','#34d399','#60a5fa','#f472b6','#facc15',
  '#84cc16','#2dd4bf','#e879f9','#38bdf8','#fbbf24',
];

// ── Init ───────────────────────────────────────────────────────────────────
async function init() {
  await loadSnapshots();
  await loadTopFormulae();
}

async function loadSnapshots() {
  const r  = await fetch('/api/snapshots');
  snapshots = await r.json();
  renderSnapList();
  populateCompareSelects();
  if (snapshots.length) {
    selectSnap(snapshots[0]);
  } else {
    document.getElementById('ov-empty').style.display = 'block';
  }
}

async function loadTopFormulae() {
  const r = await fetch('/api/top_formulae?limit=300');
  tsAllFormulae = await r.json();
}

// ── Snapshots sidebar ──────────────────────────────────────────────────────
function renderSnapList() {
  const el = document.getElementById('snap-list');
  if (!snapshots.length) {
    el.innerHTML = '<div style="padding:.8rem 1.2rem;font-family:var(--mono);font-size:.65rem;color:var(--muted)">no snapshots</div>';
    return;
  }
  el.innerHTML = snapshots.map(s => `
    <div class="snap-item ${activeSnap && activeSnap.id===s.id?'active':''}"
         onclick='selectSnap(${JSON.stringify(s).replace(/'/g,"&#39;")})'>
      <span class="s-id">#${s.id}</span>
      <span class="s-date">${s.fetch_date||s.fetched_at.slice(0,10)}</span>
      <span class="s-days">${s.period_days}d window</span>
    </div>`).join('');
}

function selectSnap(snap) {
  activeSnap = snap;
  renderSnapList();
  renderOverview();
  renderBrowse();
  updateRangeMax();
}

// ── Panel switching ────────────────────────────────────────────────────────
function showPanel(name, link) {
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('nav a').forEach(a => a.classList.remove('active'));
  document.getElementById('panel-'+name).classList.add('active');
  link.classList.add('active');
  if (name === 'charts'     && activeSnap) renderSnapshotCharts();
  if (name === 'timeseries')               refreshTimeSeries();
}

// ── Overview ───────────────────────────────────────────────────────────────
function renderOverview() {
  if (!activeSnap) return;
  const s = activeSnap;
  document.getElementById('ov-subtitle').textContent =
    `snapshot #${s.id}  ·  ${s.start_date} → ${s.end_date}  ·  fetched ${s.fetched_at.slice(0,19)} UTC`;
  document.getElementById('ov-stats').innerHTML = `
    <div class="stat-card"><div class="label">Total installs</div><div class="value">${fmtNum(s.total_count)}</div><div class="sub">${s.period_days}-day window</div></div>
    <div class="stat-card"><div class="label">Formulae tracked</div><div class="value">${fmtNum(s.total_items)}</div><div class="sub">unique packages</div></div>
    <div class="stat-card"><div class="label">Period</div><div class="value" style="font-size:.85rem">${s.start_date}</div><div class="sub">→ ${s.end_date}</div></div>
    <div class="stat-card"><div class="label">Snapshot ID</div><div class="value">#${s.id}</div><div class="sub">${s.period_days}d · ${s.category||'—'}</div></div>`;
}

// ── Browse ─────────────────────────────────────────────────────────────────
function renderBrowse() {
  if (!activeSnap) return;
  document.getElementById('br-subtitle').textContent =
    `snapshot #${activeSnap.id} · ${activeSnap.start_date} → ${activeSnap.end_date}`;
  browseOffset = 0;
  loadPage();
}

async function loadPage() {
  const url = `/api/installs/${activeSnap.id}?search=${encodeURIComponent(searchVal)}&limit=${PAGE}&offset=${browseOffset}`;
  const r   = await fetch(url);
  const { total, rows, max_count } = await r.json();
  document.getElementById('result-count').textContent = `${fmtNum(total)} results`;
  renderBrowseTable(rows, max_count);
  renderPagination(total);
}

function renderBrowseTable(rows, maxCount) {
  if (!rows.length) {
    document.getElementById('table-container').innerHTML = '<div class="empty"><span>◈</span>No results.</div>';
    return;
  }
  document.getElementById('table-container').innerHTML = `
    <table><thead><tr><th>rank</th><th>formula</th><th>installs</th><th>share</th></tr></thead>
    <tbody>${rows.map(r => `
      <tr>
        <td><span class="rank-badge">${r.rank}</span></td>
        <td><span class="formula-name">${r.formula}</span></td>
        <td><div class="count-bar"><span>${fmtNum(r.count)}</span>
          <div class="bar-bg"><div class="bar-fill" style="width:${Math.round(r.count/maxCount*100)}%"></div></div>
        </div></td>
        <td class="pct">${r.percent.toFixed(2)}%</td>
      </tr>`).join('')}
    </tbody></table>`;
}

function renderPagination(total) {
  const pages = Math.ceil(total/PAGE), cur = Math.floor(browseOffset/PAGE);
  const el = document.getElementById('pagination');
  if (pages<=1){el.innerHTML='';return;}
  el.innerHTML = `
    <span class="page-info">page ${cur+1} of ${pages}</span>
    <button class="btn" onclick="changePage(-1)" ${cur===0?'disabled':''}>← prev</button>
    <button class="btn" onclick="changePage(1)"  ${cur>=pages-1?'disabled':''}>next →</button>`;
}
function changePage(dir){ browseOffset=Math.max(0,browseOffset+dir*PAGE); loadPage(); }
let searchTimer;
function debouncedSearch(){
  clearTimeout(searchTimer);
  searchTimer=setTimeout(()=>{searchVal=document.getElementById('search-input').value;browseOffset=0;loadPage();},250);
}
function exportData(fmt){
  if(!activeSnap)return;
  window.open(`/api/export/${activeSnap.id}?format=${fmt}&search=${encodeURIComponent(searchVal)}`);
}

// ── Snapshot charts ────────────────────────────────────────────────────────
async function renderSnapshotCharts() {
  if (!activeSnap) return;
  document.getElementById('ch-subtitle').textContent =
    `snapshot #${activeSnap.id} · ${activeSnap.start_date} → ${activeSnap.end_date}`;

  const r = await fetch(`/api/installs/${activeSnap.id}?limit=20&offset=0`);
  const { rows } = await r.json();
  const labels = rows.map(r=>r.formula), counts = rows.map(r=>r.count);
  const GREENS = rows.map((_,i)=>`hsla(${142-i*4},70%,${62-i*1.5}%,${1-i*.03})`);
  const mono = "'IBM Plex Mono'";
  const cs = getComputedStyle(document.documentElement);
  const grid = cs.getPropertyValue('--chart-grid').trim();
  const tick = cs.getPropertyValue('--chart-tick').trim();
  const ttBg = cs.getPropertyValue('--tooltip-bg').trim();
  const ttBd = cs.getPropertyValue('--tooltip-border').trim();
  const ttTx = cs.getPropertyValue('--tooltip-text').trim();
  const tickStyle = { color:tick, font:{family:mono,size:10} };

  if(barChart) barChart.destroy();
  barChart = new Chart(document.getElementById('chart-bar'),{
    type:'bar',
    data:{labels,datasets:[{data:counts,backgroundColor:GREENS,borderRadius:3,borderSkipped:false}]},
    options:{plugins:{legend:{display:false},tooltip:{backgroundColor:ttBg,borderColor:ttBd,borderWidth:1,titleColor:ttTx,bodyColor:ttTx,callbacks:{label:ctx=>' '+fmtNum(ctx.raw)}}},
      scales:{x:{ticks:{...tickStyle,maxRotation:35},grid:{color:grid}},
              y:{ticks:{...tickStyle,callback:v=>fmtCompact(v)},grid:{color:grid}}}}
  });

  const top10 = rows.slice(0,10);
  if(pieChart) pieChart.destroy();
  pieChart = new Chart(document.getElementById('chart-pie'),{
    type:'doughnut',
    data:{labels:top10.map(r=>r.formula),datasets:[{data:top10.map(r=>r.count),
      backgroundColor:PALETTE.slice(0,10),borderWidth:0,hoverOffset:8}]},
    options:{plugins:{legend:{labels:{color:tick,font:{family:mono,size:10},boxWidth:10}},
      tooltip:{backgroundColor:ttBg,borderColor:ttBd,borderWidth:1,titleColor:ttTx,bodyColor:ttTx,callbacks:{label:ctx=>` ${ctx.label}: ${fmtNum(ctx.raw)} (${ctx.parsed.toFixed ? ctx.parsed.toFixed(1) : ctx.raw}%)`}}}}
  });

  const snapR = await fetch('/api/snapshots');
  const allSnaps = await snapR.json();
  if(trendChart) trendChart.destroy();
  trendChart = new Chart(document.getElementById('chart-trend'),{
    type:'line',
    data:{labels:allSnaps.map(s=>s.fetch_date).reverse(),
      datasets:[{data:allSnaps.map(s=>s.total_count).reverse(),borderColor:'#4ade80',
        backgroundColor:'rgba(74,222,128,.08)',fill:true,tension:.3,pointRadius:4,
        pointBackgroundColor:'#4ade80',pointBorderColor:cs.getPropertyValue('--bg').trim(),pointBorderWidth:1.5}]},
    options:{plugins:{legend:{display:false},tooltip:{backgroundColor:ttBg,borderColor:ttBd,borderWidth:1,titleColor:ttTx,bodyColor:ttTx,callbacks:{label:ctx=>' '+fmtNum(ctx.raw)}}},
      scales:{x:{ticks:tickStyle,grid:{color:grid}},
              y:{ticks:{...tickStyle,callback:v=>fmtCompact(v)},grid:{color:grid}}}}
  });
}

// ── Time Series ────────────────────────────────────────────────────────────

async function updateRangeMax() {
  if (!activeSnap) return;
  const r = await fetch(`/api/installs/${activeSnap.id}/max_count`);
  const { max_count } = await r.json();
  tsGlobalMax = max_count || 1000000;
  const minEl = document.getElementById('range-min');
  const maxEl = document.getElementById('range-max');
  minEl.max = tsGlobalMax; minEl.step = Math.max(1, Math.floor(tsGlobalMax/1000));
  maxEl.max = tsGlobalMax; maxEl.step = Math.max(1, Math.floor(tsGlobalMax/1000));
  maxEl.value = tsGlobalMax;
  tsMinCount = 0; tsMaxCount = Infinity;
  document.getElementById('range-min-val').textContent  = '0';
  document.getElementById('range-max-val').textContent  = '∞';
  document.getElementById('range-max-label').textContent  = fmtCompact(tsGlobalMax);
  document.getElementById('range-max-label2').textContent = fmtCompact(tsGlobalMax);
}

function onRangeChange() {
  const minV = parseInt(document.getElementById('range-min').value);
  const maxV = parseInt(document.getElementById('range-max').value);
  tsMinCount = minV;
  tsMaxCount = maxV >= tsGlobalMax ? Infinity : maxV;
  document.getElementById('range-min-val').textContent = fmtCompact(minV);
  document.getElementById('range-max-val').textContent = tsMaxCount === Infinity ? '∞' : fmtCompact(maxV);
  renderTsChart();
}

function clearCountFilter() {
  document.getElementById('range-min').value = 0;
  document.getElementById('range-max').value = tsGlobalMax;
  tsMinCount = 0; tsMaxCount = Infinity;
  document.getElementById('range-min-val').textContent = '0';
  document.getElementById('range-max-val').textContent = '∞';
  renderTsChart();
}

async function applyPreset(btn) {
  document.querySelectorAll('.preset-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  tsPreset = btn.dataset.preset;
  const customRow = document.getElementById('custom-formula-row');
  customRow.style.display = tsPreset === 'custom' ? 'block' : 'none';
  if (tsPreset !== 'custom') await refreshTimeSeries();
}

async function refreshTimeSeries() {
  if (tsPreset === 'custom') { await fetchTsData(tsFormulae); return; }
  // resolve preset -> formula names from top formulae list
  const period = document.getElementById('ts-period').value;
  let source = tsAllFormulae;
  if (period) source = source.filter(f => true); // server filters by period; use rank list as-is

  const ranges = { top10:[0,9], next10:[10,19], next20:[20,29], next30:[30,39] };
  const [from, to] = ranges[tsPreset] || [0,9];
  const names = source.slice(from, to+1).map(f=>f.formula);
  tsFormulae = names;
  await fetchTsData(names);
}

async function fetchTsData(formulae) {
  if (!formulae.length) { renderTsEmpty(); return; }
  const period = document.getElementById('ts-period').value;
  const params = formulae.map(f=>`formula=${encodeURIComponent(f)}`).join('&')
                 + (period ? `&period_days=${period}` : '');
  const r    = await fetch(`/api/timeseries?${params}`);
  tsData     = await r.json();
  renderTsChart();
  renderTsRawTable();
}

function renderTsEmpty() {
  document.getElementById('ts-empty').style.display = 'block';
  document.getElementById('ts-chart').style.display = 'none';
  document.getElementById('ts-raw-table').innerHTML = '';
}

function renderTsChart() {
  if (!tsData || !tsData.snapshots.length) { renderTsEmpty(); return; }
  document.getElementById('ts-empty').style.display = 'none';
  document.getElementById('ts-chart').style.display = 'block';

  const snaps    = tsData.snapshots;
  const labels   = snaps.map(s=>s.fetch_date);
  const datasets = [];
  const formulae = Object.keys(tsData.series);

  const minC = tsMinCount;
  const maxC = tsMaxCount === Infinity ? null : tsMaxCount;

  formulae.forEach((formula, i) => {
    const pts = tsData.series[formula];
    const data = pts.map(p => {
      if (p.count === null) return null;
      if (p.count < minC)  return null;
      if (maxC !== null && p.count > maxC) return null;
      return p.count;
    });
    // skip if all null after filter
    if (data.every(v=>v===null)) return;
    datasets.push({
      label: formula,
      data,
      borderColor: PALETTE[i % PALETTE.length],
      backgroundColor: PALETTE[i % PALETTE.length] + '22',
      fill: false,
      tension: 0.3,
      spanGaps: true,
      pointRadius: 5,
      pointHoverRadius: 8,
      pointBackgroundColor: PALETTE[i % PALETTE.length],
      pointBorderColor: '#0d0f11',
      pointBorderWidth: 1.5,
      pointHoverBorderWidth: 2,
    });
  });

  const presetLabels = {top10:'top 1–10',next10:'top 11–20',next20:'top 21–30',next30:'top 31–40',custom:'custom'};
  document.getElementById('ts-chart-title').textContent =
    `install counts — ${presetLabels[tsPreset]||tsPreset}`
    + (minC>0||maxC!==null ? ` · filtered ${fmtCompact(minC)}–${maxC?fmtCompact(maxC):'∞'}` : '');

  const mono = "'IBM Plex Mono'";
  const cs2 = getComputedStyle(document.documentElement);
  const grid2 = cs2.getPropertyValue('--chart-grid').trim();
  const tick2 = cs2.getPropertyValue('--chart-tick').trim();
  const ttBg2 = cs2.getPropertyValue('--tooltip-bg').trim();
  const ttBd2 = cs2.getPropertyValue('--tooltip-border').trim();
  const ttTx2 = cs2.getPropertyValue('--tooltip-text').trim();
  const txtCol = cs2.getPropertyValue('--text').trim();
  const tickStyle = { color:tick2, font:{family:mono,size:10} };

  if (tsChart) tsChart.destroy();
  tsChart = new Chart(document.getElementById('ts-chart'), {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true,
      interaction: { mode:'index', intersect:false },
      plugins: {
        legend: {
          position: 'bottom',
          labels: { color:txtCol, font:{family:mono,size:10}, boxWidth:12, padding:14 }
        },
        tooltip: {
          backgroundColor: ttBg2,
          borderColor: ttBd2,
          borderWidth: 1,
          titleColor: ttTx2,
          bodyColor: ttTx2,
          titleFont: { family:mono, size:11 },
          bodyFont:  { family:mono, size:11 },
          padding: 10,
          callbacks: {
            title: items => {
              const snap = snaps[items[0].dataIndex];
              return `${snap.fetch_date}  (${snap.start_date} → ${snap.end_date})`;
            },
            label: ctx => {
              if (ctx.raw === null) return ` ${ctx.dataset.label}: —`;
              const snap = snaps[ctx.dataIndex];
              const pts  = tsData.series[ctx.dataset.label];
              const pt   = pts ? pts[ctx.dataIndex] : null;
              const rank = pt && pt.rank ? `  rank #${pt.rank}` : '';
              const pct  = pt && pt.percent ? `  (${pt.percent.toFixed(2)}%)` : '';
              return ` ${ctx.dataset.label}: ${fmtNum(ctx.raw)}${pct}${rank}`;
            }
          }
        }
      },
      scales: {
        x: { ticks:tickStyle, grid:{color:grid2} },
        y: {
          ticks: { ...tickStyle, callback: v=>fmtCompact(v) },
          grid:  { color:grid2 }
        }
      }
    }
  });
}

function renderTsRawTable() {
  if (!tsData || !tsData.snapshots.length) {
    document.getElementById('ts-raw-table').innerHTML = '<div class="empty"><span>◈</span>No data.</div>';
    return;
  }
  const snaps    = tsData.snapshots;
  const formulae = Object.keys(tsData.series);
  const minC = tsMinCount, maxC = tsMaxCount === Infinity ? null : tsMaxCount;

  document.getElementById('ts-raw-info').textContent =
    `${formulae.length} formulae × ${snaps.length} snapshots`;

  const hdr = `<th>Formula</th>` + snaps.map(s=>`<th>${s.fetch_date}<br><span style="color:var(--muted);font-size:.6rem">${s.period_days}d</span></th>`).join('');
  const body = formulae.map(f => {
    const pts = tsData.series[f];
    const cells = pts.map(p => {
      if (p.count === null) return `<td style="color:var(--muted)">—</td>`;
      const filtered = p.count < minC || (maxC !== null && p.count > maxC);
      const style = filtered ? 'style="opacity:.3"' : '';
      return `<td ${style}><span style="color:var(--accent2)">${fmtNum(p.count)}</span><br><span style="color:var(--muted);font-size:.6rem">#${p.rank||'?'}</span></td>`;
    });
    return `<tr><td><span class="formula-name">${f}</span></td>${cells.join('')}</tr>`;
  }).join('');

  document.getElementById('ts-raw-table').innerHTML = `
    <table>
      <thead><tr>${hdr}</tr></thead>
      <tbody>${body}</tbody>
    </table>`;
}

function resetTimeSeries() {
  document.querySelectorAll('.preset-btn').forEach(b=>b.classList.remove('active'));
  document.querySelector('.preset-btn[data-preset="top10"]').classList.add('active');
  tsPreset = 'top10';
  document.getElementById('ts-period').value = '';
  document.getElementById('custom-formula-row').style.display = 'none';
  clearCountFilter();
  refreshTimeSeries();
}

function exportTsData(fmt) {
  if (!tsData) return;
  const snaps    = tsData.snapshots;
  const formulae = Object.keys(tsData.series);
  const rows = [];
  formulae.forEach(f => {
    tsData.series[f].forEach(p => {
      rows.push({ formula:f, fetch_date:p.fetch_date, start_date:p.start_date,
                  end_date:p.end_date, period_days:p.period_days,
                  count:p.count, rank:p.rank, percent:p.percent });
    });
  });
  if (fmt==='json') {
    dl('ts_export.json', JSON.stringify(rows, null, 2), 'application/json');
  } else {
    const keys = ['formula','fetch_date','start_date','end_date','period_days','count','rank','percent'];
    const csv  = [keys.join(','), ...rows.map(r=>keys.map(k=>r[k]??'').join(','))].join('\n');
    dl('ts_export.csv', csv, 'text/csv');
  }
}
function dl(name, content, mime) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([content],{type:mime}));
  a.download = name; a.click();
}

// ── Formula autocomplete ───────────────────────────────────────────────────
function onFormulaInput() {
  const val = document.getElementById('formula-input').value.trim().toLowerCase();
  const list = document.getElementById('ac-list');
  acIndex = -1;
  if (!val) { list.classList.remove('open'); return; }
  const matches = tsAllFormulae.filter(f=>f.formula.includes(val) && !tsFormulae.includes(f.formula)).slice(0,12);
  if (!matches.length) { list.classList.remove('open'); return; }
  list.innerHTML = matches.map((f,i)=>`<div class="ac-item" data-formula="${f.formula}" onmousedown="addFormula('${f.formula}')">${f.formula}</div>`).join('');
  list.classList.add('open');
}

function onFormulaKeydown(e) {
  const list  = document.getElementById('ac-list');
  const items = list.querySelectorAll('.ac-item');
  if (e.key==='ArrowDown')  { acIndex=Math.min(acIndex+1,items.length-1); highlightAc(items); e.preventDefault(); }
  else if(e.key==='ArrowUp'){ acIndex=Math.max(acIndex-1,-1); highlightAc(items); e.preventDefault(); }
  else if(e.key==='Enter')  {
    if (acIndex>=0 && items[acIndex]) addFormula(items[acIndex].dataset.formula);
    else { const v=document.getElementById('formula-input').value.trim(); if(v) addFormula(v); }
    e.preventDefault();
  } else if(e.key==='Escape') { list.classList.remove('open'); }
}
function highlightAc(items) {
  items.forEach((el,i)=>el.classList.toggle('selected',i===acIndex));
  if(acIndex>=0) items[acIndex].scrollIntoView({block:'nearest'});
}

function addFormula(name) {
  if (!tsFormulae.includes(name)) {
    tsFormulae.push(name);
    renderFormulaTags();
    fetchTsData(tsFormulae);
  }
  document.getElementById('formula-input').value = '';
  document.getElementById('ac-list').classList.remove('open');
}
function removeFormula(name) {
  tsFormulae = tsFormulae.filter(f=>f!==name);
  renderFormulaTags();
  fetchTsData(tsFormulae);
}
function renderFormulaTags() {
  document.getElementById('formula-tags').innerHTML =
    tsFormulae.map(f=>`<span class="tag">${f}<span class="remove" onclick="removeFormula('${f}')">✕</span></span>`).join('');
}

// ── Compare ────────────────────────────────────────────────────────────────
function populateCompareSelects() {
  ['cmp-a','cmp-b'].forEach((id,i)=>{
    const el = document.getElementById(id);
    el.innerHTML = snapshots.map(s=>`<option value="${s.id}">#${s.id} ${s.fetch_date} (${s.period_days}d)</option>`).join('');
    if (snapshots[i]) el.value = snapshots[i].id;
  });
}

async function runCompare() {
  const idA = parseInt(document.getElementById('cmp-a').value);
  const idB = parseInt(document.getElementById('cmp-b').value);
  if (!idA || !idB || idA===idB) return;
  const [rA,rB] = await Promise.all([
    fetch(`/api/installs/${idA}?limit=500`).then(r=>r.json()),
    fetch(`/api/installs/${idB}?limit=500`).then(r=>r.json()),
  ]);
  const mapA = Object.fromEntries(rA.rows.map(r=>[r.formula,r]));
  const mapB = Object.fromEntries(rB.rows.map(r=>[r.formula,r]));
  const all  = new Set([...Object.keys(mapA),...Object.keys(mapB)]);
  compareData = [...all].map(f=>{
    const a=mapA[f], b=mapB[f];
    const rankDiff = (a&&b) ? a.rank-b.rank : null;
    const cntDiff  = (a&&b) ? b.count-a.count : null;
    return {formula:f,rankA:a?.rank,rankB:b?.rank,countA:a?.count,countB:b?.count,rankDiff,cntDiff,
            status:!a?'new':!b?'gone':rankDiff>0?'up':rankDiff<0?'down':'same'};
  }).sort((a,b)=>(a.rankB||a.rankA||999)-(b.rankB||b.rankA||999));
  renderCompareTable();
}

function renderCompareTable() {
  if (!compareData) return;
  const filt  = document.getElementById('cmp-filter').value;
  const rows  = filt==='all' ? compareData : compareData.filter(r=>r.status===filt);
  const el    = document.getElementById('cmp-container');
  if (!rows.length) { el.innerHTML='<div class="empty"><span>◈</span>No rows match filter.</div>'; return; }
  el.innerHTML = `<table>
    <thead><tr><th>Formula</th><th>Rank A</th><th>Rank B</th><th>Δ Rank</th><th>Count A</th><th>Count B</th><th>Δ Count</th></tr></thead>
    <tbody>${rows.map(r=>{
      const dc = r.rankDiff===null?'':r.rankDiff>0?'diff-up':r.rankDiff<0?'diff-down':'';
      const arr= r.rankDiff===null?'':r.rankDiff>0?'↑':r.rankDiff<0?'↓':'→';
      const st = r.status==='new'?'diff-new':r.status==='gone'?'diff-gone':'';
      return `<tr>
        <td><span class="formula-name ${st}">${r.formula}</span></td>
        <td>${r.rankA??'—'}</td><td>${r.rankB??'—'}</td>
        <td class="${dc}">${arr}${r.rankDiff!==null?Math.abs(r.rankDiff):''}</td>
        <td>${r.countA!=null?fmtNum(r.countA):'—'}</td>
        <td>${r.countB!=null?fmtNum(r.countB):'—'}</td>
        <td class="${r.cntDiff>0?'diff-up':r.cntDiff<0?'diff-down':''}">${r.cntDiff!=null?(r.cntDiff>0?'+':'')+fmtNum(r.cntDiff):'—'}</td>
      </tr>`;
    }).join('')}</tbody></table>`;
}

// ── Fetch ──────────────────────────────────────────────────────────────────
function selectDays(btn) {
  document.querySelectorAll('.days-btn').forEach(b=>b.classList.remove('selected'));
  btn.classList.add('selected');
}
async function triggerFetch() {
  const days  = parseInt(document.querySelector('.days-btn.selected').dataset.days);
  const force = document.getElementById('force-cb').checked;
  const log   = document.getElementById('fetch-log');
  const btn   = document.getElementById('fetch-btn');
  log.textContent = '⟳ fetching…'; log.className='log-box';
  btn.disabled = true;
  try {
    const r    = await fetch('/api/fetch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({days,force})});
    const data = await r.json();
    log.textContent = data.message;
    log.className   = 'log-box ' + (data.ok?'log-ok':'log-err');
    if (data.ok) await loadSnapshots();
  } catch(e) {
    log.textContent = `Error: ${e}`; log.className='log-box log-err';
  }
  btn.disabled = false;
}

// ── Helpers ────────────────────────────────────────────────────────────────
function fmtNum(n)     { return n!=null ? Number(n).toLocaleString() : '—'; }
function fmtCompact(n) {
  if (n>=1e6)  return (n/1e6).toFixed(1)+'M';
  if (n>=1000) return (n/1000).toFixed(0)+'K';
  return String(n);
}

// ── Theme ──────────────────────────────────────────────────────────────────
function applyTheme(light) {
  document.documentElement.classList.toggle('light', light);
  document.getElementById('theme-btn').textContent = light ? '🌑' : '🌙';
  // Redraw any active charts so they pick up the new CSS var colours
  const cs = getComputedStyle(document.documentElement);
  const grid = cs.getPropertyValue('--chart-grid').trim();
  const tick = cs.getPropertyValue('--chart-tick').trim();
  [barChart, pieChart, trendChart, tsChart].forEach(c => {
    if (!c) return;
    if (c.config.type !== 'doughnut') {
      ['x','y'].forEach(ax => {
        if (c.options.scales[ax]) {
          c.options.scales[ax].grid.color = grid;
          c.options.scales[ax].ticks.color = tick;
        }
      });
    }
    if (c.options.plugins.tooltip) {
      c.options.plugins.tooltip.backgroundColor = cs.getPropertyValue('--tooltip-bg').trim();
      c.options.plugins.tooltip.borderColor      = cs.getPropertyValue('--tooltip-border').trim();
      c.options.plugins.tooltip.titleColor       = cs.getPropertyValue('--tooltip-text').trim();
      c.options.plugins.tooltip.bodyColor        = cs.getPropertyValue('--tooltip-text').trim();
    }
    if (c.options.plugins.legend && c.options.plugins.legend.labels) {
      c.options.plugins.legend.labels.color = cs.getPropertyValue('--text').trim();
    }
    c.update();
  });
}
function toggleTheme() {
  const isLight = !document.documentElement.classList.contains('light');
  localStorage.setItem('brew-theme', isLight ? 'light' : 'dark');
  applyTheme(isLight);
}
function initTheme() {
  const saved = localStorage.getItem('brew-theme');
  const preferLight = saved ? saved === 'light' : window.matchMedia('(prefers-color-scheme: light)').matches;
  applyTheme(preferLight);
}

// ── Boot ───────────────────────────────────────────────────────────────────
initTheme();
init();
</script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    global DB_PATH
    parser = argparse.ArgumentParser(description="Brew Analytics web UI")
    parser.add_argument("--db",   default=DEFAULT_DB, help="SQLite DB path")
    parser.add_argument("--port", default=5000, type=int)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    DB_PATH = args.db
    with get_conn() as conn:
        init_db(conn)
    print(f"⬡ Brew Analytics  →  http://{args.host}:{args.port}  (db: {Path(DB_PATH).resolve()})")
    app.run(host=args.host, port=args.port, debug=args.debug)

if __name__ == "__main__":
    main()
