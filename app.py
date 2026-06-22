"""
NASDX — A股多智能体量化分析平台
Streamlit · DeepSeek V4 Pro · Notion 风格 UI
"""
import sys, os, json, subprocess, threading, time, glob, html
from pathlib import Path
from datetime import datetime

import streamlit as st

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# ── 后台任务状态：只把 task_id 放入 session_state ───────
RUNNING_TASKS = {}
TASK_LOCK = threading.Lock()


def _new_task_id(prefix: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return f"{prefix}_{stamp}"


def _register_task(task_id: str, thread: threading.Thread, log_path: Path | None = None) -> None:
    with TASK_LOCK:
        RUNNING_TASKS[task_id] = {
            "thread": thread,
            "log_path": str(log_path) if log_path else None,
            "started_at": time.time(),
        }


def _task_alive(task_id: str | None) -> bool:
    if not task_id:
        return False
    with TASK_LOCK:
        item = RUNNING_TASKS.get(task_id)
    thread = item.get("thread") if item else None
    alive = bool(thread and thread.is_alive())
    if item and not alive:
        with TASK_LOCK:
            RUNNING_TASKS.pop(task_id, None)
    return alive


def _build_llm_env(api_key: str, base_url: str, model: str) -> dict:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["NASDX_API_KEY"] = api_key or ""
    env["NASDX_BASE_URL"] = base_url or "https://api.deepseek.com"
    env["NASDX_MODEL"] = model or "deepseek-v4-pro"
    return env

# ── 页面配置 ─────────────────────────────────────────
st.set_page_config(
    page_title="NASDX · A股量化分析",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="auto",
)

# ══════════════════════════════════════════════════════
#  CSS 注入 — cache_resource 缓存 CSS 文本，每次 rerun 都注入
# ══════════════════════════════════════════════════════
@st.cache_resource(show_spinner=False)
def _load_css() -> str:
    """从文件加载 CSS 字符串（只读一次），返回值被缓存"""
    css_path = ROOT / "static" / "style.css"
    return css_path.read_text(encoding="utf-8") if css_path.exists() else ""

# 每次 rerun 都调用 st.markdown（必须），但读文件只有一次（缓存）
_css = _load_css()
if _css:
    st.markdown(f"<style>{_css}</style>", unsafe_allow_html=True)

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
    files = sorted(ROOT.glob("reports/etf50_[0-9]*_[0-9]*.json"), key=os.path.getmtime, reverse=True)
    if not files: return None
    with open(files[0], encoding="utf-8") as f: return json.load(f)

@st.cache_data(ttl=60, show_spinner=False)
def load_stocks60():
    files = sorted(ROOT.glob("reports/stocks60_*.json"), key=os.path.getmtime, reverse=True)
    if not files: return None
    with open(files[0], encoding="utf-8") as f: return json.load(f)

@st.cache_data(ttl=60, show_spinner=False)
def load_portfolio_latest():
    path = ROOT / "reports" / "portfolio_plan_latest.json"
    if not path.exists(): return None
    with open(path, encoding="utf-8") as f: return json.load(f)

@st.cache_data(ttl=60, show_spinner=False)
def load_investment_brief_latest():
    path = ROOT / "reports" / "investment_brief_latest.json"
    if not path.exists(): return None
    with open(path, encoding="utf-8") as f: return json.load(f)

@st.cache_data(ttl=60, show_spinner=False)
def load_recommendation_tracker_latest():
    path = ROOT / "reports" / "recommendation_tracker_latest.json"
    if not path.exists(): return None
    with open(path, encoding="utf-8") as f: return json.load(f)

@st.cache_data(ttl=60, show_spinner=False)
def load_recommendation_review_latest():
    path = ROOT / "reports" / "recommendation_review_latest.json"
    if not path.exists(): return None
    with open(path, encoding="utf-8") as f: return json.load(f)

@st.cache_data(ttl=60, show_spinner=False)
def load_account_review_latest():
    path = ROOT / "reports" / "account_review_latest.json"
    if not path.exists(): return None
    with open(path, encoding="utf-8") as f: return json.load(f)

@st.cache_resource(show_spinner=False)
def load_pool():
    """加载 ETF 池数据 — 用 cache_resource 避免每次序列化开销"""
    with open(ROOT / "etf50_pool.json", encoding="utf-8") as f:
        return json.load(f)["etfs"]

@st.cache_resource(show_spinner=False)
def load_recent_reports(n=6):
    """加载最近报告 — 用 cache_resource 避免每次 glob 和序列化开销"""
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

def run_analysis_bg(code, rounds, risk_profile, workflow, analysis_mode, log_path, env):
    cmd = [
        sys.executable, "-u", str(ROOT/"run_investment_workflow.py"),
        code,
        "--workflow", workflow,
        "--rounds", str(rounds),
        "--risk-profile", risk_profile,
        "--analysis-mode", analysis_mode,
    ]
    with open(log_path, "w", encoding="utf-8", buffering=1) as f:
        subprocess.run(cmd, stdout=f, stderr=f, env=env)

# ══════════════════════════════════════════════════════
#  Session State  +  URL query_params 驱动导航
#  用 query_params 而非 st.rerun() 切换页面 → 更快
# ══════════════════════════════════════════════════════
DEFAULTS = {"running":False,"current_code":"","log_path":None,"task_id":None,"done":False,
            "api_preset":"DeepSeek","api_key":os.environ.get("NASDX_API_KEY",""),
            "api_base":os.environ.get("NASDX_BASE_URL","https://api.deepseek.com"),
            "api_model":os.environ.get("NASDX_MODEL","deepseek-v4-pro"),"api_ok":None}
for k, v in DEFAULTS.items():
    if k not in st.session_state: st.session_state[k] = v

# 从 URL 读当前页面（首次加载 / 刷新时恢复）
_qp = st.query_params
_valid_pages = {"home","plan","etf50","stocks60","deep","quant","ths"}
if "page" not in st.session_state:
    st.session_state.page = _qp.get("page","home") if _qp.get("page","home") in _valid_pages else "home"

def _nav_to(page: str):
    """切换页面：写 session + query_params + rerun 保证内容区立刻更新"""
    st.session_state.page = page
    st.query_params["page"] = page
    st.rerun()  # 必须 rerun，否则主内容区 pg 变量不刷新

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

    # 导航按钮列表 — button 比 radio 更可靠（无 index 竞态问题）
    NAV = [
        ("home",     "🏠", "首页"),
        ("plan",     "🧭", "投资路线"),
        ("etf50",    "📊", "ETF 50"),
        ("stocks60", "📈", "个股扫描"),
        ("deep",     "🤖", "深度分析"),
        ("quant",    "⚗️", "量化引擎"),
        ("ths",      "🔗", "同花顺"),
    ]
    pg = st.session_state.page
    for key, icon, label in NAV:
        is_active = pg == key
        # active 时用蓝色背景区分
        if is_active:
            st.markdown(
                f'<div style="background:rgba(59,130,246,0.12);border-left:2px solid #3b82f6;'
                f'border-radius:4px;padding:8px 12px;margin:1px 0;font-size:13px;'
                f'color:#fff;font-weight:600">{icon}  {label}</div>',
                unsafe_allow_html=True
            )
        else:
            if st.button(f"{icon}  {label}", key=f"nav_{key}",
                         use_container_width=True, type="secondary"):
                _nav_to(key)

    st.markdown('<hr class="n-divider">', unsafe_allow_html=True)

    # 深度分析输入（仅在 deep 页面显示）
    if st.session_state.page == "deep":
        st.markdown('<div class="n-label" style="padding-left:4px">股票代码</div>', unsafe_allow_html=True)
        stock_input = st.text_input("", placeholder="如 603501、512480", label_visibility="collapsed", max_chars=6)
        risk_profile = st.selectbox(
            "风险画像",
            ["均衡", "保守", "进取"],
            index=0,
            help="影响行动计划里的仓位上限，不改变研究事实。",
        )
        rounds = st.slider("辩论轮数", 1, 3, 1, help="轮数越多分析越深入，耗时越长")
        workflow_label = st.selectbox(
            "工作流",
            ["仅深度分析", "刷新行情 + ETF50扫描 + 深度分析", "刷新行情 + ETF/个股双扫描 + 深度分析"],
            index=0,
            help="默认只跑深度分析；需要最新行情和扫描榜单时再选择完整链路。",
        )
        analysis_mode_label = st.selectbox(
            "分析模式",
            ["自动（LLM优先/无Key规则版）", "规则版（无需API）", "LLM版"],
            index=0,
            help="自动模式会在没有 API Key 或本地模型时生成规则深度报告。",
        )
        est_extra = {"仅深度分析": 0, "刷新行情 + ETF50扫描 + 深度分析": 6, "刷新行情 + ETF/个股双扫描 + 深度分析": 12}
        rule_mode = analysis_mode_label.startswith("规则")
        estimate = (1 if rule_mode else rounds * 3 + 2) + est_extra.get(workflow_label, 0)
        st.caption(f"预计耗时 {estimate} 分钟")
        run_btn = st.button("▶  开始执行", disabled=st.session_state.running, use_container_width=True)
        st.markdown('<hr class="n-divider">', unsafe_allow_html=True)
    else:
        stock_input, risk_profile, rounds, workflow_label, analysis_mode_label, run_btn = "", "均衡", 1, "仅深度分析", "自动（LLM优先/无Key规则版）", False

    # 股票快速选择
    st.markdown('<div class="n-label" style="padding-left:4px;margin-bottom:8px">快速选股</div>', unsafe_allow_html=True)
    for sector, stocks in POOL.items():
        with st.expander(sector, expanded=False):
            for code, name in stocks:
                if st.button(f"{code}  {name}", key=f"q_{sector}_{code}", use_container_width=True):
                    st.session_state["_quick"] = code
                    _nav_to("deep")  # _nav_to 已包含 st.rerun()

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
                    st.toast("✅ 连接成功", icon="✅")
                except Exception as e:
                    st.session_state.api_ok = False
                    st.toast(f"❌ 连接失败: {str(e)[:40]}", icon="❌")
    with c2:
        if st.button("应用", key="apply_cfg", use_container_width=True):
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

    # 功能入口卡片
    c0, c1, c2, c3 = st.columns(4, gap="medium")
    with c0:
        st.markdown("""
        <div class="n-card n-card-accent-blue">
          <div style="font-size:20px;margin-bottom:12px">🧭</div>
          <div style="font-size:14px;font-weight:600;color:#fff;margin-bottom:4px">投资路线</div>
          <div class="n-sub" style="margin-bottom:12px">组合仓位 · ETF主线 · 个股卫星</div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("进入 →", key="g_plan", use_container_width=True):
            _nav_to("plan")

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
          <div class="n-sub" style="margin-bottom:12px">5 Agent 并行研究 · Battle 辩论</div>
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
#  投资路线页
# ══════════════════════════════════════════════════════
elif pg == "plan":
    st.markdown('<div style="padding:24px 0 20px"><div style="font-size:26px;font-weight:700;color:#fff;letter-spacing:-0.02em">投资路线</div><div style="font-size:13px;color:#636366;margin-top:4px">组合仓位框架 · ETF 主线 · 个股卫星 · 复核节奏</div></div>', unsafe_allow_html=True)

    pc1, pc2, pc3 = st.columns([1,1,4])
    with pc1:
        plan_profile = st.selectbox("风险画像", ["均衡", "保守", "进取"], index=0, key="plan_profile")
    profile_map = {"保守": "conservative", "均衡": "balanced", "进取": "aggressive"}
    with pc2:
        if st.button("生成路线", use_container_width=True):
            from nasdx.investment_brief import build_and_save_investment_brief
            from nasdx.recommendation_review import build_and_save_recommendation_review
            from nasdx.recommendation_tracker import build_and_save_recommendation_tracker
            build_and_save_investment_brief(risk_profile=profile_map.get(plan_profile, "balanced"))
            build_and_save_recommendation_tracker()
            build_and_save_recommendation_review()
            try: load_portfolio_latest.clear()
            except Exception: pass
            try: load_investment_brief_latest.clear()
            except Exception: pass
            try: load_recommendation_tracker_latest.clear()
            except Exception: pass
            try: load_recommendation_review_latest.clear()
            except Exception: pass
            try: load_account_review_latest.clear()
            except Exception: pass
            st.toast("投资路线和简报已生成", icon="✅")

    d = load_portfolio_latest()
    b = load_investment_brief_latest()
    tracker = load_recommendation_tracker_latest()
    review = load_recommendation_review_latest()
    account_review = load_account_review_latest()
    if b:
        ex1, ex2, _ = st.columns([1, 1, 4])
        with ex1:
            if st.button("导出复盘包", use_container_width=True):
                from nasdx.review_snapshot import build_review_snapshot
                snapshot = build_review_snapshot(risk_profile=profile_map.get(plan_profile, "balanced"))
                st.session_state["review_snapshot_path"] = snapshot["zip_path"]
                st.toast("复盘包已生成", icon="📦")
        with ex2:
            snapshot_path = st.session_state.get("review_snapshot_path")
            if snapshot_path and Path(snapshot_path).exists():
                st.download_button(
                    "下载复盘包",
                    data=Path(snapshot_path).read_bytes(),
                    file_name=Path(snapshot_path).name,
                    mime="application/zip",
                    use_container_width=True,
                )
    if not d:
        st.markdown('<div class="n-card" style="text-align:center;padding:48px;color:#48484a">暂无投资路线，点击「生成路线」或先运行一键投研工作流</div>', unsafe_allow_html=True)
    else:
        alloc = d.get("allocation", {})
        st.markdown(f'<div style="font-size:12px;color:#4a4a4a;margin:12px 0">生成时间 {d.get("generated_at","")} · 风险画像 {d.get("risk_profile_label","")} · {d.get("posture","")}</div>', unsafe_allow_html=True)

        a1,a2,a3,a4 = st.columns(4, gap="small")
        for col,(lb,v,c) in zip([a1,a2,a3,a4],[
            ("总仓位上限",alloc.get("max_total",""),"#3b82f6"),
            ("ETF预算",alloc.get("etf_budget",""),"#22c55e"),
            ("个股预算",alloc.get("stock_budget",""),"#f59e0b"),
            ("现金缓冲",alloc.get("cash_buffer",""),"#8b949e"),
        ]):
            with col:
                st.markdown(f'<div class="n-card" style="text-align:center;padding:12px"><div style="font-size:22px;font-weight:600;color:{c}">{v}</div><div style="font-size:11px;color:rgba(255,255,255,0.40)">{lb}</div></div>', unsafe_allow_html=True)

        st.markdown(f'<div class="n-card" style="font-size:13px;color:rgba(255,255,255,0.75);line-height:1.7;margin-top:12px">{alloc.get("mode","")}</div>', unsafe_allow_html=True)

        if b:
            st.markdown('<div class="n-section-title" style="margin-top:18px">最终简报</div>', unsafe_allow_html=True)
            brief_alloc = b.get("allocation", {})
            st.markdown(
                f'''
                <div class="n-card" style="font-size:13px;color:rgba(255,255,255,0.78);line-height:1.75">
                  <div style="font-size:12px;color:rgba(255,255,255,0.42);margin-bottom:6px">研究辅助 · {html.escape(str(b.get("generated_at","")))}</div>
                  <div style="font-size:16px;font-weight:700;color:#fff;margin-bottom:6px">{html.escape(str(b.get("primary_bias","")))}</div>
                  <div>{html.escape(str(b.get("exposure_action","")))}</div>
                  <div style="margin-top:8px;color:rgba(255,255,255,0.56)">总仓位 {html.escape(str(brief_alloc.get("max_total","")))} · ETF {html.escape(str(brief_alloc.get("etf_budget","")))} · 个股 {html.escape(str(brief_alloc.get("stock_budget","")))}</div>
                </div>
                ''',
                unsafe_allow_html=True,
            )

        def _rows(items):
            return [{
                "代码": x.get("code",""),
                "名称": x.get("name",""),
                "类型": x.get("asset_type",""),
                "分数": x.get("adjusted_score", x.get("score",0)),
                "信号": x.get("signal",""),
                "动作": x.get("action",""),
                "理由": x.get("reason",""),
            } for x in items]

        def _table(items):
            rows = _rows(items)
            if not rows:
                return '<div class="n-card" style="font-size:13px;color:rgba(255,255,255,0.45)">暂无候选</div>'
            head = ''.join(f'<th>{html.escape(k)}</th>' for k in rows[0].keys())
            body = ''
            for row in rows:
                body += '<tr>' + ''.join(f'<td>{html.escape(str(v))}</td>' for v in row.values()) + '</tr>'
            return f'''
            <div class="n-card plan-table" style="padding:0;overflow:auto">
              <table style="width:100%;border-collapse:collapse;font-size:12px">
                <thead><tr>{head}</tr></thead>
                <tbody>{body}</tbody>
              </table>
            </div>
            <style>
              .plan-table table th{{color:rgba(255,255,255,0.42);font-weight:600;text-align:left;padding:9px 10px;border-bottom:1px solid rgba(255,255,255,0.08);white-space:nowrap}}
              .plan-table table td{{color:rgba(255,255,255,0.75);padding:9px 10px;border-bottom:1px solid rgba(255,255,255,0.06);vertical-align:top}}
              .plan-table table tr:last-child td{{border-bottom:0}}
            </style>'''

        def _scenario_table(items):
            if not items:
                return '<div class="n-card" style="font-size:13px;color:rgba(255,255,255,0.45)">暂无情景</div>'
            rows = []
            for item in items:
                rows.append({
                    "情景": item.get("scenario",""),
                    "触发条件": item.get("trigger",""),
                    "动作": item.get("action",""),
                    "仓位规则": item.get("position_rule",""),
                })
            head = ''.join(f'<th>{html.escape(k)}</th>' for k in rows[0].keys())
            body = ''
            for row in rows:
                body += '<tr>' + ''.join(f'<td>{html.escape(str(v))}</td>' for v in row.values()) + '</tr>'
            return f'''
            <div class="n-card plan-table" style="padding:0;overflow:auto">
              <table style="width:100%;border-collapse:collapse;font-size:12px">
                <thead><tr>{head}</tr></thead>
                <tbody>{body}</tbody>
              </table>
            </div>'''

        def _brief_playbook_table(items):
            if not items:
                return '<div class="n-card" style="font-size:13px;color:rgba(255,255,255,0.45)">暂无候选剧本</div>'
            rows = []
            for item in items:
                rows.append({
                    "候选": item.get("candidate", ""),
                    "类型": item.get("type", ""),
                    "深度信号": item.get("deep_signal", ""),
                    "优先级": item.get("priority", ""),
                    "入场条件": item.get("entry_condition", ""),
                    "复核动作": item.get("review", ""),
                })
            head = ''.join(f'<th>{html.escape(k)}</th>' for k in rows[0].keys())
            body = ''
            sig_colors = {"bullish": "#22c55e", "neutral": "#f59e0b", "bearish": "#ef4444", "missing": "#8b949e"}
            for row in rows:
                cells = []
                for key, value in row.items():
                    text = html.escape(str(value))
                    if key == "深度信号":
                        color = sig_colors.get(str(value), "#8b949e")
                        text = f'<span style="color:{color};font-weight:700">{text}</span>'
                    cells.append(f'<td>{text}</td>')
                body += '<tr>' + ''.join(cells) + '</tr>'
            return f'''
            <div class="n-card plan-table" style="padding:0;overflow:auto">
              <table style="width:100%;border-collapse:collapse;font-size:12px">
                <thead><tr>{head}</tr></thead>
                <tbody>{body}</tbody>
              </table>
            </div>'''

        def _audit_table(items):
            if not items:
                return '<div class="n-card" style="font-size:13px;color:rgba(255,255,255,0.45)">暂无候选证据核查</div>'
            rows = []
            for item in items:
                rows.append({
                    "候选": item.get("candidate", ""),
                    "审计结论": item.get("audit_status", ""),
                    "深度信号": item.get("deep_signal", ""),
                    "核心证据": "；".join(str(x) for x in item.get("key_evidence", [])[:3]),
                    "待人工复核": "；".join(str(x) for x in item.get("manual_checks", [])[:3]) or "无",
                    "阻断项": "；".join(str(x) for x in item.get("blocking_flags", [])[:2]) or "无",
                })
            head = ''.join(f'<th>{html.escape(k)}</th>' for k in rows[0].keys())
            status_colors = {
                "小仓试错候选": "#22c55e",
                "观察等待": "#f59e0b",
                "先补深度报告": "#f59e0b",
                "先修数据": "#ef4444",
                "回避/降级": "#ef4444",
            }
            sig_colors = {"bullish": "#22c55e", "neutral": "#f59e0b", "bearish": "#ef4444", "missing": "#8b949e"}
            body = ''
            for row in rows:
                cells = []
                for key, value in row.items():
                    text = html.escape(str(value))
                    if key == "审计结论":
                        color = status_colors.get(str(value), "#8b949e")
                        text = f'<span style="color:{color};font-weight:700">{text}</span>'
                    if key == "深度信号":
                        color = sig_colors.get(str(value), "#8b949e")
                        text = f'<span style="color:{color};font-weight:700">{text}</span>'
                    cells.append(f'<td>{text}</td>')
                body += '<tr>' + ''.join(cells) + '</tr>'
            return f'''
            <div class="n-card plan-table" style="padding:0;overflow:auto">
              <table style="width:100%;border-collapse:collapse;font-size:12px;min-width:980px">
                <thead><tr>{head}</tr></thead>
                <tbody>{body}</tbody>
              </table>
            </div>'''

        def _execution_queue_table(items):
            if not items:
                return '<div class="n-card" style="font-size:13px;color:rgba(255,255,255,0.45)">暂无执行队列</div>'
            rows = []
            for item in items:
                rows.append({
                    "阶段": item.get("stage", ""),
                    "对象": item.get("target", ""),
                    "决策": item.get("decision", ""),
                    "动作": item.get("action", ""),
                    "条件": item.get("condition", ""),
                    "阻断": item.get("blocker", ""),
                    "命令": item.get("command", "") or "无",
                })
            head = ''.join(f'<th>{html.escape(k)}</th>' for k in rows[0].keys())
            stage_colors = {"盘前": "#60a5fa", "盘中": "#22c55e", "盘后": "#a78bfa"}
            decision_colors = {
                "可进入复核流程": "#22c55e",
                "小仓试错前复核": "#22c55e",
                "先补深度报告": "#f59e0b",
                "观察等待": "#f59e0b",
                "回避/降级": "#ef4444",
                "先刷新数据": "#ef4444",
                "先修数据": "#ef4444",
                "重新生成明日路线": "#a78bfa",
            }
            body = ''
            for row in rows:
                cells = []
                for key, value in row.items():
                    text = html.escape(str(value))
                    if key == "阶段":
                        color = stage_colors.get(str(value), "#8b949e")
                        text = f'<span style="color:{color};font-weight:700">{text}</span>'
                    if key == "决策":
                        color = decision_colors.get(str(value), "#8b949e")
                        text = f'<span style="color:{color};font-weight:700">{text}</span>'
                    if key == "命令" and str(value) != "无":
                        text = f'<code style="white-space:nowrap;color:#9cdcfe">{text}</code>'
                    cells.append(f'<td>{text}</td>')
                body += '<tr>' + ''.join(cells) + '</tr>'
            return f'''
            <div class="n-card plan-table" style="padding:0;overflow:auto">
              <table style="width:100%;border-collapse:collapse;font-size:12px;min-width:1120px">
                <thead><tr>{head}</tr></thead>
                <tbody>{body}</tbody>
              </table>
            </div>'''

        def _external_review_table(items):
            if not items:
                return '<div class="n-card" style="font-size:13px;color:rgba(255,255,255,0.45)">暂无外部复核包</div>'
            rows = []
            for item in items:
                links = []
                for link in item.get("source_links", [])[:3]:
                    label = html.escape(str(link.get("label", "")))
                    url = html.escape(str(link.get("url", "")), quote=True)
                    usage = html.escape(str(link.get("usage", "")), quote=True)
                    links.append(f'<a href="{url}" target="_blank" title="{usage}" style="color:#9cdcfe;text-decoration:none">{label}</a>')
                rows.append({
                    "候选": html.escape(str(item.get("candidate", ""))),
                    "复核闸门": html.escape(str(item.get("review_gate", ""))),
                    "通过时间": html.escape(str(item.get("must_pass_before", ""))),
                    "必查项": html.escape("；".join(str(x) for x in item.get("required_checks", [])[:3])),
                    "来源入口": "；".join(links) or "无",
                    "失败动作": html.escape(str(item.get("failure_action", ""))),
                })
            head = ''.join(f'<th>{html.escape(k)}</th>' for k in rows[0].keys())
            body = ''
            for row in rows:
                cells = []
                for key, value in row.items():
                    if key == "复核闸门":
                        value = f'<span style="color:#f59e0b;font-weight:700">{value}</span>'
                    cells.append(f'<td>{value}</td>')
                body += '<tr>' + ''.join(cells) + '</tr>'
            return f'''
            <div class="n-card plan-table" style="padding:0;overflow:auto">
              <table style="width:100%;border-collapse:collapse;font-size:12px;min-width:1120px">
                <thead><tr>{head}</tr></thead>
                <tbody>{body}</tbody>
              </table>
            </div>'''

        def _position_sizing_table(items):
            if not items:
                return '<div class="n-card" style="font-size:13px;color:rgba(255,255,255,0.45)">暂无仓位换算候选</div>'
            rows = []
            for item in items:
                rows.append({
                    "候选": item.get("candidate", ""),
                    "类型": item.get("type", ""),
                    "审计": item.get("audit_status", ""),
                    "最多新增": f'{float(item.get("max_new_amount") or 0):,.0f}',
                    "第一笔试错": f'{float(item.get("first_lot_amount") or 0):,.0f}',
                    "说明": item.get("reason", ""),
                })
            head = ''.join(f'<th>{html.escape(k)}</th>' for k in rows[0].keys())
            body = ''
            for row in rows:
                cells = []
                for key, value in row.items():
                    text = html.escape(str(value))
                    if key in ("最多新增", "第一笔试错"):
                        text = f'<span style="color:#9cdcfe;font-weight:700">{text}</span>'
                    if key == "审计":
                        color = "#22c55e" if str(value) == "小仓试错候选" else "#f59e0b" if str(value) in ("先补深度报告", "观察等待") else "#ef4444" if str(value) in ("先修数据", "回避/降级") else "#8b949e"
                        text = f'<span style="color:{color};font-weight:700">{text}</span>'
                    cells.append(f'<td>{text}</td>')
                body += '<tr>' + ''.join(cells) + '</tr>'
            return f'''
            <div class="n-card plan-table" style="padding:0;overflow:auto">
              <table style="width:100%;border-collapse:collapse;font-size:12px;min-width:1040px">
                <thead><tr>{head}</tr></thead>
                <tbody>{body}</tbody>
              </table>
            </div>'''

        def _account_review_table(items):
            if not items:
                return '<div class="n-card" style="font-size:13px;color:rgba(255,255,255,0.45)">暂无真实持仓</div>'
            rows = []
            for item in items:
                rows.append({
                    "持仓": f'{item.get("code", "")} {item.get("name", "")}'.strip(),
                    "数量": f'{float(item.get("quantity") or 0):,.0f}',
                    "成本": f'{float(item.get("avg_cost") or 0):,.3f}',
                    "最新价": "NA" if item.get("latest_price") is None else f'{float(item.get("latest_price") or 0):,.3f}',
                    "市值": "NA" if item.get("market_value") is None else f'{float(item.get("market_value") or 0):,.0f}',
                    "浮盈亏": "NA" if item.get("unrealized_pnl") is None else f'{float(item.get("unrealized_pnl") or 0):,.0f}',
                    "路线": item.get("route_audit") or item.get("route_status", ""),
                    "动作": item.get("route_action", ""),
                })
            head = ''.join(f'<th>{html.escape(k)}</th>' for k in rows[0].keys())
            route_colors = {
                "小仓试错候选": "#22c55e",
                "trial_candidate": "#22c55e",
                "先补深度报告": "#f59e0b",
                "needs_report": "#f59e0b",
                "观察等待": "#f59e0b",
                "watch": "#f59e0b",
                "回避/降级": "#ef4444",
                "avoid": "#ef4444",
                "not_in_current_route": "#8b949e",
            }
            body = ''
            for row in rows:
                cells = []
                for key, value in row.items():
                    text = html.escape(str(value))
                    if key == "浮盈亏" and text != "NA":
                        color = "#22c55e" if str(value).replace(",", "").startswith("-") is False else "#ef4444"
                        text = f'<span style="color:{color};font-weight:700">{text}</span>'
                    if key == "路线":
                        color = route_colors.get(str(value), "#8b949e")
                        text = f'<span style="color:{color};font-weight:700">{text}</span>'
                    cells.append(f'<td>{text}</td>')
                body += '<tr>' + ''.join(cells) + '</tr>'
            return f'''
            <div class="n-card plan-table" style="padding:0;overflow:auto">
              <table style="width:100%;border-collapse:collapse;font-size:12px;min-width:1060px">
                <thead><tr>{head}</tr></thead>
                <tbody>{body}</tbody>
              </table>
            </div>'''

        def _tracker_change_table(items):
            if not items:
                return '<div class="n-card" style="font-size:13px;color:rgba(255,255,255,0.45)">暂无状态变化</div>'
            rows = []
            for item in items:
                changes = "；".join(
                    f'{x.get("field","")}: {x.get("from","")} → {x.get("to","")}'
                    for x in item.get("changes", [])
                )
                rows.append({
                    "候选": item.get("candidate", ""),
                    "类型": item.get("type", ""),
                    "变化": changes,
                    "当前结论": item.get("current_status", ""),
                    "说明": item.get("reason", ""),
                })
            head = ''.join(f'<th>{html.escape(k)}</th>' for k in rows[0].keys())
            body = ''
            for row in rows:
                body += '<tr>' + ''.join(f'<td>{html.escape(str(v))}</td>' for v in row.values()) + '</tr>'
            return f'''
            <div class="n-card plan-table" style="padding:0;overflow:auto">
              <table style="width:100%;border-collapse:collapse;font-size:12px;min-width:980px">
                <thead><tr>{head}</tr></thead>
                <tbody>{body}</tbody>
              </table>
            </div>'''

        def _recommendation_review_table(items):
            if not items:
                return '<div class="n-card" style="font-size:13px;color:rgba(255,255,255,0.45)">暂无建议结果复盘</div>'
            rows = []
            label_map = {
                "signal_continues": "信号延续",
                "downgrade_review": "降级复核",
                "pending_evidence": "仍待补证据",
                "missing_current_data": "缺当前数据",
            }
            for item in items:
                rows.append({
                    "候选": item.get("candidate", ""),
                    "基准状态": item.get("baseline_audit") or item.get("baseline_status", ""),
                    "当前状态": item.get("current_audit") or item.get("current_status", ""),
                    "最新信号": item.get("current_signal", ""),
                    "最新分数": item.get("current_score", ""),
                    "涨跌幅": item.get("latest_change_pct", ""),
                    "复盘": label_map.get(item.get("review_status", ""), item.get("review_status", "")),
                    "动作": item.get("review_action", ""),
                })
            head = ''.join(f'<th>{html.escape(k)}</th>' for k in rows[0].keys())
            status_colors = {
                "信号延续": "#22c55e",
                "降级复核": "#ef4444",
                "仍待补证据": "#f59e0b",
                "缺当前数据": "#8b949e",
            }
            body = ''
            for row in rows:
                cells = []
                for key, value in row.items():
                    text = html.escape(str(value))
                    if key == "复盘":
                        color = status_colors.get(str(value), "#8b949e")
                        text = f'<span style="color:{color};font-weight:700">{text}</span>'
                    cells.append(f'<td>{text}</td>')
                body += '<tr>' + ''.join(cells) + '</tr>'
            return f'''
            <div class="n-card plan-table" style="padding:0;overflow:auto">
              <table style="width:100%;border-collapse:collapse;font-size:12px;min-width:1120px">
                <thead><tr>{head}</tr></thead>
                <tbody>{body}</tbody>
              </table>
            </div>'''

        if b:
            st.markdown('<hr class="n-divider">', unsafe_allow_html=True)
            if tracker:
                counts = tracker.get("counts", {})
                st.markdown('<div class="n-section-title">建议漂移追踪</div>', unsafe_allow_html=True)
                tr1, tr2, tr3, tr4 = st.columns(4, gap="small")
                for col, (lb, val, color) in zip([tr1, tr2, tr3, tr4], [
                    ("新增候选", counts.get("added", 0), "#22c55e"),
                    ("移除候选", counts.get("removed", 0), "#ef4444"),
                    ("状态变化", counts.get("changed", 0), "#f59e0b"),
                    ("稳定试错", len(tracker.get("stable_trial_candidates", [])), "#60a5fa"),
                ]):
                    with col:
                        st.markdown(f'<div class="n-card" style="text-align:center;padding:12px"><div style="font-size:22px;font-weight:700;color:{color}">{val}</div><div style="font-size:11px;color:rgba(255,255,255,0.40)">{lb}</div></div>', unsafe_allow_html=True)
                gate_change = tracker.get("action_gate_change", {})
                posture_change = tracker.get("posture_change", {})
                st.markdown(
                    f'''
                    <div class="n-card" style="font-size:13px;color:rgba(255,255,255,0.74);line-height:1.65;margin-top:8px">
                      <div>行动闸门：{html.escape(str(gate_change.get("from","暂无")))} → {html.escape(str(gate_change.get("to","暂无")))}</div>
                      <div>市场姿态：{html.escape(str(posture_change.get("from","暂无")))} → {html.escape(str(posture_change.get("to","暂无")))}</div>
                      <div style="color:rgba(255,255,255,0.48);font-size:12px">对比简报：{html.escape(str(tracker.get("prior_generated_at") or "暂无"))}</div>
                    </div>
                    ''',
                    unsafe_allow_html=True,
                )
                focus = tracker.get("review_focus", [])
                if focus:
                    st.markdown(
                        "".join(
                            f'<div style="font-size:12px;color:#f59e0b;margin:6px 0">{html.escape(str(x))}</div>'
                            for x in focus[:4]
                        ),
                        unsafe_allow_html=True,
                    )
                if tracker.get("changed_candidates"):
                    st.markdown(_tracker_change_table(tracker.get("changed_candidates", [])), unsafe_allow_html=True)

            if review:
                review_counts = review.get("counts", {})
                st.markdown('<div class="n-section-title" style="margin-top:14px">建议结果复盘</div>', unsafe_allow_html=True)
                rv1, rv2, rv3, rv4 = st.columns(4, gap="small")
                for col, (lb, val, color) in zip([rv1, rv2, rv3, rv4], [
                    ("信号延续", review_counts.get("signal_continues", 0), "#22c55e"),
                    ("降级复核", review_counts.get("downgrade_review", 0), "#ef4444"),
                    ("仍待补证据", review_counts.get("pending_evidence", 0), "#f59e0b"),
                    ("缺当前数据", review_counts.get("missing_current_data", 0), "#8b949e"),
                ]):
                    with col:
                        st.markdown(f'<div class="n-card" style="text-align:center;padding:12px"><div style="font-size:22px;font-weight:700;color:{color}">{val}</div><div style="font-size:11px;color:rgba(255,255,255,0.40)">{lb}</div></div>', unsafe_allow_html=True)
                st.markdown(
                    f'''
                    <div class="n-card" style="font-size:13px;color:rgba(255,255,255,0.74);line-height:1.65;margin-top:8px">
                      <div>{html.escape(str(review.get("summary", "")))}</div>
                      <div style="color:rgba(255,255,255,0.48);font-size:12px">复盘基准：{html.escape(str(review.get("baseline_generated_at") or "暂无"))} · 行情日期：{html.escape(str(review.get("market_data_date") or "未知"))}</div>
                    </div>
                    ''',
                    unsafe_allow_html=True,
                )
                st.markdown(_recommendation_review_table(review.get("review_rows", [])), unsafe_allow_html=True)
                st.markdown(
                    "".join(
                        f'<div style="font-size:12px;color:#9cdcfe;margin:6px 0">{html.escape(str(x))}</div>'
                        for x in review.get("next_review_actions", [])[:4]
                    ),
                    unsafe_allow_html=True,
                )

            st.markdown('<hr class="n-divider">', unsafe_allow_html=True)
            st.markdown('<div class="n-section-title">资金仓位换算</div>', unsafe_allow_html=True)
            sz1, sz2, sz3, sz4 = st.columns(4, gap="small")
            with sz1:
                total_capital = st.number_input(
                    "账户总资金",
                    min_value=0.0,
                    value=0.0,
                    step=10000.0,
                    key="sizing_total_capital",
                    help="仅用于本页临时计算，不写入文件。",
                )
            with sz2:
                current_etf = st.number_input(
                    "已有ETF/基金",
                    min_value=0.0,
                    value=0.0,
                    step=1000.0,
                    key="sizing_current_etf",
                )
            with sz3:
                current_stock = st.number_input(
                    "已有个股",
                    min_value=0.0,
                    value=0.0,
                    step=1000.0,
                    key="sizing_current_stock",
                )
            with sz4:
                current_other = st.number_input(
                    "其他占用",
                    min_value=0.0,
                    value=0.0,
                    step=1000.0,
                    key="sizing_current_other",
                )

            if total_capital > 0:
                try:
                    from nasdx.position_sizing import build_position_sizing
                    sizing = build_position_sizing(
                        b,
                        total_capital=total_capital,
                        current_etf_exposure=current_etf,
                        current_stock_exposure=current_stock,
                        current_other_exposure=current_other,
                    )
                    exposure = sizing.get("exposure", {})
                    si1, si2, si3, si4 = st.columns(4, gap="small")
                    for col, (lb, key, color) in zip([si1, si2, si3, si4], [
                        ("总仓位金额上限", "max_total_amount", "#3b82f6"),
                        ("剩余可新增", "remaining_total_capacity", "#22c55e"),
                        ("ETF剩余额度", "remaining_etf_budget", "#60a5fa"),
                        ("个股剩余额度", "remaining_stock_budget", "#f59e0b"),
                    ]):
                        with col:
                            value = f'{float(exposure.get(key) or 0):,.0f}'
                            st.markdown(f'<div class="n-card" style="text-align:center;padding:12px"><div style="font-size:20px;font-weight:700;color:{color}">{value}</div><div style="font-size:11px;color:rgba(255,255,255,0.40)">{lb}</div></div>', unsafe_allow_html=True)
                    st.markdown(_position_sizing_table(sizing.get("candidate_sizing", [])), unsafe_allow_html=True)
                    st.markdown(
                        "".join(
                            f'<div style="font-size:12px;color:#f59e0b;margin:6px 0">{html.escape(str(x))}</div>'
                            for x in sizing.get("warnings", [])[:3]
                        ),
                        unsafe_allow_html=True,
                    )
                except Exception as exc:
                    st.error(f"仓位换算失败：{exc}")
            else:
                st.markdown('<div class="n-card" style="font-size:13px;color:rgba(255,255,255,0.56)">输入账户总资金后，可临时换算总仓位、ETF/个股预算和候选第一笔试错金额；不会写入任何文件。</div>', unsafe_allow_html=True)

            st.markdown('<hr class="n-divider">', unsafe_allow_html=True)
            st.markdown('<div class="n-section-title">真实账户复盘</div>', unsafe_allow_html=True)
            ar1, ar2 = st.columns([3, 1], gap="small")
            with ar1:
                ledger_upload = st.file_uploader("成交流水 CSV", type=["csv"], key="account_review_csv")
            with ar2:
                review_capital = st.number_input(
                    "复盘总资金",
                    min_value=0.0,
                    value=0.0,
                    step=10000.0,
                    key="account_review_capital",
                )
            live_account_review = None
            if ledger_upload is not None:
                try:
                    from nasdx.account_review import build_account_review_from_text
                    raw = ledger_upload.getvalue()
                    try:
                        ledger_text = raw.decode("utf-8-sig")
                    except UnicodeDecodeError:
                        ledger_text = raw.decode("gb18030", errors="replace")
                    live_account_review = build_account_review_from_text(
                        ledger_text,
                        source_name=ledger_upload.name,
                        total_capital=review_capital or None,
                    )
                except Exception as exc:
                    st.error(f"账户复盘失败：{exc}")
            shown_account_review = live_account_review or account_review
            if shown_account_review and shown_account_review.get("review_status") == "reviewed":
                summary = shown_account_review.get("summary", {})
                ac1, ac2, ac3, ac4 = st.columns(4, gap="small")
                for col, (lb, key, color) in zip([ac1, ac2, ac3, ac4], [
                    ("持仓市值", "known_market_value", "#60a5fa"),
                    ("已实现盈亏", "realized_pnl", "#22c55e"),
                    ("浮动盈亏", "unrealized_pnl", "#f59e0b"),
                    ("仓位占比", "exposure_pct", "#a78bfa"),
                ]):
                    with col:
                        raw_value = summary.get(key)
                        if key == "exposure_pct":
                            value = "NA" if raw_value is None else f'{float(raw_value):.2f}%'
                        else:
                            value = "NA" if raw_value is None else f'{float(raw_value):,.0f}'
                        st.markdown(f'<div class="n-card" style="text-align:center;padding:12px"><div style="font-size:20px;font-weight:700;color:{color}">{html.escape(value)}</div><div style="font-size:11px;color:rgba(255,255,255,0.40)">{lb}</div></div>', unsafe_allow_html=True)
                st.markdown(_account_review_table(shown_account_review.get("holdings", [])), unsafe_allow_html=True)
                st.markdown(
                    "".join(
                        f'<div style="font-size:12px;color:#9cdcfe;margin:6px 0">{html.escape(str(x))}</div>'
                        for x in shown_account_review.get("next_actions", [])[:4]
                    ),
                    unsafe_allow_html=True,
                )
            else:
                msg = (shown_account_review or {}).get("message", "缺少成交流水 CSV，无法计算真实账户收益。")
                st.markdown(
                    f'''
                    <div class="n-card" style="font-size:13px;color:rgba(255,255,255,0.62);line-height:1.65">
                      <div style="font-weight:700;color:#f59e0b;margin-bottom:4px">缺账户流水</div>
                      <div>{html.escape(str(msg))}</div>
                      <div style="color:rgba(255,255,255,0.42);font-size:12px;margin-top:6px">必要列：日期/代码/方向/数量/成交价/手续费/印花税</div>
                    </div>
                    ''',
                    unsafe_allow_html=True,
                )

            st.markdown('<hr class="n-divider">', unsafe_allow_html=True)
            st.markdown('<div class="n-section-title">候选执行剧本</div>', unsafe_allow_html=True)
            st.markdown(_brief_playbook_table(b.get("candidate_playbook", [])), unsafe_allow_html=True)
            st.markdown('<div class="n-section-title" style="margin-top:14px">候选证据核查</div>', unsafe_allow_html=True)
            st.markdown(_audit_table(b.get("candidate_audits", [])), unsafe_allow_html=True)
            st.markdown('<div class="n-section-title" style="margin-top:14px">执行队列</div>', unsafe_allow_html=True)
            st.markdown(_execution_queue_table(b.get("execution_queue", [])), unsafe_allow_html=True)
            st.markdown('<div class="n-section-title" style="margin-top:14px">外部复核包</div>', unsafe_allow_html=True)
            st.markdown(_external_review_table(b.get("external_review_pack", [])), unsafe_allow_html=True)
            bx1, bx2 = st.columns(2, gap="medium")
            with bx1:
                st.markdown('<div class="n-section-title">最终简报风险控制</div>', unsafe_allow_html=True)
                st.markdown(
                    "".join(
                        f'<div class="n-card" style="font-size:13px;margin-bottom:8px;color:rgba(255,255,255,0.75);line-height:1.55">{html.escape(str(x))}</div>'
                        for x in b.get("risk_controls", [])
                    ),
                    unsafe_allow_html=True,
                )
            with bx2:
                st.markdown('<div class="n-section-title">最终简报数据证据</div>', unsafe_allow_html=True)
                st.markdown(
                    "".join(
                        f'<div class="n-card" style="font-size:12px;margin-bottom:8px;color:rgba(255,255,255,0.66);line-height:1.55">{html.escape(str(x))}</div>'
                        for x in b.get("data_evidence", [])
                    ),
                    unsafe_allow_html=True,
                )

        st.markdown('<hr class="n-divider">', unsafe_allow_html=True)
        left, right = st.columns(2, gap="medium")
        with left:
            st.markdown('<div class="n-section-title">ETF 主线候选</div>', unsafe_allow_html=True)
            st.markdown(_table(d.get("core_candidates", [])), unsafe_allow_html=True)
        with right:
            st.markdown('<div class="n-section-title">个股卫星候选</div>', unsafe_allow_html=True)
            st.markdown(_table(d.get("satellite_candidates", [])), unsafe_allow_html=True)

        left2, right2 = st.columns(2, gap="medium")
        with left2:
            st.markdown('<div class="n-section-title">观察名单</div>', unsafe_allow_html=True)
            st.markdown(_table(d.get("watchlist", [])[:8]), unsafe_allow_html=True)
        with right2:
            st.markdown('<div class="n-section-title">回避/减仓池</div>', unsafe_allow_html=True)
            st.markdown(_table(d.get("trim_or_avoid", [])[:8]), unsafe_allow_html=True)

        st.markdown('<hr class="n-divider">', unsafe_allow_html=True)
        n1, n2 = st.columns(2, gap="medium")
        with n1:
            st.markdown('<div class="n-section-title">下一步</div>', unsafe_allow_html=True)
            st.markdown("".join(f'<div class="n-card" style="font-size:13px;margin-bottom:8px;color:rgba(255,255,255,0.75)">{x}</div>' for x in d.get("next_actions", [])), unsafe_allow_html=True)
        with n2:
            st.markdown('<div class="n-section-title">数据状态</div>', unsafe_allow_html=True)
            q = d.get("data_quality", {})
            for label, item in q.items():
                color = {"ok":"#22c55e","warning":"#f59e0b","danger":"#ef4444"}.get(item.get("severity"), "#f59e0b")
                st.markdown(f'<div style="background:{color}10;border:1px solid {color}40;border-radius:6px;padding:8px 10px;margin-bottom:8px;font-size:12px;color:{color}">{label}: {item.get("message","未评估")}</div>', unsafe_allow_html=True)

        st.markdown('<hr class="n-divider">', unsafe_allow_html=True)
        st.markdown('<div class="n-section-title">未来情景推演</div>', unsafe_allow_html=True)
        st.markdown(_scenario_table(d.get("future_scenarios", [])), unsafe_allow_html=True)

        r1, r2 = st.columns(2, gap="medium")
        with r1:
            st.markdown('<div class="n-section-title">执行规则</div>', unsafe_allow_html=True)
            st.markdown("".join(f'<div class="n-card" style="font-size:13px;margin-bottom:8px;color:rgba(255,255,255,0.75)">{html.escape(str(x))}</div>' for x in d.get("decision_rules", [])), unsafe_allow_html=True)
        with r2:
            st.markdown('<div class="n-section-title">监控清单</div>', unsafe_allow_html=True)
            st.markdown("".join(f'<div class="n-card" style="font-size:13px;margin-bottom:8px;color:rgba(255,255,255,0.75)">{html.escape(str(x))}</div>' for x in d.get("monitoring_checklist", [])[:6]), unsafe_allow_html=True)

# ══════════════════════════════════════════════════════
#  ETF50 页
# ══════════════════════════════════════════════════════
elif pg == "etf50":
    st.markdown('<div style="padding:24px 0 20px"><div style="font-size:26px;font-weight:700;color:#fff;letter-spacing:-0.02em">ETF 50 扫描</div><div style="font-size:13px;color:#636366;margin-top:4px">50只主流ETF技术面评分 · 实时溢价率</div></div>', unsafe_allow_html=True)

    scan_running = st.session_state.get("etf50_scan_running", False)
    c_btn, c_status, _ = st.columns([1, 2, 3])
    with c_btn:
        if st.button("↻  立即扫描", use_container_width=True,
                     disabled=scan_running, key="etf50_scan_btn"):
            task_id = _new_task_id("etf50_scan")
            def _run_scan():
                subprocess.run([sys.executable, str(ROOT/"scan_etf50.py")],
                               capture_output=True)
                try: load_etf50.clear()
                except Exception: pass
            _t = threading.Thread(target=_run_scan, daemon=True)
            _t.start()
            _register_task(task_id, _t)
            st.session_state["etf50_scan_running"] = True
            st.session_state["etf50_scan_task_id"] = task_id
            st.session_state["etf50_scan_start"] = time.time()
            st.rerun()

    with c_status:
        if scan_running:
            _elapsed  = int(time.time() - st.session_state.get("etf50_scan_start", time.time()))
            _done     = not _task_alive(st.session_state.get("etf50_scan_task_id"))
            if _done:
                st.session_state["etf50_scan_running"] = False
                st.session_state["etf50_scan_task_id"] = None
                try: load_etf50.clear()
                except Exception: pass
                st.rerun()
            else:
                _estr = f"{_elapsed//60}分{_elapsed%60}秒" if _elapsed >= 60 else f"{_elapsed}秒"
                st.markdown(
                    f'<div style="padding-top:8px;font-size:12px;color:#f59e0b">'
                    f'⏳ 扫描中... 已用时 {_estr}</div>',
                    unsafe_allow_html=True,
                )
                # JS 自动刷新
                import streamlit.components.v1 as _cv1
                _cv1.html('<script>setTimeout(()=>window.parent.location.reload(),3000);</script>', height=0)

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
                try: load_stocks60.clear()
                except Exception: pass
            # spinner 结束后 Streamlit 自动重算依赖，不需要 st.rerun()

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
    st.markdown('<div style="padding:24px 0 20px"><div style="font-size:26px;font-weight:700;color:#fff;letter-spacing:-0.02em">多智能体深度分析</div><div style="font-size:13px;color:#636366;margin-top:4px">LLM 多智能体 / 无 API 规则深度报告 · 同一套行动计划</div></div>', unsafe_allow_html=True)

    # 触发分析
    if run_btn and stock_input and not st.session_state.running:
        code = stock_input.strip().zfill(6)
        task_id = _new_task_id("analysis")
        log_path = ROOT / f"nasdx_log_{code}_{task_id}.txt"
        log_path.write_text("", encoding="utf-8")
        profile_map = {"保守": "conservative", "均衡": "balanced", "进取": "aggressive"}
        profile_key = profile_map.get(risk_profile, "balanced")
        workflow_map = {
            "仅深度分析": "analysis-only",
            "刷新行情 + ETF50扫描 + 深度分析": "quick",
            "刷新行情 + ETF/个股双扫描 + 深度分析": "full",
        }
        workflow_key = workflow_map.get(workflow_label, "analysis-only")
        analysis_mode_map = {
            "自动（LLM优先/无Key规则版）": "auto",
            "规则版（无需API）": "rules",
            "LLM版": "llm",
        }
        analysis_mode_key = analysis_mode_map.get(analysis_mode_label, "auto")
        env = _build_llm_env(st.session_state.api_key, st.session_state.api_base, st.session_state.api_model)
        t = threading.Thread(target=run_analysis_bg, args=(code, rounds, profile_key, workflow_key, analysis_mode_key, log_path, env), daemon=True)
        t.start()
        _register_task(task_id, t, log_path)
        st.session_state.update({
            "running": True,
            "current_code": code,
            "log_path": str(log_path),
            "task_id": task_id,
            "done": False,
            "risk_profile": profile_key,
            "workflow_mode": workflow_key,
            "workflow_label": workflow_label,
            "analysis_mode": analysis_mode_key,
        })
        st.rerun()

    # 运行中
    if st.session_state.running:
        code = st.session_state.current_code
        workflow_display = st.session_state.get("workflow_label", "仅深度分析")
        log_path = Path(st.session_state.log_path)
        log_text = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        lines = [l for l in log_text.splitlines() if l.strip() and "[LLM]" not in l]
        STEPS = ["刷新行情","ETF50","60只个股","技术面","资金流","风险","板块","瓶颈","辩论","综合","完成"]
        done_n = sum(1 for s in STEPS if any(s in l for l in lines))
        pct = min(done_n/len(STEPS), 0.95)

        st.markdown(f"""
        <div class="n-card n-card-accent-blue" style="margin-bottom:20px">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
            <div>
              <div class="n-label">分析中</div>
              <div style="font-size:20px;font-weight:600;color:#fff">{code}</div>
              <div style="font-size:12px;color:rgba(255,255,255,0.40);margin-top:4px">{workflow_display}</div>
            </div>
            <div style="font-size:13px;color:rgba(255,255,255,0.40)">{st.session_state.api_model}</div>
          </div>
        </div>
        """, unsafe_allow_html=True)
        st.progress(pct)

        with st.expander("实时日志", expanded=True):
            st.code("\n".join(lines[-12:]) if lines else "正在启动...", language=None)

        report = load_report(code)
        thread_alive = _task_alive(st.session_state.get("task_id"))
        if "✅ 分析完成" in log_text or (report and not thread_alive):
            st.session_state.update({"running":False,"done":True,"task_id":None}); st.rerun()
        else:
            time.sleep(3); st.rerun()

    # 展示报告
    def show_report(data):
        code=data.get("stock_code",""); name=data.get("stock_name","")
        sig=data.get("final_signal","neutral"); bpct=data.get("bullish_pct",50)
        summary=clean(data.get("summary","")); research=data.get("research_results",{})
        advice=clean(data.get("operation_advice","")); decision=data.get("decision_plan",{})
        dq=data.get("data_quality") or decision.get("data_quality", {})
        votes=data.get("votes",[]); transcript=data.get("battle_transcript",[]); date_s=data.get("date","")
        mode_s = (dq or {}).get("analysis_mode_label") or ("规则深度报告" if data.get("analysis_mode") == "rules" else "LLM多智能体")
        color=sig_color(sig)

        # Hero bar
        st.markdown(f"""
        <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:20px;flex-wrap:wrap;gap:12px">
          <div>
            <div style="font-size:11px;color:rgba(255,255,255,0.40);margin-bottom:4px">{code} · {date_s} · {mode_s}</div>
            <div style="font-size:28px;font-weight:600;color:#fff;letter-spacing:-0.02em">{name}</div>
          </div>
          <div style="text-align:right">
            <div class="{sig_cls(sig)}" style="font-size:14px;padding:6px 12px;margin-bottom:6px">{sig_label(sig)}</div>
            <div style="font-size:11px;color:rgba(255,255,255,0.40)">看多占比 {bpct:.1f}%</div>
          </div>
        </div>
        """, unsafe_allow_html=True)
        if dq:
            dq_color = {"ok":"#22c55e","warning":"#f59e0b","danger":"#ef4444"}.get(dq.get("severity"), "#f59e0b")
            st.markdown(
                f'<div style="background:{dq_color}10;border:1px solid {dq_color}40;'
                f'border-radius:6px;padding:9px 12px;margin-bottom:14px;'
                f'font-size:12px;color:{dq_color}">{dq.get("message","数据状态未评估")}</div>',
                unsafe_allow_html=True,
            )

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

        # 多维度卡片
        dim_meta = {
            "technical": ("📈","技术面","MA · MACD · RSI · 布林"),
            "fund_flow":  ("💰","资金流","主力 · 超大单 · 大单"),
            "risk":       ("🛡️","风险","超买 · 背离 · 波动"),
            "sector":     ("🏭","板块","轮动 · 相对强弱"),
            "chokepoint": ("🧭","瓶颈","需求 · 卡点 · 贝叶斯"),
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
            st.markdown('<div class="n-section-title" style="margin-top:14px">行动计划</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="n-card n-card-accent-green" style="font-size:13px;line-height:1.8;color:rgba(255,255,255,0.75);white-space:pre-line">{advice if advice else "暂无"}</div>', unsafe_allow_html=True)
        with col_r:
            if decision:
                st.markdown('<div class="n-section-title">策略摘要</div>', unsafe_allow_html=True)
                st.markdown(
                    f'<div class="n-card" style="margin-bottom:12px">'
                    f'<div style="font-size:11px;color:rgba(255,255,255,0.40);margin-bottom:6px">方向 / 动作</div>'
                    f'<div style="font-size:18px;font-weight:700;color:{color};margin-bottom:8px">{decision.get("direction","")} · {decision.get("action","")}</div>'
                    f'<div style="display:flex;gap:8px;flex-wrap:wrap">'
                    f'<span class="n-pill">仓位 {decision.get("position_band","")}</span>'
                    f'<span class="n-pill">周期 {decision.get("horizon","")}</span>'
                    f'<span class="n-pill">置信 {decision.get("confidence",0.5):.0%}</span>'
                    f'<span class="n-pill">{decision.get("risk_profile_label","均衡")}</span>'
                    f'</div></div>',
                    unsafe_allow_html=True,
                )
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
              <div style="font-size:13px;color:#48484a">支持 A股个股 · ETF · LOF<br>5个AI专家并行分析，Battle辩论，DeepSeek V4 Pro 推理</div>
            </div>
            """, unsafe_allow_html=True)


# ══════════════════════════════════════════════════════
#  同花顺接入页（路由到独立模块）
# ══════════════════════════════════════════════════════
elif pg == "ths":
    try:
        import ths_page as _ths_mod
        _ths_mod.render_ths_page(st, ROOT)
    except Exception as _e:
        import traceback
        st.error(f"同花顺页面加载失败：{_e}")
        st.code(traceback.format_exc())


# ══════════════════════════════════════════════════════
#  量化引擎页（路由到独立模块）
# ══════════════════════════════════════════════════════
elif pg == "quant":
    try:
        import quant_page as _qpm  # Python sys.modules 缓存，不重复执行
        _qpm.render_quant_page(st)
    except Exception as _e:
        import traceback
        st.error(f"量化引擎加载失败：{_e}")
        st.code(traceback.format_exc())
