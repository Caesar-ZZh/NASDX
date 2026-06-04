"""
NASDX — A股多智能体量化分析平台
Streamlit · DeepSeek V4 Pro · Notion 风格 UI
"""
import requests as _req
_real_get = _req.get
def _patched_get(url, **kwargs):
    if 'eastmoney.com' in url:
        s = _req.Session(); s.trust_env = True
        return s.get(url, **kwargs)
    return _real_get(url, **kwargs)
_req.get = _patched_get

import sys, os, json, subprocess, threading, time, glob
from pathlib import Path
from datetime import datetime

import streamlit as st

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# ── 热更新 LLM 配置 ───────────────────────────────────
def _update_llm_config(api_key, base_url, model):
    os.environ["NASDX_API_KEY"]  = api_key
    os.environ["NASDX_BASE_URL"] = base_url
    os.environ["NASDX_MODEL"]    = model
    try:
        import nasdx.llm as m
        m.API_KEY = api_key; m.BASE_URL = base_url; m.MODEL_NAME = model
        m.LLMClient._instance = None
    except Exception:
        pass

# ── 页面配置 ─────────────────────────────────────────
st.set_page_config(
    page_title="NASDX · A股量化分析",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ══════════════════════════════════════════════════════
#  Linear.app / Vercel 设计系统 CSS
# ══════════════════════════════════════════════════════
st.markdown("""
<style>
/* 系统字体栈：避免网络延迟，极简无衬线 */

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Linear/Vercel 设计令牌
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
:root {
  /* 背景层级（纯黑 + 微灰） */
  --bg-base:       #0a0a0a;
  --bg-elevated:   #111111;
  --bg-elevated2:  #161616;
  --bg-elevated3:  #1a1a1a;

  /* 文字层级 */
  --text-primary:    #ffffff;
  --text-secondary:  rgba(255,255,255,0.65);
  --text-tertiary:   rgba(255,255,255,0.40);
  --text-muted:      rgba(255,255,255,0.25);

  /* Tailwind 色系 */
  --green:         #22c55e;
  --red:           #ef4444;
  --yellow:        #f59e0b;
  --blue:          #3b82f6;
  --purple:        #a855f7;
  --cyan:          #06b6d4;

  /* 边界颜色 */
  --border:        rgba(255,255,255,0.06);
  --border-hover:  rgba(255,255,255,0.10);
  --border-active: rgba(255,255,255,0.15);

  /* 圆角 */
  --radius-sm:  4px;
  --radius-md:  6px;
  --radius-lg:  8px;

  /* 无阴影或极轻阴影 */
  --shadow-none: none;
  --shadow-sm:   0 1px 2px rgba(0,0,0,0.05);
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Reset
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   整体背景
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
[data-testid="stAppViewContainer"] {
    background: var(--bg-base);
    color: var(--text-primary);
    font-family: -apple-system, 'Segoe UI', system-ui, 'Helvetica Neue', sans-serif;
    font-size: 13px;
    line-height: 1.5;
    font-variant-numeric: tabular-nums;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   侧边栏 — 深灰
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
[data-testid="stSidebar"] {
    background: var(--bg-elevated) !important;
    border-right: 1px solid var(--border);
}
[data-testid="stSidebar"] > div:first-child { padding: 16px 12px; }
[data-testid="stHeader"] { background: transparent !important; }
#MainMenu, footer, [data-testid="stToolbar"] { visibility: hidden; }

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   滚动条 — 极简灰
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.10); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: rgba(255,255,255,0.18); }

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   按钮 — Linear 极简风
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

/* 主按钮（action 用：蓝色实心） */
.stButton > button[kind="primary"] {
    background: var(--blue) !important;
    color: #ffffff !important;
    border: none !important;
    border-radius: var(--radius-md) !important;
    font-weight: 500 !important;
    font-size: 13px !important;
    padding: 8px 16px !important;
    letter-spacing: 0 !important;
    transition: background 150ms !important;
    box-shadow: none !important;
}
.stButton > button[kind="primary"]:hover {
    background: #2563eb !important;
    transform: none !important;
    box-shadow: none !important;
}
.stButton > button[kind="primary"]:active {
    background: #1d4ed8 !important;
    transform: none !important;
}

/* 次级按钮（侧边栏导航用：透明 + 左竖线） */
.stButton > button[kind="secondary"] {
    background: transparent !important;
    color: var(--text-secondary) !important;
    border: none !important;
    border-left: 2px solid transparent !important;
    border-radius: 0 !important;
    font-weight: 400 !important;
    font-size: 13px !important;
    padding: 8px 12px !important;
    letter-spacing: 0 !important;
    transition: background 150ms, color 150ms, border-left-color 150ms !important;
    box-shadow: none !important;
    text-align: left !important;
    justify-content: flex-start !important;
}
.stButton > button[kind="secondary"]:hover {
    background: rgba(255,255,255,0.04) !important;
    color: var(--text-primary) !important;
    transform: none !important;
}
.stButton > button[kind="secondary"]:active {
    background: rgba(255,255,255,0.06) !important;
    border-left-color: var(--blue) !important;
    transform: none !important;
}

/* active 侧边栏导航按钮 */
.stButton > button[kind="secondary"]:focus {
    border-left-color: var(--blue) !important;
}

/* 普通 stButton 兜底 */
.stButton > button {
    border-radius: var(--radius-md) !important;
    font-size: 13px !important;
    font-weight: 500 !important;
    letter-spacing: 0 !important;
    transition: all 150ms !important;
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   输入框 — Linear 深灰
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
.stTextInput > div > div > input,
.stNumberInput > div > div > input {
    background: var(--bg-elevated2) !important;
    border: 1px solid var(--border) !important;
    border-radius: var(--radius-md) !important;
    color: var(--text-primary) !important;
    font-size: 13px !important;
    font-family: inherit !important;
    padding: 8px 12px !important;
    transition: border-color 150ms !important;
}
.stTextInput > div > div > input:focus,
.stNumberInput > div > div > input:focus {
    border-color: var(--blue) !important;
    box-shadow: none !important;
    outline: none !important;
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Select Box
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
.stSelectbox > div > div,
[data-baseweb="select"] {
    background: var(--bg-elevated2) !important;
    border: 1px solid var(--border) !important;
    border-radius: var(--radius-md) !important;
    color: var(--text-primary) !important;
}
[data-baseweb="popover"] { background: var(--bg-elevated2) !important; border-radius: var(--radius-md) !important; border: 1px solid var(--border) !important; box-shadow: var(--shadow-sm) !important; }

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   卡片 — Linear 极简无阴影
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
.n-card {
    background: var(--bg-elevated);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    padding: 16px;
    box-shadow: none;
    transition: border-color 150ms;
    position: relative;
    overflow: hidden;
}
.n-card:hover {
    border-color: var(--border-hover);
    transform: none;
    box-shadow: none;
}

/* 彩色顶边条 — Linear 风格（细线，无渐变） */
.n-card-accent-green::before  { content:""; position:absolute; top:0; left:0; right:0; height:2px; background: var(--green); }
.n-card-accent-red::before    { content:""; position:absolute; top:0; left:0; right:0; height:2px; background: var(--red); }
.n-card-accent-yellow::before { content:""; position:absolute; top:0; left:0; right:0; height:2px; background: var(--yellow); }
.n-card-accent-blue::before   { content:""; position:absolute; top:0; left:0; right:0; height:2px; background: var(--blue); }

/* 卡片变体（无玻璃毛效果） */
.n-card-glass {
    background: var(--bg-elevated);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    padding: 16px;
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   文字层级
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
.n-label {
    font-size: 11px; font-weight: 600;
    letter-spacing: 0.05em; text-transform: uppercase;
    color: var(--text-muted); margin-bottom: 6px;
}
.n-title {
    font-size: 32px; font-weight: 600; color: var(--text-primary);
    letter-spacing: -0.02em; line-height: 1.2;
}
.n-sub { font-size: 13px; color: var(--text-secondary); line-height: 1.6; }

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Section 标题 — Linear 小标题
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
.n-section-title {
    font-size: 12px; font-weight: 600;
    letter-spacing: 0.02em; text-transform: none;
    color: var(--text-primary);
    padding: 16px 0 8px 0; margin-bottom: 0;
    border-bottom: 1px solid var(--border);
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   信号徽章 — Linear 标签
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
.sig-bull {
    display: inline-flex; align-items: center; gap: 5px;
    background: rgba(34,197,94,0.10);
    color: var(--green);
    border: 1px solid rgba(34,197,94,0.25);
    border-radius: var(--radius-sm); padding: 4px 10px;
    font-size: 12px; font-weight: 500;
}
.sig-bear {
    display: inline-flex; align-items: center; gap: 5px;
    background: rgba(239,68,68,0.10);
    color: var(--red);
    border: 1px solid rgba(239,68,68,0.25);
    border-radius: var(--radius-sm); padding: 4px 10px;
    font-size: 12px; font-weight: 500;
}
.sig-neut {
    display: inline-flex; align-items: center; gap: 5px;
    background: rgba(245,158,11,0.10);
    color: var(--yellow);
    border: 1px solid rgba(245,158,11,0.25);
    border-radius: var(--radius-sm); padding: 4px 10px;
    font-size: 12px; font-weight: 500;
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   进度条 — Linear 纯色
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
.bar-wrap {
    background: rgba(255,255,255,0.06);
    border-radius: 2px; height: 3px;
    overflow: hidden; margin: 8px 0;
}
.bar-fill-green  { height:100%; background: var(--green); border-radius: 2px; transition: width 150ms; }
.bar-fill-red    { height:100%; background: var(--red); border-radius: 2px; }
.bar-fill-yellow { height:100%; background: var(--yellow); border-radius: 2px; }

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Pill 标签 — Linear 标签
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
.n-pill {
    display: inline-block;
    background: rgba(255,255,255,0.05);
    color: var(--text-secondary);
    border-radius: var(--radius-sm); padding: 4px 8px;
    font-size: 12px; font-weight: 400;
    margin: 2px; border: 1px solid var(--border);
    letter-spacing: 0;
    transition: background 150ms;
}
.n-pill:hover { background: rgba(255,255,255,0.08); }

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   气泡 — Linear 消息框
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
.bubble {
    padding: 12px 14px;
    border-radius: var(--radius-lg);
    font-size: 13px; line-height: 1.6;
    margin: 8px 0; color: var(--text-primary);
    position: relative;
}
.bubble-bull  { background: rgba(34,197,94,0.08);  border: 1px solid rgba(34,197,94,0.20); }
.bubble-bear  { background: rgba(239,68,68,0.08);  border: 1px solid rgba(239,68,68,0.20); }
.bubble-judge { background: rgba(168,85,247,0.08); border: 1px solid rgba(168,85,247,0.20); }

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   分割线
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
hr.n-divider { border: none; border-top: 1px solid var(--border); margin: 16px 0; }

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Expander — Linear 折叠面板
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
.streamlit-expanderHeader {
    background: var(--bg-elevated) !important;
    border: 1px solid var(--border) !important;
    border-radius: var(--radius-lg) !important;
    font-size: 13px !important;
    color: var(--text-primary) !important;
    font-weight: 500 !important;
    letter-spacing: 0 !important;
    transition: background 150ms, border-color 150ms !important;
}
.streamlit-expanderHeader:hover { background: var(--bg-elevated2) !important; border-color: var(--border-hover) !important; }
.streamlit-expanderContent {
    background: var(--bg-base) !important;
    border: 1px solid var(--border) !important;
    border-top: none !important;
    border-radius: 0 0 var(--radius-lg) var(--radius-lg) !important;
    padding: 12px 0 !important;
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Metric — Linear 数字展示
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
[data-testid="stMetric"] {
    background: var(--bg-elevated);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    padding: 12px 14px;
    box-shadow: none;
}
[data-testid="stMetricLabel"] {
    font-size: 11px !important;
    color: var(--text-muted) !important;
    font-weight: 500 !important;
    letter-spacing: 0.05em !important;
    text-transform: uppercase !important;
}
[data-testid="stMetricValue"] {
    font-size: 20px !important;
    font-weight: 600 !important;
    color: var(--text-primary) !important;
    letter-spacing: -0.02em !important;
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Progress — Linear 蓝
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
[data-testid="stProgress"] > div {
    background: rgba(255,255,255,0.06) !important;
    border-radius: 2px !important;
    height: 3px !important;
}
[data-testid="stProgress"] > div > div {
    background: var(--blue) !important;
    border-radius: 2px !important;
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Slider — Linear 风格
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
.stSlider > div > div > div > div {
    background: var(--blue) !important;
}
.stSlider > div > div > div[data-baseweb="slider"] {
    padding: 0 !important;
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Tab — Linear 下划线风格
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
.stTabs [data-baseweb="tab-list"] {
    background: transparent !important;
    border-radius: 0 !important;
    padding: 0 !important;
    gap: 0 !important;
    border: 1px solid var(--border) !important;
    border-bottom: 2px solid var(--border) !important;
}
.stTabs [data-baseweb="tab"] {
    background: transparent !important;
    border-radius: 0 !important;
    color: var(--text-secondary) !important;
    font-size: 13px !important;
    font-weight: 500 !important;
    letter-spacing: 0 !important;
    padding: 12px 16px !important;
    transition: color 150ms !important;
    border: none !important;
}
.stTabs [aria-selected="true"] {
    background: transparent !important;
    color: var(--text-primary) !important;
    font-weight: 500 !important;
    box-shadow: none !important;
    border-bottom: 2px solid var(--blue) !important;
}
.stTabs [data-baseweb="tab-panel"] {
    padding-top: 16px !important;
}
.stTabs [data-baseweb="tab-border"] { display: none !important; }

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Checkbox — Linear 开关风格
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
.stCheckbox > label > div[data-testid="stMarkdownContainer"] {
    color: var(--text-secondary) !important;
    font-size: 13px !important;
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Multiselect — Linear 标签
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
[data-baseweb="tag"] {
    background: rgba(59,130,246,0.15) !important;
    border-color: rgba(59,130,246,0.30) !important;
    border-radius: var(--radius-sm) !important;
    color: var(--blue) !important;
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Line chart
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
[data-testid="stArrowVegaLiteChart"] canvas,
[data-testid="stVegaLiteChart"] canvas {
    border-radius: var(--radius-lg) !important;
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Toast 通知
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
[data-testid="stNotification"] {
    background: var(--bg-elevated) !important;
    border: 1px solid var(--border) !important;
    border-radius: var(--radius-lg) !important;
    box-shadow: var(--shadow-sm) !important;
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Code block
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
.stCode, pre, code {
    background: var(--bg-elevated2) !important;
    border: 1px solid var(--border) !important;
    border-radius: var(--radius-md) !important;
    font-size: 12px !important;
    color: var(--text-secondary) !important;
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Info / Warning / Error boxes
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
[data-testid="stAlert"] {
    border-radius: var(--radius-lg) !important;
    border: 1px solid var(--border) !important;
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Spinner
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
[data-testid="stSpinner"] { color: var(--blue) !important; }

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   全局过渡 — Linear 150ms 线性
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
* { -webkit-tap-highlight-color: transparent; }
.stButton > button, .n-card, [data-testid="stMetric"] {
    transition-timing-function: linear;
}
</style>
""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════
#  股票池
# ══════════════════════════════════════════════════════
POOL = {
    "半导体":   [("688981","中芯国际"),("603501","韦尔股份"),("603986","兆易创新"),("688347","华虹半导体"),("300223","北京君正"),("512480","芯片ETF"),("513310","中韩半导体ETF")],
    "半导体设备": [("002371","北方华创"),("688012","中微公司"),("688082","盛美上海"),("561980","半导体设备ETF招商"),("159516","半导体设备ETF国泰")],
    "通信·光模块":[("300308","中际旭创"),("300502","新易盛"),("000063","中兴通讯"),("515050","5G通信ETF"),("515880","通信ETF国泰")],
    "AI算力":   [("688256","寒武纪"),("688041","海光信息"),("603019","中科曙光"),("515070","人工智能ETF")],
    "电力":     [("600900","长江电力"),("601985","中国核电"),("600406","国电南瑞"),("159611","电力ETF广发")],
    "军工":     [("000768","中航西飞"),("600893","航发动力"),("512660","军工ETF")],
    "机器人":   [("002527","新时达"),("002747","埃斯顿"),("562500","机器人ETF华夏")],
    "海外ETF":  [("513160","港股科技ETF"),("159131","港股通信息技术ETF"),("159941","纳指ETF广发"),("513500","标普500ETF"),("159687","亚太精选ETF")],
    "红利防御":  [("518880","黄金ETF华安"),("510880","红利ETF华泰柏瑞"),("515220","煤炭ETF国泰")],
}

PRESETS = {
    "DeepSeek": ("https://api.deepseek.com", ["deepseek-v4-pro","deepseek-v4-flash","deepseek-chat","deepseek-reasoner"]),
    "Claude 中转": ("https://newapi.ecdigit.cn/v1", ["claude-opus-4-6-thinking","claude-sonnet-4-6","claude-haiku-4-5-20251001"]),
    "阿里通义":  ("https://dashscope.aliyuncs.com/compatible-mode/v1", ["qwen-plus","qwen-turbo","qwen-max"]),
    "月之暗面":  ("https://api.moonshot.cn/v1", ["moonshot-v1-8k","moonshot-v1-32k"]),
    "Ollama 本地": ("http://localhost:11434/v1", ["qwen2.5:14b","deepseek-r1:7b","llama3.1:8b"]),
    "自定义":    ("", []),
}

# ══════════════════════════════════════════════════════
#  工具函数（全部加 cache_data 缓存，60s TTL）
# ══════════════════════════════════════════════════════
@st.cache_data(ttl=60, show_spinner=False)
def load_report(code):
    files = sorted(ROOT.glob(f"reports/report_{code}_*.json"))
    if not files: return None
    with open(files[-1], encoding="utf-8") as f: return json.load(f)

@st.cache_data(ttl=60, show_spinner=False)
def load_etf50():
    files = sorted(ROOT.glob("reports/etf50_*.json"))
    if not files: return None
    with open(files[-1], encoding="utf-8") as f: return json.load(f)

@st.cache_data(ttl=60, show_spinner=False)
def load_stocks60():
    files = sorted(ROOT.glob("reports/stocks60_*.json"))
    if not files: return None
    with open(files[-1], encoding="utf-8") as f: return json.load(f)

@st.cache_data(ttl=300, show_spinner=False)
def load_pool():
    with open(ROOT / "etf50_pool.json", encoding="utf-8") as f:
        return json.load(f)["etfs"]

@st.cache_data(ttl=300, show_spinner=False)
def load_recent_reports(n=6):
    files = sorted(ROOT.glob("reports/report_*.json"), key=os.path.getmtime, reverse=True)[:n]
    results = []
    for rp in files:
        try:
            with open(rp, encoding="utf-8") as f:
                results.append((rp, json.load(f)))
        except Exception:
            pass
    return results

def clean(text):
    if not text: return ""
    if "</thinking>" in text: text = text.split("</thinking>")[-1].strip()
    return text.replace("<thinking>","").strip()

def sig_color(s): return {"bullish":"#22c55e","bearish":"#ef4444","neutral":"#f59e0b"}.get(s,"rgba(255,255,255,0.40)")
def sig_bg(s):    return {"bullish":"rgba(34,197,94,0.10)","bearish":"rgba(239,68,68,0.10)","neutral":"rgba(245,158,11,0.10)"}.get(s,"rgba(255,255,255,0.05)")
def sig_label(s): return {"bullish":"↑ 看多","bearish":"↓ 看空","neutral":"→ 中性"}.get(s,s)
def sig_cls(s):   return {"bullish":"sig-bull","bearish":"sig-bear","neutral":"sig-neut"}.get(s,"sig-neut")
def sc_color(v):  return "#22c55e" if v>=65 else "#ef4444" if v<=40 else "#f59e0b"
def bar(v, color=None):
    c = color or sc_color(v)
    cls = "bar-fill-green" if c=="#22c55e" else "bar-fill-red" if c=="#ef4444" else "bar-fill-yellow"
    return f'<div class="bar-wrap"><div class="{cls}" style="width:{min(v,100):.0f}%"></div></div>'

def run_analysis_bg(code, rounds, log_path):
    cmd = [sys.executable, "-u", str(ROOT/"run_analysis.py"), code, "--rounds", str(rounds)]
    with open(log_path, "w", encoding="utf-8", buffering=1) as f:
        subprocess.run(cmd, stdout=f, stderr=f)

# ══════════════════════════════════════════════════════
#  Session State  +  URL query_params 驱动导航
#  用 query_params 而非 st.rerun() 切换页面 → 更快
# ══════════════════════════════════════════════════════
DEFAULTS = {"running":False,"current_code":"","log_path":None,"thread":None,"done":False,
            "api_preset":"DeepSeek","api_key":"sk-bc93edf010d6424985374c9f858fa336",
            "api_base":"https://api.deepseek.com","api_model":"deepseek-v4-pro","api_ok":None}
for k, v in DEFAULTS.items():
    if k not in st.session_state: st.session_state[k] = v

# 从 URL 读当前页面（首次加载 / 刷新时恢复）
_qp = st.query_params
_valid_pages = {"home","etf50","stocks60","deep","quant","ths"}
if "page" not in st.session_state:
    st.session_state.page = _qp.get("page","home") if _qp.get("page","home") in _valid_pages else "home"

def _nav_to(page: str):
    """切换页面：写 session + query_params，不 rerun（更快）"""
    st.session_state.page = page
    st.query_params["page"] = page

# ══════════════════════════════════════════════════════
#  侧边栏
# ══════════════════════════════════════════════════════
with st.sidebar:
    # Logo
    st.markdown("""
    <div style="padding:16px 8px 20px 8px">
      <div style="display:flex;align-items:center;gap:10px">
        <div style="font-size:24px">📊</div>
        <div>
          <div style="font-size:15px;font-weight:700;color:#ffffff;letter-spacing:-0.02em">NASDX</div>
          <div style="font-size:11px;color:#48484a">A股量化分析平台</div>
        </div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # 导航 — 用 on_change 回调代替 st.rerun()，减少一次渲染循环
    NAV = [("🏠","首页","home"),("📊","ETF 50","etf50"),("📈","个股扫描","stocks60"),
           ("🤖","深度分析","deep"),("⚗️","量化引擎","quant"),("🔗","同花顺","ths")]

    pg = st.session_state.page
    for icon, label, key in NAV:
        is_active = pg == key
        # active 页面用不同样式
        btn_style = "margin:1px 0;" + ("background:rgba(10,132,255,0.15);border-radius:10px;" if is_active else "")
        st.markdown(f'<div style="{btn_style}">', unsafe_allow_html=True)
        if st.button(
            f"{icon}  {label}",
            key=f"nav_{key}",
            use_container_width=True,
            type="primary" if is_active else "secondary",
        ):
            _nav_to(key)
        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown('<hr class="n-divider">', unsafe_allow_html=True)

    # 深度分析输入（仅在 deep 页面显示）
    if st.session_state.page == "deep":
        st.markdown('<div class="n-label" style="padding-left:4px">股票代码</div>', unsafe_allow_html=True)
        stock_input = st.text_input("", placeholder="如 603501、512480", label_visibility="collapsed", max_chars=6)
        rounds = st.slider("辩论轮数", 1, 3, 1, help="轮数越多分析越深入，耗时越长")
        st.caption(f"预计耗时 {rounds*3+2} 分钟")
        run_btn = st.button("▶  开始分析", disabled=st.session_state.running, use_container_width=True)
        st.markdown('<hr class="n-divider">', unsafe_allow_html=True)
    else:
        stock_input, rounds, run_btn = "", 1, False

    # 股票快速选择
    st.markdown('<div class="n-label" style="padding-left:4px;margin-bottom:8px">快速选股</div>', unsafe_allow_html=True)
    for sector, stocks in POOL.items():
        with st.expander(sector, expanded=False):
            for code, name in stocks:
                if st.button(f"{code}  {name}", key=f"q_{sector}_{code}", use_container_width=True):
                    st.session_state.page = "deep"
                    st.session_state["_quick"] = code
                    st.rerun()

    st.markdown('<hr class="n-divider">', unsafe_allow_html=True)

    # API 配置
    st.markdown('<div class="n-label" style="padding-left:4px;margin-bottom:8px">API 配置</div>', unsafe_allow_html=True)

    preset = st.selectbox("平台", list(PRESETS.keys()),
                          index=list(PRESETS.keys()).index(st.session_state.api_preset),
                          key="preset_sel", label_visibility="collapsed")
    if preset != st.session_state.api_preset:
        st.session_state.api_preset = preset
        base, models = PRESETS[preset]
        if base: st.session_state.api_base = base
        if models: st.session_state.api_model = models[0]
        st.session_state.api_ok = None
        st.rerun()

    api_key_in = st.text_input("API Key", value=st.session_state.api_key, type="password", label_visibility="collapsed", placeholder="API Key")
    if api_key_in != st.session_state.api_key:
        st.session_state.api_key = api_key_in; st.session_state.api_ok = None

    api_base_in = st.text_input("Base URL", value=st.session_state.api_base, label_visibility="collapsed", placeholder="Base URL")
    if api_base_in != st.session_state.api_base:
        st.session_state.api_base = api_base_in; st.session_state.api_ok = None

    preset_models = PRESETS.get(preset, ("", []))[1]
    if preset_models:
        cur_idx = preset_models.index(st.session_state.api_model) if st.session_state.api_model in preset_models else 0
        model_sel = st.selectbox("模型", preset_models + ["自定义..."], index=cur_idx, key="model_sel", label_visibility="collapsed")
        model_final = st.text_input("", value=st.session_state.api_model, key="model_custom", placeholder="自定义模型名") if model_sel == "自定义..." else model_sel
    else:
        model_final = st.text_input("模型名", value=st.session_state.api_model, label_visibility="collapsed", placeholder="模型名称")
    if model_final != st.session_state.api_model:
        st.session_state.api_model = model_final; st.session_state.api_ok = None

    c1, c2 = st.columns(2)
    with c1:
        if st.button("测试", key="test_api", use_container_width=True):
            with st.spinner(""):
                try:
                    import openai as _oa
                    c = _oa.OpenAI(api_key=st.session_state.api_key, base_url=st.session_state.api_base, timeout=10)
                    c.chat.completions.create(model=st.session_state.api_model, messages=[{"role":"user","content":"hi"}], max_tokens=5)
                    st.session_state.api_ok = True
                    _update_llm_config(st.session_state.api_key, st.session_state.api_base, st.session_state.api_model)
                except Exception as e:
                    st.session_state.api_ok = False
            st.rerun()
    with c2:
        if st.button("应用", key="apply_cfg", use_container_width=True):
            _update_llm_config(st.session_state.api_key, st.session_state.api_base, st.session_state.api_model)
            st.toast("配置已应用", icon="✅")

    status_html = {True:'<span style="color:#30d158;font-size:12px">● 已连接</span>', False:'<span style="color:#ff453a;font-size:12px">● 连接失败</span>', None:'<span style="color:#48484a;font-size:12px">● 未测试</span>'}[st.session_state.api_ok]
    st.markdown(f'<div style="padding:4px 4px 0">{status_html} &nbsp; <span style="color:#48484a;font-size:11px">{st.session_state.api_model}</span></div>', unsafe_allow_html=True)

    st.markdown("""
    <div style="padding:16px 4px 4px;font-size:11px;color:#4a4a4a">
      数据来源 AkShare &nbsp;·&nbsp;
      <a href="https://github.com/Caesar-ZZh/NASDX" style="color:#48484a;text-decoration:none">GitHub ↗</a>
    </div>
    """, unsafe_allow_html=True)

# 处理快速选股跳转
if "_quick" in st.session_state:
    stock_input = st.session_state.pop("_quick")

pg = st.session_state.page

# ══════════════════════════════════════════════════════
#  首页
# ══════════════════════════════════════════════════════
if pg == "home":
    # 顶部标题 + 副标题 + 日期
    st.markdown(f"""
    <div style="padding:28px 0 20px 0">
      <div class="n-title">NASDX</div>
      <div class="n-sub">A股多智能体量化分析平台 · DeepSeek V4 Pro · 今日 {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
    </div>
    """, unsafe_allow_html=True)

    # 三个功能入口卡片
    c1, c2, c3 = st.columns(3, gap="medium")
    with c1:
        st.markdown("""
        <div class="n-card n-card-accent-green">
          <div style="font-size:20px;margin-bottom:12px">📊</div>
          <div style="font-size:14px;font-weight:600;color:#fff;margin-bottom:4px">ETF 50 扫描</div>
          <div class="n-sub" style="margin-bottom:12px">50只主流ETF技术面评分 · 实时溢价率</div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("进入 →", key="g_etf", use_container_width=True):
            _nav_to("etf50")

    with c2:
        st.markdown("""
        <div class="n-card n-card-accent-yellow">
          <div style="font-size:20px;margin-bottom:12px">📈</div>
          <div style="font-size:14px;font-weight:600;color:#fff;margin-bottom:4px">60只个股扫描</div>
          <div class="n-sub" style="margin-bottom:12px">10大热门板块龙头 · 技术面综合评分</div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("进入 →", key="g_st", use_container_width=True):
            _nav_to("stocks60")

    with c3:
        st.markdown("""
        <div class="n-card n-card-accent-blue">
          <div style="font-size:20px;margin-bottom:12px">🤖</div>
          <div style="font-size:14px;font-weight:600;color:#fff;margin-bottom:4px">深度分析</div>
          <div class="n-sub" style="margin-bottom:12px">4 Agent 并行研究 · Battle 辩论</div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("进入 →", key="g_deep", use_container_width=True):
            _nav_to("deep")

    st.markdown('<hr class="n-divider">', unsafe_allow_html=True)

    # ETF50 最新结果
    d = load_etf50()
    if d:
        st.markdown(f'<div class="n-section-title">ETF50 最新扫描 · {d["datetime"][:16]}</div>', unsafe_allow_html=True)

        sc1,sc2,sc3,sc4 = st.columns(4, gap="small")
        for col,(label,val,color) in zip([sc1,sc2,sc3,sc4],[
            ("看多",d["bullish"],"#22c55e"),("中性",d["neutral"],"#f59e0b"),
            ("看空",d["bearish"],"#ef4444"),("总计",d["total"],"#3b82f6")]):
            with col:
                st.markdown(f"""
                <div class="n-card" style="text-align:center;padding:12px 8px">
                  <div style="font-size:24px;font-weight:600;color:{color}">{val}</div>
                  <div style="font-size:11px;color:rgba(255,255,255,0.40);margin-top:4px">{label}</div>
                </div>""", unsafe_allow_html=True)

        top3 = d.get("top3", [])
        if top3:
            st.markdown('<div style="margin-top:12px"></div>', unsafe_allow_html=True)
            t1,t2,t3 = st.columns(3, gap="medium")
            medals = [("🥇","n-card-accent-green"),("🥈","n-card-accent-blue"),("🥉","n-card-accent-yellow")]
            for col, r, (medal, accent) in zip([t1,t2,t3], top3, medals):
                sc = r.get("score",0); color = sc_color(sc)
                prem = r.get("premium"); prem_s = f'溢价 {prem:+.2f}%' if prem is not None else ""
                prem_c = "#ef4444" if prem and prem>2 else "#22c55e" if prem and prem<-0.5 else "rgba(255,255,255,0.40)"
                with col:
                    st.markdown(f"""
                    <div class="n-card {accent}">
                      <div style="font-size:16px;margin-bottom:8px">{medal}</div>
                      <div style="font-size:11px;color:rgba(255,255,255,0.40);margin-bottom:2px">{r['code']}</div>
                      <div style="font-size:14px;font-weight:600;color:#fff;margin-bottom:8px">{r['name']}</div>
                      <div style="font-size:26px;font-weight:600;color:{color};letter-spacing:-0.02em">{sc}</div>
                      <div style="font-size:11px;color:rgba(255,255,255,0.40);margin-top:2px">评分</div>
                      <div style="margin-top:8px">{bar(sc)}</div>
                      <div style="font-size:11px;color:{prem_c};margin-top:6px">{prem_s}</div>
                    </div>""", unsafe_allow_html=True)

    # 历史报告
    all_r = sorted(ROOT.glob("reports/report_*.json"), key=os.path.getmtime, reverse=True)[:6]
    if all_r:
        st.markdown('<hr class="n-divider">', unsafe_allow_html=True)
        st.markdown('<div class="n-section-title">最近深度分析</div>', unsafe_allow_html=True)
        rc = st.columns(3, gap="medium")
        for col, rp in zip(rc * 2, all_r):
            with open(rp, encoding="utf-8") as f: rd = json.load(f)
            sig = rd.get("final_signal","neutral")
            color = sig_color(sig); sl = sig_label(sig); bp = rd.get("bullish_pct",50)
            with col:
                st.markdown(f"""
                <div class="n-card" style="cursor:pointer;padding:12px 14px">
                  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
                    <span style="font-size:13px;font-weight:600;color:#fff">{rd.get('stock_code','')} {rd.get('stock_name','')}</span>
                    <span class="{sig_cls(sig)}">{sl}</span>
                  </div>
                  <div style="font-size:11px;color:rgba(255,255,255,0.40)">{rd.get('date','')} · 看多 {bp:.0f}%</div>
                  {bar(bp)}
                </div>""", unsafe_allow_html=True)
                if st.button("查看", key=f"h_{rp.stem}", use_container_width=True):
                    st.session_state["_quick"] = rd.get("stock_code","")
                    _nav_to("deep")

# ══════════════════════════════════════════════════════
#  ETF50 页
# ══════════════════════════════════════════════════════
elif pg == "etf50":
    st.markdown('<div style="padding:24px 0 20px"><div style="font-size:26px;font-weight:700;color:#fff;letter-spacing:-0.02em">ETF 50 扫描</div><div style="font-size:13px;color:#636366;margin-top:4px">50只主流ETF技术面评分 · 实时溢价率</div></div>', unsafe_allow_html=True)

    c_btn, _ = st.columns([1,4])
    with c_btn:
        if st.button("↻  立即扫描", use_container_width=True):
            with st.spinner("扫描中，约 3 分钟..."):
                subprocess.run([sys.executable, str(ROOT/"scan_etf50.py")], capture_output=True)
            st.rerun()

    d = load_etf50()
    if not d:
        st.markdown('<div class="n-card" style="text-align:center;padding:48px;color:#48484a">暂无数据，点击「立即扫描」或等待定时任务</div>', unsafe_allow_html=True)
    else:
        st.markdown(f'<div style="font-size:12px;color:#4a4a4a;margin:12px 0">{d["datetime"][:16]} · {d["total"]} 只有效</div>', unsafe_allow_html=True)

        s1,s2,s3,s4 = st.columns(4, gap="small")
        for col,(lb,v,c) in zip([s1,s2,s3,s4],[("看多",d["bullish"],"#22c55e"),("中性",d["neutral"],"#f59e0b"),("看空",d["bearish"],"#ef4444"),("总计",d["total"],"#3b82f6")]):
            with col:
                st.markdown(f'<div class="n-card" style="text-align:center;padding:12px"><div style="font-size:24px;font-weight:600;color:{c}">{v}</div><div style="font-size:11px;color:rgba(255,255,255,0.40)">{lb}</div></div>', unsafe_allow_html=True)

        top3 = d.get("top3",[])
        if top3:
            st.markdown('<hr class="n-divider">', unsafe_allow_html=True)
            t1,t2,t3 = st.columns(3, gap="medium")
            for col,r,m in zip([t1,t2,t3],top3,["🥇","🥈","🥉"]):
                sc=r.get("score",0); color=sc_color(sc)
                prem=r.get("premium"); prem_s=f'{prem:+.2f}%' if prem is not None else "-"
                prem_c="#ef4444" if prem and prem>2 else "#22c55e" if prem and prem<-0.5 else "rgba(255,255,255,0.40)"
                with col:
                    st.markdown(f"""
                    <div class="n-card">
                      <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:12px">
                        <div>
                          <div style="font-size:11px;color:rgba(255,255,255,0.40)">{r['code']}</div>
                          <div style="font-size:14px;font-weight:600;color:#fff">{r['name']}</div>
                        </div>
                        <div style="font-size:16px">{m}</div>
                      </div>
                      <div style="font-size:28px;font-weight:600;color:{color};letter-spacing:-0.02em">{sc}<span style="font-size:12px;color:rgba(255,255,255,0.40)"> 分</span></div>
                      {bar(sc)}
                      <div style="display:flex;justify-content:space-between;margin-top:10px;font-size:12px">
                        <span class="{sig_cls(r.get('signal',''))}">{sig_label(r.get('signal',''))}</span>
                        <span style="color:{prem_c}">溢价 {prem_s}</span>
                      </div>
                    </div>""", unsafe_allow_html=True)

        st.markdown('<hr class="n-divider">', unsafe_allow_html=True)
        fc1, fc2 = st.columns([1,4])
        with fc1:
            sig_f = st.selectbox("", ["全部","看多","中性","看空"], key="etf_sig", label_visibility="collapsed")
        sm = {"全部":None,"看多":"bullish","中性":"neutral","看空":"bearish"}
        filtered = [r for r in d.get("results",[]) if not sm[sig_f] or r.get("signal")==sm[sig_f]]

        for i,r in enumerate(filtered,1):
            sc=r.get("score",0); sig=r.get("signal","neutral"); color=sc_color(sc)
            today_chg=r.get("spot_chg"); chg_s=f"{today_chg:+.2f}%" if today_chg is not None else "-"
            chg_c="#22c55e" if today_chg and today_chg>0 else "#ef4444" if today_chg and today_chg<0 else "rgba(255,255,255,0.40)"
            prem=r.get("premium"); prem_s=f"{prem:+.2f}%" if prem is not None else "-"
            prem_c="#ef4444" if prem and prem>2 else "#22c55e" if prem and prem<-0.5 else "rgba(255,255,255,0.40)"
            medal={1:"🥇",2:"🥈",3:"🥉"}.get(i,"")
            reasons=r.get("reasons",[])
            pills="".join(f'<span class="n-pill">{x}</span>' for x in reasons[:3])
            with st.expander(f"{medal}  {r['code']}  {r['name']}  ·  {sc} 分  ·  今日 {chg_s}  ·  溢价 {prem_s}", expanded=(i<=3)):
                ec1,ec2,ec3,ec4,ec5 = st.columns(5)
                ec1.metric("评分",f"{sc}")
                ec2.metric("今日涨跌",chg_s)
                ec3.metric("场内价",f"{r.get('spot_price','-')}")
                ec4.metric("溢价率",prem_s)
                ec5.metric("类别",r.get("category",""))
                if pills: st.markdown(pills, unsafe_allow_html=True)

# ══════════════════════════════════════════════════════
#  个股60 页
# ══════════════════════════════════════════════════════
elif pg == "stocks60":
    st.markdown('<div style="padding:24px 0 20px"><div style="font-size:26px;font-weight:700;color:#fff;letter-spacing:-0.02em">60 只个股扫描</div><div style="font-size:13px;color:#636366;margin-top:4px">10大热门板块龙头 · 技术面综合评分</div></div>', unsafe_allow_html=True)

    c_btn, _ = st.columns([1,4])
    with c_btn:
        if st.button("↻  立即扫描", use_container_width=True, key="scan_st"):
            with st.spinner("扫描中，约 5 分钟..."):
                subprocess.run([sys.executable, str(ROOT/"scan_stocks_full.py")], capture_output=True, timeout=600)
            st.rerun()

    d = load_stocks60()
    if not d:
        st.markdown('<div class="n-card" style="text-align:center;padding:48px;color:#48484a">暂无数据</div>', unsafe_allow_html=True)
    else:
        st.markdown(f'<div style="font-size:12px;color:#4a4a4a;margin:12px 0">{d["datetime"][:16]} · {d["total"]} 只有效</div>', unsafe_allow_html=True)

        s1,s2,s3 = st.columns(3, gap="small")
        for col,(lb,v,c) in zip([s1,s2,s3],[("看多",d["bullish"],"#22c55e"),("中性",d["neutral"],"#f59e0b"),("看空",d["bearish"],"#ef4444")]):
            with col:
                st.markdown(f'<div class="n-card" style="text-align:center;padding:12px"><div style="font-size:24px;font-weight:600;color:{c}">{v}</div><div style="font-size:11px;color:rgba(255,255,255,0.40)">{lb}</div></div>', unsafe_allow_html=True)

        top3 = d.get("top3",[])
        if top3:
            st.markdown('<hr class="n-divider">', unsafe_allow_html=True)
            t1,t2,t3 = st.columns(3, gap="medium")
            for col,r,m in zip([t1,t2,t3],top3,["🥇","🥈","🥉"]):
                sc=r.get("score",0); color=sc_color(sc)
                chg=r.get("chg",0); chg_c="#22c55e" if chg>0 else "#ef4444"
                with col:
                    st.markdown(f"""
                    <div class="n-card">
                      <div style="display:flex;justify-content:space-between;margin-bottom:10px">
                        <div>
                          <div style="font-size:11px;color:rgba(255,255,255,0.40)">{r['code']}</div>
                          <div style="font-size:14px;font-weight:600;color:#fff">{r['name']}</div>
                        </div>
                        <div style="font-size:16px">{m}</div>
                      </div>
                      <div style="font-size:28px;font-weight:600;color:{color};letter-spacing:-0.02em">{sc}<span style="font-size:12px;color:rgba(255,255,255,0.40)"> 分</span></div>
                      {bar(sc)}
                      <div style="font-size:14px;font-weight:600;color:{chg_c};margin-top:8px">{chg:+.2f}%</div>
                    </div>""", unsafe_allow_html=True)

        st.markdown('<hr class="n-divider">', unsafe_allow_html=True)
        fc1, fc2 = st.columns([1,2])
        with fc1: sig_f = st.selectbox("", ["全部","看多","中性","看空"], key="st_sig", label_visibility="collapsed")
        with fc2:
            results = d.get("results",[])
            sectors = ["全部板块"]+sorted(set(r.get("sector","") for r in results))
            sector_f = st.selectbox("", sectors, key="st_sector", label_visibility="collapsed")
        sm2 = {"全部":None,"看多":"bullish","中性":"neutral","看空":"bearish"}
        filtered2 = [r for r in results if (not sm2[sig_f] or r.get("signal")==sm2[sig_f]) and (sector_f=="全部板块" or r.get("sector","")==sector_f)]

        for i,r in enumerate(filtered2,1):
            sc=r.get("score",0); sig=r.get("signal","neutral"); color=sc_color(sc)
            chg=r.get("chg",0); chg_c="#22c55e" if chg>0 else "#ef4444"
            medal={1:"🥇",2:"🥈",3:"🥉"}.get(i,"")
            with st.expander(f"{medal}  {r['code']}  {r['name']}  [{r.get('sector','')}]  ·  {sc} 分  ·  {chg:+.2f}%"):
                dc1,dc2,dc3,dc4,dc5 = st.columns(5)
                dc1.metric("收盘",f"¥{r.get('close',0):.2f}")
                dc2.metric("涨跌",f"{chg:+.2f}%")
                dc3.metric("RSI",f"{r.get('rsi',0):.0f}")
                dc4.metric("量比",f"{r.get('vol_ratio',0):.1f}")
                dc5.metric("MACD",f"{r.get('macd_bar',0):+.3f}")

# ══════════════════════════════════════════════════════
#  深度分析页
# ══════════════════════════════════════════════════════
elif pg == "deep":
    st.markdown('<div style="padding:24px 0 20px"><div style="font-size:26px;font-weight:700;color:#fff;letter-spacing:-0.02em">多智能体深度分析</div><div style="font-size:13px;color:#636366;margin-top:4px">4 Agent 并行研究 · Battle 多空辩论 · DeepSeek V4 Pro</div></div>', unsafe_allow_html=True)

    # 触发分析
    if run_btn and stock_input and not st.session_state.running:
        code = stock_input.strip().zfill(6)
        log_path = ROOT / f"nasdx_log_{code}.txt"
        log_path.write_text("", encoding="utf-8")
        t = threading.Thread(target=run_analysis_bg, args=(code, rounds, log_path), daemon=True)
        t.start()
        st.session_state.update({"running":True,"current_code":code,"log_path":str(log_path),"thread":t,"done":False})
        st.rerun()

    # 运行中
    if st.session_state.running:
        code = st.session_state.current_code
        log_path = Path(st.session_state.log_path)
        log_text = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        lines = [l for l in log_text.splitlines() if l.strip() and "[LLM]" not in l]
        STEPS = ["技术面","资金流","风险","板块","辩论","综合","完成"]
        done_n = sum(1 for s in STEPS if any(s in l for l in lines))
        pct = min(done_n/len(STEPS), 0.95)

        st.markdown(f"""
        <div class="n-card n-card-accent-blue" style="margin-bottom:20px">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
            <div>
              <div class="n-label">分析中</div>
              <div style="font-size:20px;font-weight:600;color:#fff">{code}</div>
            </div>
            <div style="font-size:13px;color:rgba(255,255,255,0.40)">{st.session_state.api_model}</div>
          </div>
        </div>
        """, unsafe_allow_html=True)
        st.progress(pct)

        with st.expander("实时日志", expanded=True):
            st.code("\n".join(lines[-12:]) if lines else "正在启动...", language=None)

        report = load_report(code)
        thread_alive = st.session_state.thread and st.session_state.thread.is_alive()
        if "✅ 分析完成" in log_text or (report and not thread_alive):
            st.session_state.update({"running":False,"done":True}); st.rerun()
        else:
            time.sleep(3); st.rerun()

    # 展示报告
    def show_report(data):
        code=data.get("stock_code",""); name=data.get("stock_name","")
        sig=data.get("final_signal","neutral"); bpct=data.get("bullish_pct",50)
        summary=clean(data.get("summary","")); research=data.get("research_results",{})
        votes=data.get("votes",[]); transcript=data.get("battle_transcript",[]); date_s=data.get("date","")
        color=sig_color(sig)

        # Hero bar
        st.markdown(f"""
        <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:20px;flex-wrap:wrap;gap:12px">
          <div>
            <div style="font-size:11px;color:rgba(255,255,255,0.40);margin-bottom:4px">{code} · {date_s}</div>
            <div style="font-size:28px;font-weight:600;color:#fff;letter-spacing:-0.02em">{name}</div>
          </div>
          <div style="text-align:right">
            <div class="{sig_cls(sig)}" style="font-size:14px;padding:6px 12px;margin-bottom:6px">{sig_label(sig)}</div>
            <div style="font-size:11px;color:rgba(255,255,255,0.40)">看多占比 {bpct:.1f}%</div>
          </div>
        </div>
        """, unsafe_allow_html=True)

        # 看多进度
        st.markdown(f"""
        <div class="n-card" style="margin-bottom:16px;padding:12px 14px">
          <div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:8px">
            <span style="color:#22c55e;font-weight:600">看多 {bpct:.1f}%</span>
            <span style="color:#ef4444;font-weight:600">看空 {100-bpct:.1f}%</span>
          </div>
          {bar(bpct)}
        </div>
        """, unsafe_allow_html=True)

        # 4维度卡片
        dim_meta = {
            "technical": ("📈","技术面","MA · MACD · RSI · 布林"),
            "fund_flow":  ("💰","资金流","主力 · 超大单 · 大单"),
            "risk":       ("🛡️","风险","超买 · 背离 · 波动"),
            "sector":     ("🏭","板块","轮动 · 相对强弱"),
            "synthesis":  ("🎯","综合","多维整合"),
        }
        dim_cols = st.columns(len(research), gap="small")
        for col,(dim,r) in zip(dim_cols, research.items()):
            if not r: continue
            icon,title,sub = dim_meta.get(dim,(dim,dim,""))
            rs=r.get("signal","neutral"); rcolor=sig_color(rs)
            conf=r.get("confidence",0.5)
            with col:
                st.markdown(f"""
                <div class="n-card" style="padding:12px">
                  <div style="font-size:16px;margin-bottom:8px">{icon}</div>
                  <div style="font-size:11px;color:rgba(255,255,255,0.40);margin-bottom:2px">{title}</div>
                  <div style="font-size:14px;font-weight:600;color:{rcolor}">{sig_label(rs)}</div>
                  <div style="font-size:11px;color:rgba(255,255,255,0.40);margin-top:4px">{conf:.0%} · {sub}</div>
                </div>""", unsafe_allow_html=True)

        st.markdown('<hr class="n-divider">', unsafe_allow_html=True)

        # 综合研判 + 要点
        col_l, col_r = st.columns([3,2], gap="medium")
        with col_l:
            st.markdown('<div class="n-section-title">综合研判</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="n-card" style="font-size:13px;line-height:1.8;color:rgba(255,255,255,0.75)">{summary.replace(chr(10),"<br>") if summary else "暂无"}</div>', unsafe_allow_html=True)
        with col_r:
            st.markdown('<div class="n-section-title">各维度要点</div>', unsafe_allow_html=True)
            for dim,r in research.items():
                if not r: continue
                icon,title,_ = dim_meta.get(dim,(dim,dim,""))
                pts=r.get("key_points",[])[:2]
                if not pts: continue
                dcolor=sig_color(r.get("signal","neutral"))
                st.markdown(f'<div style="font-size:11px;color:{dcolor};font-weight:600;margin:10px 0 4px">{icon} {title}</div>', unsafe_allow_html=True)
                for pt in pts:
                    st.markdown(f'<span class="n-pill">{pt}</span>', unsafe_allow_html=True)

        st.markdown('<hr class="n-divider">', unsafe_allow_html=True)

        # Battle 辩论
        st.markdown('<div class="n-section-title">Battle 辩论</div>', unsafe_allow_html=True)
        for msg in transcript:
            css = "bubble-bull" if msg.startswith("🟢") else "bubble-bear" if msg.startswith("🔴") else "bubble-judge"
            clean_msg = msg.replace("<","&lt;").replace(">","&gt;").replace("\n","<br>")
            st.markdown(f'<div class="bubble {css}">{clean_msg}</div>', unsafe_allow_html=True)

        # 投票
        st.markdown('<div class="n-section-title">专家投票</div>', unsafe_allow_html=True)
        if votes:
            vcols = st.columns(len(votes), gap="small")
            for col,v in zip(vcols,votes):
                vcolor=sig_color(v.get("vote","neutral"))
                vlabel=sig_label(v.get("vote","neutral"))
                with col:
                    st.markdown(f"""
                    <div class="n-card" style="text-align:center;padding:12px">
                      <div style="font-size:11px;color:rgba(255,255,255,0.40);margin-bottom:6px">{v.get('agent_name',v.get('agent',''))}</div>
                      <div style="font-size:14px;font-weight:600;color:{vcolor}">{vlabel}</div>
                      <div style="font-size:10px;color:rgba(255,255,255,0.40);margin-top:6px;line-height:1.5">{v.get('reasoning','')[:40]}</div>
                    </div>""", unsafe_allow_html=True)

        st.markdown('<hr class="n-divider">', unsafe_allow_html=True)
        st.markdown('<div style="text-align:center;font-size:11px;color:rgba(255,255,255,0.25)">⚠️ 仅供学习研究，不构成投资建议 · NASDX</div>', unsafe_allow_html=True)

    if st.session_state.done and st.session_state.current_code:
        data = load_report(st.session_state.current_code)
        if data: show_report(data)
        else: st.error("报告生成失败，请查看日志")
    elif not st.session_state.running:
        code_to_show = (stock_input or "").strip().zfill(6) if stock_input else ""
        data = load_report(code_to_show) if code_to_show else None
        if data:
            st.info(f"显示 {code_to_show} 的历史报告（{data.get('date','')}）")
            show_report(data)
        else:
            st.markdown("""
            <div style="text-align:center;padding:80px 20px">
              <div style="font-size:48px;margin-bottom:16px">🤖</div>
              <div style="font-size:18px;font-weight:600;color:#fff;margin-bottom:8px">在左侧输入股票代码</div>
              <div style="font-size:13px;color:#48484a">支持 A股个股 · ETF · LOF<br>4个AI专家并行分析，Battle辩论，DeepSeek V4 Pro 推理</div>
            </div>
            """, unsafe_allow_html=True)


# ══════════════════════════════════════════════════════
#  同花顺接入页（路由到独立模块）
# ══════════════════════════════════════════════════════
elif pg == "ths":
    try:
        from ths_page import render_ths_page
        render_ths_page(st, ROOT)
    except Exception as _e:
        st.error(f"同花顺页面加载失败：{_e}")


# ══════════════════════════════════════════════════════
#  量化引擎页（路由到独立模块）
# ══════════════════════════════════════════════════════
elif pg == "quant":
    try:
        from quant_page import render_quant_page
        render_quant_page(st, ROOT)
    except Exception as _e:
        st.error(f"量化引擎加载失败：{_e}")
        import traceback; st.code(traceback.format_exc())
