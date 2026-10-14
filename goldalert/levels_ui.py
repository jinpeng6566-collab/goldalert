"""目标价位设置：本地网页界面。

双击「设置目标价位.bat」会启动一个只监听本机（127.0.0.1）的小服务，
并自动打开浏览器。页面上可以：

- 增删改关键价位、启用 / 停用单个价位；
- 调整接近距离、冷却时间、重新武装距离和各提醒开关；
- 让 AI 按最近采集的数据给一组建议价位，满意就一键采纳。

安全：只监听本机，且所有接口都要求启动时随机生成的一次性令牌，
别的网页没法偷偷读你的设置或改你的价位。
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import socket
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from dotenv import load_dotenv

from .ai import MarketAnalyzer
from .config import Config
from .levels import LEVEL_STATE_KEY, SOURCE_LABEL, LevelSettings, LevelStore
from .params import apply as apply_params, collect as collect_params
from .sources import Quote, create_session
from .store import HistoryStore, StateStore
from .util import ensure_utf8_streams, fmt_ts, now_cn, setup_logging

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PORT = 8765
PING_MARK = "goldalert-levels-ui"
LIVE_WINDOW_SECONDS = 90.0
RECENT_ALERT_SECONDS = 600.0


def read_last_record(path) -> dict | None:
    """只读历史文件尾部，避免每次刷新都扫整个文件。"""
    target = Path(path)
    try:
        with target.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            if size <= 0:
                return None
            fh.seek(max(0, size - 8192))
            chunk = fh.read().decode("utf-8", "replace")
    except OSError:
        return None

    for line in reversed(chunk.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None


HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>GoldAlert 目标价位</title>
<style>
:root{
  --bg:#f4f5f7;--panel:#ffffff;--panel-2:#f7f8fa;--line:#e8eaf0;--line-2:#f1f3f7;
  --text:#101828;--muted:#667085;--faint:#98a2b3;
  --brand:#0e7a46;--brand-hi:#128a50;--brand-soft:#e8f3ec;--brand-ring:rgba(14,122,70,.18);
  --up:#d92d20;--up-soft:#fdeceb;--down:#0e8a5a;--down-soft:#e7f4ee;
  --warn:#b45309;--warn-soft:#fdf3e3;--danger:#c0392b;--danger-soft:#fdecea;
  --info:#2563eb;--info-soft:#eef2ff;--ai:#7c3aed;--ai-soft:#f3eefe;
  --radius:16px;--radius-sm:10px;
  --shadow:0 1px 2px rgba(16,24,40,.04),0 10px 26px -14px rgba(16,24,40,.14);
  --shadow-hover:0 1px 2px rgba(16,24,40,.05),0 16px 34px -16px rgba(16,24,40,.20);
}
@media (prefers-color-scheme:dark){
  :root{
    --bg:#0d1014;--panel:#15181e;--panel-2:#1a1e25;--line:#252a33;--line-2:#1e222a;
    --text:#e7ebf0;--muted:#98a2b3;--faint:#6b7480;
    --brand:#36a36d;--brand-hi:#41b47c;--brand-soft:#12271b;--brand-ring:rgba(54,163,109,.22);
    --up:#f1706b;--up-soft:#33201f;--down:#3bb07c;--down-soft:#132a1f;
    --warn:#e0a33a;--warn-soft:#332a15;--danger:#ef6b6b;--danger-soft:#331b1b;
    --info:#7aa2f7;--info-soft:#182238;--ai:#b794f6;--ai-soft:#241c38;
    --shadow:0 1px 2px rgba(0,0,0,.4),0 18px 40px -22px rgba(0,0,0,.75);
    --shadow-hover:0 1px 2px rgba(0,0,0,.45),0 24px 48px -24px rgba(0,0,0,.85);
  }
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{
  margin:0;background:var(--bg);color:var(--text);
  font:15px/1.6 "Microsoft YaHei UI","PingFang SC","Hiragino Sans GB","Segoe UI",system-ui,sans-serif;
  -webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility;
}
body::before{
  content:"";position:fixed;inset:0 0 auto 0;height:240px;pointer-events:none;z-index:0;
  background:
    radial-gradient(900px 320px at 50% -160px,rgba(14,122,70,.10),transparent 70%),
    radial-gradient(620px 260px at 100% -120px,rgba(37,99,235,.05),transparent 70%);
}
::selection{background:var(--brand-soft);color:var(--text)}
.wrap{position:relative;z-index:1;max-width:1060px;margin:0 auto;padding:0 20px 48px}
.bar{position:sticky;top:0;z-index:5;display:flex;justify-content:space-between;align-items:center;gap:14px;
  padding:14px 2px;margin:0 -2px 12px;background:var(--bg);
  background:color-mix(in srgb,var(--bg) 78%,transparent);
  backdrop-filter:blur(10px) saturate(140%);-webkit-backdrop-filter:blur(10px) saturate(140%);
  border-bottom:1px solid var(--line-2)}
.brand{display:flex;align-items:center;gap:10px;font-size:15px;font-weight:700;letter-spacing:.1px}
.brand i{width:8px;height:8px;border-radius:99px;background:var(--brand);
  box-shadow:0 0 0 4px var(--brand-soft);flex:none}
.brand em{font-style:normal;font-weight:500;color:var(--faint);font-size:12.5px;
  padding-left:10px;border-left:1px solid var(--line)}
.status{display:inline-flex;align-items:center;gap:7px;font-size:12.5px;color:var(--muted);
  background:var(--panel);border:1px solid var(--line);border-radius:999px;padding:5px 12px;white-space:nowrap}
.status i{width:7px;height:7px;border-radius:99px;background:var(--faint);flex:none}
.status.st-ok i{background:var(--brand);box-shadow:0 0 0 3px var(--brand-soft)}
.status.st-warn i{background:var(--warn);box-shadow:0 0 0 3px var(--warn-soft)}
.status.st-off i{background:var(--danger);box-shadow:0 0 0 3px var(--danger-soft)}
h1{font-size:20px;margin:0 0 8px;font-weight:700;letter-spacing:-.01em}
.meta{color:var(--muted);font-size:12.5px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);
  padding:20px;margin-top:14px;box-shadow:var(--shadow);
  transition:box-shadow .18s ease,border-color .18s ease}
.card:hover{box-shadow:var(--shadow-hover)}
.chead{display:flex;justify-content:space-between;align-items:baseline;gap:12px;margin-bottom:14px}
h2{font-size:14px;margin:0;font-weight:700;color:var(--text);letter-spacing:.1px}
h2::before{content:"";display:inline-block;width:3px;height:13px;border-radius:2px;
  background:var(--brand);margin-right:9px;vertical-align:-2px}
.pill{font-size:12px;color:var(--muted);background:var(--panel-2);border:1px solid var(--line);
  padding:2px 10px;border-radius:999px;font-variant-numeric:tabular-nums}
.hero{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:16px;
  padding:15px 18px;background:linear-gradient(180deg,var(--panel-2),var(--panel) 62%)}
.hero-line{display:flex;align-items:baseline;gap:10px}
.price{font-size:33px;font-weight:800;letter-spacing:-1px;line-height:1.05;
  font-variant-numeric:tabular-nums}
.unit{color:var(--faint);font-size:13px}
.chg{display:inline-block;min-height:21px;font-size:12.5px;font-weight:700;padding:2px 10px;
  border-radius:999px;font-variant-numeric:tabular-nums;margin-top:4px}
.chg.up{color:var(--up);background:var(--up-soft)}
.chg.down{color:var(--down);background:var(--down-soft)}
.stats{display:flex;flex-wrap:wrap;gap:20px}
.stat span{display:block;color:var(--faint);font-size:11.5px;letter-spacing:.4px;margin-bottom:2px}
.stat b{font-size:14.5px;font-weight:700;font-variant-numeric:tabular-nums;letter-spacing:-.2px}
.tblwrap{overflow-x:auto;margin:0 -4px}
table{width:100%;border-collapse:collapse;font-size:14px}
th{color:var(--faint);font-weight:600;font-size:11.5px;letter-spacing:.5px;text-align:left;
  padding:0 12px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:12px;border-bottom:1px solid var(--line-2);vertical-align:middle;transition:background .12s ease}
tbody tr:last-child td{border-bottom:none}
tbody tr:hover td{background:var(--panel-2)}
th.num,td.num{text-align:right;font-variant-numeric:tabular-nums}
td.strong{font-weight:700;letter-spacing:-.2px}
td.nearnum{color:var(--warn);font-weight:700}
tr.off td{opacity:.42}
td.ops{text-align:right;white-space:nowrap}
.tag{display:inline-block;font-size:11.5px;font-weight:600;padding:3px 10px;border-radius:999px;
  background:var(--line-2);color:var(--muted);white-space:nowrap}
.tag.src-manual{background:var(--info-soft);color:var(--info)}
.tag.src-ai{background:var(--ai-soft);color:var(--ai)}
.tag.near{background:var(--warn-soft);color:var(--warn)}
.tag.fired{background:var(--danger-soft);color:var(--danger)}
.tag.off{background:var(--line-2);color:var(--faint)}
.drift{display:flex;flex-direction:column;gap:3px}
.drow{display:grid;grid-template-columns:168px 1fr 92px;gap:16px;align-items:center;
  padding:7px 8px;border-radius:10px;transition:background .12s ease}
.drow:hover{background:var(--panel-2)}
.drow.dim{opacity:.4}
.dlabel{display:flex;justify-content:space-between;gap:8px;font-size:12.5px}
.dname{color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.dprice{font-weight:700;font-variant-numeric:tabular-nums}
.dbar{position:relative;height:7px;background:var(--line-2);border-radius:999px}
.dmid{position:absolute;left:50%;top:-3px;bottom:-3px;width:1px;background:var(--faint);opacity:.35;z-index:1}
.dfill{position:absolute;top:0;bottom:0;border-radius:999px}
.dfill.above{background:linear-gradient(90deg,#e6a852,#d98d33)}
.dfill.below{background:linear-gradient(90deg,#5b93e8,#3f78d6)}
.ddot{font-size:12.5px;text-align:right;font-variant-numeric:tabular-nums;font-weight:600}
.ddot.above{color:#c07a24}
.ddot.below{color:#3b7ae0}
button{padding:9px 16px;border:1px solid var(--brand);background:var(--brand);color:#fff;
  border-radius:var(--radius-sm);font:inherit;font-size:14px;font-weight:600;cursor:pointer;
  transition:background .15s ease,border-color .15s ease,box-shadow .15s ease,transform .06s ease;
  box-shadow:0 1px 2px rgba(16,24,40,.08)}
button:hover{background:var(--brand-hi);border-color:var(--brand-hi)}
button:active{transform:translateY(1px)}
button:focus-visible{outline:none;box-shadow:0 0 0 3px var(--brand-ring)}
button:disabled{opacity:.45;cursor:default;transform:none}
button.ghost{background:var(--panel);color:var(--text);border-color:var(--line);box-shadow:none}
button.ghost:hover{background:var(--panel-2);border-color:var(--faint)}
button.link{background:none;border:none;box-shadow:none;color:var(--muted);padding:3px 8px;
  font-size:12.5px;font-weight:600;border-radius:8px}
button.link:hover{background:var(--info-soft);color:var(--info)}
button.link.danger{color:var(--danger)}
button.link.danger:hover{background:var(--danger-soft);color:var(--danger)}
input{padding:9px 12px;border:1px solid var(--line);border-radius:var(--radius-sm);font:inherit;
  font-size:14px;background:var(--panel);color:var(--text);outline:none;
  transition:border-color .15s ease,box-shadow .15s ease}
input:hover{border-color:var(--faint)}
input:focus{border-color:var(--brand);box-shadow:0 0 0 3px var(--brand-ring)}
input[type=number]{width:140px;font-variant-numeric:tabular-nums}
textarea{width:100%;min-height:64px;resize:vertical;padding:9px 12px;border:1px solid var(--line);
  border-radius:var(--radius-sm);font:inherit;font-size:14px;background:var(--panel);color:var(--text);
  outline:none;transition:border-color .15s ease,box-shadow .15s ease;line-height:1.6}
textarea:hover{border-color:var(--faint)}
textarea:focus{border-color:var(--brand);box-shadow:0 0 0 3px var(--brand-ring)}
.qa{margin-top:12px;padding:13px 15px;border:1px solid var(--line);border-radius:12px;
  background:var(--brand-soft);color:var(--text);font-size:13.5px;line-height:1.75;white-space:pre-wrap}
.qa.bad{background:var(--danger-soft);color:var(--danger)}
.row{display:flex;flex-wrap:wrap;gap:10px;align-items:center}
.hint{font-size:13px;min-height:18px;margin-top:10px;font-weight:600}
.hint.good{color:var(--brand)}
.hint.bad{color:var(--danger)}
.alert{background:var(--danger-soft);color:var(--danger);padding:13px 15px;border-radius:12px;
  margin-top:10px;font-size:13.5px;line-height:1.7}
.empty{color:var(--muted);font-size:13px;text-align:center;padding:18px 4px 8px}
.group{border-top:1px solid var(--line);padding-top:18px;margin-top:18px}
.group:first-child{border-top:none;padding-top:0;margin-top:0}
.ghead{margin-bottom:10px}
.ghead b{font-size:14px;font-weight:700}
.ghead .meta{display:block;margin-top:3px;line-height:1.7}
.prow{display:grid;grid-template-columns:170px 200px 1fr;gap:16px;align-items:start;
  padding:10px 8px;border-radius:10px;transition:background .12s ease}
.prow:hover{background:var(--panel-2)}
.pname{font-size:13.5px;font-weight:600;padding-top:8px}
.pin{display:flex;align-items:center;gap:8px}
.pin input[type=number]{width:104px}
.pin .unit{color:var(--muted);font-size:12.5px}
.pdesc{font-size:12.5px;color:var(--muted);line-height:1.75;padding-top:8px}
.chk{display:inline-flex;align-items:center;gap:8px;font-size:13.5px;padding-top:6px}
.chk input[type=checkbox]{appearance:none;-webkit-appearance:none;width:18px;height:18px;
  padding:0;border:1.5px solid var(--line);border-radius:5px;background:var(--panel);
  cursor:pointer;position:relative;transition:background .15s ease,border-color .15s ease}
.chk input[type=checkbox]:hover{border-color:var(--faint)}
.chk input[type=checkbox]:checked{background:var(--brand);border-color:var(--brand)}
.chk input[type=checkbox]:checked::after{content:";position:absolute;left:5px;top:1px;
  width:4px;height:9px;border:solid #fff;border-width:0 2px 2px 0;transform:rotate(45deg)}
footer{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:12px;
  margin-top:22px;color:var(--faint);font-size:12px}
footer a{color:var(--muted);text-decoration:none;border-bottom:1px dashed var(--faint);padding-bottom:1px}
footer a:hover{color:var(--danger);border-color:var(--danger)}
@media (max-width:820px){
  .price{font-size:28px}
  .prow{grid-template-columns:1fr;gap:6px}
  .pname,.pdesc{padding-top:0}
  .drow{grid-template-columns:132px 1fr 80px;gap:10px}
}
@media (max-width:560px){
  .wrap{padding:0 12px 40px}
  .card{padding:16px}
  .drow{grid-template-columns:1fr;gap:4px}
  .ddot{text-align:left}
  .bar{padding:12px 0}
}
</style>
</head>
<body>
<div class="wrap">
  <div class="bar">
    <div class="brand"><i></i>GoldAlert <em>目标价位</em></div>
    <div class="status" id="meta"><i></i><span id="meta-text">正在连接…</span></div>
  </div>

  <div id="alert" class="alert" style="display:none"></div>
  <div class="hint" id="hint"></div>

  <div class="card hero">
    <div>
      <div class="hero-line"><span class="price" id="price">—</span><span class="unit">美元/盎司</span></div>
      <div class="hero-line"><span class="chg" id="chg"></span></div>
    </div>
    <div class="stats" id="stats"></div>
  </div>

  <div class="card">
    <div class="chead"><h2>AI 提问</h2><span class="meta" id="q-state"></span></div>
    <div class="meta">用一句话问关于当前盘面的问题，AI 只根据监控采到的行情数据回答，不预测、不给买卖建议。</div>
    <textarea id="q-input" maxlength="200" rows="2" placeholder="例如：现在是震荡还是单边？这波涨得算快吗？现价上下最近的位置大概在哪？"></textarea>
    <div class="row" style="margin-top:10px">
      <button onclick="askQuestion()" id="q-btn">提问</button>
      <button class="ghost" onclick="clearQuestion()" id="q-clear">清空</button>
    </div>
    <div class="qa" id="q-answer" style="display:none"></div>
  </div>

  <div class="card">
    <div class="chead"><h2>我盯着的价位</h2><span class="pill" id="lv-count"></span></div>
    <div class="tblwrap">
      <table>
        <thead><tr>
          <th class="num">价位</th><th>说明</th><th>来源</th><th>状态</th>
          <th class="num">距现价</th><th>上次提醒</th><th></th>
        </tr></thead>
        <tbody id="rows"></tbody>
      </table>
    </div>
    <div class="empty" id="empty" style="display:none">还没有价位。在下面加一个，例如今日高点或某个整数关口。</div>
  </div>

  <div class="card">
    <div class="chead"><h2>距离现价分布</h2>
      <span class="meta">中间的竖线是现价，柱子越长表示离现价越远</span></div>
    <div class="drift" id="drift"></div>
  </div>

  <div class="card">
    <div class="chead"><h2>添加价位</h2></div>
    <div class="row">
      <input type="number" id="np" step="0.01" placeholder="例如 4250">
      <span class="meta">美元/盎司</span>
      <input type="text" id="nl" maxlength="20" placeholder="说明，例如 上破看多">
      <button onclick="addLevel()">添加</button>
    </div>
    <div class="row" style="margin-top:9px">
      <button class="ghost" onclick="fill('price')">填入现价</button>
      <button class="ghost" onclick="fill('high')">填入今日高</button>
      <button class="ghost" onclick="fill('low')">填入今日低</button>
    </div>
  </div>

  <div class="card">
    <div class="chead"><h2>参数设置</h2></div>
    <div class="meta">每一项右边都写了它是什么意思。改完点最下面「保存参数」，
      正在跑的监控几秒内自动生效，不用重启。</div>
    <div id="params" style="margin-top:14px">正在读取…</div>
    <div class="row" style="margin-top:18px">
      <button onclick="saveParams()">保存参数</button>
      <button class="ghost" onclick="reloadParams()">放弃改动，重新读取</button>
    </div>
  </div>

  <div class="card">
    <div class="chead"><h2>AI 建议价位</h2><span class="meta" id="ai-state"></span></div>
    <div class="row">
      <button onclick="askAi()" id="ai-btn">让 AI 给一组关键价位</button>
      <button class="ghost" onclick="adopt()" id="adopt-btn" style="display:none">全部采纳为我的价位</button>
    </div>
    <div class="meta" id="ai-list" style="margin-top:10px;line-height:2"></div>
  </div>

  <footer>
    <span>改动立刻生效，正在跑的监控几秒内就能读到。</span>
    <a href="#" onclick="shutdown();return false;">关闭设置服务</a>
  </footer>
</div>
<script>
const TOKEN = new URLSearchParams(location.search).get('token') || '';
let state = null, aiLevels = [], paramsDirty = false;

function esc(t){return String(t==null?'':t).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function setMeta(text,cls){
  const el=document.getElementById('meta');
  if(el){el.className='status '+(cls||'');}
  document.getElementById('meta-text').textContent=text;
}
function fmt(n,d){return n==null?'—':Number(n).toLocaleString('zh-CN',{minimumFractionDigits:d||0,maximumFractionDigits:d||0});}
function hint(t,bad){const el=document.getElementById('hint');el.textContent=t||'';el.className='hint '+((t&&bad)?'bad':'good');}
function showAlert(t){const el=document.getElementById('alert');el.textContent=t||'';el.style.display=t?'block':'none';}
function setAiState(t){document.getElementById('ai-state').textContent=t||'';}

async function api(path,payload){
  const init={headers:{'X-GoldAlert-Token':TOKEN}};
  if(payload){init.method='POST';init.headers['Content-Type']='application/json';init.body=JSON.stringify(Object.assign({token:TOKEN},payload));}
  const resp=await fetch(path,init);
  const data=await resp.json().catch(function(){return {};});
  if(!resp.ok){throw new Error(data.message||('HTTP '+resp.status));}
  return data;
}
function post(action,extra){return api('/api/action',Object.assign({action:action},extra||{}));}

async function refresh(){
  try{
    state=await api('/api/state');
    showAlert('');
    render(state);
  }catch(err){
    setMeta('连不上设置服务','st-off');
    showAlert('设置服务没在运行，这个页面现在点不动（改动也不会保存）。'
      +'请关掉本页，双击桌面上的「设置目标价位」重新打开。技术细节：'+err.message);
  }
}

function render(s){
  document.getElementById('price').textContent = s.price==null?'—':fmt(s.price,2);

  const chg=document.getElementById('chg');
  if(s.change==null){chg.className='chg';chg.textContent='';}
  else{
    const up=s.change>=0;
    chg.className='chg '+(up?'up':'down');
    chg.textContent=(up?'▲ +':'▼ ')+fmt(Math.abs(s.change),2)
      +((s.change_pct==null)?'':'　'+Math.abs(s.change_pct).toFixed(2)+'%');
  }

  if(s.price==null){setMeta('还没采集到数据，监控可能没在运行','st-off');}
  else if(s.running){setMeta('监控运行中 · 更新于 '+Math.round(s.age_seconds)+' 秒前','st-ok');}
  else{setMeta('监控好像没在运行 · 最后更新 '+s.price_time,'st-warn');}

  const stats=[];
  if(s.today_high!=null){stats.push('<div class="stat"><span>今日最高</span><b>'+fmt(s.today_high,2)+'</b></div>');}
  if(s.today_low!=null){stats.push('<div class="stat"><span>今日最低</span><b>'+fmt(s.today_low,2)+'</b></div>');}
  if(s.bid!=null&&s.ask!=null){stats.push('<div class="stat"><span>买 / 卖</span><b>'+fmt(s.bid,2)+' / '+fmt(s.ask,2)+'</b></div>');}
  if(s.source){stats.push('<div class="stat"><span>数据源</span><b>'+esc(s.source)+'</b></div>');}
  document.getElementById('stats').innerHTML=stats.join('');

  const levels=s.levels||[];
  const prox=(s.settings&&s.settings.proximity_usd)||0;
  document.getElementById('rows').innerHTML=levels.map(function(l){
    const near=(l.distance!=null&&Math.abs(l.distance)<=prox);
    const dist=(l.distance==null)?'—':((l.distance>0?'+':'')+fmt(l.distance,2));
    return '<tr'+(l.enabled?'':' class="off"')+'>'
      +'<td class="num strong">'+fmt(l.price,2)+'</td>'
      +'<td>'+esc(l.label||'未命名')+'</td>'
      +'<td><span class="tag src-'+esc(l.source)+'">'+esc(l.source_label)+'</span></td>'
      +'<td><span class="tag '+esc(l.state_class)+'">'+esc(l.state)+'</span></td>'
      +'<td class="num'+(near?' nearnum':'')+'">'+dist+'</td>'
      +'<td class="meta">'+esc(l.last_alert||'—')+'</td>'
      +'<td class="ops">'
        +'<button class="link" onclick="editLevel(this.dataset.id)" data-id="'+l.id+'">修改</button>'
        +'<button class="link" onclick="toggleLevel(this.dataset.id)" data-id="'+l.id+'">'+(l.enabled?'停用':'启用')+'</button>'
        +'<button class="link danger" onclick="removeLevel(this.dataset.id,Number(this.dataset.price))" data-id="'+l.id+'" data-price="'+l.price+'">删除</button>'
      +'</td></tr>';
  }).join('');
  document.getElementById('empty').style.display=levels.length?'none':'block';
  document.getElementById('lv-count').textContent=levels.length?(levels.length+' 个'):'';

  renderDrift(s);
  renderParams(s.params||[]);
}

function renderDrift(s){
  const host=document.getElementById('drift');
  const rows=(s.levels||[]).filter(function(l){return l.distance!=null;});
  if(s.price==null||!rows.length){
    host.innerHTML='<div class="meta">还没有可对比的价位。</div>';
    return;
  }
  let max=1;
  rows.forEach(function(l){max=Math.max(max,Math.abs(l.distance));});
  const sorted=rows.slice().sort(function(a,b){return b.price-a.price;});
  host.innerHTML=sorted.map(function(l){
    const d=l.distance, above=d>=0;
    const w=Math.max(3,Math.min(50,Math.abs(d)/max*50));
    const left=above?50:(50-w);
    return '<div class="drow'+(l.enabled?'':' dim')+'">'
      +'<div class="dlabel"><span class="dname">'+esc(l.label||'未命名')+'</span>'
      +'<span class="dprice">'+fmt(l.price,2)+'</span></div>'
      +'<div class="dbar"><span class="dmid"></span>'
      +'<span class="dfill '+(above?'above':'below')+'" style="left:'+left.toFixed(2)+'%;width:'+w.toFixed(2)+'%"></span></div>'
      +'<div class="ddot '+(above?'above':'below')+'">'+(above?'+':'')+fmt(d,2)+'</div>'
      +'</div>';
  }).join('');
}

async function addLevel(){
  const price=document.getElementById('np').value.trim();
  const label=document.getElementById('nl').value.trim();
  if(!price){hint('先在「添加价位」里填个数字，例如 4250',true);document.getElementById('np').focus();return;}
  try{
    const r=await post('add',{price:price,label:label});
    document.getElementById('np').value='';document.getElementById('nl').value='';
    hint(r.message);refresh();
  }catch(e){hint('添加失败：'+e.message,true);}
}
async function editLevel(id){
  const level=(state.levels||[]).find(function(l){return l.id===id;});
  if(!level){return;}
  const price=prompt('新价位：',level.price);
  if(price===null){return;}
  const label=prompt('说明（可留空）：',level.label||'');
  if(label===null){return;}
  try{const r=await post('update',{id:id,price:price,label:label});hint(r.message);refresh();}
  catch(e){hint('修改失败：'+e.message,true);}
}
async function toggleLevel(id){
  try{await post('toggle',{id:id});refresh();}catch(e){hint('操作失败：'+e.message,true);}
}
async function removeLevel(id,price){
  if(!confirm('删除 '+fmt(price,2)+' 这个价位？')){return;}
  try{await post('remove',{id:id});hint('已删除');refresh();}catch(e){hint('删除失败：'+e.message,true);}
}
function fill(field){
  if(!state){return;}
  const v=field==='price'?state.price:(field==='high'?state.today_high:state.today_low);
  if(v==null){hint('数据源这次没给这个价格，手动填一下吧');return;}
  document.getElementById('np').value=Number(v).toFixed(2);
}
async function askAi(){
  const btn=document.getElementById('ai-btn');
  btn.disabled=true;setAiState('AI 正在算，一般几秒就好…');
  try{
    const r=await post('ai');
    aiLevels=r.levels||[];
    if(!aiLevels.length){setAiState('AI 这次没给出可用价位，过会儿再试。');}
    else{setAiState('AI 给了 '+aiLevels.length+' 个：');}
    renderAi();
  }catch(e){aiLevels=[];renderAi();setAiState('AI 失败：'+e.message);}
  finally{btn.disabled=false;}
}
function renderAi(){
  document.getElementById('ai-list').innerHTML=aiLevels.map(function(l){
    return '· '+fmt(l.price,2)+'　'+esc(l.label||'关键价位');
  }).join('<br>');
  document.getElementById('adopt-btn').style.display=aiLevels.length?'inline-block':'none';
}
async function adopt(){
  if(!aiLevels.length){return;}
  try{
    const r=await post('adopt',{levels:aiLevels});
    aiLevels=[];renderAi();setAiState(r.message);hint(r.message);refresh();
  }catch(e){setAiState('采纳失败：'+e.message);}
}
async function shutdown(){
  if(!confirm('关闭设置服务？关掉后这个页面就用不了了，监控不受影响。')){return;}
  try{await post('shutdown');}catch(e){}
  document.body.innerHTML='<div class="wrap"><h1>设置服务已关闭</h1>'
    +'<p class="meta">下次双击「设置目标价位.bat」就能重新打开。</p></div>';
}

function paramInput(p){
  const id='p-'+String(p.key).split('.').join('-');
  if(p.type==='bool'){
    return '<label class="chk"><input type="checkbox" id="'+id+'" data-key="'+p.key+'"'
      +(p.value?' checked':'')+'></label>';
  }
  const value=(p.value==null)?'':p.value;
  return '<input type="number" id="'+id+'" data-key="'+p.key+'" value="'+value+'"'
    +' step="'+((p.step==null)?1:p.step)+'"'
    +((p.min==null)?'':' min="'+p.min+'"')
    +((p.max==null)?'':' max="'+p.max+'"')+'>'
    +((p.unit==null)?'':'<span class="unit">'+esc(p.unit)+'</span>');
}
function renderParams(groups){
  const host=document.getElementById('params');
  if(!groups||!groups.length){host.textContent='（没有可调参数）';return;}
  if(paramsDirty){return;}
  host.innerHTML=groups.map(function(g){
    return '<div class="group">'
      +'<div class="ghead"><b>'+esc(g.title)+'</b><span class="meta">'+esc(g.note||'')+'</span></div>'
      +g.params.map(function(p){
        return '<div class="prow">'
          +'<div class="pname">'+esc(p.label)+'</div>'
          +'<div class="pin">'+paramInput(p)+'</div>'
          +'<div class="pdesc">'+esc(p.meaning||'')+'</div>'
          +'</div>';
      }).join('')
      +'</div>';
  }).join('');
}
function paramValues(){
  const values={};
  document.querySelectorAll('#params [data-key]').forEach(function(el){
    values[el.dataset.key]= el.type==='checkbox' ? el.checked : el.value;
  });
  return values;
}
async function saveParams(){
  try{
    const r=await post('params',{values:paramValues()});
    paramsDirty=false;hint(r.message);refresh();
  }catch(e){hint('保存失败：'+e.message,true);}
}
function reloadParams(){paramsDirty=false;hint('已重新读取');refresh();}

function setQState(t){document.getElementById('q-state').textContent=t||'';}
function clearQuestion(){
  const box=document.getElementById('q-answer');
  box.textContent='';box.style.display='none';box.className='qa';
  setQState('');
}
async function askQuestion(){
  const input=document.getElementById('q-input');
  const box=document.getElementById('q-answer');
  const btn=document.getElementById('q-btn');
  const q=(input.value||'').trim();
  if(!q){setQState('先写个问题吧');input.focus();return;}
  btn.disabled=true;setQState('AI 正在想，一般几秒就好…');
  box.className='qa';box.style.display='none';
  try{
    const r=await post('ask',{question:q});
    box.textContent=r.answer||'';
    box.className='qa';box.style.display='block';
    setQState(r.price==null?'':'基于现价 '+fmt(r.price,2)+' 回答');
  }catch(e){
    box.textContent=e.message;
    box.className='qa bad';box.style.display='block';
    setQState('没答上来');
  }finally{btn.disabled=false;}
}

document.getElementById('params').addEventListener('input',function(){paramsDirty=true;});
document.getElementById('params').addEventListener('change',function(){paramsDirty=true;});

refresh();
setInterval(refresh,1500);
</script>
</body>
</html>
"""


class LevelApp:
    """界面背后的业务逻辑：读状态、执行操作。"""

    def __init__(self, config: Config, logger: logging.Logger, token: str,
                 config_path=None) -> None:
        self.config = config
        self.config_path = Path(config_path) if config_path else PROJECT_ROOT / "config.json"
        self.logger = logger
        self.token = token
        self.store = LevelStore(config.levels.file, logger)
        self.state = StateStore(config.state_file)
        self.history = HistoryStore(config.history_file, config.max_history_points)
        self.analyzer = None
        if config.ai.enabled and config.levels.ai_enabled:
            self.analyzer = MarketAnalyzer(config.ai, create_session(), logger)
        self.server: ThreadingHTTPServer | None = None

    # ------------------------------------------------------------------ 状态
    def _current_quote(self) -> Quote | None:
        record = read_last_record(self.config.history_file)
        if not record:
            return None
        try:
            return Quote(
                symbol=self.config.symbol,
                name="Gold",
                price=float(record["price"]),
                source=str(record.get("source") or ""),
                fetched_at=float(record["t"]),
                prev_close=record.get("prev_close"),
                high=record.get("high"),
                low=record.get("low"),
                quote_time=record.get("quote_time"),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def reload_config_if_changed(self) -> bool:
        """config.json 被改过（比如另开一个窗口改的）就重读一次。"""
        try:
            mtime = self.config_path.stat().st_mtime
        except OSError:
            return False
        if mtime == getattr(self, "_config_mtime", None):
            return False
        self._config_mtime = mtime
        try:
            self.config = Config.load(self.config_path)
        except Exception as exc:
            self.logger.warning("设置页重读 config.json 失败: %s", exc)
            return False
        self.analyzer = None
        if self.config.ai.enabled and self.config.levels.ai_enabled:
            self.analyzer = MarketAnalyzer(self.config.ai, create_session(), self.logger)
        return True

    def state_payload(self) -> dict:
        self.reload_config_if_changed()
        self.store.load_if_changed()
        quote = self._current_quote()
        snapshot = self.state.get(LEVEL_STATE_KEY)
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        settings = self.store.settings
        now_ts = now_cn().timestamp()

        levels = []
        for level in sorted(self.store.all_levels(), key=lambda item: item.price):
            entry = snapshot.get(level.id) or {}
            try:
                last_alert = float(entry.get("last_alert") or 0.0)
            except (TypeError, ValueError):
                last_alert = 0.0
            distance = None if quote is None else quote.price - level.price

            if not level.enabled:
                state_text, state_class = "已停用", "off"
            elif quote is None:
                state_text, state_class = "待命", ""
            elif last_alert and (now_ts - last_alert) < RECENT_ALERT_SECONDS:
                state_text, state_class = "刚提醒过", "fired"
            elif (
                not bool(entry.get("near_armed", 1))
                and abs(distance) <= settings.proximity_usd
            ):
                state_text, state_class = "已在附近", "near"
            else:
                state_text, state_class = "待命", ""

            levels.append(
                {
                    "id": level.id,
                    "price": level.price,
                    "label": level.label,
                    "source": level.source,
                    "source_label": SOURCE_LABEL.get(level.source, level.source),
                    "enabled": level.enabled,
                    "state": state_text,
                    "state_class": state_class,
                    "distance": distance,
                    "last_alert": fmt_ts(last_alert)[11:16] if last_alert else "",
                }
            )

        age = None
        if quote is not None:
            age = max(0.0, now_ts - quote.fetched_at)

        return {
            "price": quote.price if quote else None,
            "price_time": fmt_ts(quote.fetched_at) if quote else None,
            "age_seconds": age,
            "running": bool(age is not None and age < LIVE_WINDOW_SECONDS),
            "today_high": quote.high if quote else None,
            "today_low": quote.low if quote else None,
            "change": quote.change if quote else None,
            "change_pct": quote.change_pct if quote else None,
            "bid": quote.bid if quote else None,
            "ask": quote.ask if quote else None,
            "source": quote.source if quote else None,
            "levels": levels,
            "settings": settings.to_dict(),
            "file": str(self.store.path),
            "ai_ready": bool(self.analyzer and self.analyzer.available),
            "params": collect_params(self.config, settings),
        }

    # ------------------------------------------------------------------ 操作
    @staticmethod
    def _to_price(value) -> float:
        try:
            price = float(str(value).strip().replace(",", ""))
        except (TypeError, ValueError):
            raise ValueError("价位得是数字，例如 4250 或 4200.5")
        if price <= 0:
            raise ValueError("价位要大于 0")
        return price

    def action(self, payload: dict) -> dict:
        name = str(payload.get("action") or "")

        if name == "add":
            price = self._to_price(payload.get("price"))
            label = str(payload.get("label") or "").strip()[:20]
            level, created = self.store.upsert(price, label)
            if created:
                return {"ok": True, "message": f"已添加 {price:,.2f}，监控几秒内开始盯它。"}
            return {
                "ok": True,
                "message": f"已经有「{level.label}」这个价位了，直接把它改成了 {price:,.2f}。",
            }

        if name == "update":
            level_id = str(payload.get("id") or "")
            if self.store.get(level_id) is None:
                raise ValueError("这个价位已经不在了，刷新一下页面")
            price = self._to_price(payload.get("price"))
            self.store.update(
                level_id, price=price, label=str(payload.get("label") or "").strip()[:20]
            )
            return {"ok": True, "message": f"已改成 {price:,.2f}。"}

        if name == "remove":
            level_id = str(payload.get("id") or "")
            if not self.store.remove(level_id):
                raise ValueError("这个价位已经不在了")
            return {"ok": True, "message": "已删除。"}

        if name == "toggle":
            level_id = str(payload.get("id") or "")
            level = self.store.get(level_id)
            if level is None:
                raise ValueError("这个价位已经不在了")
            new_state = not level.enabled
            self.store.update(level_id, enabled=new_state)
            return {"ok": True, "message": "已启用。" if new_state else "已停用。"}

        if name == "settings":
            current = self.store.settings
            settings = LevelSettings(
                enabled=bool(payload.get("enabled", current.enabled)),
                notify_near=bool(payload.get("notify_near", current.notify_near)),
                notify_break=bool(payload.get("notify_break", current.notify_break)),
                notify_ai_update=bool(
                    payload.get("notify_ai_update", current.notify_ai_update)
                ),
                proximity_usd=float(payload.get("proximity_usd", current.proximity_usd)),
                rearm_usd=float(payload.get("rearm_usd", current.rearm_usd)),
                cooldown_minutes=float(
                    payload.get("cooldown_minutes", current.cooldown_minutes)
                ),
            )
            problems = settings.problems()
            if problems:
                raise ValueError("；".join(problems))
            self.store.save_settings(settings)
            return {"ok": True, "message": "设置已保存，监控几秒内生效。"}

        if name in ("params", "config"):
            values = payload.get("values")
            if not isinstance(values, dict):
                raise ValueError("参数格式不对")
            problems = apply_params(self.config_path, self.store, values)
            if problems:
                raise ValueError("；".join(problems))
            self.reload_config_if_changed()
            return {"ok": True, "message": "已保存，正在跑的监控几秒内自动生效。"}

        if name == "ai":
            if self.analyzer is None or not self.analyzer.available:
                raise ValueError(
                    "没读到 DeepSeek 的 API Key，AI 价位暂时用不了（检查 .env）"
                )
            quote = self._current_quote()
            if quote is None:
                raise ValueError("监控还没采集到价格，等有数据了再试")
            lookback = getattr(self.analyzer.config, "lookback_hours", 6.0)
            records = self.history.records_since(quote.fetched_at - lookback * 3600.0)
            items = self.analyzer.suggest_levels(
                quote, records, self.config.levels.ai_max_levels
            )
            return {"ok": True, "levels": items, "price": quote.price}

        if name == "ask":
            if self.analyzer is None or not self.analyzer.available:
                raise ValueError("没读到 DeepSeek 的 API Key，AI 提问暂时用不了（检查 .env）")
            question = str(payload.get("question") or "").strip()[:200]
            if not question:
                raise ValueError("先写个问题吧")
            quote = self._current_quote()
            if quote is None:
                raise ValueError("监控还没采集到价格，等有数据了再试")
            lookback = getattr(self.analyzer.config, "lookback_hours", 6.0)
            records = self.history.records_since(quote.fetched_at - lookback * 3600.0)
            answer = self.analyzer.ask(quote, records, question)
            if not answer:
                raise ValueError("AI 这次没能给出回答，过会儿再试")
            return {"ok": True, "answer": answer, "price": quote.price}

        if name == "adopt":
            items = payload.get("levels") or []
            if not isinstance(items, list):
                raise ValueError("要采纳的价位格式不对")
            added = 0
            updated = 0
            for item in items:
                if not isinstance(item, dict):
                    continue
                try:
                    price = self._to_price(item.get("price"))
                except ValueError:
                    continue
                _, created = self.store.upsert(
                    price, str(item.get("label") or "AI 建议").strip()[:20]
                )
                if created:
                    added += 1
                else:
                    updated += 1
            if not added and not updated:
                raise ValueError("没有可采纳的价位")
            if updated and not added:
                message = f"这 {updated} 个价位你本来就有了，已直接改成 AI 给的新价格。"
            elif updated:
                message = f"新加了 {added} 个，另有 {updated} 个同名价位被更新成 AI 的价格。"
            else:
                message = f"已把 {added} 个价位加进你的列表。"
            return {"ok": True, "message": message}

        if name == "shutdown":
            if self.server is not None:
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            return {"ok": True, "message": "设置服务已关闭。"}

        raise ValueError(f"不认识的请求: {name}")


class _Handler(BaseHTTPRequestHandler):
    server_version = "GoldAlertLevels/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 静音，避免刷日志
        return

    @property
    def app(self) -> LevelApp:
        return self.server.app  # type: ignore[attr-defined]

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0].strip().lower()
        return host in ("127.0.0.1", "localhost", "::1", "[::1]")

    def _token_ok(self) -> bool:
        token = self.headers.get("X-GoldAlert-Token") or ""
        if not token:
            token = parse_qs(urlsplit(self.path).query).get("token", [""])[0]
        return bool(token) and secrets.compare_digest(token, self.app.token)

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if not self._host_ok():
            self._json(403, {"message": "只允许从本机访问"})
            return
        if path == "/api/ping":
            self._json(200, {"app": PING_MARK})
            return
        if not self._token_ok():
            self._json(403, {"message": "令牌不对，请从桌面快捷方式重新打开设置页"})
            return
        if path in ("/", "/index.html"):
            html = HTML.replace("__TOKEN__", self.app.token)
            self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/api/state":
            try:
                self._json(200, self.app.state_payload())
            except Exception as exc:
                self.app.logger.warning("读取状态失败: %s", exc)
                self._json(500, {"message": f"{type(exc).__name__}: {exc}"})
            return
        self._json(404, {"message": "没有这个地址"})

    def do_POST(self) -> None:
        if not self._host_ok() or not self._token_ok():
            self._json(403, {"message": "只允许从本机且令牌正确时操作"})
            return
        if urlsplit(self.path).path != "/api/action":
            self._json(404, {"message": "没有这个地址"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("请求体要是一个 JSON 对象")
        except (ValueError, TypeError) as exc:
            self._json(400, {"message": f"请求格式不对：{exc}"})
            return

        try:
            result = self.app.action(payload)
        except ValueError as exc:
            self._json(400, {"message": str(exc)})
            return
        except Exception as exc:
            self.app.logger.warning("界面操作失败: %s", exc)
            self._json(500, {"message": f"{type(exc).__name__}: {exc}"})
            return
        self._json(200, result)


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def is_running(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.5) as sock:
            sock.sendall(b"GET /api/ping HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
            data = sock.recv(1024).decode("utf-8", "replace")
        return PING_MARK in data
    except OSError:
        return False


def _bind_server(ports: list[int]):
    """按顺序试着绑定端口，都被占用时退到系统随机端口。"""
    last_error: OSError | None = None
    for candidate in ports:
        try:
            return _Server(("127.0.0.1", candidate), _Handler)
        except OSError as exc:
            last_error = exc
    try:
        return _Server(("127.0.0.1", 0), _Handler)
    except OSError:
        if last_error is not None:
            raise last_error
        raise


def _ui_file() -> Path:
    return PROJECT_ROOT / "data" / "ui.json"


def _remember(port: int, token: str) -> None:
    try:
        target = _ui_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps({"port": port, "token": token, "pid": os.getpid()}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


def _recall() -> tuple[int, str] | None:
    try:
        raw = json.loads(_ui_file().read_text(encoding="utf-8"))
        return int(raw["port"]), str(raw["token"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def load_or_create_token() -> str:
    """令牌固定存在 data/ui.json，重启后你开着的页面不会失效。"""
    existing = _recall()
    if existing and existing[1]:
        return existing[1]
    return secrets.token_urlsafe(18)


def start_settings_service(config: Config, logger: logging.Logger,
                           preferred_port: int = DEFAULT_PORT):
    """在后台线程里跑起设置页。

    返回 ``(server, url)``；已经有实例在监听时返回 ``(None, 那个实例的地址)``。
    监控进程启动时调用它，这样只要监控在跑，设置页就一直能用。
    端口优先沿用上次记录的端口，这样重启监控后浏览器里开着的页面不会失效。
    """
    recalled = _recall()
    ports: list[int] = []
    if recalled and recalled[0] > 0:
        ports.append(recalled[0])
    if preferred_port not in ports:
        ports.append(preferred_port)

    for port in ports:
        if is_running(port):
            return None, f"http://127.0.0.1:{port}/?token={load_or_create_token()}"

    token = load_or_create_token()
    app = LevelApp(config, logger, token)
    server = _bind_server(ports)
    port = int(server.server_address[1])
    server.app = app  # type: ignore[attr-defined]
    app.server = server

    thread = threading.Thread(target=server.serve_forever, name="goldalert-settings", daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{port}/?token={token}"
    _remember(port, token)
    return server, url


def main(argv=None) -> int:
    ensure_utf8_streams()
    os.chdir(PROJECT_ROOT)
    load_dotenv(PROJECT_ROOT / ".env")

    config = Config.load(PROJECT_ROOT / "config.json")
    logger = setup_logging(config.log_file, quiet=True)

    server, url = start_settings_service(config, logger)
    logger.info("目标价位设置页: %s", url)
    webbrowser.open(url)
    if server is None:
        return 0  # 已经有别的实例在跑，把浏览器指过去即可

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        details = traceback.format_exc()
        try:
            error_log = PROJECT_ROOT / "logs" / "levels_ui_error.log"
            error_log.parent.mkdir(parents=True, exist_ok=True)
            error_log.write_text(details, encoding="utf-8")
        except Exception:
            pass
        raise


