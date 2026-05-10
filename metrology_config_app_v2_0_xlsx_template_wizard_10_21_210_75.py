#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
量测数据采集配置平台 V1 - 单文件可运行版

适用场景：
- 管理员登录；
- 添加生产编号；
- 在生产编号下添加量测项；
- 每个量测项配置共享 CSV 地址，例如：\\\\192.168.1.100\\share\\result.csv；
- CSV 是“每个生产编号一行”的结构，例如表头：生产编号,Dx1,Dy1,Dx2,Dy2,Rz；
- 指标可自定义，例如 Dx1、Dy1、Dx2、Dy2、Rz；
- 系统可手动/定时读取 CSV，按生产编号匹配对应行，再抓取指标入库；
- 可导出/导入生产编号配置 JSON。

运行：
    python metrology_config_app.py

访问：
    http://10.21.210.75:8020

默认账号：
    admin
默认密码：
    admin123

注意：
- 这是 V1 原型级单文件应用，仅依赖 Python 标准库。
- 正式上线建议改成 FastAPI + PostgreSQL + Vue，并接入公司权限体系。
- UNC 路径必须由运行本程序的电脑/服务器有权限访问。
"""

import csv
import hashlib
import html
import io
import json
import os
import secrets
import sqlite3
import threading
import time
import traceback
import zipfile
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timedelta, timezone
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

APP_TITLE = "量测数据采集配置平台 V2.0 - Excel多Sheet模板向导版"
DB_FILE = "metrology_config_v1.db"
HOST = os.environ.get("MDCP_HOST", "0.0.0.0")
PORT = int(os.environ.get("MDCP_PORT", "8020"))
SESSIONS = {}
SCHEDULER_STOP = threading.Event()
APP_TZ = timezone(timedelta(hours=8))  # Asia/Shanghai / Asia/Singapore, UTC+8
READ_TIMEOUT_SECONDS = int(os.environ.get("MDCP_READ_TIMEOUT_SECONDS", "20"))
READ_RETRY_COUNT = int(os.environ.get("MDCP_READ_RETRY_COUNT", "3"))
READ_RETRY_INTERVAL_SECONDS = float(os.environ.get("MDCP_READ_RETRY_INTERVAL_SECONDS", "1.0"))
FILE_STABLE_WAIT_SECONDS = float(os.environ.get("MDCP_FILE_STABLE_WAIT_SECONDS", "0.4"))
READ_EXECUTOR = ThreadPoolExecutor(max_workers=int(os.environ.get("MDCP_READ_WORKERS", "4")))
DISPLAY_IP = os.environ.get("MDCP_DISPLAY_IP", "10.21.210.75")
TEMPLATE_CACHE_DIR = Path(os.environ.get("MDCP_TEMPLATE_CACHE_DIR", "template_upload_cache"))


# ==========================================================
# Utility
# ==========================================================

def now_str():
    return datetime.now(APP_TZ).strftime("%Y-%m-%d %H:%M:%S")


def h(value):
    return html.escape("" if value is None else str(value))


def hash_password(password: str) -> str:
    return hashlib.sha256(("mdcp_v1_salt_" + password).encode("utf-8")).hexdigest()


def row_hash(row: dict) -> str:
    raw = json.dumps(row, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def parse_cookie(header):
    jar = cookies.SimpleCookie()
    if header:
        jar.load(header)
    return {k: morsel.value for k, morsel in jar.items()}


def redirect(location):
    return 302, {"Location": location}, b""


def safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def safe_float(value):
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    try:
        return float(text)
    except Exception:
        return None


def has_role(user, *roles):
    # 当前版本仍以单管理员/工程师原型为主；旧 session 里没有 role 时按 admin 处理。
    return bool(user) and user.get("role", "admin") in roles


def can_manage_config(user):
    return has_role(user, "admin", "engineer")


def require_permission(user, allowed, message="当前账号没有权限执行该操作"):
    if not allowed:
        raise PermissionError(message)


# ==========================================================
# Database
# ==========================================================

def get_conn():
    # timeout + WAL: reduces "database is locked" errors when scheduler and UI write concurrently.
    conn = sqlite3.connect(DB_FILE, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def ensure_column(cur, table_name, column_name, column_sql):
    existing = {r["name"] for r in cur.execute(f"PRAGMA table_info({table_name})").fetchall()}
    if column_name not in existing:
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS admin_user (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS production_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        production_code TEXT UNIQUE NOT NULL,
        production_name TEXT,
        product_model TEXT,
        process_version TEXT,
        description TEXT,
        status TEXT DEFAULT 'enabled',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS measurement_item_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        production_id INTEGER NOT NULL,
        item_name TEXT NOT NULL,
        process_step TEXT,
        execution_time_text TEXT,
        equipment_name TEXT,
        data_source_type TEXT DEFAULT 'auto',
        data_source_path TEXT,
        excel_sheet_name TEXT,
        csv_encoding TEXT DEFAULT 'auto',
        delimiter TEXT DEFAULT ',',
        production_code_column TEXT DEFAULT '生产编号',
        scan_frequency_seconds INTEGER DEFAULT 60,
        enabled INTEGER DEFAULT 1,
        last_collect_time TEXT,
        last_collect_status TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT,
        FOREIGN KEY(production_id) REFERENCES production_config(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS metric_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_id INTEGER NOT NULL,
        metric_name TEXT NOT NULL,
        source_column TEXT NOT NULL,
        unit TEXT,
        data_type TEXT DEFAULT 'number',
        target REAL,
        lsl REAL,
        usl REAL,
        lcl REAL,
        ucl REAL,
        enabled INTEGER DEFAULT 1,
        sort_order INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT,
        FOREIGN KEY(item_id) REFERENCES measurement_item_config(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS measurement_result (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        production_id INTEGER,
        production_code TEXT,
        item_id INTEGER,
        measurement_item_name TEXT,
        process_step TEXT,
        execution_time_text TEXT,
        equipment_name TEXT,
        metric_name TEXT,
        metric_value_text TEXT,
        metric_value_number REAL,
        unit TEXT,
        target REAL,
        lsl REAL,
        usl REAL,
        lcl REAL,
        ucl REAL,
        result_status TEXT,
        source_path TEXT,
        source_row_hash TEXT,
        source_metric_hash TEXT UNIQUE,
        collect_time TEXT DEFAULT CURRENT_TIMESTAMP,
        raw_row_json TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS collect_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        production_id INTEGER,
        production_code TEXT,
        item_id INTEGER,
        measurement_item_name TEXT,
        data_source_path TEXT,
        status TEXT,
        message TEXT,
        matched_rows INTEGER DEFAULT 0,
        inserted_count INTEGER DEFAULT 0,
        skipped_count INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        action TEXT,
        object_type TEXT,
        object_id TEXT,
        detail TEXT,
        ip_address TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS template_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        template_name TEXT NOT NULL,
        template_version TEXT DEFAULT 'v1.0',
        data_source_type TEXT DEFAULT 'csv',
        header_row_index INTEGER DEFAULT 1,
        delimiter TEXT DEFAULT ',',
        encoding TEXT DEFAULT 'auto',
        excel_sheet_name TEXT,
        production_code_column TEXT NOT NULL,
        sample_fields_json TEXT,
        description TEXT,
        created_at TEXT,
        updated_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS template_metric_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        template_id INTEGER NOT NULL,
        metric_name TEXT NOT NULL,
        source_column TEXT NOT NULL,
        data_type TEXT DEFAULT 'number',
        unit TEXT,
        target REAL,
        lsl REAL,
        usl REAL,
        lcl REAL,
        ucl REAL,
        sort_order INTEGER DEFAULT 0,
        created_at TEXT,
        updated_at TEXT,
        FOREIGN KEY(template_id) REFERENCES template_config(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS template_apply_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        template_id INTEGER,
        production_id INTEGER,
        production_code TEXT,
        item_id INTEGER,
        applied_by TEXT,
        applied_at TEXT,
        detail TEXT
    )
    """)

    ensure_column(cur, "measurement_item_config", "data_source_type", "data_source_type TEXT DEFAULT 'auto'")
    ensure_column(cur, "measurement_item_config", "excel_sheet_name", "excel_sheet_name TEXT")
    ensure_column(cur, "template_config", "excel_sheet_name", "excel_sheet_name TEXT")

    admin_username = os.environ.get("MDCP_ADMIN_USERNAME", "admin")
    admin_password = os.environ.get("MDCP_ADMIN_PASSWORD", "admin123")

    cur.execute("SELECT COUNT(*) AS c FROM admin_user")
    if cur.fetchone()["c"] == 0:
        cur.execute(
            "INSERT INTO admin_user (username, password_hash) VALUES (?, ?)",
            (admin_username, hash_password(admin_password))
        )
    elif os.environ.get("MDCP_ADMIN_PASSWORD"):
        # If env password is explicitly supplied, update/create that admin account.
        cur.execute("SELECT id FROM admin_user WHERE username=?", (admin_username,))
        if cur.fetchone():
            cur.execute("UPDATE admin_user SET password_hash=? WHERE username=?", (hash_password(admin_password), admin_username))
        else:
            cur.execute("INSERT INTO admin_user (username, password_hash) VALUES (?, ?)", (admin_username, hash_password(admin_password)))

    conn.commit()
    conn.close()


# ==========================================================
# Audit log
# ==========================================================

def write_audit(username, action, object_type="", object_id="", detail="", ip_address=""):
    try:
        conn = get_conn()
        conn.execute("""
        INSERT INTO audit_log (username, action, object_type, object_id, detail, ip_address, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (username or "system", action, object_type, str(object_id or ""), detail or "", ip_address or "", now_str()))
        conn.commit()
        conn.close()
    except Exception as ex:
        print("[audit_log_failed]", ex)

# ==========================================================
# CSV collection service
# ==========================================================

def _read_file_bytes_stably(path: str):
    """Read bytes with retry and size/mtime stability check.

    This avoids parsing a half-written CSV when the equipment is writing the file.
    It does not request write access and it never modifies the source file.
    """
    last_error = None
    for attempt in range(1, READ_RETRY_COUNT + 1):
        try:
            if not path:
                raise FileNotFoundError("数据源路径为空")
            if not os.path.exists(path):
                raise FileNotFoundError(f"路径不存在或无权限访问：{path}")

            st1 = os.stat(path)
            time.sleep(FILE_STABLE_WAIT_SECONDS)
            st2 = os.stat(path)
            if (st1.st_size != st2.st_size) or (int(st1.st_mtime_ns) != int(st2.st_mtime_ns)):
                raise RuntimeError("文件仍在写入或变化中，等待下次重试")

            # Open read-only. On Windows, if writer uses exclusive lock, this may raise PermissionError.
            with open(path, "rb") as f:
                data = f.read()

            st3 = os.stat(path)
            if (st2.st_size != st3.st_size) or (int(st2.st_mtime_ns) != int(st3.st_mtime_ns)):
                raise RuntimeError("读取期间文件发生变化，等待下次重试")
            return data, st3
        except (PermissionError, OSError, RuntimeError, FileNotFoundError) as ex:
            last_error = ex
            if attempt < READ_RETRY_COUNT:
                time.sleep(READ_RETRY_INTERVAL_SECONDS * attempt)
                continue
            raise last_error


def read_csv_rows(path: str, encoding: str, delimiter: str):
    """Read CSV as DictReader with retry, stability checks and encoding fallback.

    Improvements over V1.5:
    - retries PermissionError/OSError when equipment is writing or locking the file;
    - checks size/mtime stability before and after read;
    - decodes from an in-memory byte snapshot, so parsing does not hold the source file;
    - supports encoding auto-detection fallback.
    """
    if delimiter == "\\t":
        delimiter = "\t"

    data, stat_info = _read_file_bytes_stably(path)

    encodings = []
    configured = (encoding or "auto").strip().lower()
    if configured and configured != "auto":
        encodings.append(configured)
    for fallback in ["utf-8-sig", "utf-8", "gb18030", "gbk", "cp936", "big5"]:
        if fallback not in encodings:
            encodings.append(fallback)

    last_error = None
    for enc in encodings:
        try:
            text = data.decode(enc, errors="strict")
            reader = csv.DictReader(io.StringIO(text), delimiter=delimiter or ",")
            rows = [dict(r) for r in reader]
            fieldnames = reader.fieldnames or []
            return fieldnames, rows, enc
        except UnicodeDecodeError as ex:
            last_error = ex
            continue

    # Last-resort tolerant read. This protects production collection from one abnormal character.
    text = data.decode("gb18030", errors="replace")
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter or ",")
    rows = [dict(r) for r in reader]
    fieldnames = reader.fieldnames or []
    return fieldnames, rows, "gb18030(errors=replace)"


def excel_column_index(cell_ref: str) -> int:
    letters = "".join(ch for ch in (cell_ref or "") if ch.isalpha()).upper()
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return max(0, idx - 1)


def normalize_xlsx_target(target: str) -> str:
    target = (target or "").replace("\\", "/")
    if target.startswith("/"):
        return target.lstrip("/")
    if target.startswith("xl/"):
        return target
    return "xl/" + target.lstrip("/")


def xlsx_list_sheets_from_bytes(data: bytes):
    ns_main = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    ns_rel = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}
    office_rel = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        workbook = ET.fromstring(zf.read("xl/workbook.xml"))
        rels_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        rels = {rel.attrib.get("Id"): rel.attrib.get("Target", "") for rel in rels_root.findall("r:Relationship", ns_rel)}
        sheets = []
        for sheet in workbook.findall(".//x:sheet", ns_main):
            name = sheet.attrib.get("name", "")
            rid = sheet.attrib.get(office_rel, "")
            target = normalize_xlsx_target(rels.get(rid, ""))
            sheets.append((name, target))
        return sheets


def _xlsx_rows_matrix_from_bytes(data: bytes, sheet_name: str = ""):
    ns_main = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        shared_strings = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root.findall("x:si", ns_main):
                shared_strings.append("".join(t.text or "" for t in si.findall(".//x:t", ns_main)))

        sheets = xlsx_list_sheets_from_bytes(data)
        if not sheets:
            raise ValueError("Excel 文件中没有可读取的 Sheet。")
        selected = sheets[0]
        if sheet_name:
            selected = next((s for s in sheets if s[0] == sheet_name), None)
            if selected is None:
                available = "，".join(name for name, _target in sheets)
                raise ValueError(f"找不到 Sheet：{sheet_name}。可用 Sheet：{available}")

        worksheet = ET.fromstring(zf.read(selected[1]))
        parsed_rows = []
        for row in worksheet.findall(".//x:sheetData/x:row", ns_main):
            values = {}
            for cell in row.findall("x:c", ns_main):
                col_idx = excel_column_index(cell.attrib.get("r", ""))
                cell_type = cell.attrib.get("t", "")
                if cell_type == "inlineStr":
                    value = "".join(t.text or "" for t in cell.findall(".//x:t", ns_main))
                else:
                    v = cell.find("x:v", ns_main)
                    raw = "" if v is None or v.text is None else v.text
                    if cell_type == "s":
                        try:
                            value = shared_strings[int(raw)]
                        except Exception:
                            value = raw
                    elif cell_type == "b":
                        value = "TRUE" if raw == "1" else "FALSE"
                    else:
                        value = raw
                values[col_idx] = value
            if values:
                max_col = max(values.keys())
                parsed_rows.append([values.get(i, "") for i in range(max_col + 1)])
    return parsed_rows, selected[0]


def _rows_matrix_to_dicts(parsed_rows, selected_name: str, header_row_index: int = 1, preview_limit=None):
    if not parsed_rows:
        return [], [], f"xlsx:{selected_name}"
    header_idx = max(0, header_row_index - 1)
    if header_idx >= len(parsed_rows):
        raise ValueError(f"表头所在行 {header_row_index} 超出 Excel 数据范围。")
    fieldnames = [str(v).strip() for v in parsed_rows[header_idx]]
    rows = []
    for raw_row in parsed_rows[header_idx + 1:]:
        if not any(str(v).strip() for v in raw_row):
            continue
        row_dict = {}
        for idx, name in enumerate(fieldnames):
            if name:
                row_dict[name] = raw_row[idx] if idx < len(raw_row) else ""
        rows.append(row_dict)
        if preview_limit is not None and len(rows) >= preview_limit:
            break
    return fieldnames, rows, f"xlsx:{selected_name}"


def _parse_xlsx_rows_from_bytes(data: bytes, sheet_name: str = "", header_row_index: int = 1):
    parsed_rows, selected_name = _xlsx_rows_matrix_from_bytes(data, sheet_name)
    return _rows_matrix_to_dicts(parsed_rows, selected_name, header_row_index, preview_limit=10)


def read_xlsx_rows(path: str, sheet_name: str = "", header_row_index: int = 1):
    if Path(path or "").suffix.lower() == ".xls":
        raise ValueError("当前版本支持 .xlsx/.xlsm；旧版 .xls 请先另存为 .xlsx。")
    data, _stat_info = _read_file_bytes_stably(path)
    parsed_rows, selected_name = _xlsx_rows_matrix_from_bytes(data, sheet_name)
    return _rows_matrix_to_dicts(parsed_rows, selected_name, header_row_index, preview_limit=None)


def read_source_rows(data_source_type: str, path: str, encoding: str, delimiter: str, sheet_name: str = "", header_row_index: int = 1):
    source_type = (data_source_type or "auto").strip().lower()
    suffix = Path(path or "").suffix.lower()
    if source_type == "auto":
        source_type = "excel" if suffix in (".xlsx", ".xlsm", ".xls") else "csv"
    if source_type == "excel":
        return read_xlsx_rows(path, sheet_name, header_row_index)
    return read_csv_rows(path, encoding, delimiter)


def save_template_cache_file(file_bytes: bytes, filename: str):
    TEMPLATE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe_suffix = Path(filename or "template.xlsx").suffix.lower() or ".xlsx"
    token = secrets.token_urlsafe(16)
    path = TEMPLATE_CACHE_DIR / f"{token}{safe_suffix}"
    path.write_bytes(file_bytes)
    return token, str(path)


def get_template_cache_path(token: str):
    if not token:
        return None
    for p in TEMPLATE_CACHE_DIR.glob(token + ".*"):
        return str(p)
    return None

def judge_status(value_number, lsl, usl, lcl, ucl):
    if value_number is None:
        return "TEXT"
    if lsl is not None and value_number < lsl:
        return "OOS"
    if usl is not None and value_number > usl:
        return "OOS"
    if lcl is not None and value_number < lcl:
        return "OOC"
    if ucl is not None and value_number > ucl:
        return "OOC"
    return "PASS"


def collect_item(item_id: int, dry_run=False):
    """Collect one measurement item. If dry_run=True, only test and return detail."""
    conn = get_conn()
    cur = conn.cursor()
    item = cur.execute("""
        SELECT mi.*, p.production_code, p.production_name
        FROM measurement_item_config mi
        JOIN production_config p ON p.id = mi.production_id
        WHERE mi.id=?
    """, (item_id,)).fetchone()

    if not item:
        conn.close()
        return {"ok": False, "status": "NOT_FOUND", "message": "量测项不存在"}

    metrics = cur.execute("""
        SELECT * FROM metric_config
        WHERE item_id=? AND enabled=1
        ORDER BY sort_order, id
    """, (item_id,)).fetchall()

    if not metrics:
        status = "NO_METRICS_CONFIGURED"
        msg = "该量测项下没有启用的指标配置。请先进入“指标”，添加 Dx1、Dy1、Dx2、Dy2、Rz 等指标后再采集。"
        if not dry_run:
            write_collect_log(cur, item, status, msg, 0, 0, 0)
            update_item_status(cur, item_id, status)
            conn.commit()
        conn.close()
        return {
            "ok": False,
            "status": status,
            "message": msg,
            "matched_rows": 0,
            "inserted": 0,
            "skipped": 0,
            "metric_preview": {}
        }

    production_code = item["production_code"]
    data_source_path = item["data_source_path"]
    code_column = item["production_code_column"] or "生产编号"

    inserted = 0
    skipped = 0
    try:
        fieldnames, rows, used_encoding = read_source_rows(
            item["data_source_type"] if "data_source_type" in item.keys() else "auto",
            data_source_path,
            item["csv_encoding"] or "auto",
            item["delimiter"] or ",",
            item["excel_sheet_name"] if "excel_sheet_name" in item.keys() else "",
            1
        )

        if code_column not in fieldnames:
            status = "MISSING_CODE_COLUMN"
            msg = f"数据源中找不到生产编号字段：{code_column}。当前字段：{', '.join(fieldnames)}"
            if not dry_run:
                write_collect_log(cur, item, status, msg, 0, 0, 0)
                update_item_status(cur, item_id, status)
                conn.commit()
            conn.close()
            return {
                "ok": False,
                "status": status,
                "message": msg,
                "fieldnames": fieldnames,
                "used_encoding": used_encoding
            }

        missing_metric_columns = [m["source_column"] for m in metrics if m["source_column"] not in fieldnames]
        if missing_metric_columns:
            status = "MISSING_METRIC_COLUMN"
            msg = f"数据源中找不到指标字段：{', '.join(missing_metric_columns)}。当前字段：{', '.join(fieldnames)}"
            if not dry_run:
                write_collect_log(cur, item, status, msg, 0, 0, 0)
                update_item_status(cur, item_id, status)
                conn.commit()
            conn.close()
            return {
                "ok": False,
                "status": status,
                "message": msg,
                "fieldnames": fieldnames,
                "missing_metric_columns": missing_metric_columns,
                "used_encoding": used_encoding
            }

        matched = [r for r in rows if str(r.get(code_column, "")).strip() == str(production_code).strip()]
        if not matched:
            status = "NO_MATCHED_PRODUCTION_CODE"
            msg = f"未找到生产编号 {production_code} 对应的数据行。"
            if not dry_run:
                write_collect_log(cur, item, status, msg, 0, 0, 0)
                update_item_status(cur, item_id, status)
                conn.commit()
            conn.close()
            return {
                "ok": False,
                "status": status,
                "message": msg,
                "fieldnames": fieldnames,
                "used_encoding": used_encoding,
                "matched_rows": 0
            }

        # 每个生产编号一行：若意外存在多行，默认取最后一行作为最新行。
        target_row = matched[-1]
        base_hash = row_hash(target_row)

        preview = {m["metric_name"]: target_row.get(m["source_column"]) for m in metrics}
        if dry_run:
            conn.close()
            return {
                "ok": True,
                "status": "TEST_SUCCESS",
                "message": "测试读取成功。",
                "fieldnames": fieldnames,
                "used_encoding": used_encoding,
                "matched_rows": len(matched),
                "selected_row": target_row,
                "metric_preview": preview
            }

        for m in metrics:
            source_col = m["source_column"]
            value_text = "" if target_row.get(source_col) is None else str(target_row.get(source_col)).strip()
            value_number = safe_float(value_text) if m["data_type"] == "number" else None
            status = judge_status(value_number, m["lsl"], m["usl"], m["lcl"], m["ucl"])
            metric_hash = hashlib.sha256(
                f"{item_id}|{m['id']}|{base_hash}|{m['metric_name']}|{value_text}".encode("utf-8")
            ).hexdigest()

            try:
                cur.execute("""
                INSERT INTO measurement_result (
                    production_id, production_code, item_id, measurement_item_name,
                    process_step, execution_time_text, equipment_name,
                    metric_name, metric_value_text, metric_value_number, unit,
                    target, lsl, usl, lcl, ucl, result_status,
                    source_path, source_row_hash, source_metric_hash, collect_time, raw_row_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    item["production_id"], production_code, item_id, item["item_name"],
                    item["process_step"], item["execution_time_text"], item["equipment_name"],
                    m["metric_name"], value_text, value_number, m["unit"],
                    m["target"], m["lsl"], m["usl"], m["lcl"], m["ucl"], status,
                    data_source_path, base_hash, metric_hash, now_str(),
                    json.dumps(target_row, ensure_ascii=False)
                ))
                inserted += 1
            except sqlite3.IntegrityError:
                skipped += 1

        log_status = "SUCCESS"
        msg = f"采集成功：匹配 {len(matched)} 行，新增 {inserted} 条，跳过重复 {skipped} 条。"
        write_collect_log(cur, item, log_status, msg, len(matched), inserted, skipped)
        update_item_status(cur, item_id, log_status)
        conn.commit()
        conn.close()
        return {
            "ok": True,
            "status": log_status,
            "message": msg,
            "matched_rows": len(matched),
            "inserted": inserted,
            "skipped": skipped,
            "selected_row": target_row,
            "metric_preview": preview
        }

    except Exception as ex:
        status = "READ_ERROR"
        msg = f"读取失败：{ex}"
        if not dry_run:
            try:
                write_collect_log(cur, item, status, msg, 0, inserted, skipped)
                update_item_status(cur, item_id, status)
                conn.commit()
            except Exception:
                pass
        conn.close()
        return {
            "ok": False,
            "status": status,
            "message": msg,
            "traceback": traceback.format_exc()
        }


def _write_timeout_log(item_id: int, dry_run: bool, message: str):
    if dry_run:
        return
    try:
        conn = get_conn()
        cur = conn.cursor()
        item = cur.execute("""
            SELECT mi.*, p.production_code, p.production_name
            FROM measurement_item_config mi
            JOIN production_config p ON p.id = mi.production_id
            WHERE mi.id=?
        """, (item_id,)).fetchone()
        if item:
            write_collect_log(cur, item, "READ_TIMEOUT", message, 0, 0, 0)
            update_item_status(cur, item_id, "READ_TIMEOUT")
            conn.commit()
        conn.close()
    except Exception as ex:
        print("[timeout_log_failed]", ex)


def collect_item_with_timeout(item_id: int, dry_run=False):
    """Protect UI requests from hanging on slow/broken UNC paths.

    Note: Python cannot forcibly kill a blocked OS-level UNC read inside a thread.
    This returns control to the UI after READ_TIMEOUT_SECONDS; the underlying read may
    finish later. For strict industrial isolation, move collectors into separate worker
    processes/services.
    """
    future = READ_EXECUTOR.submit(collect_item, item_id, dry_run)
    try:
        return future.result(timeout=READ_TIMEOUT_SECONDS)
    except FutureTimeoutError:
        msg = f"读取超时：超过 {READ_TIMEOUT_SECONDS} 秒未返回。可能是共享路径网络异常、权限问题或设备正在独占写入。系统已放弃本次前台等待，后台后续周期会重试。"
        _write_timeout_log(item_id, dry_run, msg)
        return {
            "ok": False,
            "status": "READ_TIMEOUT",
            "message": msg,
            "matched_rows": 0,
            "inserted": 0,
            "skipped": 0
        }


def write_collect_log(cur, item, status, message, matched_rows, inserted_count, skipped_count):
    cur.execute("""
    INSERT INTO collect_log (
        production_id, production_code, item_id, measurement_item_name,
        data_source_path, status, message, matched_rows, inserted_count, skipped_count, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        item["production_id"], item["production_code"], item["id"], item["item_name"],
        item["data_source_path"], status, message, matched_rows, inserted_count, skipped_count, now_str()
    ))


def update_item_status(cur, item_id, status):
    cur.execute("""
    UPDATE measurement_item_config
    SET last_collect_time=?, last_collect_status=?, updated_at=?
    WHERE id=?
    """, (now_str(), status, now_str(), item_id))


def scheduler_loop():
    """Simple polling scheduler. Suitable for V1 demo."""
    last_run = {}
    while not SCHEDULER_STOP.is_set():
        try:
            conn = get_conn()
            rows = conn.execute("""
                SELECT id, scan_frequency_seconds, last_collect_time
                FROM measurement_item_config
                WHERE enabled=1
            """).fetchall()
            conn.close()

            now_ts = time.time()
            for r in rows:
                freq = max(10, int(r["scan_frequency_seconds"] or 60))
                item_id = r["id"]
                prev = last_run.get(item_id, 0)
                if now_ts - prev >= freq:
                    last_run[item_id] = now_ts
                    collect_item_with_timeout(item_id, dry_run=False)
        except Exception as ex:
            print("[scheduler]", ex)
        SCHEDULER_STOP.wait(5)


# ==========================================================
# Auth
# ==========================================================

def current_user(handler):
    c = parse_cookie(handler.headers.get("Cookie"))
    sid = c.get("sid")
    if not sid or sid not in SESSIONS:
        return None
    return SESSIONS[sid]


# ==========================================================
# HTML layout
# ==========================================================

def base_layout(title, body, user=None):
    css = """
    :root{--bg:#f5f7fb;--card:#fff;--text:#172033;--muted:#667085;--line:#e5e7eb;--primary:#2563eb;--primary2:#1d4ed8;--danger:#dc2626;--ok:#16a34a;--warn:#ea580c;}
    *{box-sizing:border-box} body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',Arial,sans-serif;background:var(--bg);color:var(--text)}
    a{color:var(--primary);text-decoration:none}.topbar{height:56px;background:#111827;color:#fff;display:flex;align-items:center;justify-content:space-between;padding:0 22px}.topbar a{color:#bfdbfe}.brand{font-weight:800}.layout{display:flex;min-height:calc(100vh - 56px)}
    .sidebar{width:230px;background:white;border-right:1px solid var(--line);padding:16px 10px}.sidebar a{display:block;padding:11px 14px;border-radius:10px;color:#374151;margin-bottom:6px}.sidebar a:hover{background:#eff6ff;color:var(--primary2)}
    .content{flex:1;padding:24px;overflow:auto}.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px;margin-bottom:18px;box-shadow:0 2px 10px rgba(15,23,42,.04)}
    h1{margin:0 0 18px;font-size:24px} h2{margin:0 0 12px;font-size:18px}.grid{display:grid;grid-template-columns:repeat(4,minmax(160px,1fr));gap:16px}.metric .label{color:var(--muted);font-size:13px}.metric .value{font-size:28px;font-weight:800;margin-top:8px}
    table{width:100%;border-collapse:collapse;font-size:14px} th,td{border-bottom:1px solid var(--line);padding:10px 8px;text-align:left;white-space:nowrap} th{background:#f9fafb;color:#344054}.table-wrap{overflow:auto}
    input,select,textarea{border:1px solid #d0d5dd;border-radius:10px;padding:9px 10px;min-width:180px;background:#fff} textarea{font-family:ui-monospace,Consolas,monospace}.form-row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:12px}.form-grid{display:grid;grid-template-columns:180px 1fr;gap:12px;align-items:center;max-width:950px}
    button,.btn{background:var(--primary);border:none;color:#fff;padding:9px 14px;border-radius:10px;font-weight:700;cursor:pointer;display:inline-block}.btn.secondary{background:#475467}.btn.danger{background:var(--danger)}button:hover,.btn:hover{background:var(--primary2)}
    .badge{display:inline-block;padding:3px 8px;border-radius:999px;font-size:12px;font-weight:700}.enabled,.SUCCESS,.PASS,.TEST_SUCCESS{background:#dcfce7;color:#166534}.disabled,.READ_ERROR,.READ_TIMEOUT,.OOS,.MISSING_CODE_COLUMN,.MISSING_METRIC_COLUMN{background:#fee2e2;color:#991b1b}.NO_MATCHED_PRODUCTION_CODE,.OOC{background:#ffedd5;color:#9a3412}.TEXT{background:#e0e7ff;color:#3730a3}
    .note{font-size:13px;color:var(--muted);line-height:1.7}.error{color:var(--danger);font-size:14px}.success{color:var(--ok);font-size:14px}.login-wrap{min-height:100vh;display:flex;align-items:center;justify-content:center;background:linear-gradient(135deg,#0f172a,#1d4ed8)}.login-card{width:390px;background:white;border-radius:18px;padding:28px;box-shadow:0 18px 60px rgba(0,0,0,.25)}.login-card input,.login-card button{width:100%;margin:8px 0}.login-card p{color:var(--muted)}
    pre{background:#0b1020;color:#e5e7eb;padding:14px;border-radius:12px;overflow:auto}.actions{display:flex;gap:8px;flex-wrap:wrap}.small{font-size:12px;color:var(--muted)}
    @media(max-width:900px){.grid{grid-template-columns:repeat(2,1fr)}.layout{flex-direction:column}.sidebar{width:100%;display:flex;overflow:auto}.sidebar a{white-space:nowrap}.form-grid{grid-template-columns:1fr}}
    """
    if user:
        shell = f"""
        <div class="topbar"><div class="brand">{h(APP_TITLE)}</div><div>版本：V2.0-XLSX-TEMPLATE ｜ 管理员：{h(user.get('username'))} ｜ <a href="/logout">退出</a></div></div>
        <div class="layout">
          <aside class="sidebar">
            <a href="/">首页 Dashboard</a>
            <a href="/productions">生产编号管理</a>
            <a href="/templates">模板库</a>
            <a href="/results">采集结果</a>
            <a href="/logs">采集日志</a>
            <a href="/audit_logs">审计日志</a>
            <a href="/import_config">导入配置</a>
            <a href="/about">说明</a>
          </aside>
          <main class="content">{body}</main>
        </div>
        """
    else:
        shell = body
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{h(title)}</title><style>{css}</style></head><body>{shell}</body></html>"""


def badge(text):
    return f'<span class="badge {h(text)}">{h(text)}</span>'


def page_login(error=""):
    return base_layout("登录", f"""
    <div class="login-wrap">
      <form class="login-card" method="post" action="/login">
        <h1>{h(APP_TITLE)}</h1>
        <p>管理员登录</p>
        {f'<div class="error">{h(error)}</div>' if error else ''}
        <input name="username" placeholder="管理员账号" required autocomplete="username">
        <input name="password" type="password" placeholder="管理员密码" required autocomplete="current-password">
        <button type="submit">登录</button>
        <div class="note" style="margin-top:12px">默认账号：admin<br>默认密码：admin123<br>正式使用建议通过环境变量 MDCP_ADMIN_USERNAME / MDCP_ADMIN_PASSWORD 设置管理员账号密码。</div>
      </form>
    </div>
    """)


# ==========================================================
# Pages
# ==========================================================

def page_dashboard(user):
    conn = get_conn()
    p_count = conn.execute("SELECT COUNT(*) AS c FROM production_config").fetchone()["c"]
    item_count = conn.execute("SELECT COUNT(*) AS c FROM measurement_item_config WHERE enabled=1").fetchone()["c"]
    result_count = conn.execute("SELECT COUNT(*) AS c FROM measurement_result").fetchone()["c"]
    error_count = conn.execute("SELECT COUNT(*) AS c FROM collect_log WHERE status NOT IN ('SUCCESS')").fetchone()["c"]
    recent_logs = conn.execute("SELECT * FROM collect_log ORDER BY created_at DESC LIMIT 8").fetchall()
    recent_results = conn.execute("SELECT * FROM measurement_result ORDER BY collect_time DESC LIMIT 10").fetchall()
    conn.close()

    log_rows = "".join(f"<tr><td>{h(r['created_at'])}</td><td>{h(r['production_code'])}</td><td>{h(r['measurement_item_name'])}</td><td>{badge(r['status'])}</td><td>{h(r['message'])}</td></tr>" for r in recent_logs) or "<tr><td colspan='5'>暂无日志</td></tr>"
    result_rows = "".join(f"<tr><td>{h(r['collect_time'])}</td><td>{h(r['production_code'])}</td><td>{h(r['measurement_item_name'])}</td><td>{h(r['metric_name'])}</td><td>{h(r['metric_value_text'])} {h(r['unit'])}</td><td>{badge(r['result_status'])}</td></tr>" for r in recent_results) or "<tr><td colspan='6'>暂无结果</td></tr>"
    return base_layout("首页", f"""
    <h1>首页 Dashboard</h1>
    <div class="grid">
      <div class="card metric"><div class="label">生产编号数量</div><div class="value">{p_count}</div></div>
      <div class="card metric"><div class="label">启用量测项</div><div class="value">{item_count}</div></div>
      <div class="card metric"><div class="label">采集结果数</div><div class="value">{result_count}</div></div>
      <div class="card metric"><div class="label">异常日志数</div><div class="value">{error_count}</div></div>
    </div>
    <div class="card"><h2>最近采集结果</h2><div class="table-wrap"><table><tr><th>采集时间</th><th>生产编号</th><th>量测项</th><th>指标</th><th>值</th><th>状态</th></tr>{result_rows}</table></div></div>
    <div class="card"><h2>最近采集日志</h2><div class="table-wrap"><table><tr><th>时间</th><th>生产编号</th><th>量测项</th><th>状态</th><th>信息</th></tr>{log_rows}</table></div></div>
    """, user)


def page_productions(user):
    conn = get_conn()
    rows = conn.execute("""
        SELECT p.*, COUNT(mi.id) AS item_count
        FROM production_config p
        LEFT JOIN measurement_item_config mi ON mi.production_id=p.id
        GROUP BY p.id
        ORDER BY p.id DESC
    """).fetchall()
    conn.close()
    rows_html = "".join(f"""
    <tr>
      <td>{h(r['production_code'])}</td><td>{h(r['production_name'])}</td><td>{h(r['product_model'])}</td><td>{h(r['process_version'])}</td>
      <td>{r['item_count']}</td><td>{badge(r['status'])}</td>
      <td class="actions">
        <a class="btn" href="/items?production_id={r['id']}">量测项</a>
        <a class="btn secondary" href="/production_edit?id={r['id']}">编辑</a>
        <a class="btn secondary" href="/export_config?production_id={r['id']}">导出配置</a>
      </td>
    </tr>
    """ for r in rows) or "<tr><td colspan='7'>暂无生产编号</td></tr>"
    return base_layout("生产编号管理", f"""
    <h1>生产编号管理</h1>
    <div class="card"><a class="btn" href="/production_new">新增生产编号</a> <a class="btn secondary" href="/templates">模板库</a> <a class="btn secondary" href="/import_config">导入配置 JSON</a></div>
    <div class="card"><div class="table-wrap"><table><tr><th>生产编号</th><th>生产名称</th><th>产品型号</th><th>工艺版本</th><th>量测项数量</th><th>状态</th><th>操作</th></tr>{rows_html}</table></div></div>
    """, user)


def page_production_form(user, production_id=None, error=""):
    row = None
    if production_id:
        conn = get_conn()
        row = conn.execute("SELECT * FROM production_config WHERE id=?", (production_id,)).fetchone()
        conn.close()
    title = "编辑生产编号" if row else "新增生产编号"
    return base_layout(title, f"""
    <h1>{title}</h1>
    <div class="card">
      {f'<div class="error">{h(error)}</div>' if error else ''}
      <form method="post" action="/production_save">
        <input type="hidden" name="id" value="{h(row['id'] if row else '')}">
        <div class="form-grid">
          <label>生产编号 *</label><input name="production_code" value="{h(row['production_code'] if row else '')}" required placeholder="例如 PROD_A_V1">
          <label>生产名称</label><input name="production_name" value="{h(row['production_name'] if row else '')}" placeholder="例如 产品A V1">
          <label>产品型号</label><input name="product_model" value="{h(row['product_model'] if row else '')}">
          <label>工艺版本</label><input name="process_version" value="{h(row['process_version'] if row else '')}">
          <label>状态</label><select name="status"><option value="enabled" {'selected' if (row and row['status']=='enabled') or not row else ''}>enabled</option><option value="disabled" {'selected' if row and row['status']=='disabled' else ''}>disabled</option></select>
          <label>描述</label><textarea name="description" rows="4">{h(row['description'] if row else '')}</textarea>
        </div>
        <br><button type="submit">保存</button> <a class="btn secondary" href="/productions">返回</a>
      </form>
    </div>
    """, user)


def page_items(user, production_id):
    conn = get_conn()
    prod = conn.execute("SELECT * FROM production_config WHERE id=?", (production_id,)).fetchone()
    if not prod:
        conn.close()
        return base_layout("未找到", "<h1>生产编号不存在</h1>", user)
    rows = conn.execute("SELECT * FROM measurement_item_config WHERE production_id=? ORDER BY id DESC", (production_id,)).fetchall()
    conn.close()
    rows_html = "".join(f"""
    <tr>
      <td>{h(r['item_name'])}</td><td>{h(r['process_step'])}</td><td>{h(r['execution_time_text'])}</td><td>{h(r['equipment_name'])}</td>
      <td>{h(r['data_source_path'])}</td><td>{h(r['scan_frequency_seconds'])} s</td><td>{badge('enabled' if r['enabled'] else 'disabled')}</td><td>{badge(r['last_collect_status'] or 'NA')}</td>
      <td class="actions">
        <a class="btn" href="/metrics?item_id={r['id']}">指标</a>
        <a class="btn secondary" href="/item_edit?id={r['id']}">编辑</a>
        <a class="btn secondary" href="/test_collect?item_id={r['id']}">测试读取</a>
        <a class="btn secondary" href="/collect_now?item_id={r['id']}">立即采集</a>
      </td>
    </tr>
    """ for r in rows) or "<tr><td colspan='9'>暂无量测项</td></tr>"
    return base_layout("量测项配置", f"""
    <h1>量测项配置：{h(prod['production_code'])}</h1>
    <div class="card">
      <a class="btn" href="/item_new?production_id={production_id}">新增量测项</a>
      <a class="btn secondary" href="/template_apply?production_id={production_id}">从模板新增量测项</a>
      <a class="btn secondary" href="/productions">返回生产编号</a>
      <a class="btn secondary" href="/export_config?production_id={production_id}">导出该生产编号配置</a>
    </div>
    <div class="card"><div class="table-wrap"><table><tr><th>量测项</th><th>量测工序</th><th>执行时间</th><th>设备</th><th>数据源路径</th><th>频率</th><th>启用</th><th>最近状态</th><th>操作</th></tr>{rows_html}</table></div></div>
    """, user)


def page_item_form(user, item_id=None, production_id=None, error=""):
    conn = get_conn()
    item = None
    if item_id:
        item = conn.execute("SELECT * FROM measurement_item_config WHERE id=?", (item_id,)).fetchone()
        production_id = item["production_id"] if item else production_id
    prod = conn.execute("SELECT * FROM production_config WHERE id=?", (production_id,)).fetchone() if production_id else None
    conn.close()
    if not prod:
        return base_layout("错误", "<h1>请先选择生产编号</h1>", user)
    title = "编辑量测项" if item else "新增量测项"
    return base_layout(title, f"""
    <h1>{title}：{h(prod['production_code'])}</h1>
    <div class="card">
      {f'<div class="error">{h(error)}</div>' if error else ''}
      <form method="post" action="/item_save">
        <input type="hidden" name="id" value="{h(item['id'] if item else '')}">
        <input type="hidden" name="production_id" value="{h(production_id)}">
        <div class="form-grid">
          <label>量测项名称 *</label><input name="item_name" value="{h(item['item_name'] if item else '')}" required placeholder="例如 光刻后CD量测">
          <label>量测工序</label><input name="process_step" value="{h(item['process_step'] if item else '')}" placeholder="例如 PHOTO_CD_MEAS">
          <label>量测执行时间</label><input name="execution_time_text" value="{h(item['execution_time_text'] if item else '')}" placeholder="例如 光刻后 / 每日10:00 / 工序完成后">
          <label>量测设备</label><input name="equipment_name" value="{h(item['equipment_name'] if item else '')}" placeholder="例如 CDSEM01">
          <label>数据源类型</label><select name="data_source_type"><option value="auto" {'selected' if (item and 'data_source_type' in item.keys() and item['data_source_type']=='auto') or not item else ''}>自动判断</option><option value="csv" {'selected' if item and 'data_source_type' in item.keys() and item['data_source_type']=='csv' else ''}>CSV</option><option value="excel" {'selected' if item and 'data_source_type' in item.keys() and item['data_source_type']=='excel' else ''}>Excel xlsx/xlsm</option></select>
          <label>数据源路径 *</label><input name="data_source_path" value="{h(item['data_source_path'] if item else r'\\192.168.1.100\share\result.xlsx')}" required style="min-width:520px">
          <label>Excel Sheet 名称</label><input name="excel_sheet_name" value="{h(item['excel_sheet_name'] if item and 'excel_sheet_name' in item.keys() else '')}" placeholder="Excel 多 Sheet 时填写，例如 Sheet1">
          <label>CSV编码</label><select name="csv_encoding">
  <option value="auto" {'selected' if (item and item['csv_encoding']=='auto') or not item else ''}>auto 自动识别</option>
  <option value="utf-8-sig" {'selected' if item and item['csv_encoding']=='utf-8-sig' else ''}>utf-8-sig</option>
  <option value="utf-8" {'selected' if item and item['csv_encoding']=='utf-8' else ''}>utf-8</option>
  <option value="gb18030" {'selected' if item and item['csv_encoding']=='gb18030' else ''}>gb18030</option>
  <option value="gbk" {'selected' if item and item['csv_encoding']=='gbk' else ''}>gbk</option>
</select>
          <label>分隔符</label><input name="delimiter" value="{h(item['delimiter'] if item else ',')}" placeholder=", 或 \t">
          <label>生产编号字段名</label><input name="production_code_column" value="{h(item['production_code_column'] if item else '生产编号')}" placeholder="生产编号">
          <label>抓取频率/秒</label><input name="scan_frequency_seconds" value="{h(item['scan_frequency_seconds'] if item else 60)}" type="number" min="10">
          <label>是否启用</label><select name="enabled"><option value="1" {'selected' if (item and item['enabled']) or not item else ''}>启用</option><option value="0" {'selected' if item and not item['enabled'] else ''}>停用</option></select>
        </div>
        <br><button type="submit">保存</button> <a class="btn secondary" href="/items?production_id={production_id}">返回</a>
      </form>
      <p class="note">说明：CSV/Excel 表头默认类似：生产编号,Dx1,Dy1,Dx2,Dy2,Rz。系统会按“生产编号字段名”找到当前生产编号那一行，再根据指标配置抓取对应列。</p>
    </div>
    """, user)


def page_metrics(user, item_id):
    conn = get_conn()
    item = conn.execute("""
        SELECT mi.*, p.production_code FROM measurement_item_config mi
        JOIN production_config p ON p.id=mi.production_id
        WHERE mi.id=?
    """, (item_id,)).fetchone()
    if not item:
        conn.close()
        return base_layout("错误", "<h1>量测项不存在</h1>", user)
    rows = conn.execute("SELECT * FROM metric_config WHERE item_id=? ORDER BY sort_order, id", (item_id,)).fetchall()
    conn.close()
    rows_html = "".join(f"""
    <tr><td>{h(r['metric_name'])}</td><td>{h(r['source_column'])}</td><td>{h(r['unit'])}</td><td>{h(r['data_type'])}</td><td>{h(r['target'])}</td><td>{h(r['lsl'])}</td><td>{h(r['usl'])}</td><td>{h(r['lcl'])}</td><td>{h(r['ucl'])}</td><td>{badge('enabled' if r['enabled'] else 'disabled')}</td><td><a class="btn secondary" href="/metric_edit?id={r['id']}">编辑</a></td></tr>
    """ for r in rows) or "<tr><td colspan='11'>暂无指标。建议先批量添加：Dx1,Dy1,Dx2,Dy2,Rz</td></tr>"
    return base_layout("指标配置", f"""
    <h1>指标配置：{h(item['production_code'])} / {h(item['item_name'])}</h1>
    <div class="card">
      <form method="post" action="/metric_bulk_add" class="form-row">
        <input type="hidden" name="item_id" value="{item_id}">
        <input name="metric_names" style="min-width:420px" value="Dx1,Dy1,Dx2,Dy2,Rz" placeholder="Dx1,Dy1,Dx2,Dy2,Rz">
        <input name="unit" placeholder="单位，可空，例如 um">
        <button type="submit">批量添加指标</button>
        <a class="btn secondary" href="/metric_new?item_id={item_id}">单个新增</a>
        <a class="btn secondary" href="/items?production_id={item['production_id']}">返回量测项</a>
      </form>
      <p class="note">批量添加时，平台指标名和 数据源字段名默认一致，例如 Dx1 ← CSV列 Dx1。</p>
    </div>
    <div class="card"><div class="table-wrap"><table><tr><th>指标名称</th><th>源字段</th><th>单位</th><th>类型</th><th>Target</th><th>LSL</th><th>USL</th><th>LCL</th><th>UCL</th><th>状态</th><th>操作</th></tr>{rows_html}</table></div></div>
    """, user)


def page_metric_form(user, metric_id=None, item_id=None, error=""):
    conn = get_conn()
    metric = None
    if metric_id:
        metric = conn.execute("SELECT * FROM metric_config WHERE id=?", (metric_id,)).fetchone()
        item_id = metric["item_id"] if metric else item_id
    item = conn.execute("SELECT * FROM measurement_item_config WHERE id=?", (item_id,)).fetchone() if item_id else None
    conn.close()
    if not item:
        return base_layout("错误", "<h1>量测项不存在</h1>", user)
    title = "编辑指标" if metric else "新增指标"
    return base_layout(title, f"""
    <h1>{title}</h1>
    <div class="card">
      {f'<div class="error">{h(error)}</div>' if error else ''}
      <form method="post" action="/metric_save">
        <input type="hidden" name="id" value="{h(metric['id'] if metric else '')}">
        <input type="hidden" name="item_id" value="{h(item_id)}">
        <div class="form-grid">
          <label>指标名称 *</label><input name="metric_name" value="{h(metric['metric_name'] if metric else '')}" required placeholder="例如 Dx1">
          <label>源字段名 *</label><input name="source_column" value="{h(metric['source_column'] if metric else '')}" required placeholder="例如 Dx1">
          <label>单位</label><input name="unit" value="{h(metric['unit'] if metric else '')}" placeholder="例如 um / nm / deg">
          <label>数据类型</label><select name="data_type"><option value="number" {'selected' if (metric and metric['data_type']=='number') or not metric else ''}>number</option><option value="text" {'selected' if metric and metric['data_type']=='text' else ''}>text</option></select>
          <label>Target</label><input name="target" value="{h(metric['target'] if metric else '')}">
          <label>LSL</label><input name="lsl" value="{h(metric['lsl'] if metric else '')}">
          <label>USL</label><input name="usl" value="{h(metric['usl'] if metric else '')}">
          <label>LCL</label><input name="lcl" value="{h(metric['lcl'] if metric else '')}">
          <label>UCL</label><input name="ucl" value="{h(metric['ucl'] if metric else '')}">
          <label>排序</label><input name="sort_order" value="{h(metric['sort_order'] if metric else 0)}" type="number">
          <label>状态</label><select name="enabled"><option value="1" {'selected' if (metric and metric['enabled']) or not metric else ''}>启用</option><option value="0" {'selected' if metric and not metric['enabled'] else ''}>停用</option></select>
        </div>
        <br><button type="submit">保存</button> <a class="btn secondary" href="/metrics?item_id={item_id}">返回</a>
      </form>
    </div>
    """, user)


def page_test_collect(user, item_id):
    result = collect_item_with_timeout(item_id, dry_run=True)
    body = f"""
    <h1>测试读取结果</h1>
    <div class="card">
      <p>状态：{badge(result.get('status'))}</p>
      <p>{h(result.get('message'))}</p>
      <p class="note"><b>注意：</b>测试读取只验证路径、表头、生产编号和指标映射，不会写入采集结果。要入库请点击下面的“确认无误，立即采集入库”。如果 metric_preview 为空，说明该量测项还没有添加指标配置。</p>
      <a class="btn" href="/collect_now?item_id={item_id}">确认无误，立即采集入库</a>
      <a class="btn secondary" href="/item_edit?id={item_id}">返回编辑量测项</a>
    </div>
    <div class="card"><h2>识别到的字段</h2><pre>{h(json.dumps(result.get('fieldnames', []), ensure_ascii=False, indent=2))}</pre></div>
    <div class="card"><h2>匹配到的行数 / 指标预览</h2><pre>{h(json.dumps({'matched_rows': result.get('matched_rows'), 'metric_preview': result.get('metric_preview')}, ensure_ascii=False, indent=2))}</pre></div>
    <div class="card"><h2>选中的生产编号行</h2><pre>{h(json.dumps(result.get('selected_row', {}), ensure_ascii=False, indent=2))}</pre></div>
    """
    return base_layout("测试读取", body, user)


def page_collect_now(user, item_id):
    result = collect_item_with_timeout(item_id, dry_run=False)
    body = f"""
    <h1>立即采集结果</h1>
    <div class="card">
      <p>状态：{badge(result.get('status'))}</p>
      <p>{h(result.get('message'))}</p>
      <a class="btn" href="/results">查看采集结果</a>
      <a class="btn secondary" href="/logs">查看采集日志</a>
      <a class="btn secondary" href="/item_edit?id={item_id}">返回量测项</a>
    </div>
    <div class="card"><h2>详情</h2><pre>{h(json.dumps(result, ensure_ascii=False, indent=2))}</pre></div>
    """
    return base_layout("立即采集", body, user)


def page_results(user, query=None):
    query = query or {}
    production_code = query.get("production_code", [""])[0].strip()
    metric_name = query.get("metric_name", [""])[0].strip()
    where, params = [], []
    if production_code:
        where.append("production_code LIKE ?")
        params.append(f"%{production_code}%")
    if metric_name:
        where.append("metric_name LIKE ?")
        params.append(f"%{metric_name}%")
    sql = "SELECT * FROM measurement_result"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY collect_time DESC LIMIT 500"
    conn = get_conn()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    rows_html = "".join(f"""
    <tr><td>{h(r['collect_time'])}</td><td>{h(r['production_code'])}</td><td>{h(r['measurement_item_name'])}</td><td>{h(r['process_step'])}</td><td>{h(r['execution_time_text'])}</td><td>{h(r['equipment_name'])}</td><td>{h(r['metric_name'])}</td><td>{h(r['metric_value_text'])}</td><td>{h(r['unit'])}</td><td>{badge(r['result_status'])}</td><td>{h(r['source_path'])}</td></tr>
    """ for r in rows) or "<tr><td colspan='11'>暂无结果</td></tr>"
    return base_layout("采集结果", f"""
    <h1>采集结果</h1>
    <div class="card"><form class="form-row" method="get" action="/results"><input name="production_code" value="{h(production_code)}" placeholder="生产编号"><input name="metric_name" value="{h(metric_name)}" placeholder="指标名"><button type="submit">查询</button><a class="btn secondary" href="/results">重置</a></form></div>
    <div class="card"><div class="table-wrap"><table><tr><th>采集时间</th><th>生产编号</th><th>量测项</th><th>工序</th><th>执行时间</th><th>设备</th><th>指标</th><th>值</th><th>单位</th><th>状态</th><th>来源</th></tr>{rows_html}</table></div></div>
    """, user)


def page_logs(user):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM collect_log ORDER BY created_at DESC LIMIT 500").fetchall()
    conn.close()
    rows_html = "".join(f"""
    <tr><td>{h(r['created_at'])}</td><td>{h(r['production_code'])}</td><td>{h(r['measurement_item_name'])}</td><td>{badge(r['status'])}</td><td>{h(r['matched_rows'])}</td><td>{h(r['inserted_count'])}</td><td>{h(r['skipped_count'])}</td><td>{h(r['message'])}</td><td>{h(r['data_source_path'])}</td></tr>
    """ for r in rows) or "<tr><td colspan='9'>暂无日志</td></tr>"
    return base_layout("采集日志", f"""
    <h1>采集日志</h1>
    <div class="card"><div class="table-wrap"><table><tr><th>时间</th><th>生产编号</th><th>量测项</th><th>状态</th><th>匹配行</th><th>新增</th><th>跳过</th><th>信息</th><th>数据源</th></tr>{rows_html}</table></div></div>
    """, user)


def page_audit_logs(user):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM audit_log ORDER BY created_at DESC LIMIT 500").fetchall()
    conn.close()
    rows_html = "".join(
        f"<tr><td>{h(r['created_at'])}</td><td>{h(r['username'])}</td><td>{h(r['action'])}</td><td>{h(r['object_type'])}</td><td>{h(r['object_id'])}</td><td>{h(r['ip_address'])}</td><td>{h(r['detail'])}</td></tr>"
        for r in rows
    ) or "<tr><td colspan='7'>暂无审计日志</td></tr>"
    return base_layout("审计日志", f"""
    <h1>审计日志</h1>
    <div class="card note">记录登录、配置新增/修改、指标配置、配置导入/导出等关键操作。V1.6 用于基础追溯，正式版建议接公司 AD/LDAP 账号。</div>
    <div class="card"><div class="table-wrap"><table><tr><th>时间</th><th>用户</th><th>动作</th><th>对象类型</th><th>对象ID</th><th>IP</th><th>详情</th></tr>{rows_html}</table></div></div>
    """, user)


def export_config_json(production_id):
    conn = get_conn()
    prod = conn.execute("SELECT * FROM production_config WHERE id=?", (production_id,)).fetchone()
    if not prod:
        conn.close()
        return None
    items = conn.execute("SELECT * FROM measurement_item_config WHERE production_id=? ORDER BY id", (production_id,)).fetchall()
    result = {
        "config_version": "1.0",
        "export_time": now_str(),
        "production": {k: prod[k] for k in prod.keys() if k not in ("id", "created_at", "updated_at")},
        "measurement_items": []
    }
    for item in items:
        metrics = conn.execute("SELECT * FROM metric_config WHERE item_id=? ORDER BY sort_order, id", (item["id"],)).fetchall()
        item_obj = {k: item[k] for k in item.keys() if k not in ("id", "production_id", "created_at", "updated_at", "last_collect_time", "last_collect_status")}
        item_obj["metrics"] = [{k: m[k] for k in m.keys() if k not in ("id", "item_id", "created_at", "updated_at")} for m in metrics]
        result["measurement_items"].append(item_obj)
    conn.close()
    return result


def page_import_config(user, message=""):
    return base_layout("导入配置", f"""
    <h1>导入配置 JSON</h1>
    <div class="card">
      {f'<div class="success">{h(message)}</div>' if message else ''}
      <form method="post" action="/import_config">
        <textarea name="config_json" rows="22" style="width:100%" placeholder="粘贴从平台导出的 JSON 配置"></textarea><br><br>
        <button type="submit">导入配置</button>
      </form>
      <p class="note">若生产编号已存在，当前版本会阻止重复导入。可以先修改 JSON 中的 production_code 后再导入。</p>
    </div>
    """, user)


def import_config(json_text):
    cfg = json.loads(json_text)
    prod = cfg.get("production") or {}
    code = prod.get("production_code")
    if not code:
        raise ValueError("配置中缺少 production.production_code")
    conn = get_conn()
    cur = conn.cursor()
    exists = cur.execute("SELECT id FROM production_config WHERE production_code=?", (code,)).fetchone()
    if exists:
        conn.close()
        raise ValueError(f"生产编号已存在：{code}")
    cur.execute("""
    INSERT INTO production_config (production_code, production_name, product_model, process_version, description, status, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (code, prod.get("production_name"), prod.get("product_model"), prod.get("process_version"), prod.get("description"), prod.get("status", "enabled"), now_str()))
    production_id = cur.lastrowid
    for item in cfg.get("measurement_items", []):
        cur.execute("""
        INSERT INTO measurement_item_config (
            production_id, item_name, process_step, execution_time_text, equipment_name,
            data_source_path, csv_encoding, delimiter, production_code_column,
            scan_frequency_seconds, enabled, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            production_id, item.get("item_name"), item.get("process_step"), item.get("execution_time_text"), item.get("equipment_name"),
            item.get("data_source_path"), item.get("csv_encoding", "auto"), item.get("delimiter", ","), item.get("production_code_column", "生产编号"),
            item.get("scan_frequency_seconds", 60), item.get("enabled", 1), now_str()
        ))
        item_id = cur.lastrowid
        for m in item.get("metrics", []):
            cur.execute("""
            INSERT INTO metric_config (item_id, metric_name, source_column, unit, data_type, target, lsl, usl, lcl, ucl, enabled, sort_order, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (item_id, m.get("metric_name"), m.get("source_column"), m.get("unit"), m.get("data_type", "number"), m.get("target"), m.get("lsl"), m.get("usl"), m.get("lcl"), m.get("ucl"), m.get("enabled", 1), m.get("sort_order", 0), now_str()))
    conn.commit()
    conn.close()
    return production_id



# ==========================================================
# Template wizard
# ==========================================================

def _decode_template_bytes(data: bytes, encoding: str = "auto"):
    encodings = []
    configured = (encoding or "auto").strip().lower()
    if configured and configured != "auto":
        encodings.append(configured)
    for enc in ["utf-8-sig", "utf-8", "gb18030", "gbk", "cp936", "big5"]:
        if enc not in encodings:
            encodings.append(enc)
    last_error = None
    for enc in encodings:
        try:
            return data.decode(enc, errors="strict"), enc
        except UnicodeDecodeError as ex:
            last_error = ex
            continue
    return data.decode("gb18030", errors="replace"), "gb18030(errors=replace)"


def parse_template_csv_from_text(csv_text: str, delimiter: str = ",", header_row_index: int = 1):
    if delimiter == "\\t":
        delimiter = "\t"
    header_row_index = max(1, safe_int(header_row_index, 1))
    reader = csv.reader(io.StringIO(csv_text), delimiter=delimiter or ",")
    all_rows = list(reader)
    if len(all_rows) < header_row_index:
        raise ValueError(f"文件行数不足，无法读取第 {header_row_index} 行作为表头")
    headers = [str(x).strip() for x in all_rows[header_row_index - 1]]
    if not any(headers):
        raise ValueError("表头为空，请确认表头所在行是否正确")
    if len([x for x in headers if x]) != len(set([x for x in headers if x])):
        raise ValueError("表头存在重复字段，请先处理模板文件")
    preview_rows = []
    for raw in all_rows[header_row_index:header_row_index + 5]:
        if not any(str(x).strip() for x in raw):
            continue
        row = {}
        for idx, name in enumerate(headers):
            if name:
                row[name] = raw[idx] if idx < len(raw) else ""
        preview_rows.append(row)
    return headers, preview_rows


def page_templates(user):
    conn = get_conn()
    templates = conn.execute("""
        SELECT t.*,
               COUNT(tm.id) AS metric_count
        FROM template_config t
        LEFT JOIN template_metric_config tm ON tm.template_id=t.id
        GROUP BY t.id
        ORDER BY t.id DESC
    """).fetchall()
    conn.close()
    rows_html = "".join(f"""
    <tr>
      <td>{h(r['template_name'])}</td>
      <td>{h(r['template_version'])}</td>
      <td>{h(r['data_source_type'])}</td>
      <td>{h(r['production_code_column'])}</td>
      <td>{r['metric_count']}</td>
      <td>{h(r['updated_at'] or r['created_at'])}</td>
      <td class="actions">
        <a class="btn secondary" href="/template_detail?id={r['id']}">查看</a>
      </td>
    </tr>
    """ for r in templates) or "<tr><td colspan='7'>暂无模板</td></tr>"
    return base_layout("模板库", f"""
    <h1>模板库</h1>
    <div class="card">
      <a class="btn" href="/template_upload">上传/粘贴模板并生成字段映射</a>
      <p class="note">模板用于保存“生产编号字段 + 量测指标字段”的映射。后续新增生产编号时，可一键套用模板生成量测项和指标配置。</p>
    </div>
    <div class="card">
      <h2>模板列表</h2>
      <div class="table-wrap"><table>
        <tr><th>模板名称</th><th>版本</th><th>数据源类型</th><th>生产编号字段</th><th>指标数量</th><th>更新时间</th><th>操作</th></tr>
        {rows_html}
      </table></div>
    </div>
    """, user)


def page_template_upload(user, message=""):
    sample = "生产编号,Dx1,Dy1,Dx2,Dy2,Rz\\nTEST001,0.12,0.15,0.11,0.16,3.25\\n"
    return base_layout("创建模板", f"""
    <h1>创建字段映射模板</h1>
    <div class="card">
      {f'<div class="success">{h(message)}</div>' if message else ''}
      <form method="post" action="/template_parse" enctype="multipart/form-data">
        <div class="form-grid">
          <label>模板名称 *</label><input name="template_name" required placeholder="例如 DxDyRz 标准模板">
          <label>模板版本</label><input name="template_version" value="v1.0">
          <label>数据源类型</label><select name="data_source_type"><option value="excel">Excel 多Sheet</option><option value="csv">CSV</option><option value="auto">自动判断</option></select>
          <label>Excel Sheet 名称</label><input name="excel_sheet_name" placeholder="可空；上传 xlsx 后若为空会先让你选择 Sheet">
          <label>CSV 编码</label><select name="encoding"><option value="auto">auto 自动识别</option><option value="utf-8-sig">utf-8-sig</option><option value="utf-8">utf-8</option><option value="gb18030">gb18030</option><option value="gbk">gbk</option></select>
          <label>CSV 分隔符</label><select name="delimiter"><option value=",">逗号 ,</option><option value="\\t">Tab</option><option value=";">分号 ;</option></select>
          <label>表头所在行</label><input name="header_row_index" value="1">
          <label>上传模板文件</label><input type="file" name="template_file" accept=".csv,.txt,.xlsx,.xlsm">
          <label>或粘贴 CSV 内容</label><textarea name="csv_text" rows="7">{h(sample)}</textarea>
          <label>描述</label><textarea name="description" rows="3" placeholder="可填写适用产品、设备、注意事项"></textarea>
        </div>
        <br><button type="submit">读取表头，进入字段选择</button>
      </form>
      <p class="note">支持 CSV 和 Excel xlsx/xlsm。Excel 文件可以包含多个 Sheet；如果未填写 Sheet 名称，系统会先显示 Sheet 列表让你选择。</p>
    </div>
    """, user)


def page_template_sheet_select(user, form, file_info):
    file_bytes = file_info.get("content") if file_info else b""
    if not file_bytes:
        raise ValueError("请上传 Excel 模板文件")
    token, cached_path = save_template_cache_file(file_bytes, file_info.get("filename", "template.xlsx"))
    sheets = xlsx_list_sheets_from_bytes(file_bytes)
    if not sheets:
        raise ValueError("Excel 文件中没有可读取的 Sheet")
    options = "".join(f'<option value="{h(name)}">{h(name)}</option>' for name, _target in sheets)
    hidden = ""
    for key in ["template_name","template_version","data_source_type","encoding","delimiter","header_row_index","description"]:
        hidden += f'<input type="hidden" name="{key}" value="{h(form.get(key, [""])[0])}">'
    return base_layout("选择Sheet", f"""
    <h1>选择 Excel Sheet</h1>
    <div class="card">
      <p class="note">已识别到 {len(sheets)} 个 Sheet。请选择用于建立字段映射模板的 Sheet。</p>
      <form method="post" action="/template_parse">
        {hidden}
        <input type="hidden" name="template_cache_token" value="{h(token)}">
        <input type="hidden" name="data_source_type" value="excel">
        <div class="form-grid">
          <label>Excel Sheet *</label><select name="excel_sheet_name">{options}</select>
        </div>
        <br><button type="submit">读取该 Sheet 表头</button> <a class="btn secondary" href="/template_upload">返回</a>
      </form>
    </div>
    """, user)


def page_template_mapping(user, form, files):
    template_name = form.get("template_name", [""])[0].strip()
    template_version = form.get("template_version", ["v1.0"])[0].strip() or "v1.0"
    data_source_type = form.get("data_source_type", ["auto"])[0].strip() or "auto"
    encoding = form.get("encoding", ["auto"])[0].strip() or "auto"
    delimiter = form.get("delimiter", [","])[0]
    header_row_index = max(1, safe_int(form.get("header_row_index", [1])[0], 1))
    description = form.get("description", [""])[0].strip()
    excel_sheet_name = form.get("excel_sheet_name", [""])[0].strip()
    csv_text = form.get("csv_text", [""])[0]

    file_info = files.get("template_file")
    cache_token = form.get("template_cache_token", [""])[0].strip()

    source_label = "text-input"
    headers = []
    preview_rows = []

    file_bytes = None
    filename = ""
    if file_info and file_info.get("content"):
        file_bytes = file_info["content"]
        filename = file_info.get("filename", "")
    elif cache_token:
        cached_path = get_template_cache_path(cache_token)
        if not cached_path:
            raise ValueError("模板缓存文件不存在，请重新上传。")
        file_bytes = Path(cached_path).read_bytes()
        filename = cached_path

    suffix = Path(filename or "").suffix.lower()
    inferred_type = data_source_type
    if inferred_type == "auto":
        inferred_type = "excel" if suffix in (".xlsx", ".xlsm", ".xls") else "csv"

    if inferred_type == "excel":
        if suffix == ".xls":
            raise ValueError("当前版本支持 .xlsx/.xlsm；旧版 .xls 请先另存为 .xlsx。")
        if not file_bytes:
            raise ValueError("请选择 Excel 模板文件。")
        if not excel_sheet_name:
            return page_template_sheet_select(user, form, file_info)
        headers, preview_rows, source_label = _parse_xlsx_rows_from_bytes(file_bytes, excel_sheet_name, header_row_index)
        data_source_type = "excel"
    else:
        used_encoding = "text-input"
        if file_bytes:
            csv_text, used_encoding = _decode_template_bytes(file_bytes, encoding)
        elif not csv_text.strip():
            raise ValueError("请上传 CSV/Excel 模板文件，或粘贴 CSV 内容")
        headers, preview_rows = parse_template_csv_from_text(csv_text, delimiter, header_row_index)
        source_label = used_encoding
        data_source_type = "csv"

    if not headers:
        raise ValueError("没有识别到表头字段，请确认表头所在行是否正确。")

    options = "".join(f'<option value="{h(col)}" {"selected" if col in ("生产编号","PN","ProductCode","production_code") else ""}>{h(col)}</option>' for col in headers)

    metric_checks = ""
    ignore_cols = ("生产编号", "PN", "ProductCode", "production_code", "备注", "时间", "量测时间", "日期", "设备", "人员")
    for col in headers:
        default_checked = "" if col in ignore_cols else "checked"
        metric_checks += f"""
        <label style="display:inline-block;margin:6px 18px 6px 0;">
          <input type="checkbox" name="metric_columns" value="{h(col)}" {default_checked}> {h(col)}
        </label>
        """

    preview_header = "".join(f"<th>{h(x)}</th>" for x in headers)
    preview_body = "".join("<tr>" + "".join(f"<td>{h(row.get(col,''))}</td>" for col in headers) + "</tr>" for row in preview_rows[:10])
    if not preview_body:
        preview_body = f"<tr><td colspan='{len(headers)}'>暂无数据行预览，仅识别到表头</td></tr>"

    hidden_fields = f"""
      <input type="hidden" name="template_name" value="{h(template_name)}">
      <input type="hidden" name="template_version" value="{h(template_version)}">
      <input type="hidden" name="data_source_type" value="{h(data_source_type)}">
      <input type="hidden" name="encoding" value="{h(encoding)}">
      <input type="hidden" name="delimiter" value="{h(delimiter)}">
      <input type="hidden" name="header_row_index" value="{h(header_row_index)}">
      <input type="hidden" name="excel_sheet_name" value="{h(excel_sheet_name)}">
      <input type="hidden" name="description" value="{h(description)}">
      <input type="hidden" name="sample_fields_json" value="{h(json.dumps(headers, ensure_ascii=False))}">
    """

    return base_layout("字段映射确认", f"""
    <h1>字段映射确认</h1>
    <div class="card">
      <p class="note">已识别 {len(headers)} 个字段；数据源类型：{h(data_source_type)}；Sheet/编码：{h(excel_sheet_name or source_label)}。请选择哪个字段是生产编号，以及哪些字段作为量测指标。</p>
      <form method="post" action="/template_save">
        {hidden_fields}
        <div class="form-grid">
          <label>模板名称</label><input value="{h(template_name)}" disabled>
          <label>模板版本</label><input value="{h(template_version)}" disabled>
          <label>数据源类型</label><input value="{h(data_source_type)}" disabled>
          <label>Excel Sheet</label><input value="{h(excel_sheet_name)}" disabled>
          <label>生产编号字段 *</label><select name="production_code_column">{options}</select>
          <label>量测指标字段 *</label><div>{metric_checks}</div>
        </div>
        <br><button type="submit">保存模板</button> <a class="btn secondary" href="/template_upload">返回重选</a>
      </form>
    </div>
    <div class="card">
      <h2>数据预览</h2>
      <div class="table-wrap"><table><tr>{preview_header}</tr>{preview_body}</table></div>
    </div>
    """, user)


def page_template_detail(user, template_id):
    conn = get_conn()
    t = conn.execute("SELECT * FROM template_config WHERE id=?", (template_id,)).fetchone()
    if not t:
        conn.close()
        return base_layout("未找到", "<h1>模板不存在</h1>", user)
    metrics = conn.execute("SELECT * FROM template_metric_config WHERE template_id=? ORDER BY sort_order,id", (template_id,)).fetchall()
    conn.close()
    rows_html = "".join(f"""
    <tr>
      <td>{h(m['metric_name'])}</td><td>{h(m['source_column'])}</td><td>{h(m['data_type'])}</td><td>{h(m['unit'])}</td>
      <td>{h(m['target'])}</td><td>{h(m['lsl'])}</td><td>{h(m['usl'])}</td><td>{h(m['lcl'])}</td><td>{h(m['ucl'])}</td>
    </tr>
    """ for m in metrics) or "<tr><td colspan='9'>暂无指标</td></tr>"
    fields = json.loads(t["sample_fields_json"] or "[]")
    return base_layout("模板详情", f"""
    <h1>模板详情：{h(t['template_name'])}</h1>
    <div class="card">
      <p><b>版本：</b>{h(t['template_version'])}</p>
      <p><b>数据源类型：</b>{h(t['data_source_type'])}</p>
      <p><b>Excel Sheet：</b>{h(t['excel_sheet_name'] if 'excel_sheet_name' in t.keys() else '')}</p>
      <p><b>生产编号字段：</b>{h(t['production_code_column'])}</p>
      <p><b>表头字段：</b>{h(', '.join(fields))}</p>
      <p><b>描述：</b>{h(t['description'])}</p>
      <a class="btn secondary" href="/templates">返回模板库</a>
    </div>
    <div class="card">
      <h2>指标字段</h2>
      <div class="table-wrap"><table><tr><th>指标名</th><th>源字段名</th><th>类型</th><th>单位</th><th>Target</th><th>LSL</th><th>USL</th><th>LCL</th><th>UCL</th></tr>{rows_html}</table></div>
    </div>
    """, user)


def page_template_apply(user, production_id):
    conn = get_conn()
    prod = conn.execute("SELECT * FROM production_config WHERE id=?", (production_id,)).fetchone()
    templates = conn.execute("""
        SELECT t.*, COUNT(tm.id) AS metric_count
        FROM template_config t
        LEFT JOIN template_metric_config tm ON tm.template_id=t.id
        GROUP BY t.id
        ORDER BY t.id DESC
    """).fetchall()
    conn.close()
    if not prod:
        return base_layout("错误", "<h1>生产编号不存在</h1>", user)
    template_options = "".join(f'<option value="{t["id"]}">{h(t["template_name"])} / {h(t["template_version"])} / {h(t["data_source_type"])} / {t["metric_count"]}个指标</option>' for t in templates)
    if not template_options:
        template_options = '<option value="">暂无模板，请先到模板库创建</option>'
    return base_layout("套用模板", f"""
    <h1>从模板新增量测项：{h(prod['production_code'])}</h1>
    <div class="card">
      <form method="post" action="/template_apply">
        <input type="hidden" name="production_id" value="{production_id}">
        <div class="form-grid">
          <label>选择模板 *</label><select name="template_id" required>{template_options}</select>
          <label>量测项名称</label><input name="item_name" placeholder="不填则使用模板名称">
          <label>量测工序</label><input name="process_step" placeholder="例如 MEAS_STEP_01">
          <label>量测执行时间</label><input name="execution_time_text" placeholder="例如 工序完成后 / 每日10:00">
          <label>量测设备</label><input name="equipment_name" placeholder="例如 TOOL01">
          <label>实时数据源路径 *</label><input name="data_source_path" required style="min-width:520px" placeholder="例如 \\\\192.168.1.100\\share\\result.csv">
          <label>抓取频率秒</label><input name="scan_frequency_seconds" value="60">
        </div>
        <br><button type="submit">套用模板并生成量测项</button> <a class="btn secondary" href="/items?production_id={production_id}">返回</a>
      </form>
      <p class="note">套用模板会复制模板中的生产编号字段和指标字段到当前生产编号下，不会与模板保持动态绑定，避免模板后续修改影响已上线配置。</p>
    </div>
    """, user)


def handle_template_save(user, form, ip_address):
    require_permission(user, can_manage_config(user))
    template_name = form.get("template_name", [""])[0].strip()
    template_version = form.get("template_version", ["v1.0"])[0].strip() or "v1.0"
    data_source_type = form.get("data_source_type", ["csv"])[0].strip() or "csv"
    encoding = form.get("encoding", ["auto"])[0].strip() or "auto"
    delimiter = form.get("delimiter", [","])[0]
    header_row_index = max(1, safe_int(form.get("header_row_index", [1])[0], 1))
    description = form.get("description", [""])[0].strip()
    excel_sheet_name = form.get("excel_sheet_name", [""])[0].strip()
    production_code_column = form.get("production_code_column", [""])[0].strip()
    metric_columns = [x.strip() for x in form.get("metric_columns", []) if x.strip()]
    sample_fields_json = form.get("sample_fields_json", ["[]"])[0]

    if not template_name:
        raise ValueError("模板名称不能为空")
    if not production_code_column:
        raise ValueError("必须选择生产编号字段")
    if not metric_columns:
        raise ValueError("至少选择一个量测指标字段")
    if production_code_column in metric_columns:
        raise ValueError("生产编号字段不能同时作为量测指标")

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO template_config (
            template_name, template_version, data_source_type, header_row_index, delimiter, encoding, excel_sheet_name,
            production_code_column, sample_fields_json, description, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        template_name, template_version, data_source_type, header_row_index, delimiter, encoding, excel_sheet_name,
        production_code_column, sample_fields_json, description, now_str(), now_str()
    ))
    template_id = cur.lastrowid
    for idx, col in enumerate(metric_columns):
        cur.execute("""
            INSERT INTO template_metric_config (
                template_id, metric_name, source_column, data_type, unit, sort_order, created_at, updated_at
            ) VALUES (?, ?, ?, 'number', '', ?, ?, ?)
        """, (template_id, col, col, idx, now_str(), now_str()))
    conn.commit()
    conn.close()
    write_audit(user.get("username"), "SAVE_TEMPLATE", "template_config", template_id, f"保存模板 {template_name}，指标：{', '.join(metric_columns)}", ip_address)
    return template_id


def handle_template_apply(user, form, ip_address):
    require_permission(user, can_manage_config(user))
    production_id = safe_int(form.get("production_id", [0])[0])
    template_id = safe_int(form.get("template_id", [0])[0])
    item_name_input = form.get("item_name", [""])[0].strip()
    process_step = form.get("process_step", [""])[0].strip()
    execution_time_text = form.get("execution_time_text", [""])[0].strip()
    equipment_name = form.get("equipment_name", [""])[0].strip()
    data_source_path = form.get("data_source_path", [""])[0].strip()
    scan_frequency_seconds = max(10, safe_int(form.get("scan_frequency_seconds", [60])[0], 60))
    if not data_source_path:
        raise ValueError("实时数据源路径不能为空")

    conn = get_conn()
    cur = conn.cursor()
    prod = cur.execute("SELECT * FROM production_config WHERE id=?", (production_id,)).fetchone()
    t = cur.execute("SELECT * FROM template_config WHERE id=?", (template_id,)).fetchone()
    metrics = cur.execute("SELECT * FROM template_metric_config WHERE template_id=? ORDER BY sort_order,id", (template_id,)).fetchall()
    if not prod:
        conn.close()
        raise ValueError("生产编号不存在")
    if not t:
        conn.close()
        raise ValueError("模板不存在")
    if not metrics:
        conn.close()
        raise ValueError("模板下没有指标，无法套用")

    item_name = item_name_input or t["template_name"]
    cur.execute("""
        INSERT INTO measurement_item_config (
            production_id, item_name, process_step, execution_time_text, equipment_name,
            data_source_type, data_source_path, excel_sheet_name, csv_encoding, delimiter, production_code_column,
            scan_frequency_seconds, enabled, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
    """, (
        production_id, item_name, process_step, execution_time_text, equipment_name,
        t["data_source_type"] or "auto", data_source_path, t["excel_sheet_name"] if "excel_sheet_name" in t.keys() else "",
        t["encoding"] or "auto", t["delimiter"] or ",", t["production_code_column"],
        scan_frequency_seconds, now_str()
    ))
    item_id = cur.lastrowid

    for idx, m in enumerate(metrics):
        cur.execute("""
            INSERT INTO metric_config (
                item_id, metric_name, source_column, unit, data_type, target, lsl, usl, lcl, ucl, enabled, sort_order, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """, (
            item_id, m["metric_name"], m["source_column"], m["unit"], m["data_type"],
            m["target"], m["lsl"], m["usl"], m["lcl"], m["ucl"], idx, now_str()
        ))

    cur.execute("""
        INSERT INTO template_apply_log (template_id, production_id, production_code, item_id, applied_by, applied_at, detail)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (template_id, production_id, prod["production_code"], item_id, user.get("username"), now_str(), f"套用模板 {t['template_name']} 到量测项 {item_name}"))
    conn.commit()
    conn.close()
    write_audit(user.get("username"), "APPLY_TEMPLATE", "template_config", template_id, f"套用模板到生产编号 {prod['production_code']}，生成量测项 {item_name}", ip_address)
    return item_id


def page_about(user):
    return base_layout("说明", f"""
    <h1>说明</h1>
    <div class="card">
      <h2>当前版本支持的 CSV / Excel 格式</h2>
      <p>默认支持“每个生产编号一行”的总表结构，CSV 或 Excel 指定 Sheet 均可：</p>
      <pre>生产编号,Dx1,Dy1,Dx2,Dy2,Rz
PROD_A_V1,1.2,2.3,1.1,2.1,0.8
PROD_B_V1,1.5,2.2,1.4,2.0,0.7</pre>
      <p>量测项中配置 <b>生产编号字段名=生产编号</b>，指标中配置 <b>Dx1、Dy1、Dx2、Dy2、Rz</b>，系统会读取当前生产编号对应行。</p>
    </div>
    <div class="card">
      <h2>共享路径注意事项</h2>
      <p class="note">例如：<code>\\\\192.168.1.100\\share\\result.csv</code>。运行本程序的电脑必须能访问该路径，并且 Windows 当前用户要有共享目录读取权限。若部署在 Linux/Docker，建议把共享目录挂载为本地路径，例如 <code>/mnt/metrology/result.csv</code>。</p>
    </div>
    """, user)


# ==========================================================
# HTTP handler
# ==========================================================

class AppHandler(BaseHTTPRequestHandler):
    def send_bytes(self, status, headers, data):
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_html(self, text, status=200, headers=None):
        data = text.encode("utf-8")
        hds = {"Content-Type": "text/html; charset=utf-8"}
        if headers:
            hds.update(headers)
        self.send_bytes(status, hds, data)

    def require_user(self):
        user = current_user(self)
        if not user:
            status, headers, data = redirect("/login")
            self.send_bytes(status, headers, data)
            return None
        return user

    def parse_post_data(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            raw = body.decode("utf-8", errors="replace")
            return parse_qs(raw), {}

        boundary = None
        for part in content_type.split(";"):
            part = part.strip()
            if part.startswith("boundary="):
                boundary = part.split("=", 1)[1].strip().strip('"')
                break
        if not boundary:
            raise ValueError("multipart/form-data 缺少 boundary")

        delimiter = ("--" + boundary).encode("utf-8")
        form = {}
        files = {}
        for part in body.split(delimiter):
            part = part.strip()
            if not part or part == b"--":
                continue
            if part.endswith(b"--"):
                part = part[:-2].strip()
            if b"\r\n\r\n" not in part:
                continue
            header_bytes, data = part.split(b"\r\n\r\n", 1)
            data = data.rstrip(b"\r\n")
            header_text = header_bytes.decode("utf-8", errors="replace")
            name = None
            filename = None
            part_content_type = ""
            for line in header_text.split("\r\n"):
                lower = line.lower()
                if lower.startswith("content-disposition:"):
                    chunks = line.split(";")
                    for chunk in chunks:
                        chunk = chunk.strip()
                        if chunk.startswith("name="):
                            name = chunk.split("=", 1)[1].strip().strip('"')
                        elif chunk.startswith("filename="):
                            filename = chunk.split("=", 1)[1].strip().strip('"')
                elif lower.startswith("content-type:"):
                    part_content_type = line.split(":", 1)[1].strip()
            if not name:
                continue
            if filename:
                files[name] = {"filename": filename, "content": data, "content_type": part_content_type}
            else:
                form.setdefault(name, []).append(data.decode("utf-8", errors="replace"))
        return form, files

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        q = parse_qs(parsed.query)

        if path == "/version":
            self.send_html("<h1>Metrology Config App V2.0</h1><p>PORT=8020</p><p>LAN mode + CSV/XLSX multi-sheet template wizard + UTC+8 + robust file retry + audit log</p>")
            return

        if path == "/login":
            self.send_html(page_login())
            return
        if path == "/logout":
            sid = parse_cookie(self.headers.get("Cookie")).get("sid")
            if sid in SESSIONS:
                write_audit(SESSIONS[sid].get("username"), "LOGOUT", "session", sid[:8], "管理员退出登录", self.client_address[0])
                del SESSIONS[sid]
            status, headers, data = redirect("/login")
            headers["Set-Cookie"] = "sid=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"
            self.send_bytes(status, headers, data)
            return

        user = self.require_user()
        if not user:
            return

        if path == "/":
            self.send_html(page_dashboard(user))
        elif path == "/productions":
            self.send_html(page_productions(user))
        elif path == "/production_new":
            self.send_html(page_production_form(user))
        elif path == "/production_edit":
            self.send_html(page_production_form(user, safe_int(q.get("id", [0])[0])))
        elif path == "/items":
            self.send_html(page_items(user, safe_int(q.get("production_id", [0])[0])))
        elif path == "/item_new":
            self.send_html(page_item_form(user, production_id=safe_int(q.get("production_id", [0])[0])))
        elif path == "/item_edit":
            self.send_html(page_item_form(user, item_id=safe_int(q.get("id", [0])[0])))
        elif path == "/metrics":
            self.send_html(page_metrics(user, safe_int(q.get("item_id", [0])[0])))
        elif path == "/metric_new":
            self.send_html(page_metric_form(user, item_id=safe_int(q.get("item_id", [0])[0])))
        elif path == "/metric_edit":
            self.send_html(page_metric_form(user, metric_id=safe_int(q.get("id", [0])[0])))
        elif path == "/test_collect":
            self.send_html(page_test_collect(user, safe_int(q.get("item_id", [0])[0])))
        elif path == "/collect_now":
            self.send_html(page_collect_now(user, safe_int(q.get("item_id", [0])[0])))
        elif path == "/templates":
            self.send_html(page_templates(user))
        elif path == "/template_upload":
            self.send_html(page_template_upload(user))
        elif path == "/template_detail":
            self.send_html(page_template_detail(user, safe_int(q.get("id", [0])[0])))
        elif path == "/template_apply":
            self.send_html(page_template_apply(user, safe_int(q.get("production_id", [0])[0])))
        elif path == "/results":
            self.send_html(page_results(user, q))
        elif path == "/logs":
            self.send_html(page_logs(user))
        elif path == "/audit_logs":
            self.send_html(page_audit_logs(user))
        elif path == "/export_config":
            production_id = safe_int(q.get("production_id", [0])[0])
            cfg = export_config_json(production_id)
            if not cfg:
                self.send_html(base_layout("错误", "<h1>配置不存在</h1>", user), status=404)
                return
            data = json.dumps(cfg, ensure_ascii=False, indent=2).encode("utf-8")
            filename = f"metrology_config_{cfg['production']['production_code']}.json"
            write_audit(user.get("username"), "EXPORT_CONFIG", "production_config", production_id, f"导出配置 {filename}", self.client_address[0])
            self.send_bytes(200, {
                "Content-Type": "application/json; charset=utf-8",
                "Content-Disposition": f"attachment; filename={filename}"
            }, data)
        elif path == "/import_config":
            self.send_html(page_import_config(user))
        elif path == "/about":
            self.send_html(page_about(user))
        else:
            self.send_html(base_layout("404", "<h1>404 Not Found</h1>", user), status=404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        form, files = self.parse_post_data()

        if path == "/login":
            username = form.get("username", [""])[0].strip()
            password = form.get("password", [""])[0]
            conn = get_conn()
            user = conn.execute("SELECT * FROM admin_user WHERE username=?", (username,)).fetchone()
            conn.close()
            if user and user["password_hash"] == hash_password(password):
                write_audit(username, "LOGIN_SUCCESS", "admin_user", username, "管理员登录成功", self.client_address[0])
                sid = secrets.token_urlsafe(32)
                SESSIONS[sid] = {"username": username, "login_time": now_str()}
                status, headers, data = redirect("/")
                headers["Set-Cookie"] = f"sid={sid}; Path=/; HttpOnly; SameSite=Lax"
                self.send_bytes(status, headers, data)
            else:
                write_audit(username or "unknown", "LOGIN_FAILED", "admin_user", username, "管理员登录失败", self.client_address[0])
                self.send_html(page_login("账号或密码错误"), status=401)
            return

        user = self.require_user()
        if not user:
            return

        try:
            if path == "/production_save":
                self.handle_production_save(form)
            elif path == "/item_save":
                self.handle_item_save(form)
            elif path == "/metric_bulk_add":
                self.handle_metric_bulk_add(form)
            elif path == "/metric_save":
                self.handle_metric_save(form)
            elif path == "/template_parse":
                self.send_html(page_template_mapping(user, form, files))
            elif path == "/template_save":
                tid = handle_template_save(user, form, self.client_address[0])
                status, headers, data = redirect(f"/template_detail?id={tid}")
                self.send_bytes(status, headers, data)
            elif path == "/template_apply":
                item_id = handle_template_apply(user, form, self.client_address[0])
                status, headers, data = redirect(f"/metrics?item_id={item_id}")
                self.send_bytes(status, headers, data)
            elif path == "/import_config":
                config_json = form.get("config_json", [""])[0]
                pid = import_config(config_json)
                write_audit(user.get("username"), "IMPORT_CONFIG", "production_config", pid, "导入生产编号配置 JSON", self.client_address[0])
                self.send_html(page_import_config(user, f"导入成功，生产编号 ID={pid}"))
            else:
                self.send_html(base_layout("404", "<h1>404 Not Found</h1>", user), status=404)
        except Exception as ex:
            self.send_html(base_layout("错误", f"<h1>处理失败</h1><div class='card'><p class='error'>{h(ex)}</p><pre>{h(traceback.format_exc())}</pre></div>", user), status=500)

    def handle_production_save(self, form):
        pid = form.get("id", [""])[0].strip()
        vals = (
            form.get("production_code", [""])[0].strip(),
            form.get("production_name", [""])[0].strip(),
            form.get("product_model", [""])[0].strip(),
            form.get("process_version", [""])[0].strip(),
            form.get("description", [""])[0].strip(),
            form.get("status", ["enabled"])[0],
            now_str()
        )
        conn = get_conn()
        cur = conn.cursor()
        if pid:
            cur.execute("""
            UPDATE production_config SET production_code=?, production_name=?, product_model=?, process_version=?, description=?, status=?, updated_at=? WHERE id=?
            """, vals + (safe_int(pid),))
            new_id = safe_int(pid)
        else:
            cur.execute("""
            INSERT INTO production_config (production_code, production_name, product_model, process_version, description, status, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """, vals)
            new_id = cur.lastrowid
        conn.commit()
        conn.close()
        write_audit(current_user(self).get("username"), "SAVE_PRODUCTION", "production_config", new_id, f"保存生产编号 {vals[0]}", self.client_address[0])
        status, headers, data = redirect(f"/items?production_id={new_id}")
        self.send_bytes(status, headers, data)

    def handle_item_save(self, form):
        item_id = form.get("id", [""])[0].strip()
        production_id = safe_int(form.get("production_id", [0])[0])
        vals = (
            production_id,
            form.get("item_name", [""])[0].strip(),
            form.get("process_step", [""])[0].strip(),
            form.get("execution_time_text", [""])[0].strip(),
            form.get("equipment_name", [""])[0].strip(),
            form.get("data_source_type", ["auto"])[0],
            form.get("data_source_path", [""])[0].strip(),
            form.get("excel_sheet_name", [""])[0].strip(),
            form.get("csv_encoding", ["auto"])[0],
            form.get("delimiter", [","])[0],
            form.get("production_code_column", ["生产编号"])[0].strip(),
            max(10, safe_int(form.get("scan_frequency_seconds", [60])[0], 60)),
            safe_int(form.get("enabled", [1])[0], 1),
            now_str()
        )
        conn = get_conn()
        cur = conn.cursor()
        if item_id:
            cur.execute("""
            UPDATE measurement_item_config SET production_id=?, item_name=?, process_step=?, execution_time_text=?, equipment_name=?, data_source_type=?, data_source_path=?, excel_sheet_name=?, csv_encoding=?, delimiter=?, production_code_column=?, scan_frequency_seconds=?, enabled=?, updated_at=? WHERE id=?
            """, vals + (safe_int(item_id),))
            new_id = safe_int(item_id)
        else:
            cur.execute("""
            INSERT INTO measurement_item_config (production_id, item_name, process_step, execution_time_text, equipment_name, data_source_type, data_source_path, excel_sheet_name, csv_encoding, delimiter, production_code_column, scan_frequency_seconds, enabled, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, vals)
            new_id = cur.lastrowid
        conn.commit()
        conn.close()
        write_audit(current_user(self).get("username"), "SAVE_MEASUREMENT_ITEM", "measurement_item_config", new_id, f"保存量测项 {vals[1]}", self.client_address[0])
        status, headers, data = redirect(f"/metrics?item_id={new_id}")
        self.send_bytes(status, headers, data)

    def handle_metric_bulk_add(self, form):
        item_id = safe_int(form.get("item_id", [0])[0])
        names = form.get("metric_names", [""])[0]
        unit = form.get("unit", [""])[0].strip()
        metric_names = [x.strip() for x in names.replace("，", ",").split(",") if x.strip()]
        conn = get_conn()
        cur = conn.cursor()
        for idx, name in enumerate(metric_names):
            cur.execute("""
            INSERT INTO metric_config (item_id, metric_name, source_column, unit, data_type, enabled, sort_order, updated_at)
            VALUES (?, ?, ?, ?, 'number', 1, ?, ?)
            """, (item_id, name, name, unit, idx, now_str()))
        conn.commit()
        conn.close()
        write_audit(current_user(self).get("username"), "BULK_ADD_METRICS", "metric_config", item_id, f"批量添加指标：{', '.join(metric_names)}", self.client_address[0])
        status, headers, data = redirect(f"/metrics?item_id={item_id}")
        self.send_bytes(status, headers, data)

    def handle_metric_save(self, form):
        metric_id = form.get("id", [""])[0].strip()
        item_id = safe_int(form.get("item_id", [0])[0])
        vals = (
            item_id,
            form.get("metric_name", [""])[0].strip(),
            form.get("source_column", [""])[0].strip(),
            form.get("unit", [""])[0].strip(),
            form.get("data_type", ["number"])[0],
            safe_float(form.get("target", [""])[0]),
            safe_float(form.get("lsl", [""])[0]),
            safe_float(form.get("usl", [""])[0]),
            safe_float(form.get("lcl", [""])[0]),
            safe_float(form.get("ucl", [""])[0]),
            safe_int(form.get("enabled", [1])[0], 1),
            safe_int(form.get("sort_order", [0])[0], 0),
            now_str()
        )
        conn = get_conn()
        cur = conn.cursor()
        if metric_id:
            cur.execute("""
            UPDATE metric_config SET item_id=?, metric_name=?, source_column=?, unit=?, data_type=?, target=?, lsl=?, usl=?, lcl=?, ucl=?, enabled=?, sort_order=?, updated_at=? WHERE id=?
            """, vals + (safe_int(metric_id),))
        else:
            cur.execute("""
            INSERT INTO metric_config (item_id, metric_name, source_column, unit, data_type, target, lsl, usl, lcl, ucl, enabled, sort_order, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, vals)
        conn.commit()
        conn.close()
        write_audit(current_user(self).get("username"), "SAVE_METRIC", "metric_config", metric_id or "new", f"保存指标 {vals[1]}", self.client_address[0])
        status, headers, data = redirect(f"/metrics?item_id={item_id}")
        self.send_bytes(status, headers, data)

    def log_message(self, fmt, *args):
        print("[%s] %s" % (now_str(), fmt % args))


# ==========================================================
# Main
# ==========================================================

def main():
    init_db()
    t = threading.Thread(target=scheduler_loop, daemon=True)
    t.start()
    server = ThreadingHTTPServer((HOST, PORT), AppHandler)
    print("=" * 78)
    print(APP_TITLE)
    print(f"本机监听：http://{HOST}:{PORT}")
    print(f"局域网访问：http://{DISPLAY_IP}:{PORT}")
    print(f"默认账号：{os.environ.get('MDCP_ADMIN_USERNAME', 'admin')}")
    print("默认密码：来自 MDCP_ADMIN_PASSWORD 环境变量；未设置时为 admin123（仅建议本地测试）")
    print(r"CSV路径示例：\\192.168.1.100\share\result.xlsx")
    print("按 Ctrl+C 停止")
    print("=" * 78)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止服务...")
    finally:
        SCHEDULER_STOP.set()
        server.server_close()


if __name__ == "__main__":
    main()
