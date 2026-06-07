#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MDCP V2.3 GitHub rewrite: dashboard pie chart, result deletion, and no-process collection guard."""
import csv, hashlib, html, io, json, math, os, secrets, sqlite3, threading, time, traceback, zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

APP_VERSION='V2.3-GitHub'; APP_TITLE='量测数据采集配置平台 V2.3 - GitHub重写版'
DB_FILE=os.environ.get('MDCP_DB_FILE','metrology_config_v23.db')
HOST=os.environ.get('MDCP_HOST','127.0.0.1'); PORT=int(os.environ.get('MDCP_PORT','8023'))
DISPLAY_IP=os.environ.get('MDCP_DISPLAY_IP','10.21.210.75'); TZ=timezone(timedelta(hours=8))
SESSIONS={}; STOP=threading.Event()

def now(): return datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')
def h(x): return html.escape('' if x is None else str(x))
def si(x,d=0):
    try: return int(x)
    except Exception: return d
def sf(x):
    if x is None or str(x).strip()=='': return None
    try: return float(str(x).strip())
    except Exception: return None
def hpw(p): return hashlib.sha256(('mdcp_salt_'+p).encode()).hexdigest()
def rhash(r): return hashlib.sha256(json.dumps(r,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def ck(header):
    jar=cookies.SimpleCookie();
    if header: jar.load(header)
    return {k:v.value for k,v in jar.items()}
def redir(url): return 302,{'Location':url},b''
def badge(s):
    lab={'MS3_PASS':'MS3达成','MS2_PASS':'仅达MS2','MISS_MS2':'未达MS2','TEXT':'TEXT','SUCCESS':'SUCCESS','PROCESS_STEP_REQUIRED':'缺少工序','TEST_SUCCESS':'测试成功'}.get(str(s),str(s))
    cls='ok' if str(s) in ('MS3_PASS','PASS','SUCCESS','TEST_SUCCESS') else 'warn' if str(s) in ('MS2_PASS','TEXT') else 'bad'
    return f'<span class="badge {cls}">{h(lab)}</span>'

def db():
    c=sqlite3.connect(DB_FILE,timeout=30,check_same_thread=False); c.row_factory=sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA busy_timeout=30000'); return c

def init_db():
    c=db(); q=c.cursor()
    q.execute('CREATE TABLE IF NOT EXISTS user(id INTEGER PRIMARY KEY, username TEXT UNIQUE, password_hash TEXT, created_at TEXT)')
    q.execute('CREATE TABLE IF NOT EXISTS production(id INTEGER PRIMARY KEY, code TEXT UNIQUE, name TEXT, status TEXT DEFAULT "enabled", created_at TEXT, updated_at TEXT)')
    q.execute('''CREATE TABLE IF NOT EXISTS item(id INTEGER PRIMARY KEY, production_id INTEGER, name TEXT, process_step TEXT, process_col TEXT, equipment TEXT, source_type TEXT DEFAULT "auto", source_path TEXT, sheet TEXT, header_row INTEGER DEFAULT 1, encoding TEXT DEFAULT "auto", delimiter TEXT DEFAULT ",", code_col TEXT DEFAULT "生产编号", freq INTEGER DEFAULT 60, enabled INTEGER DEFAULT 1, last_status TEXT, last_time TEXT, created_at TEXT, updated_at TEXT)''')
    q.execute('''CREATE TABLE IF NOT EXISTS metric(id INTEGER PRIMARY KEY, item_id INTEGER, name TEXT, source_col TEXT, unit TEXT, dtype TEXT DEFAULT "number", target REAL, ms2_l REAL, ms2_u REAL, ms3_l REAL, ms3_u REAL, enabled INTEGER DEFAULT 1, sort_order INTEGER DEFAULT 0)''')
    q.execute('''CREATE TABLE IF NOT EXISTS result(id INTEGER PRIMARY KEY, production_code TEXT, item_id INTEGER, item_name TEXT, process_step TEXT, equipment TEXT, metric_name TEXT, value_text TEXT, value_number REAL, unit TEXT, status TEXT, source_path TEXT, row_hash TEXT, metric_hash TEXT UNIQUE, collect_time TEXT, raw_json TEXT)''')
    q.execute('CREATE TABLE IF NOT EXISTS collect_log(id INTEGER PRIMARY KEY, production_code TEXT, item_name TEXT, status TEXT, message TEXT, matched INTEGER, inserted INTEGER, skipped INTEGER, created_at TEXT)')
    q.execute('CREATE TABLE IF NOT EXISTS audit_log(id INTEGER PRIMARY KEY, username TEXT, action TEXT, detail TEXT, ip TEXT, created_at TEXT)')
    if q.execute('SELECT COUNT(*) c FROM user').fetchone()['c']==0:
        q.execute('INSERT INTO user(username,password_hash,created_at) VALUES(?,?,?)',(os.environ.get('MDCP_ADMIN_USERNAME','admin'),hpw(os.environ.get('MDCP_ADMIN_PASSWORD','admin123')),now()))
    elif os.environ.get('MDCP_ADMIN_PASSWORD'):
        q.execute('UPDATE user SET password_hash=? WHERE username=?',(hpw(os.environ['MDCP_ADMIN_PASSWORD']),os.environ.get('MDCP_ADMIN_USERNAME','admin')))
    c.commit(); c.close()

def audit(u,a,d='',ip=''):
    try:
        c=db(); c.execute('INSERT INTO audit_log(username,action,detail,ip,created_at) VALUES(?,?,?,?,?)',(u or 'system',a,d,ip,now())); c.commit(); c.close()
    except Exception: pass

def read_bytes(path):
    if not path: raise FileNotFoundError('数据源路径为空')
    if not os.path.exists(path): raise FileNotFoundError('路径不存在或无权限访问：'+path)
    last=None
    for i in range(3):
        try:
            s1=os.stat(path); time.sleep(.25); s2=os.stat(path)
            if s1.st_size!=s2.st_size or s1.st_mtime_ns!=s2.st_mtime_ns: raise RuntimeError('文件仍在写入，稍后重试')
            data=Path(path).read_bytes(); s3=os.stat(path)
            if s2.st_size!=s3.st_size or s2.st_mtime_ns!=s3.st_mtime_ns: raise RuntimeError('读取期间文件变化，稍后重试')
            return data
        except Exception as e:
            last=e; time.sleep(.6*(i+1))
    raise last

def decode(data, enc='auto'):
    encs=[]
    if enc and enc!='auto': encs.append(enc)
    for e in ['utf-8-sig','utf-8','gb18030','gbk','cp936','big5']:
        if e not in encs: encs.append(e)
    for e in encs:
        try: return data.decode(e),e
        except UnicodeDecodeError: pass
    return data.decode('gb18030',errors='replace'),'gb18030(errors=replace)'

def read_csv_rows(path,enc='auto',delim=','):
    text,used=decode(read_bytes(path),enc); delim='\t' if delim=='\\t' else (delim or ',')
    r=csv.DictReader(io.StringIO(text),delimiter=delim); return r.fieldnames or [],[dict(x) for x in r],used

def col_index(ref):
    letters=''.join(ch for ch in ref if ch.isalpha()).upper(); n=0
    for ch in letters: n=n*26+ord(ch)-64
    return max(0,n-1)
def nt(t):
    t=(t or '').replace('\\','/'); return t.lstrip('/') if t.startswith('xl/') else 'xl/'+t.lstrip('/')
def sheets(data):
    ns={'x':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}; rn={'r':'http://schemas.openxmlformats.org/package/2006/relationships'}; rk='{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        wb=ET.fromstring(z.read('xl/workbook.xml')); rel=ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        rels={x.attrib.get('Id'):x.attrib.get('Target','') for x in rel.findall('r:Relationship',rn)}
        return [(s.attrib.get('name',''),nt(rels.get(s.attrib.get(rk,''),''))) for s in wb.findall('.//x:sheet',ns)]

def read_xlsx_rows(path,sheet='',header=1):
    if Path(path).suffix.lower()=='.xls': raise ValueError('不支持 .xls，请另存为 .xlsx')
    data=read_bytes(path); ns={'x':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        ss=[]
        if 'xl/sharedStrings.xml' in z.namelist():
            root=ET.fromstring(z.read('xl/sharedStrings.xml')); ss=[''.join(t.text or '' for t in si.findall('.//x:t',ns)) for si in root.findall('x:si',ns)]
        sh=sheets(data); target=sh[0]
        if sheet:
            f=[x for x in sh if x[0]==sheet]
            if not f: raise ValueError('找不到Sheet：'+sheet+'；可用：'+'，'.join(x[0] for x in sh))
            target=f[0]
        ws=ET.fromstring(z.read(target[1])); mat=[]
        for row in ws.findall('.//x:sheetData/x:row',ns):
            vals={}
            for cell in row.findall('x:c',ns):
                ci=col_index(cell.attrib.get('r','')); typ=cell.attrib.get('t','')
                if typ=='inlineStr': val=''.join(t.text or '' for t in cell.findall('.//x:t',ns))
                else:
                    v=cell.find('x:v',ns); raw='' if v is None or v.text is None else v.text
                    val=ss[int(raw)] if typ=='s' and raw.isdigit() and int(raw)<len(ss) else raw
                vals[ci]=val
            if vals: mat.append([vals.get(i,'') for i in range(max(vals)+1)])
    hi=max(0,int(header or 1)-1)
    if hi>=len(mat): raise ValueError('表头行超出范围')
    fields=[str(x).strip() for x in mat[hi]]; rows=[]
    for raw in mat[hi+1:]:
        if any(str(x).strip() for x in raw): rows.append({f:raw[i] if i<len(raw) else '' for i,f in enumerate(fields) if f})
    return fields,rows,'xlsx:'+target[0]

def read_source(it):
    st=(it['source_type'] or 'auto').lower(); suffix=Path(it['source_path'] or '').suffix.lower()
    if st=='auto': st='excel' if suffix in ('.xlsx','.xlsm','.xls') else 'csv'
    return read_xlsx_rows(it['source_path'],it['sheet'] or '',it['header_row'] or 1) if st=='excel' else read_csv_rows(it['source_path'],it['encoding'] or 'auto',it['delimiter'] or ',')

def judge(v,l2,u2,l3,u3):
    if v is None: return 'TEXT'
    if l2 is not None and v<l2: return 'MISS_MS2'
    if u2 is not None and v>u2: return 'MISS_MS2'
    if l3 is not None and v<l3: return 'MS2_PASS'
    if u3 is not None and v>u3: return 'MS2_PASS'
    return 'MS3_PASS' if (l3 is not None or u3 is not None) else 'MS2_PASS' if (l2 is not None or u2 is not None) else 'PASS'

def log_collect(pc,item,st,msg,matched=0,ins=0,skip=0):
    c=db(); c.execute('INSERT INTO collect_log(production_code,item_name,status,message,matched,inserted,skipped,created_at) VALUES(?,?,?,?,?,?,?,?)',(pc,item,st,msg,matched,ins,skip,now())); c.commit(); c.close()

def collect_item(iid,dry=False):
    c=db(); q=c.cursor(); it=q.execute('SELECT i.*,p.code production_code FROM item i JOIN production p ON p.id=i.production_id WHERE i.id=?',(iid,)).fetchone()
    if not it: c.close(); return {'ok':False,'status':'NOT_FOUND','message':'量测项不存在'}
    mets=q.execute('SELECT * FROM metric WHERE item_id=? AND enabled=1 ORDER BY sort_order,id',(iid,)).fetchall()
    if not mets: c.close(); return {'ok':False,'status':'NO_METRICS','message':'没有启用指标'}
    proc_col=(it['process_col'] or '').strip(); fixed=(it['process_step'] or '').strip()
    if not proc_col and not fixed:
        msg='未配置固定工序，也未配置工序字段名。系统拒绝无工序采集。'
        if not dry:
            log_collect(it['production_code'],it['name'],'PROCESS_STEP_REQUIRED',msg); q.execute('UPDATE item SET last_status=?,last_time=? WHERE id=?',('PROCESS_STEP_REQUIRED',now(),iid)); c.commit()
        c.close(); return {'ok':False,'status':'PROCESS_STEP_REQUIRED','message':msg}
    ins=skip=0
    try:
        fields,rows,used=read_source(it); code_col=it['code_col'] or '生产编号'
        if code_col not in fields: raise ValueError('找不到生产编号字段：'+code_col+'；当前字段：'+', '.join(fields))
        if proc_col and proc_col not in fields: raise ValueError('找不到工序字段：'+proc_col+'；当前字段：'+', '.join(fields))
        miss=[m['source_col'] for m in mets if m['source_col'] not in fields]
        if miss: raise ValueError('找不到指标字段：'+', '.join(miss))
        matched=[r for r in rows if str(r.get(code_col,'')).strip()==str(it['production_code']).strip()]
        if not matched: raise ValueError('未找到生产编号对应行：'+it['production_code'])
        if proc_col:
            targets=[r for r in matched if str(r.get(proc_col,'')).strip()]; blank=len(matched)-len(targets)
            if not targets: raise ValueError(f'匹配到{len(matched)}行，但工序字段均为空，拒绝采集')
        else: targets=[matched[-1]]; blank=0
        preview=[{'process_step':str(r.get(proc_col,'')).strip() if proc_col else fixed,'metrics':{m['name']:r.get(m['source_col']) for m in mets}} for r in targets[:10]]
        if dry: c.close(); return {'ok':True,'status':'TEST_SUCCESS','message':'测试读取成功','fieldnames':fields,'used':used,'matched_rows':len(matched),'collect_rows':len(targets),'blank_process_rows_skipped':blank,'row_previews':preview}
        for r in targets:
            rowh=rhash(r); ep=str(r.get(proc_col,'')).strip() if proc_col else fixed
            for m in mets:
                vt='' if r.get(m['source_col']) is None else str(r.get(m['source_col'])).strip(); vn=sf(vt) if m['dtype']=='number' else None; st=judge(vn,m['ms2_l'],m['ms2_u'],m['ms3_l'],m['ms3_u'])
                mh=hashlib.sha256(f"{iid}|{m['id']}|{rowh}|{ep}|{vt}".encode()).hexdigest()
                try:
                    q.execute('INSERT INTO result(production_code,item_id,item_name,process_step,equipment,metric_name,value_text,value_number,unit,status,source_path,row_hash,metric_hash,collect_time,raw_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(it['production_code'],iid,it['name'],ep,it['equipment'],m['name'],vt,vn,m['unit'],st,it['source_path'],rowh,mh,now(),json.dumps(r,ensure_ascii=False)))
                    ins+=1
                except sqlite3.IntegrityError: skip+=1
        msg=f'采集成功：匹配{len(matched)}行，采集{len(targets)}行，新增{ins}条，跳过重复{skip}条，空工序跳过{blank}行。'
        q.execute('UPDATE item SET last_status=?,last_time=? WHERE id=?',('SUCCESS',now(),iid)); c.commit(); c.close(); log_collect(it['production_code'],it['name'],'SUCCESS',msg,len(matched),ins,skip)
        return {'ok':True,'status':'SUCCESS','message':msg,'inserted':ins,'skipped':skip,'row_previews':preview}
    except Exception as e:
        msg='读取失败：'+str(e)
        if not dry:
            q.execute('UPDATE item SET last_status=?,last_time=? WHERE id=?',('READ_ERROR',now(),iid)); c.commit(); log_collect(it['production_code'],it['name'],'READ_ERROR',msg)
        c.close(); return {'ok':False,'status':'READ_ERROR','message':msg,'traceback':traceback.format_exc()}

def scheduler():
    last={}
    while not STOP.is_set():
        try:
            c=db(); rows=c.execute('SELECT id,freq FROM item WHERE enabled=1').fetchall(); c.close(); t=time.time()
            for r in rows:
                if t-last.get(r['id'],0)>=max(10,r['freq'] or 60): last[r['id']]=t; collect_item(r['id'])
        except Exception as e: print('scheduler',e)
        STOP.wait(5)

def layout(title,body,user=None):
    css="""body{margin:0;background:#f5f7fb;color:#172033;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',Arial,sans-serif}a{color:#2563eb;text-decoration:none}.top{height:56px;background:#111827;color:white;display:flex;justify-content:space-between;align-items:center;padding:0 22px}.top a{color:#bfdbfe}.wrap{display:flex;min-height:calc(100vh - 56px)}.side{width:220px;background:white;border-right:1px solid #e5e7eb;padding:16px 10px}.side a{display:block;padding:11px 14px;border-radius:10px;color:#374151}.side a:hover{background:#eff6ff}.main{flex:1;padding:24px}.card{background:white;border:1px solid #e5e7eb;border-radius:16px;padding:18px;margin-bottom:18px;box-shadow:0 2px 10px rgba(15,23,42,.04)}h1{margin:0 0 18px;font-size:24px}table{width:100%;border-collapse:collapse;font-size:14px}th,td{border-bottom:1px solid #e5e7eb;padding:9px 8px;text-align:left;white-space:nowrap}th{background:#f9fafb}.tw{overflow:auto}input,select{border:1px solid #d0d5dd;border-radius:10px;padding:9px 10px;min-width:150px}button,.btn{background:#2563eb;border:0;color:#fff;padding:9px 14px;border-radius:10px;font-weight:700;cursor:pointer;display:inline-block}.danger{background:#dc2626!important}.secondary{background:#475467!important}.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:12px}.grid{display:grid;grid-template-columns:repeat(4,minmax(160px,1fr));gap:16px}.label{color:#667085;font-size:13px}.value{font-size:28px;font-weight:800;margin-top:8px}.badge{display:inline-block;padding:3px 8px;border-radius:99px;font-size:12px;font-weight:700}.ok{background:#dcfce7;color:#166534}.warn{background:#ffedd5;color:#9a3412}.bad{background:#fee2e2;color:#991b1b}.note{font-size:13px;color:#667085;line-height:1.7}.login{min-height:100vh;display:flex;align-items:center;justify-content:center;background:linear-gradient(135deg,#0f172a,#1d4ed8)}.login form{width:390px;background:white;border-radius:18px;padding:28px}.login input,.login button{width:100%;margin:8px 0}.chart{width:100%;min-height:240px}pre{background:#0b1020;color:#e5e7eb;padding:12px;border-radius:10px;overflow:auto}span.small{color:#667085;font-size:12px}form.inline{display:inline} """
    if user: body=f"<div class='top'><b>{h(APP_TITLE)}</b><div>版本：{APP_VERSION} ｜ {h(user)} ｜ <a href='/logout'>退出</a></div></div><div class='wrap'><div class='side'><a href='/'>Dashboard</a><a href='/productions'>生产编号</a><a href='/results'>采集结果</a><a href='/logs'>采集日志</a><a href='/audit'>审计日志</a></div><main class='main'>{body}</main></div>"
    return f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{h(title)}</title><style>{css}</style></head><body>{body}</body></html>"

def login_page(err=''):
    return layout('登录',f"<div class='login'><form method='post' action='/login'><h1>{h(APP_TITLE)}</h1><p class='note'>管理员登录</p><p style='color:#dc2626'>{h(err)}</p><input name='username' placeholder='账号' required><input name='password' type='password' placeholder='密码' required><button>登录</button><p class='note'>默认：admin / admin123；局域网使用请设置 MDCP_ADMIN_PASSWORD。</p></form></div>")

def current(req):
    sid=ck(req.headers.get('Cookie')).get('sid'); return SESSIONS.get(sid)
def pct(a,b): return 0 if not b else round(a*100/b,1)
def pie(ms3,ms2,miss,total):
    cx=130; cy=120; r=82; data=[('MS3达成',ms3,'#16a34a'),('仅达MS2',ms2,'#ea580c'),('未达MS2',miss,'#dc2626')]
    paths=''; start=-math.pi/2
    if not total: paths=f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="#eef2f7"/>'
    else:
        for lab,cnt,col in data:
            if cnt<=0: continue
            ang=2*math.pi*cnt/total; end=start+ang
            if cnt==total: paths+=f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{col}"/>'
            else:
                x1,y1=cx+r*math.cos(start),cy+r*math.sin(start); x2,y2=cx+r*math.cos(end),cy+r*math.sin(end)
                paths+=f'<path d="M {cx} {cy} L {x1:.1f} {y1:.1f} A {r} {r} 0 {1 if ang>math.pi else 0} 1 {x2:.1f} {y2:.1f} Z" fill="{col}" stroke="#fff" stroke-width="2"/>'
            start=end
    lg=''; y=78
    for lab,cnt,col in data:
        lg+=f'<rect x="280" y="{y-10}" width="12" height="12" rx="2" fill="{col}"/><text x="300" y="{y}" font-size="13" fill="#344054">{lab}：{cnt}（{pct(cnt,total)}%）</text>'; y+=32
    return f'<svg class="chart" viewBox="0 0 520 260">{paths}<circle cx="{cx}" cy="{cy}" r="34" fill="rgba(255,255,255,.92)"/><text x="{cx}" y="{cy-4}" text-anchor="middle" font-size="22" font-weight="800">{total}</text><text x="{cx}" y="{cy+18}" text-anchor="middle" font-size="12" fill="#667085">结果数</text>{lg}</svg>'

def dashboard(user):
    c=db(); st=c.execute("SELECT COUNT(*) total,SUM(CASE WHEN status IN ('MS3_PASS','PASS') THEN 1 ELSE 0 END) ms3,SUM(CASE WHEN status='MS2_PASS' THEN 1 ELSE 0 END) ms2,SUM(CASE WHEN status='MISS_MS2' THEN 1 ELSE 0 END) miss FROM result").fetchone(); pc=c.execute('SELECT COUNT(*) c FROM production').fetchone()['c']; ic=c.execute('SELECT COUNT(*) c FROM item WHERE enabled=1').fetchone()['c']; recent=c.execute("SELECT * FROM result WHERE status='MISS_MS2' ORDER BY collect_time DESC LIMIT 10").fetchall(); c.close()
    total=st['total'] or 0; ms3=st['ms3'] or 0; ms2=st['ms2'] or 0; miss=st['miss'] or 0
    rows=''.join(f"<tr><td>{h(r['collect_time'])}</td><td>{h(r['production_code'])}</td><td>{h(r['process_step'])}</td><td>{h(r['metric_name'])}</td><td>{h(r['value_text'])}</td><td>{badge(r['status'])}</td></tr>" for r in recent) or '<tr><td colspan=6>暂无</td></tr>'
    return layout('Dashboard',f"<h1>Dashboard</h1><div class='grid'><div class='card'><div class='label'>结果数</div><div class='value'>{total}</div></div><div class='card'><div class='label'>MS3达成率</div><div class='value'>{pct(ms3,total)}%</div></div><div class='card'><div class='label'>MS2达成率</div><div class='value'>{pct(ms3+ms2,total)}%</div></div><div class='card'><div class='label'>未达MS2率</div><div class='value'>{pct(miss,total)}%</div></div><div class='card'><div class='label'>生产编号</div><div class='value'>{pc}</div></div><div class='card'><div class='label'>启用量测项</div><div class='value'>{ic}</div></div></div><div class='card'><h2>状态分布</h2>{pie(ms3,ms2,miss,total)}</div><div class='card'><h2>最近未达MS2</h2><div class='tw'><table><tr><th>时间</th><th>生产编号</th><th>工序</th><th>指标</th><th>值</th><th>状态</th></tr>{rows}</table></div></div>",user)

def productions(user):
    c=db(); rows=c.execute('SELECT p.*,COUNT(i.id) n FROM production p LEFT JOIN item i ON i.production_id=p.id GROUP BY p.id ORDER BY p.id DESC').fetchall(); c.close()
    trs=''.join(f"<tr><td>{h(r['code'])}</td><td>{h(r['name'])}</td><td>{h(r['status'])}</td><td>{r['n']}</td><td><a class='btn' href='/items?pid={r['id']}'>量测项</a></td></tr>" for r in rows) or '<tr><td colspan=5>暂无</td></tr>'
    return layout('生产编号',f"<h1>生产编号管理</h1><div class='card'><form class='row' method='post' action='/production_save'><input name='code' placeholder='生产编号' required><input name='name' placeholder='名称'><button>新增</button></form></div><div class='card'><table><tr><th>生产编号</th><th>名称</th><th>状态</th><th>量测项</th><th>操作</th></tr>{trs}</table></div>",user)

def items_page(user,pid):
    c=db(); p=c.execute('SELECT * FROM production WHERE id=?',(pid,)).fetchone(); rows=c.execute('SELECT * FROM item WHERE production_id=? ORDER BY id DESC',(pid,)).fetchall(); c.close()
    if not p: return layout('错误','<h1>生产编号不存在</h1>',user)
    trs=''.join(f"<tr><td>{h(r['name'])}</td><td>{h(r['process_step'])}</td><td>{h(r['process_col'])}</td><td>{h(r['source_path'])}</td><td>{badge(r['last_status'] or 'NA')}</td><td><a class='btn' href='/metrics?iid={r['id']}'>指标</a> <a class='btn secondary' href='/test?iid={r['id']}'>测试</a> <a class='btn secondary' href='/collect?iid={r['id']}'>采集</a></td></tr>" for r in rows) or '<tr><td colspan=6>暂无</td></tr>'
    form=f"<form method='post' action='/item_save'><input type='hidden' name='pid' value='{pid}'><div class='row'><input name='name' placeholder='量测项名称' required><input name='process_step' placeholder='固定工序；和工序字段二选一'><input name='process_col' placeholder='工序字段名'><input name='equipment' placeholder='设备'><select name='source_type'><option value='auto'>auto</option><option value='csv'>csv</option><option value='excel'>excel</option></select><input name='source_path' placeholder='数据源路径' style='min-width:360px' required><input name='sheet' placeholder='Excel Sheet'><input name='code_col' value='生产编号'><button>新增量测项</button></div><p class='note'>必须填写固定工序或工序字段名，否则会被禁止保存/采集。</p></form>"
    return layout('量测项',f"<h1>量测项：{h(p['code'])}</h1><div class='card'>{form}</div><div class='card'><table><tr><th>量测项</th><th>固定工序</th><th>工序字段</th><th>数据源</th><th>状态</th><th>操作</th></tr>{trs}</table></div>",user)

def metrics_page(user,iid):
    c=db(); it=c.execute('SELECT * FROM item WHERE id=?',(iid,)).fetchone(); rows=c.execute('SELECT * FROM metric WHERE item_id=? ORDER BY sort_order,id',(iid,)).fetchall(); c.close()
    trs=''.join(f"<tr><td>{h(r['name'])}</td><td>{h(r['source_col'])}</td><td>{h(r['unit'])}</td><td>{h(r['ms2_l'])}</td><td>{h(r['ms2_u'])}</td><td>{h(r['ms3_l'])}</td><td>{h(r['ms3_u'])}</td></tr>" for r in rows) or '<tr><td colspan=7>暂无</td></tr>'
    return layout('指标',f"<h1>指标配置：{h(it['name'] if it else '')}</h1><div class='card'><form class='row' method='post' action='/metric_bulk'><input type='hidden' name='iid' value='{iid}'><input name='names' value='Dx1,Dy1,Dx2,Dy2,Rz' style='min-width:360px'><input name='unit' placeholder='单位'><button>批量添加</button></form></div><div class='card'><table><tr><th>指标</th><th>源字段</th><th>单位</th><th>MS2下</th><th>MS2上</th><th>MS3下</th><th>MS3上</th></tr>{trs}</table></div>",user)

def results_page(user,q):
    pc=q.get('production_code',[''])[0].strip(); metric=q.get('metric_name',[''])[0].strip(); where=[]; params=[]
    if pc: where.append('production_code LIKE ?'); params.append('%'+pc+'%')
    if metric: where.append('metric_name LIKE ?'); params.append('%'+metric+'%')
    sql='SELECT * FROM result'+((' WHERE '+' AND '.join(where)) if where else '')+' ORDER BY collect_time DESC LIMIT 500'
    c=db(); rows=c.execute(sql,params).fetchall(); c.close()
    trs=''.join(f"<tr><td><input form='bulk' type='checkbox' name='ids' value='{r['id']}'></td><td>{h(r['collect_time'])}</td><td>{h(r['production_code'])}</td><td>{h(r['item_name'])}</td><td>{h(r['process_step'])}</td><td>{h(r['metric_name'])}</td><td>{h(r['value_text'])}</td><td>{badge(r['status'])}</td><td><form class='inline' method='post' action='/result_delete' onsubmit=\"return confirm('确认删除？')\"><input type='hidden' name='id' value='{r['id']}'><button class='danger'>删除</button></form></td></tr>" for r in rows) or '<tr><td colspan=9>暂无</td></tr>'
    return layout('采集结果',f"<h1>采集结果</h1><div class='card'><form class='row' method='get'><input name='production_code' value='{h(pc)}' placeholder='生产编号'><input name='metric_name' value='{h(metric)}' placeholder='指标'><button>查询</button><a class='btn secondary' href='/results'>重置</a></form><form id='bulk' method='post' action='/results_bulk_delete' onsubmit=\"return confirm('确认删除勾选结果？')\"><button class='danger'>删除勾选结果</button></form></div><div class='card'><div class='tw'><table><tr><th>选</th><th>时间</th><th>生产编号</th><th>量测项</th><th>工序</th><th>指标</th><th>值</th><th>状态</th><th>操作</th></tr>{trs}</table></div></div>",user)

def logs_page(user):
    c=db(); rows=c.execute('SELECT * FROM collect_log ORDER BY created_at DESC LIMIT 300').fetchall(); c.close()
    trs=''.join(f"<tr><td>{h(r['created_at'])}</td><td>{h(r['production_code'])}</td><td>{h(r['item_name'])}</td><td>{badge(r['status'])}</td><td>{h(r['message'])}</td></tr>" for r in rows) or '<tr><td colspan=5>暂无</td></tr>'
    return layout('采集日志',f"<h1>采集日志</h1><div class='card'><table><tr><th>时间</th><th>生产编号</th><th>量测项</th><th>状态</th><th>信息</th></tr>{trs}</table></div>",user)

def audit_page(user):
    c=db(); rows=c.execute('SELECT * FROM audit_log ORDER BY created_at DESC LIMIT 300').fetchall(); c.close()
    trs=''.join(f"<tr><td>{h(r['created_at'])}</td><td>{h(r['username'])}</td><td>{h(r['action'])}</td><td>{h(r['ip'])}</td><td>{h(r['detail'])}</td></tr>" for r in rows) or '<tr><td colspan=5>暂无</td></tr>'
    return layout('审计日志',f"<h1>审计日志</h1><div class='card'><table><tr><th>时间</th><th>用户</th><th>动作</th><th>IP</th><th>详情</th></tr>{trs}</table></div>",user)

class H(BaseHTTPRequestHandler):
    def sendb(self,s,hs,data):
        self.send_response(s); [self.send_header(k,v) for k,v in hs.items()]; self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
    def html(self,x,s=200): self.sendb(s,{'Content-Type':'text/html; charset=utf-8'},x.encode())
    def need(self):
        u=current(self)
        if not u:
            s,hs,d=redir('/login'); self.sendb(s,hs,d); return None
        return u
    def post(self):
        n=int(self.headers.get('Content-Length','0')); return parse_qs(self.rfile.read(n).decode('utf-8',errors='replace'))
    def do_GET(self):
        p=urlparse(self.path); q=parse_qs(p.query)
        if p.path=='/version': self.html(f'<h1>{APP_TITLE}</h1><p>{APP_VERSION}</p><p>PORT={PORT}</p>'); return
        if p.path=='/login': self.html(login_page()); return
        if p.path=='/logout':
            sid=ck(self.headers.get('Cookie')).get('sid')
            if sid in SESSIONS: audit(SESSIONS[sid],'LOGOUT','退出',self.client_address[0]); del SESSIONS[sid]
            s,hs,d=redir('/login'); hs['Set-Cookie']='sid=; Path=/; Max-Age=0; HttpOnly'; self.sendb(s,hs,d); return
        u=self.need();
        if not u: return
        if p.path=='/': self.html(dashboard(u))
        elif p.path=='/productions': self.html(productions(u))
        elif p.path=='/items': self.html(items_page(u,si(q.get('pid',[0])[0])))
        elif p.path=='/metrics': self.html(metrics_page(u,si(q.get('iid',[0])[0])))
        elif p.path=='/test': self.html(layout('测试读取',f"<h1>测试读取</h1><div class='card'><pre>{h(json.dumps(collect_item(si(q.get('iid',[0])[0]),True),ensure_ascii=False,indent=2))}</pre></div>",u))
        elif p.path=='/collect': self.html(layout('立即采集',f"<h1>立即采集</h1><div class='card'><pre>{h(json.dumps(collect_item(si(q.get('iid',[0])[0]),False),ensure_ascii=False,indent=2))}</pre><a class='btn' href='/results'>查看结果</a></div>",u))
        elif p.path=='/results': self.html(results_page(u,q))
        elif p.path=='/logs': self.html(logs_page(u))
        elif p.path=='/audit': self.html(audit_page(u))
        else: self.html(layout('404','<h1>404</h1>',u),404)
    def do_POST(self):
        p=urlparse(self.path); f=self.post()
        if p.path=='/login':
            un=f.get('username',[''])[0].strip(); pw=f.get('password',[''])[0]
            c=db(); r=c.execute('SELECT * FROM user WHERE username=?',(un,)).fetchone(); c.close()
            if r and r['password_hash']==hpw(pw):
                sid=secrets.token_urlsafe(32); SESSIONS[sid]=un; audit(un,'LOGIN_SUCCESS','登录成功',self.client_address[0]); s,hs,d=redir('/'); hs['Set-Cookie']=f'sid={sid}; Path=/; HttpOnly'; self.sendb(s,hs,d)
            else: audit(un or 'unknown','LOGIN_FAILED','登录失败',self.client_address[0]); self.html(login_page('账号或密码错误'),401)
            return
        u=self.need();
        if not u: return
        try:
            if p.path=='/production_save':
                code=f.get('code',[''])[0].strip(); name=f.get('name',[''])[0].strip(); c=db(); c.execute('INSERT INTO production(code,name,status,created_at,updated_at) VALUES(?,?,?,?,?)',(code,name,'enabled',now(),now())); c.commit(); c.close(); audit(u,'SAVE_PRODUCTION',code,self.client_address[0]); s,hs,d=redir('/productions'); self.sendb(s,hs,d)
            elif p.path=='/item_save':
                ps=f.get('process_step',[''])[0].strip(); pc=f.get('process_col',[''])[0].strip()
                if not ps and not pc: raise ValueError('量测项必须填写固定工序或工序字段名')
                c=db(); c.execute('INSERT INTO item(production_id,name,process_step,process_col,equipment,source_type,source_path,sheet,code_col,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(si(f.get('pid',[0])[0]),f.get('name',[''])[0].strip(),ps,pc,f.get('equipment',[''])[0].strip(),f.get('source_type',['auto'])[0],f.get('source_path',[''])[0].strip(),f.get('sheet',[''])[0].strip(),f.get('code_col',['生产编号'])[0].strip(),now(),now())); iid=c.execute('SELECT last_insert_rowid() x').fetchone()['x']; c.commit(); c.close(); audit(u,'SAVE_ITEM',str(iid),self.client_address[0]); s,hs,d=redir('/metrics?iid='+str(iid)); self.sendb(s,hs,d)
            elif p.path=='/metric_bulk':
                iid=si(f.get('iid',[0])[0]); names=[x.strip() for x in f.get('names',[''])[0].replace('，',',').split(',') if x.strip()]; unit=f.get('unit',[''])[0].strip(); c=db()
                for i,n in enumerate(names): c.execute('INSERT INTO metric(item_id,name,source_col,unit,dtype,enabled,sort_order) VALUES(?,?,?,?,?,?,?)',(iid,n,n,unit,'number',1,i))
                c.commit(); c.close(); audit(u,'BULK_ADD_METRICS',','.join(names),self.client_address[0]); s,hs,d=redir('/metrics?iid='+str(iid)); self.sendb(s,hs,d)
            elif p.path=='/result_delete':
                rid=si(f.get('id',[0])[0]); c=db(); c.execute('DELETE FROM result WHERE id=?',(rid,)); c.commit(); c.close(); audit(u,'DELETE_RESULT',str(rid),self.client_address[0]); s,hs,d=redir('/results'); self.sendb(s,hs,d)
            elif p.path=='/results_bulk_delete':
                ids=[si(x) for x in f.get('ids',[]) if si(x)>0]; c=db();
                if ids: c.execute('DELETE FROM result WHERE id IN ('+','.join('?' for _ in ids)+')',ids)
                c.commit(); c.close(); audit(u,'BULK_DELETE_RESULTS',str(len(ids)),self.client_address[0]); s,hs,d=redir('/results'); self.sendb(s,hs,d)
            else: self.html(layout('404','<h1>404</h1>',u),404)
        except Exception as e:
            self.html(layout('错误',f'<h1>处理失败</h1><div class="card"><p style="color:#dc2626">{h(e)}</p><pre>{h(traceback.format_exc())}</pre></div>',u),500)
    def log_message(self,fmt,*args): print('[%s] %s'%(now(),fmt%args))

def main():
    init_db(); threading.Thread(target=scheduler,daemon=True).start(); srv=ThreadingHTTPServer((HOST,PORT),H)
    print('='*70); print(APP_TITLE); print(f'本机监听: http://{HOST}:{PORT}')
    if HOST=='0.0.0.0': print(f'局域网访问: http://{DISPLAY_IP}:{PORT}')
    print('账号:',os.environ.get('MDCP_ADMIN_USERNAME','admin'),' 默认密码：admin123；正式使用请设置 MDCP_ADMIN_PASSWORD'); print('='*70)
    try: srv.serve_forever()
    except KeyboardInterrupt: pass
    finally: STOP.set(); srv.server_close()
if __name__=='__main__': main()
