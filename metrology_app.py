#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
半导体量测数据平台 Demo - 单文件可运行版
Metrology Data Platform Demo

运行方式：
1. 安装 Python 3.9+
2. 在命令行进入本文件所在目录
3. 执行：python metrology_app.py
4. 浏览器打开：http://127.0.0.1:8000
5. 默认账号：admin
6. 默认密码：admin123

说明：
- 这是一个内部演示版 Demo，不依赖 Flask/Django/FastAPI。
- 使用 Python 标准库实现：HTTP Server + SQLite + Cookie Session。
- 适合先演示登录、量测数据查询、SPC 趋势、异常告警、CSV 导入。
- 正式产线使用前，需要接入 AD/LDAP、HTTPS、权限细化、MES 接口和设备采集服务。
"""

import csv
import html
import io
import os
import secrets
import sqlite3
import statistics
from datetime import datetime, timedelta
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

APP_TITLE = "半导体量测数据平台 Demo"
DB_FILE = "metrology_demo.db"
HOST = "127.0.0.1"
PORT = 8000

SESSIONS = {}


# -----------------------------
# Database
# -----------------------------

def get_conn():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        real_name TEXT,
        role TEXT,
        department TEXT,
        status TEXT DEFAULT 'active',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS measurement_result (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        measurement_id TEXT UNIQUE NOT NULL,

        lot_id TEXT NOT NULL,
        wafer_id TEXT,
        slot_no INTEGER,

        product_id TEXT,
        operation TEXT,
        layer_name TEXT,

        tool_id TEXT,
        recipe_id TEXT,

        measurement_item TEXT NOT NULL,
        site_id TEXT,
        site_x REAL,
        site_y REAL,

        value REAL,
        unit TEXT,

        target REAL,
        lsl REAL,
        usl REAL,
        lcl REAL,
        ucl REAL,

        result_status TEXT,
        measure_time TEXT,
        collect_time TEXT DEFAULT CURRENT_TIMESTAMP,
        source_type TEXT,
        raw_file_path TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS alarm_record (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        alarm_id TEXT UNIQUE NOT NULL,
        alarm_type TEXT,
        severity TEXT,

        lot_id TEXT,
        wafer_id TEXT,
        tool_id TEXT,
        measurement_item TEXT,

        alarm_message TEXT,
        alarm_status TEXT DEFAULT 'New',
        owner TEXT,
        comment TEXT,

        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS login_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        login_time TEXT DEFAULT CURRENT_TIMESTAMP,
        ip_address TEXT,
        user_agent TEXT,
        login_status TEXT
    )
    """)

    # default admin
    cur.execute("SELECT COUNT(*) AS c FROM users")
    if cur.fetchone()["c"] == 0:
        cur.execute("""
        INSERT INTO users (username, password_hash, real_name, role, department)
        VALUES (?, ?, ?, ?, ?)
        """, ("admin", hash_password("admin123"), "系统管理员", "admin", "Metrology"))

    # demo measurement data
    cur.execute("SELECT COUNT(*) AS c FROM measurement_result")
    if cur.fetchone()["c"] == 0:
        seed_demo_measurements(cur)

    conn.commit()
    conn.close()


def hash_password(password: str) -> str:
    # Demo only. 正式系统请改用 bcrypt / argon2。
    import hashlib
    return hashlib.sha256(("metrology_salt_" + password).encode("utf-8")).hexdigest()


def seed_demo_measurements(cur):
    now = datetime.now()
    products = ["PROD_A", "PROD_B"]
    layers = ["M1", "VIA1", "POLY"]
    operations = ["PHOTO_CD_MEAS", "OVERLAY_MEAS", "FILM_THK_MEAS"]
    tools = ["CDSEM01", "OVERLAY01", "THK01"]
    items = [
        ("CD_TOP", "nm", 45.0, 42.0, 48.0, 43.0, 47.0),
        ("OVL_X", "nm", 0.0, -5.0, 5.0, -3.0, 3.0),
        ("THK_OX", "A", 1000.0, 960.0, 1040.0, 970.0, 1030.0),
    ]

    idx = 1
    for day in range(20):
        for lot_num in range(1, 4):
            lot_id = f"L{(now - timedelta(days=20-day)).strftime('%m%d')}{lot_num:03d}"
            product = products[(day + lot_num) % len(products)]
            layer = layers[(day + lot_num) % len(layers)]
            for wafer in range(1, 6):
                for item_idx, (item, unit, target, lsl, usl, lcl, ucl) in enumerate(items):
                    operation = operations[item_idx]
                    tool = tools[item_idx]
                    recipe = f"{layer}_{item}_RCP"

                    # make deterministic-ish values
                    drift = (day - 10) * 0.03
                    wafer_effect = (wafer - 3) * 0.08
                    if item == "CD_TOP":
                        value = target + drift + wafer_effect
                    elif item == "OVL_X":
                        value = target + (day - 10) * 0.08 + (wafer - 3) * 0.15
                    else:
                        value = target + (day - 10) * 1.2 + (wafer - 3) * 2.0

                    # inject a few abnormal values
                    if day == 16 and lot_num == 2 and wafer == 4 and item == "CD_TOP":
                        value = 48.6
                    if day == 17 and lot_num == 1 and wafer == 2 and item == "OVL_X":
                        value = 3.6
                    if day == 18 and lot_num == 3 and wafer == 5 and item == "THK_OX":
                        value = 1048

                    status = judge_status(value, lsl, usl, lcl, ucl)
                    measure_time = (now - timedelta(days=20-day, hours=lot_num, minutes=wafer * 3)).strftime("%Y-%m-%d %H:%M:%S")

                    cur.execute("""
                    INSERT INTO measurement_result (
                        measurement_id, lot_id, wafer_id, slot_no,
                        product_id, operation, layer_name,
                        tool_id, recipe_id,
                        measurement_item, site_id, site_x, site_y,
                        value, unit, target, lsl, usl, lcl, ucl,
                        result_status, measure_time, source_type
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        f"M{idx:08d}", lot_id, f"W{wafer:02d}", wafer,
                        product, operation, layer,
                        tool, recipe,
                        item, "CENTER", 0, 0,
                        round(value, 4), unit, target, lsl, usl, lcl, ucl,
                        status, measure_time, "DEMO"
                    ))

                    if status in ("OOC", "OOS"):
                        alarm_type = status
                        severity = "High" if status == "OOS" else "Medium"
                        cur.execute("""
                        INSERT INTO alarm_record (
                            alarm_id, alarm_type, severity, lot_id, wafer_id, tool_id,
                            measurement_item, alarm_message, alarm_status, created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            f"A{idx:08d}", alarm_type, severity, lot_id, f"W{wafer:02d}",
                            tool, item, f"{item} value {round(value, 4)} triggered {status}", "New",
                            measure_time
                        ))

                    idx += 1


def judge_status(value, lsl, usl, lcl, ucl):
    if value is None:
        return "NA"
    if lsl is not None and value < lsl:
        return "OOS"
    if usl is not None and value > usl:
        return "OOS"
    if lcl is not None and value < lcl:
        return "OOC"
    if ucl is not None and value > ucl:
        return "OOC"
    return "PASS"


# -----------------------------
# HTML helpers
# -----------------------------

def e(s):
    return html.escape("" if s is None else str(s))


def base_layout(title, body, user=None):
    if user:
        nav = f"""
        <div class="topbar">
            <div class="brand">{APP_TITLE}</div>
            <div class="user">当前用户：{e(user['real_name'] or user['username'])} ｜ <a href="/logout">退出</a></div>
        </div>
        <div class="layout">
            <aside class="sidebar">
                <a href="/">数据总览</a>
                <a href="/lots">Lot 查询</a>
                <a href="/spc">SPC 趋势</a>
                <a href="/alarms">异常告警</a>
                <a href="/tools">设备状态</a>
                <a href="/upload">CSV 导入</a>
                <a href="/about">系统说明</a>
            </aside>
            <main class="content">{body}</main>
        </div>
        """
    else:
        nav = body

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<style>
:root {{
  --bg: #f5f7fb;
  --card: #ffffff;
  --text: #1f2937;
  --muted: #6b7280;
  --line: #e5e7eb;
  --primary: #2563eb;
  --primary2: #1d4ed8;
  --green: #16a34a;
  --yellow: #ca8a04;
  --red: #dc2626;
  --orange: #ea580c;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
  background: var(--bg);
  color: var(--text);
}}
a {{ color: var(--primary); text-decoration: none; }}
.topbar {{
  height: 56px;
  background: #111827;
  color: white;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 22px;
}}
.topbar a {{ color: #bfdbfe; }}
.brand {{ font-weight: 700; }}
.user {{ font-size: 14px; color: #e5e7eb; }}
.layout {{ display: flex; min-height: calc(100vh - 56px); }}
.sidebar {{
  width: 210px;
  background: white;
  border-right: 1px solid var(--line);
  padding: 16px 10px;
}}
.sidebar a {{
  display: block;
  padding: 11px 14px;
  border-radius: 10px;
  color: #374151;
  margin-bottom: 6px;
}}
.sidebar a:hover {{ background: #eff6ff; color: var(--primary2); }}
.content {{
  flex: 1;
  padding: 24px;
  overflow: auto;
}}
.card {{
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 16px;
  padding: 18px;
  box-shadow: 0 2px 10px rgba(15,23,42,0.04);
  margin-bottom: 18px;
}}
.grid {{ display: grid; grid-template-columns: repeat(4, minmax(160px, 1fr)); gap: 16px; }}
.metric .label {{ color: var(--muted); font-size: 13px; }}
.metric .value {{ font-size: 28px; font-weight: 800; margin-top: 8px; }}
h1 {{ margin: 0 0 18px; font-size: 24px; }}
h2 {{ margin: 0 0 12px; font-size: 18px; }}
table {{
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
}}
th, td {{
  border-bottom: 1px solid var(--line);
  padding: 10px 8px;
  text-align: left;
  white-space: nowrap;
}}
th {{ color: #374151; background: #f9fafb; }}
.badge {{
  display: inline-block;
  padding: 3px 8px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
}}
.PASS {{ background: #dcfce7; color: #166534; }}
.OOC {{ background: #ffedd5; color: #9a3412; }}
.OOS {{ background: #fee2e2; color: #991b1b; }}
.New {{ background: #dbeafe; color: #1e40af; }}
.form-row {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 14px; }}
input, select, textarea {{
  border: 1px solid #d1d5db;
  padding: 9px 10px;
  border-radius: 10px;
  min-width: 180px;
}}
button, .btn {{
  background: var(--primary);
  color: white;
  border: none;
  padding: 9px 14px;
  border-radius: 10px;
  cursor: pointer;
  font-weight: 700;
}}
button:hover, .btn:hover {{ background: var(--primary2); }}
.login-wrap {{
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #0f172a, #1d4ed8);
}}
.login-card {{
  width: 380px;
  background: white;
  border-radius: 18px;
  padding: 28px;
  box-shadow: 0 18px 60px rgba(0,0,0,0.25);
}}
.login-card h1 {{ margin-bottom: 8px; }}
.login-card p {{ color: var(--muted); margin-top: 0; }}
.login-card input {{ width: 100%; margin: 8px 0; }}
.login-card button {{ width: 100%; margin-top: 10px; }}
.error {{ color: var(--red); font-size: 14px; }}
.success {{ color: var(--green); font-size: 14px; }}
.note {{ color: var(--muted); font-size: 13px; line-height: 1.7; }}
.chartbox {{ overflow-x: auto; }}
svg {{ background: white; border: 1px solid var(--line); border-radius: 12px; }}
pre {{
  background: #0b1020;
  color: #e5e7eb;
  padding: 14px;
  border-radius: 12px;
  overflow: auto;
}}
@media (max-width: 900px) {{
  .grid {{ grid-template-columns: repeat(2, 1fr); }}
  .layout {{ flex-direction: column; }}
  .sidebar {{ width: 100%; display: flex; overflow-x: auto; }}
  .sidebar a {{ white-space: nowrap; }}
}}
</style>
</head>
<body>{nav}</body>
</html>"""


def redirect(location):
    return 302, {"Location": location}, b""


def render_status_badge(status):
    return f'<span class="badge {e(status)}">{e(status)}</span>'


# -----------------------------
# Auth
# -----------------------------

def parse_cookie(header):
    jar = cookies.SimpleCookie()
    if header:
        jar.load(header)
    return {k: morsel.value for k, morsel in jar.items()}


def current_user(handler):
    c = parse_cookie(handler.headers.get("Cookie"))
    sid = c.get("sid")
    if not sid or sid not in SESSIONS:
        return None
    user_id = SESSIONS[sid]["user_id"]
    conn = get_conn()
    user = conn.execute("SELECT * FROM users WHERE id=? AND status='active'", (user_id,)).fetchone()
    conn.close()
    return user


def require_login(handler):
    user = current_user(handler)
    if not user:
        return None
    return user


# -----------------------------
# Pages
# -----------------------------

def page_login(error=""):
    body = f"""
    <div class="login-wrap">
      <form class="login-card" method="post" action="/login">
        <h1>{APP_TITLE}</h1>
        <p>Metrology Data Platform</p>
        {"<div class='error'>" + e(error) + "</div>" if error else ""}
        <input name="username" placeholder="用户名" autocomplete="username" required>
        <input name="password" type="password" placeholder="密码" autocomplete="current-password" required>
        <button type="submit">登录</button>
        <div class="note" style="margin-top:14px;">
          默认账号：admin<br>
          默认密码：admin123<br>
          仅用于本地 Demo，正式系统请接公司 AD/LDAP。
        </div>
      </form>
    </div>
    """
    return base_layout("登录", body)


def page_dashboard(user):
    conn = get_conn()
    today = datetime.now().strftime("%Y-%m-%d")
    total_lots = conn.execute("SELECT COUNT(DISTINCT lot_id) AS c FROM measurement_result").fetchone()["c"]
    today_lots = conn.execute("SELECT COUNT(DISTINCT lot_id) AS c FROM measurement_result WHERE measure_time LIKE ?", (today + "%",)).fetchone()["c"]
    total_wafers = conn.execute("SELECT COUNT(DISTINCT lot_id || '-' || wafer_id) AS c FROM measurement_result").fetchone()["c"]
    ooc_count = conn.execute("SELECT COUNT(*) AS c FROM measurement_result WHERE result_status='OOC'").fetchone()["c"]
    oos_count = conn.execute("SELECT COUNT(*) AS c FROM measurement_result WHERE result_status='OOS'").fetchone()["c"]
    alarm_count = conn.execute("SELECT COUNT(*) AS c FROM alarm_record WHERE alarm_status='New'").fetchone()["c"]

    top_items = conn.execute("""
        SELECT measurement_item, result_status, COUNT(*) AS c
        FROM measurement_result
        WHERE result_status IN ('OOC','OOS')
        GROUP BY measurement_item, result_status
        ORDER BY c DESC
        LIMIT 8
    """).fetchall()

    recent = conn.execute("""
        SELECT lot_id, wafer_id, measurement_item, value, unit, tool_id, result_status, measure_time
        FROM measurement_result
        ORDER BY measure_time DESC
        LIMIT 12
    """).fetchall()
    conn.close()

    top_rows = "".join(
        f"<tr><td>{e(r['measurement_item'])}</td><td>{render_status_badge(r['result_status'])}</td><td>{r['c']}</td></tr>"
        for r in top_items
    ) or "<tr><td colspan='3'>暂无异常</td></tr>"

    recent_rows = "".join(
        f"<tr><td>{e(r['measure_time'])}</td><td>{e(r['lot_id'])}</td><td>{e(r['wafer_id'])}</td><td>{e(r['measurement_item'])}</td><td>{e(r['value'])} {e(r['unit'])}</td><td>{e(r['tool_id'])}</td><td>{render_status_badge(r['result_status'])}</td></tr>"
        for r in recent
    )

    body = f"""
    <h1>数据总览</h1>
    <div class="grid">
      <div class="card metric"><div class="label">总 Lot 数</div><div class="value">{total_lots}</div></div>
      <div class="card metric"><div class="label">总 Wafer 数</div><div class="value">{total_wafers}</div></div>
      <div class="card metric"><div class="label">OOC 点数</div><div class="value">{ooc_count}</div></div>
      <div class="card metric"><div class="label">OOS 点数</div><div class="value">{oos_count}</div></div>
    </div>

    <div class="grid">
      <div class="card metric"><div class="label">今日 Lot 数</div><div class="value">{today_lots}</div></div>
      <div class="card metric"><div class="label">待处理告警</div><div class="value">{alarm_count}</div></div>
      <div class="card metric"><div class="label">平台状态</div><div class="value" style="color:#16a34a;">Online</div></div>
      <div class="card metric"><div class="label">数据源</div><div class="value">Demo</div></div>
    </div>

    <div class="card">
      <h2>异常 Top 项</h2>
      <table>
        <tr><th>Measurement Item</th><th>Status</th><th>Count</th></tr>
        {top_rows}
      </table>
    </div>

    <div class="card">
      <h2>最近量测记录</h2>
      <table>
        <tr><th>时间</th><th>Lot</th><th>Wafer</th><th>Item</th><th>Value</th><th>Tool</th><th>Status</th></tr>
        {recent_rows}
      </table>
    </div>
    """
    return base_layout("Dashboard", body, user)


def page_lots(user, query):
    lot_id = query.get("lot_id", [""])[0].strip()
    product_id = query.get("product_id", [""])[0].strip()
    status = query.get("status", [""])[0].strip()

    where = []
    params = []
    if lot_id:
        where.append("lot_id LIKE ?")
        params.append(f"%{lot_id}%")
    if product_id:
        where.append("product_id LIKE ?")
        params.append(f"%{product_id}%")
    if status:
        where.append("result_status = ?")
        params.append(status)

    sql = """
    SELECT lot_id, wafer_id, slot_no, product_id, operation, layer_name, tool_id,
           recipe_id, measurement_item, value, unit, target, lsl, usl, lcl, ucl,
           result_status, measure_time
    FROM measurement_result
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY measure_time DESC LIMIT 300"

    conn = get_conn()
    rows = conn.execute(sql, params).fetchall()
    conn.close()

    row_html = "".join(
        f"""
        <tr>
          <td>{e(r['measure_time'])}</td>
          <td>{e(r['lot_id'])}</td>
          <td>{e(r['wafer_id'])}</td>
          <td>{e(r['product_id'])}</td>
          <td>{e(r['operation'])}</td>
          <td>{e(r['layer_name'])}</td>
          <td>{e(r['measurement_item'])}</td>
          <td>{e(r['value'])} {e(r['unit'])}</td>
          <td>{e(r['tool_id'])}</td>
          <td>{render_status_badge(r['result_status'])}</td>
        </tr>
        """
        for r in rows
    ) or "<tr><td colspan='10'>没有查询到数据</td></tr>"

    body = f"""
    <h1>Lot 查询</h1>
    <div class="card">
      <form method="get" action="/lots" class="form-row">
        <input name="lot_id" placeholder="Lot ID，例如 L0506001" value="{e(lot_id)}">
        <input name="product_id" placeholder="Product，例如 PROD_A" value="{e(product_id)}">
        <select name="status">
          <option value="">全部状态</option>
          <option value="PASS" {"selected" if status=="PASS" else ""}>PASS</option>
          <option value="OOC" {"selected" if status=="OOC" else ""}>OOC</option>
          <option value="OOS" {"selected" if status=="OOS" else ""}>OOS</option>
        </select>
        <button type="submit">查询</button>
        <a class="btn" href="/lots">重置</a>
      </form>
      <div class="note">最多显示 300 条。正式版建议增加分页、导出和按时间范围筛选。</div>
    </div>

    <div class="card">
      <h2>量测结果</h2>
      <table>
        <tr>
          <th>时间</th><th>Lot</th><th>Wafer</th><th>Product</th><th>Operation</th>
          <th>Layer</th><th>Item</th><th>Value</th><th>Tool</th><th>Status</th>
        </tr>
        {row_html}
      </table>
    </div>
    """
    return base_layout("Lot 查询", body, user)


def svg_spc_chart(points, target, lsl, usl, lcl, ucl):
    width, height = 980, 420
    margin_left, margin_right, margin_top, margin_bottom = 70, 30, 30, 70
    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - margin_bottom

    values = [float(p["value"]) for p in points if p["value"] is not None]
    limits = [x for x in [target, lsl, usl, lcl, ucl] if x is not None]
    all_vals = values + [float(x) for x in limits]
    if not all_vals:
        return "<div class='note'>无数据可绘图</div>"

    ymin, ymax = min(all_vals), max(all_vals)
    pad = (ymax - ymin) * 0.15 if ymax != ymin else 1
    ymin -= pad
    ymax += pad

    def x_pos(i):
        if len(points) <= 1:
            return margin_left + plot_w / 2
        return margin_left + plot_w * i / (len(points) - 1)

    def y_pos(v):
        return margin_top + plot_h * (ymax - float(v)) / (ymax - ymin)

    def line_for_limit(v, label, color):
        if v is None:
            return ""
        y = y_pos(v)
        return f"""
        <line x1="{margin_left}" y1="{y:.1f}" x2="{width-margin_right}" y2="{y:.1f}" stroke="{color}" stroke-dasharray="6,4" />
        <text x="{width-margin_right-65}" y="{y-5:.1f}" fill="{color}" font-size="12">{label}: {float(v):.3f}</text>
        """

    poly = " ".join(f"{x_pos(i):.1f},{y_pos(p['value']):.1f}" for i, p in enumerate(points))
    circles = ""
    for i, p in enumerate(points):
        status = p["result_status"]
        color = "#16a34a" if status == "PASS" else "#ea580c" if status == "OOC" else "#dc2626"
        x = x_pos(i)
        y = y_pos(p["value"])
        circles += f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="{color}"><title>{e(p["lot_id"])} {e(p["measure_time"])} value={e(p["value"])} status={e(status)}</title></circle>'

    y_ticks = ""
    for j in range(6):
        v = ymin + (ymax - ymin) * j / 5
        y = y_pos(v)
        y_ticks += f'<line x1="{margin_left-5}" y1="{y:.1f}" x2="{margin_left}" y2="{y:.1f}" stroke="#9ca3af"/><text x="8" y="{y+4:.1f}" font-size="12" fill="#6b7280">{v:.2f}</text>'

    x_labels = ""
    for i, p in enumerate(points):
        if i % max(1, len(points)//8) == 0:
            x = x_pos(i)
            x_labels += f'<text x="{x-35:.1f}" y="{height-30}" font-size="11" fill="#6b7280" transform="rotate(25 {x:.1f},{height-30})">{e(p["lot_id"])}</text>'

    return f"""
    <div class="chartbox">
    <svg width="{width}" height="{height}" viewBox="0 0 {width} {height}">
      <line x1="{margin_left}" y1="{margin_top}" x2="{margin_left}" y2="{height-margin_bottom}" stroke="#374151"/>
      <line x1="{margin_left}" y1="{height-margin_bottom}" x2="{width-margin_right}" y2="{height-margin_bottom}" stroke="#374151"/>
      {y_ticks}
      {line_for_limit(usl, "USL", "#dc2626")}
      {line_for_limit(lsl, "LSL", "#dc2626")}
      {line_for_limit(ucl, "UCL", "#ea580c")}
      {line_for_limit(lcl, "LCL", "#ea580c")}
      {line_for_limit(target, "Target", "#2563eb")}
      <polyline points="{poly}" fill="none" stroke="#111827" stroke-width="2"/>
      {circles}
      {x_labels}
      <text x="{margin_left}" y="20" fill="#111827" font-size="14">SPC Trend</text>
    </svg>
    </div>
    """


def page_spc(user, query):
    item = query.get("item", ["CD_TOP"])[0].strip() or "CD_TOP"
    tool = query.get("tool", [""])[0].strip()

    conn = get_conn()
    items = [r["measurement_item"] for r in conn.execute("SELECT DISTINCT measurement_item FROM measurement_result ORDER BY measurement_item").fetchall()]
    tools = [r["tool_id"] for r in conn.execute("SELECT DISTINCT tool_id FROM measurement_result ORDER BY tool_id").fetchall()]

    where = ["measurement_item=?"]
    params = [item]
    if tool:
        where.append("tool_id=?")
        params.append(tool)

    rows = conn.execute(f"""
        SELECT lot_id, wafer_id, value, unit, target, lsl, usl, lcl, ucl,
               result_status, measure_time, tool_id
        FROM measurement_result
        WHERE {" AND ".join(where)}
        ORDER BY measure_time ASC
        LIMIT 120
    """, params).fetchall()
    conn.close()

    points = [dict(r) for r in rows]
    target = rows[0]["target"] if rows else None
    lsl = rows[0]["lsl"] if rows else None
    usl = rows[0]["usl"] if rows else None
    lcl = rows[0]["lcl"] if rows else None
    ucl = rows[0]["ucl"] if rows else None

    values = [r["value"] for r in rows if r["value"] is not None]
    avg = statistics.mean(values) if values else None
    stdev = statistics.pstdev(values) if len(values) > 1 else 0
    cp = None
    cpk = None
    if values and stdev and lsl is not None and usl is not None:
        cp = (usl - lsl) / (6 * stdev)
        cpk = min((usl - avg) / (3 * stdev), (avg - lsl) / (3 * stdev))

    item_options = "".join(f'<option value="{e(x)}" {"selected" if x==item else ""}>{e(x)}</option>' for x in items)
    tool_options = '<option value="">全部设备</option>' + "".join(f'<option value="{e(x)}" {"selected" if x==tool else ""}>{e(x)}</option>' for x in tools)

    chart = svg_spc_chart(points, target, lsl, usl, lcl, ucl) if points else "<div class='note'>暂无数据</div>"

    rows_html = "".join(
        f"<tr><td>{e(r['measure_time'])}</td><td>{e(r['lot_id'])}</td><td>{e(r['wafer_id'])}</td><td>{e(r['value'])} {e(r['unit'])}</td><td>{e(r['tool_id'])}</td><td>{render_status_badge(r['result_status'])}</td></tr>"
        for r in list(reversed(rows[-30:]))
    )

    body = f"""
    <h1>SPC 趋势</h1>
    <div class="card">
      <form method="get" action="/spc" class="form-row">
        <select name="item">{item_options}</select>
        <select name="tool">{tool_options}</select>
        <button type="submit">生成趋势</button>
      </form>
    </div>

    <div class="grid">
      <div class="card metric"><div class="label">平均值</div><div class="value">{f"{avg:.4f}" if avg is not None else "-"}</div></div>
      <div class="card metric"><div class="label">标准差</div><div class="value">{f"{stdev:.4f}" if stdev is not None else "-"}</div></div>
      <div class="card metric"><div class="label">Cp</div><div class="value">{f"{cp:.3f}" if cp is not None else "-"}</div></div>
      <div class="card metric"><div class="label">Cpk</div><div class="value">{f"{cpk:.3f}" if cpk is not None else "-"}</div></div>
    </div>

    <div class="card">
      <h2>{e(item)} 趋势图</h2>
      {chart}
      <div class="note">蓝色虚线为 Target，橙色为控制限，红色为规格限。绿色点 PASS，橙色点 OOC，红色点 OOS。</div>
    </div>

    <div class="card">
      <h2>最近 30 条数据</h2>
      <table>
        <tr><th>时间</th><th>Lot</th><th>Wafer</th><th>Value</th><th>Tool</th><th>Status</th></tr>
        {rows_html}
      </table>
    </div>
    """
    return base_layout("SPC 趋势", body, user)


def page_alarms(user, query):
    status = query.get("status", [""])[0].strip()
    where = []
    params = []
    if status:
        where.append("alarm_status=?")
        params.append(status)

    sql = """
    SELECT alarm_id, alarm_type, severity, lot_id, wafer_id, tool_id,
           measurement_item, alarm_message, alarm_status, created_at
    FROM alarm_record
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC LIMIT 300"

    conn = get_conn()
    rows = conn.execute(sql, params).fetchall()
    conn.close()

    rows_html = "".join(
        f"""
        <tr>
          <td>{e(r['created_at'])}</td>
          <td>{e(r['alarm_id'])}</td>
          <td>{render_status_badge(r['alarm_type'])}</td>
          <td>{e(r['severity'])}</td>
          <td>{e(r['lot_id'])}</td>
          <td>{e(r['wafer_id'])}</td>
          <td>{e(r['tool_id'])}</td>
          <td>{e(r['measurement_item'])}</td>
          <td>{e(r['alarm_message'])}</td>
          <td>{render_status_badge(r['alarm_status'])}</td>
        </tr>
        """
        for r in rows
    ) or "<tr><td colspan='10'>暂无告警</td></tr>"

    body = f"""
    <h1>异常告警</h1>
    <div class="card">
      <form method="get" action="/alarms" class="form-row">
        <select name="status">
          <option value="">全部状态</option>
          <option value="New" {"selected" if status=="New" else ""}>New</option>
          <option value="Closed" {"selected" if status=="Closed" else ""}>Closed</option>
        </select>
        <button type="submit">筛选</button>
        <a class="btn" href="/alarms">重置</a>
      </form>
    </div>

    <div class="card">
      <h2>告警列表</h2>
      <table>
        <tr>
          <th>时间</th><th>Alarm ID</th><th>类型</th><th>等级</th><th>Lot</th>
          <th>Wafer</th><th>Tool</th><th>Item</th><th>Message</th><th>Status</th>
        </tr>
        {rows_html}
      </table>
    </div>
    """
    return base_layout("异常告警", body, user)


def page_tools(user):
    conn = get_conn()
    rows = conn.execute("""
    SELECT tool_id,
           COUNT(DISTINCT lot_id) AS lot_count,
           COUNT(*) AS point_count,
           SUM(CASE WHEN result_status='OOC' THEN 1 ELSE 0 END) AS ooc_count,
           SUM(CASE WHEN result_status='OOS' THEN 1 ELSE 0 END) AS oos_count,
           MAX(measure_time) AS last_time
    FROM measurement_result
    GROUP BY tool_id
    ORDER BY tool_id
    """).fetchall()
    conn.close()

    rows_html = "".join(
        f"""
        <tr>
          <td>{e(r['tool_id'])}</td>
          <td>{r['lot_count']}</td>
          <td>{r['point_count']}</td>
          <td>{r['ooc_count']}</td>
          <td>{r['oos_count']}</td>
          <td>{e(r['last_time'])}</td>
          <td>{render_status_badge("PASS")}</td>
        </tr>
        """
        for r in rows
    )

    body = f"""
    <h1>设备状态</h1>
    <div class="card">
      <h2>量测设备数据上传状态</h2>
      <table>
        <tr><th>Tool ID</th><th>Lot Count</th><th>Point Count</th><th>OOC</th><th>OOS</th><th>Last Upload</th><th>Status</th></tr>
        {rows_html}
      </table>
      <div class="note">当前为 Demo 逻辑。正式版可接设备心跳、SECS/GEM alarm、EDA 数据流、文件采集延迟。</div>
    </div>
    """
    return base_layout("设备状态", body, user)


def page_upload(user, message=""):
    sample = """measurement_id,lot_id,wafer_id,slot_no,product_id,operation,layer_name,tool_id,recipe_id,measurement_item,site_id,site_x,site_y,value,unit,target,lsl,usl,lcl,ucl,measure_time
MCSV0001,LTEST001,W01,1,PROD_A,PHOTO_CD_MEAS,M1,CDSEM01,M1_CD_RCP,CD_TOP,CENTER,0,0,45.2,nm,45,42,48,43,47,2026-05-06 10:00:00
MCSV0002,LTEST001,W02,2,PROD_A,PHOTO_CD_MEAS,M1,CDSEM01,M1_CD_RCP,CD_TOP,CENTER,0,0,47.5,nm,45,42,48,43,47,2026-05-06 10:03:00
"""
    body = f"""
    <h1>CSV 导入</h1>
    <div class="card">
      {"<div class='success'>" + e(message) + "</div>" if message else ""}
      <p class="note">把 CSV 内容粘贴到下面，点击导入。平台会自动判定 PASS / OOC / OOS。</p>
      <form method="post" action="/upload">
        <textarea name="csv_text" style="width:100%; height:220px;" placeholder="粘贴 CSV 内容">{e(sample)}</textarea>
        <br><br>
        <button type="submit">导入 CSV</button>
      </form>
    </div>
    <div class="card">
      <h2>字段要求</h2>
      <pre>{e(sample.splitlines()[0])}</pre>
    </div>
    """
    return base_layout("CSV 导入", body, user)


def page_about(user):
    body = """
    <h1>系统说明</h1>
    <div class="card">
      <h2>这个 Demo 已包含的能力</h2>
      <ul>
        <li>登录界面：账号 admin / 密码 admin123</li>
        <li>SQLite 数据库自动初始化</li>
        <li>Dashboard 数据总览</li>
        <li>Lot 查询</li>
        <li>SPC 趋势图和 Cp/Cpk 简单计算</li>
        <li>OOC/OOS 告警列表</li>
        <li>设备状态统计</li>
        <li>CSV 粘贴导入</li>
      </ul>
    </div>
    <div class="card">
      <h2>正式产线版建议补强</h2>
      <ul>
        <li>登录接入公司 AD / LDAP / SSO</li>
        <li>前后端分离：Vue + FastAPI / Spring Boot</li>
        <li>数据库升级到 PostgreSQL / ClickHouse</li>
        <li>MES 只读同步 Lot / Wafer / Product / Step / Spec</li>
        <li>设备侧接 CSV Watcher / SECS-GEM / EDA</li>
        <li>SPC 规则版本管理、Limit 版本管理、审计日志</li>
        <li>部署到公司内网服务器，启用 HTTPS 和备份机制</li>
      </ul>
    </div>
    """
    return base_layout("系统说明", body, user)


# -----------------------------
# HTTP handler
# -----------------------------

class AppHandler(BaseHTTPRequestHandler):
    def send_bytes(self, status, headers, data):
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_html(self, html_text, status=200, extra_headers=None):
        data = html_text.encode("utf-8")
        headers = {"Content-Type": "text/html; charset=utf-8"}
        if extra_headers:
            headers.update(extra_headers)
        self.send_bytes(status, headers, data)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/login":
            self.send_html(page_login())
            return

        if path == "/logout":
            c = parse_cookie(self.headers.get("Cookie"))
            sid = c.get("sid")
            if sid in SESSIONS:
                del SESSIONS[sid]
            status, headers, data = redirect("/login")
            headers["Set-Cookie"] = "sid=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"
            self.send_bytes(status, headers, data)
            return

        user = require_login(self)
        if not user:
            status, headers, data = redirect("/login")
            self.send_bytes(status, headers, data)
            return

        if path == "/":
            self.send_html(page_dashboard(user))
        elif path == "/lots":
            self.send_html(page_lots(user, query))
        elif path == "/spc":
            self.send_html(page_spc(user, query))
        elif path == "/alarms":
            self.send_html(page_alarms(user, query))
        elif path == "/tools":
            self.send_html(page_tools(user))
        elif path == "/upload":
            self.send_html(page_upload(user))
        elif path == "/about":
            self.send_html(page_about(user))
        else:
            self.send_html(base_layout("404", "<h1>404 Not Found</h1>", user), status=404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length).decode("utf-8", errors="replace")
        form = parse_qs(raw_body)

        if path == "/login":
            username = form.get("username", [""])[0].strip()
            password = form.get("password", [""])[0]

            conn = get_conn()
            user = conn.execute(
                "SELECT * FROM users WHERE username=? AND status='active'",
                (username,)
            ).fetchone()

            ok = bool(user and user["password_hash"] == hash_password(password))
            conn.execute("""
            INSERT INTO login_logs (username, ip_address, user_agent, login_status)
            VALUES (?, ?, ?, ?)
            """, (username, self.client_address[0], self.headers.get("User-Agent", ""), "SUCCESS" if ok else "FAILED"))
            conn.commit()
            conn.close()

            if ok:
                sid = secrets.token_urlsafe(32)
                SESSIONS[sid] = {"user_id": user["id"], "login_time": datetime.now().isoformat()}
                status, headers, data = redirect("/")
                headers["Set-Cookie"] = f"sid={sid}; Path=/; HttpOnly; SameSite=Lax"
                self.send_bytes(status, headers, data)
            else:
                self.send_html(page_login("用户名或密码错误"), status=401)
            return

        user = require_login(self)
        if not user:
            status, headers, data = redirect("/login")
            self.send_bytes(status, headers, data)
            return

        if path == "/upload":
            csv_text = form.get("csv_text", [""])[0]
            imported, skipped = import_csv_text(csv_text)
            self.send_html(page_upload(user, f"导入完成：新增 {imported} 条，跳过 {skipped} 条重复/无效数据。"))
            return

        self.send_html(base_layout("404", "<h1>404 Not Found</h1>", user), status=404)

    def log_message(self, fmt, *args):
        # Reduce console noise
        print("[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), fmt % args))


def import_csv_text(csv_text):
    imported = 0
    skipped = 0
    reader = csv.DictReader(io.StringIO(csv_text))
    conn = get_conn()
    cur = conn.cursor()

    required = {"measurement_id", "lot_id", "measurement_item", "value"}
    if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
        conn.close()
        return 0, 1

    for row in reader:
        try:
            measurement_id = row.get("measurement_id", "").strip()
            lot_id = row.get("lot_id", "").strip()
            item = row.get("measurement_item", "").strip()
            value = float(row.get("value", ""))
            if not measurement_id or not lot_id or not item:
                skipped += 1
                continue

            def f(name):
                v = row.get(name, "")
                return float(v) if v not in ("", None) else None

            lsl, usl, lcl, ucl = f("lsl"), f("usl"), f("lcl"), f("ucl")
            status = judge_status(value, lsl, usl, lcl, ucl)

            cur.execute("""
            INSERT INTO measurement_result (
                measurement_id, lot_id, wafer_id, slot_no, product_id, operation, layer_name,
                tool_id, recipe_id, measurement_item, site_id, site_x, site_y, value, unit,
                target, lsl, usl, lcl, ucl, result_status, measure_time, source_type
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                measurement_id, lot_id, row.get("wafer_id"), int(row.get("slot_no") or 0),
                row.get("product_id"), row.get("operation"), row.get("layer_name"),
                row.get("tool_id"), row.get("recipe_id"), item, row.get("site_id"),
                f("site_x"), f("site_y"), value, row.get("unit"),
                f("target"), lsl, usl, lcl, ucl, status,
                row.get("measure_time") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "CSV"
            ))

            if status in ("OOC", "OOS"):
                alarm_id = "ACSV" + secrets.token_hex(6).upper()
                cur.execute("""
                INSERT INTO alarm_record (
                    alarm_id, alarm_type, severity, lot_id, wafer_id, tool_id,
                    measurement_item, alarm_message, alarm_status, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    alarm_id, status, "High" if status == "OOS" else "Medium",
                    lot_id, row.get("wafer_id"), row.get("tool_id"), item,
                    f"{item} value {value} triggered {status}", "New",
                    row.get("measure_time") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                ))

            imported += 1
        except Exception:
            skipped += 1

    conn.commit()
    conn.close()
    return imported, skipped


def main():
    init_db()
    server = ThreadingHTTPServer((HOST, PORT), AppHandler)
    print("=" * 72)
    print(APP_TITLE)
    print(f"启动成功：http://{HOST}:{PORT}")
    print("默认账号：admin")
    print("默认密码：admin123")
    print("按 Ctrl+C 停止服务")
    print("=" * 72)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
