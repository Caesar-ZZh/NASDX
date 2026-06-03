"""
NASDX Web UI — Streamlit 入口
运行: streamlit run app.py
"""
import sys, os, json, subprocess, threading, time
from pathlib import Path
from datetime import datetime

import streamlit as st

# ── 路径 ────────────────────────────────────────────
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# ── 页面配置 ─────────────────────────────────────────
st.set_page_config(
    page_title="NASDX · A股多智能体分析",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── 全局 CSS ─────────────────────────────────────────
st.markdown("""
<style>
/* 整体暗色主题 */
[data-testid="stAppViewContainer"] {
    background: #0d1117;
    color: #c9d1d9;
}
[data-testid="stSidebar"] {
    background: #161b22;
    border-right: 1px solid #30363d;
}
[data-testid="stHeader"] { background: transparent; }

/* 隐藏默认菜单 */
#MainMenu, footer { visibility: hidden; }

/* 卡片样式 */
.metric-card {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 10px;
    padding: 18px 20px;
    margin-bottom: 12px;
    position: relative;
    overflow: hidden;
}
.metric-card::before {
    content: "";
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 3px;
}
.metric-card.bull::before { background: #00C853; }
.metric-card.bear::before { background: #FF1744; }
.metric-card.neutral::before { background: #FFD600; }

.card-title { font-size: 12px; color: #8b949e; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 4px; }
.card-value { font-size: 22px; font-weight: bold; color: #fff; }
.card-sub   { font-size: 12px; color: #8b949e; margin-top: 4px; }

/* 信号徽章 */
.badge-bull { background:#00C85322; color:#00C853; border:1.5px solid #00C853; border-radius:16px; padding:4px 14px; font-weight:bold; display:inline-block; }
.badge-bear { background:#FF174422; color:#FF1744; border:1.5px solid #FF1744; border-radius:16px; padding:4px 14px; font-weight:bold; display:inline-block; }
.badge-neutral { background:#FFD60022; color:#FFD600; border:1.5px solid #FFD600; border-radius:16px; padding:4px 14px; font-weight:bold; display:inline-block; }

/* 辩论气泡 */
.bubble-bull {
    background: #00C85310;
    border-left: 3px solid #00C853;
    border-radius: 6px;
    padding: 10px 14px;
    margin: 6px 0;
    font-size: 13px;
    color: #c9d1d9;
}
.bubble-bear {
    background: #FF174410;
    border-left: 3px solid #FF1744;
    border-radius: 6px;
    padding: 10px 14px;
    margin: 6px 0;
    font-size: 13px;
    color: #c9d1d9;
}
.bubble-judge {
    background: #bc8cff10;
    border-left: 3px solid #bc8cff;
    border-radius: 6px;
    padding: 10px 14px;
    margin: 6px 0;
    font-size: 13px;
    color: #c9d1d9;
}

/* 表格 */
.vote-row {
    display: flex;
    gap: 12px;
    align-items: center;
    padding: 8px 12px;
    border-bottom: 1px solid #30363d;
    font-size: 13px;
}

/* 按钮 */
.stButton > button {
    background: linear-gradient(135deg, #58a6ff, #bc8cff) !important;
    color: #fff !important;
    border: none !important;
    border-radius: 8px !important;
    font-weight: bold !important;
    font-size: 15px !important;
    padding: 10px 24px !important;
    width: 100% !important;
    transition: opacity 0.2s !important;
}
.stButton > button:hover { opacity: 0.85 !important; }

/* 进度文本 */
.progress-line {
    font-family: monospace;
    font-size: 13px;
    color: #58a6ff;
    padding: 3px 0;
}

/* Section header */
.section-header {
    font-size: 14px;
    font-weight: bold;
    color: #58a6ff;
    text-transform: uppercase;
    letter-spacing: 1px;
    margin: 20px 0 10px 0;
    padding-bottom: 6px;
    border-bottom: 1px solid #30363d;
}

/* Key point pills */
.kp { display:inline-block; background:#21262d; border:1px solid #30363d; border-radius:12px; padding:3px 10px; font-size:12px; color:#c9d1d9; margin:2px 2px; }
</style>
""", unsafe_allow_html=True)


# ── 股票池（供快速选择）────────────────────────────────
STOCK_POOL = {
    "半导体 · 股票": [
        ("688981","中芯国际"),("603501","韦尔股份"),("603986","兆易创新"),
        ("688347","华虹半导体"),("300223","北京君正"),
    ],
    "半导体 · ETF": [
        ("512480","芯片ETF"),("159995","半导体芯片ETF"),("512760","芯片ETF国泰"),
        ("588200","科创芯片ETF嘉实"),("589170","科创芯片设计ETF"),("588810","科创芯片ETF富国"),
        ("589130","科创芯片ETF易方达"),("513310","中韩半导体ETF"),("501225","全球芯片LOF"),
    ],
    "半导体设备 · 股票": [
        ("002371","北方华创"),("688012","中微公司"),("688120","华海清科"),
        ("688037","芯源微"),("688082","盛美上海"),
    ],
    "半导体设备 · ETF": [
        ("588000","科创50ETF华夏"),("588080","科创50ETF易方达"),
        ("561980","半导体设备ETF招商"),("159516","半导体设备ETF国泰"),("159327","半导体设备ETF万家"),
    ],
    "通信 · 股票": [
        ("000063","中兴通讯"),("300308","中际旭创"),("600498","烽火通信"),
        ("000988","华工科技"),("300502","新易盛"),
    ],
    "通信 · ETF": [
        ("515050","5G通信ETF"),("159869","通信ETF"),("159507","通信ETF广发"),
    ],
    "电力 · 股票": [
        ("600900","长江电力"),("600406","国电南瑞"),("600905","三峡能源"),
        ("601985","中国核电"),("600089","特变电工"),
    ],
    "电力 · ETF": [
        ("159611","电力ETF广发"),("562560","电力ETF富国"),("561380","电网设备ETF国泰"),
    ],
    "AI算力 · 股票": [
        ("688256","寒武纪"),("688041","海光信息"),("002230","科大讯飞"),
        ("603019","中科曙光"),("002415","海康威视"),
    ],
    "AI算力 · ETF": [
        ("515070","人工智能ETF"),("159819","科技龙头ETF"),
    ],
    "军工 · 股票": [
        ("000768","中航西飞"),("600893","航发动力"),("002179","中航光电"),
        ("002049","紫光国微"),("000733","振华科技"),
    ],
    "军工 · ETF": [
        ("512660","军工ETF"),("512680","军工ETF招商"),
    ],
    "消费科技ETF": [
        ("159779","消费电子ETF招商"),("561100","消费电子ETF富国"),
        ("159272","机器人ETF富国"),("159530","机器人ETF易方达"),
    ],
    "海外ETF": [
        ("513300","纳斯达克ETF华夏"),("513110","纳指ETF华泰柏瑞"),
        ("159941","纳指ETF广发"),("159509","纳指科技ETF景顺"),
        ("513390","纳指100ETF博时"),("501312","海外科技LOF"),
        ("159687","亚太精选ETF南方"),
    ],
    "红利宽基ETF": [
        ("159547","红利低波ETF华夏"),("159842","券商ETF银华"),
        ("161128","标普信息科技LOF"),("515220","煤炭ETF国泰"),
    ],
}


# ── 工具函数 ─────────────────────────────────────────
def load_report(stock_code: str) -> dict | None:
    """加载最新的 JSON 报告"""
    reports = sorted(ROOT.glob(f"reports/report_{stock_code}_*.json"))
    if not reports:
        return None
    with open(reports[-1], "r", encoding="utf-8") as f:
        return json.load(f)


def signal_badge(signal: str) -> str:
    label = {"bullish": "📈 看多", "bearish": "📉 看空", "neutral": "➡️ 中性"}.get(signal, signal)
    cls   = {"bullish": "bull", "bearish": "bear", "neutral": "neutral"}.get(signal, "neutral")
    return f'<span class="badge-{cls}">{label}</span>'


def run_analysis_bg(stock_code: str, rounds: int, log_path: Path):
    """子进程跑分析，日志写文件"""
    env = os.environ.copy()
    cmd = [
        sys.executable, "-u", str(ROOT / "run_analysis.py"),
        stock_code, "--rounds", str(rounds),
    ]
    with open(log_path, "w", encoding="utf-8", buffering=1) as f:
        subprocess.run(cmd, stdout=f, stderr=f, env=env)


# ── Session State 初始化 ─────────────────────────────
for k, v in {
    "running": False,
    "current_code": "",
    "log_path": None,
    "thread": None,
    "done": False,
}.items():
    if k not in st.session_state:
        st.session_state[k] = v


# ══════════════════════════════════════════
#  侧边栏
# ══════════════════════════════════════════
with st.sidebar:
    st.markdown("""
    <div style="text-align:center; padding: 8px 0 20px 0;">
        <div style="font-size:28px;">📊</div>
        <div style="font-size:18px; font-weight:bold; color:#fff;">NASDX</div>
        <div style="font-size:11px; color:#8b949e;">A股多智能体分析系统</div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("### 🎯 输入股票代码")
    stock_input = st.text_input(
        label="股票代码",
        placeholder="如 603501",
        label_visibility="collapsed",
        max_chars=6,
    )

    st.markdown("### ⚙️ 分析参数")
    rounds = st.slider("辩论轮数", min_value=1, max_value=3, value=1,
                       help="轮数越多分析越深入，时间也越长")
    st.caption(f"预计耗时约 {rounds * 3 + 2} 分钟")

    run_btn = st.button("🚀 开始分析", disabled=st.session_state.running)

    st.markdown("---")
    st.markdown("### 📋 快速选股")
    for sector, stocks in STOCK_POOL.items():
        with st.expander(sector, expanded=False):
            for code, name in stocks:
                if st.button(f"{code} {name}", key=f"quick_{code}"):
                    st.session_state["quick_select"] = code
                    st.rerun()

    st.markdown("---")
    st.markdown('<div style="font-size:11px;color:#8b949e;text-align:center;">Powered by claude-opus-4-6-thinking<br>数据来源 AkShare</div>', unsafe_allow_html=True)


# ── 处理快速选股 ──────────────────────────────────────
if "quick_select" in st.session_state:
    stock_input = st.session_state.pop("quick_select")


# ══════════════════════════════════════════
#  主区域 — Header
# ══════════════════════════════════════════
st.markdown("""
<div style="padding: 12px 0 24px 0;">
    <h1 style="color:#fff; font-size:26px; margin:0;">
        📊 NASDX <span style="color:#58a6ff;">多智能体</span>股票分析
    </h1>
    <p style="color:#8b949e; margin:4px 0 0 0; font-size:14px;">
        4个专家AI并行研究 · 多空Battle辩论 · Opus Thinking深度推理
    </p>
</div>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════
#  触发分析
# ══════════════════════════════════════════
if run_btn and stock_input and not st.session_state.running:
    code = stock_input.strip().zfill(6)
    log_path = ROOT / f"nasdx_log_{code}.txt"
    log_path.write_text("", encoding="utf-8")

    t = threading.Thread(
        target=run_analysis_bg,
        args=(code, rounds, log_path),
        daemon=True,
    )
    t.start()
    st.session_state.update({
        "running": True,
        "current_code": code,
        "log_path": str(log_path),
        "thread": t,
        "done": False,
    })
    st.rerun()


# ══════════════════════════════════════════
#  运行中：实时进度
# ══════════════════════════════════════════
if st.session_state.running:
    code = st.session_state.current_code
    log_path = Path(st.session_state.log_path)

    st.markdown(f"""
    <div class="metric-card neutral">
        <div class="card-title">正在分析</div>
        <div class="card-value">{code}</div>
        <div class="card-sub">claude-opus-4-6-thinking · 深度推理中...</div>
    </div>
    """, unsafe_allow_html=True)

    # 读取日志
    log_text = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    lines = [l for l in log_text.splitlines() if l.strip() and "[LLM]" not in l]

    # 进度条估算
    STEPS = ["技术面", "资金流", "风险", "板块", "辩论", "综合", "完成"]
    done_steps = sum(1 for s in STEPS if any(s in l for l in lines))
    progress = min(done_steps / len(STEPS), 0.95)

    st.progress(progress)

    with st.expander("📟 实时日志", expanded=True):
        log_display = "\n".join(lines[-20:]) if lines else "启动中..."
        st.code(log_display, language=None)

    # 检查是否完成
    report = load_report(code)
    thread_alive = st.session_state.thread and st.session_state.thread.is_alive()

    if "✅ 分析完成" in log_text or (report and not thread_alive):
        st.session_state.update({"running": False, "done": True})
        st.rerun()
    else:
        time.sleep(3)
        st.rerun()


# ══════════════════════════════════════════
#  展示报告
# ══════════════════════════════════════════
def show_report(data: dict):
    code = data.get("stock_code", "")
    name = data.get("stock_name", "")
    signal = data.get("final_signal", "neutral")
    bullish_pct = data.get("bullish_pct", 50)
    summary = data.get("summary", "")
    op_advice = data.get("operation_advice", "")
    research = data.get("research_results", {})
    votes = data.get("votes", [])
    transcript = data.get("battle_transcript", [])
    date_str = data.get("date", "")

    # 清理 thinking 标签
    def clean(text):
        if "</thinking>" in text:
            text = text.split("</thinking>")[-1].strip()
        return text.replace("<thinking>", "").strip()

    summary = clean(summary)

    # ── 顶部 Hero ──────────────────────────────────
    signal_label = {"bullish":"📈 看多","bearish":"📉 看空","neutral":"➡️ 中性"}.get(signal,"")
    signal_color = {"bullish":"#00C853","bearish":"#FF1744","neutral":"#FFD600"}.get(signal,"#888")
    signal_cls   = {"bullish":"bull","bearish":"bear","neutral":"neutral"}.get(signal,"neutral")

    st.markdown(f"""
    <div style="background:#161b22; border:1px solid #30363d; border-radius:12px; padding:24px 28px; margin-bottom:20px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:16px;">
        <div>
            <div style="font-size:26px; font-weight:bold; color:#fff;">{code} &nbsp; {name}</div>
            <div style="color:#8b949e; font-size:13px; margin-top:4px;">NASDX 多智能体报告 · {date_str} · claude-opus-4-6-thinking</div>
        </div>
        <div style="text-align:right;">
            <div style="font-size:28px; font-weight:bold; color:{signal_color}; border:2px solid {signal_color}; border-radius:20px; padding:8px 24px; background:{signal_color}18;">{signal_label}</div>
            <div style="color:#8b949e; font-size:12px; margin-top:6px;">看多占比 {bullish_pct:.1f}%</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ── 四格指标卡 ─────────────────────────────────
    dim_meta = {
        "technical": ("📈 技术面", "MA·MACD·RSI·布林"),
        "fund_flow":  ("💰 资金流向", "主力·超大单·大单"),
        "risk":       ("🛡️ 风险评估", "超买·背离·波动"),
        "sector":     ("🏭 板块分析", "轮动·相对强弱"),
        "synthesis":  ("🎯 综合研判", "多维整合"),
    }

    cols = st.columns(len(research))
    for col, (dim, r) in zip(cols, research.items()):
        title, subtitle = dim_meta.get(dim, (dim, ""))
        sig = r.get("signal", "neutral")
        cls = {"bullish":"bull","bearish":"bear","neutral":"neutral"}.get(sig,"neutral")
        sig_label = {"bullish":"看多","bearish":"看空","neutral":"中性"}.get(sig,sig)
        sig_color = {"bullish":"#00C853","bearish":"#FF1744","neutral":"#FFD600"}.get(sig,"#888")
        conf = r.get("confidence", 0.5)
        with col:
            st.markdown(f"""
            <div class="metric-card {cls}">
                <div class="card-title">{title}</div>
                <div style="display:flex;justify-content:space-between;align-items:center;">
                    <span class="card-value" style="color:{sig_color};">{sig_label}</span>
                    <span style="font-size:13px;color:#8b949e;">{conf:.0%}</span>
                </div>
                <div class="card-sub">{subtitle}</div>
            </div>
            """, unsafe_allow_html=True)

    # ── 看多占比进度条 ──────────────────────────────
    bar_w = min(100, max(0, bullish_pct))
    bear_pct = 100 - bullish_pct
    st.markdown(f"""
    <div style="background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px 20px;margin:16px 0;">
        <div style="display:flex;justify-content:space-between;margin-bottom:8px;font-size:13px;">
            <span style="color:#00C853;">看多 {bullish_pct:.1f}%</span>
            <span style="color:#FF1744;">看空 {bear_pct:.1f}%</span>
        </div>
        <div style="background:#21262d;border-radius:6px;height:10px;overflow:hidden;">
            <div style="width:{bar_w}%;height:100%;background:linear-gradient(90deg,#00C853,#00E676);border-radius:6px;"></div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ── 两列：综合研判 + 关键点 ─────────────────────
    col_l, col_r = st.columns([3, 2])

    with col_l:
        st.markdown('<div class="section-header">📝 综合研判</div>', unsafe_allow_html=True)
        st.markdown(f"""
        <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px 18px;font-size:13px;line-height:1.8;color:#c9d1d9;">
        {summary.replace(chr(10),"<br>") if summary else "暂无"}
        </div>
        """, unsafe_allow_html=True)

    with col_r:
        st.markdown('<div class="section-header">💡 各维度要点</div>', unsafe_allow_html=True)
        for dim, r in research.items():
            title, _ = dim_meta.get(dim, (dim, ""))
            sig = r.get("signal","neutral")
            sig_color = {"bullish":"#00C853","bearish":"#FF1744","neutral":"#FFD600"}.get(sig,"#888")
            pts = r.get("key_points",[])[:3]
            if not pts:
                continue
            st.markdown(f'<div style="font-size:12px;color:{sig_color};font-weight:bold;margin:10px 0 4px 0;">{title}</div>', unsafe_allow_html=True)
            for pt in pts:
                st.markdown(f'<div class="kp">· {pt}</div>', unsafe_allow_html=True)

    # ── 辩论记录 ────────────────────────────────────
    st.markdown('<div class="section-header">⚔️ Battle 辩论记录</div>', unsafe_allow_html=True)
    if transcript:
        for msg in transcript:
            if msg.startswith("🟢"):
                css = "bubble-bull"
            elif msg.startswith("🔴"):
                css = "bubble-bear"
            else:
                css = "bubble-judge"
            clean_msg = msg.replace("<","&lt;").replace(">","&gt;").replace("\n","<br>")
            st.markdown(f'<div class="{css}">{clean_msg}</div>', unsafe_allow_html=True)
    else:
        st.caption("暂无辩论记录")

    # ── 投票结果 ────────────────────────────────────
    st.markdown('<div class="section-header">🗳️ 专家投票</div>', unsafe_allow_html=True)
    if votes:
        vote_cols = st.columns(len(votes))
        for col, v in zip(vote_cols, votes):
            vote = v.get("vote","neutral")
            color = {"bullish":"#00C853","bearish":"#FF1744","neutral":"#FFD600"}.get(vote,"#888")
            label = {"bullish":"看多","bearish":"看空","neutral":"中性"}.get(vote,vote)
            with col:
                st.markdown(f"""
                <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px;text-align:center;">
                    <div style="font-size:11px;color:#8b949e;margin-bottom:4px;">{v.get('agent','')}</div>
                    <div style="font-size:18px;font-weight:bold;color:{color};">{label}</div>
                    <div style="font-size:11px;color:#8b949e;margin-top:6px;">{v.get('reasoning','')[:40]}</div>
                </div>
                """, unsafe_allow_html=True)

    # ── 免责声明 ─────────────────────────────────────
    st.markdown("""
    <div style="margin-top:32px;padding-top:16px;border-top:1px solid #30363d;color:#8b949e;font-size:11px;text-align:center;">
    ⚠️ 本报告由 NASDX AI 多智能体系统生成，仅供学习研究，不构成任何投资建议。
    </div>
    """, unsafe_allow_html=True)


# ══════════════════════════════════════════
#  主逻辑：展示哪个报告
# ══════════════════════════════════════════
# 已完成分析 → 自动展示
if st.session_state.done and st.session_state.current_code:
    data = load_report(st.session_state.current_code)
    if data:
        show_report(data)
    else:
        st.error("报告生成失败，请查看日志")

# 未运行 → 展示输入框内对应的历史报告 或 欢迎页
elif not st.session_state.running:
    code_to_show = (stock_input or "").strip().zfill(6) if stock_input else ""
    data = load_report(code_to_show) if code_to_show else None

    if data:
        st.info(f"📂 显示 {code_to_show} 的历史报告（{data.get('date','')}）· 点击「开始分析」获取最新结果")
        show_report(data)
    else:
        # 欢迎页
        st.markdown("""
        <div style="text-align:center; padding: 60px 20px;">
            <div style="font-size:56px; margin-bottom:16px;">📊</div>
            <h2 style="color:#fff; font-size:22px; margin-bottom:8px;">输入股票代码，开始分析</h2>
            <p style="color:#8b949e; font-size:14px; max-width:500px; margin:0 auto 32px auto;">
                在左侧输入6位股票代码，或从快速选股中点击标的<br>
                4个专家AI将并行分析，多空辩论后给出操作建议
            </p>
        </div>
        """, unsafe_allow_html=True)

        # 显示最近报告
        all_reports = sorted(ROOT.glob("reports/report_*.json"), key=os.path.getmtime, reverse=True)
        if all_reports:
            st.markdown('<div class="section-header">📂 历史报告</div>', unsafe_allow_html=True)
            cols = st.columns(min(len(all_reports), 4))
            for col, rp in zip(cols, all_reports[:4]):
                with open(rp, "r", encoding="utf-8") as f:
                    rd = json.load(f)
                sig = rd.get("final_signal","neutral")
                color = {"bullish":"#00C853","bearish":"#FF1744","neutral":"#FFD600"}.get(sig,"#888")
                label = {"bullish":"📈 看多","bearish":"📉 看空","neutral":"➡️ 中性"}.get(sig,"")
                with col:
                    if st.button(
                        f"{rd.get('stock_code','')} {rd.get('stock_name','')}\n{label}",
                        key=f"hist_{rp.name}"
                    ):
                        st.session_state["quick_select"] = rd.get("stock_code","")
                        st.rerun()
