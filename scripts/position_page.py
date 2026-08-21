"""
持仓调仓顾问 — 独立渲染模块，由 quant_page.py 调用
"""
from __future__ import annotations
# ── scripts/ 运行引导：把仓库根加入 sys.path（移动自根目录）──
import sys
from pathlib import Path
_ROOT_DIR = str(Path(__file__).resolve().parents[1])
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)
import sys, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# ── 名称查询缓存 ──────────────────────────────────────
_NAME_CACHE: dict[str, str] = {}


def _build_name_map() -> dict[str, str]:
    """一次性构建 code -> name 映射表，缓存在函数级（Streamlit 对此无感知）"""
    name_map = {}

    # 1. 加载 etf50_pool.json
    try:
        with open(ROOT / "etf50_pool.json", encoding="utf-8") as f:
            for e in json.load(f).get("etfs", []):
                name_map[e.get("code", "")] = e.get("name", "")
    except Exception:
        pass

    # 2. 加载 stocks.json
    try:
        with open(ROOT / "stocks.json", encoding="utf-8") as f:
            cfg = json.load(f)
            for sector in cfg.get("sectors", []):
                for item in sector.get("stocks", []) + sector.get("etfs", []):
                    code = item.get("code", "")
                    if code:
                        name_map[code] = item.get("name", "")
    except Exception:
        pass

    return name_map


# 全局映射表，在第一次调用后缓存
_GLOBAL_NAME_MAP = None


def _get_name_map() -> dict[str, str]:
    """延迟初始化全局名称映射表（单例模式）"""
    global _GLOBAL_NAME_MAP
    if _GLOBAL_NAME_MAP is None:
        _GLOBAL_NAME_MAP = _build_name_map()
    return _GLOBAL_NAME_MAP


def _lookup_name(code: str) -> str:
    """查询代码对应的名称，仅使用本地 JSON 数据"""
    if not code or len(code) < 6:
        return ""

    # 优先查内存缓存（当前会话）
    if code in _NAME_CACHE:
        return _NAME_CACHE[code]

    # 查全局映射表
    name = _get_name_map().get(code, "")
    if name:
        _NAME_CACHE[code] = name

    return name


def _sc(v):
    return "#22c55e" if v >= 60 else "#ef4444" if v <= 40 else "#f59e0b"


def _bar(v, h=4):
    c = _sc(v)
    return (f'<div style="background:rgba(255,255,255,0.06);border-radius:2px;height:{h}px;overflow:hidden">'
            f'<div style="width:{min(v,100):.0f}%;height:100%;background:{c};border-radius:2px"></div></div>')


def _metric(label, value, color="#fff", sub=""):
    sub_html = f'<div style="font-size:11px;color:rgba(255,255,255,0.3);margin-top:2px">{sub}</div>' if sub else ""
    return (f'<div style="background:#111;border:1px solid rgba(255,255,255,0.06);'
            f'border-radius:6px;padding:12px;text-align:center">'
            f'<div style="font-size:18px;font-weight:700;color:{color};font-variant-numeric:tabular-nums">{value}</div>'
            f'<div style="font-size:10px;color:rgba(255,255,255,0.3);text-transform:uppercase;'
            f'letter-spacing:.05em;margin-top:3px">{label}</div>'
            f'{sub_html}</div>')


def _card(body, accent=None):
    top = f"background:linear-gradient(90deg,transparent,{accent},transparent)" if accent else "none"
    return (f'<div style="background:#111;border:1px solid rgba(255,255,255,0.06);'
            f'border-radius:8px;padding:14px 16px;position:relative;overflow:hidden">'
            f'<div style="position:absolute;top:0;left:0;right:0;height:1px;{top}"></div>'
            f'{body}</div>')


def render_position_advisor(st):
    """持仓调仓顾问主页面"""

    st.markdown(_card(
        '<div style="font-size:12px;color:rgba(255,255,255,0.45);line-height:1.9">'
        '输入你的持仓，系统综合 <b style="color:#3b82f6">QLib Alpha158 因子</b> + '
        '<b style="color:#22c55e">VnPy 风险指标</b> + '
        '<b style="color:#f59e0b">FinRL 集成信号</b> + '
        '<b style="color:#a855f7">ETF50 量化结果</b>，给出调仓建议和替换候选。'
        '</div>', accent="#a855f7"
    ), unsafe_allow_html=True)
    st.markdown('<div style="height:10px"></div>', unsafe_allow_html=True)

    # ── 持仓输入表格 ──────────────────────────────────────
    st.markdown('<div style="font-size:11px;color:rgba(255,255,255,0.3);text-transform:uppercase;'
                'letter-spacing:.06em;padding-bottom:8px">持仓清单</div>', unsafe_allow_html=True)

    if "pos_rows" not in st.session_state:
        st.session_state.pos_rows = [
            {"code": "159611", "name": "电力ETF广发", "cost": 1.28, "shares": 10000, "type": "etf"},
            {"code": "513160", "name": "港股科技ETF", "cost": 1.10, "shares": 5000,  "type": "etf"},
        ]

    # ★ 启动时预填充：name 为空但 code 有值的行，立即查名
    for i, row in enumerate(st.session_state.pos_rows):
        code = row.get("code", "").strip()
        if code and len(code) == 6 and not row.get("name", ""):
            auto = _lookup_name(code)
            if auto:
                row["name"] = auto
                # 同步到 widget state，确保输入框显示正确值
                st.session_state[f"pn_{i}"] = auto

    # 表头
    hdr = st.columns([2, 3, 2, 2, 2, 1])
    for col, h in zip(hdr, ["代码", "名称（选填）", "成本价", "持有股数", "类型", ""]):
        col.markdown(f'<div style="font-size:10px;color:rgba(255,255,255,0.25);'
                     f'text-transform:uppercase;letter-spacing:.05em;padding:4px 0">{h}</div>',
                     unsafe_allow_html=True)

    need_rerun = False
    to_del = []
    for i, row in enumerate(st.session_state.pos_rows):
        c1, c2, c3, c4, c5, c6 = st.columns([2, 3, 2, 2, 2, 1])
        with c1:
            old_code = row.get("code", "")
            new_code = st.text_input("", value=old_code,
                                      key=f"pc_{i}", label_visibility="collapsed", max_chars=6)
            new_code_padded = new_code.strip().zfill(6) if len(new_code.strip()) == 6 else new_code.strip()

            # 代码变了（且达到6位）→ 自动查名、自动判断类型
            if new_code_padded != old_code and len(new_code_padded) == 6:
                row["code"] = new_code_padded
                auto_name = _lookup_name(new_code_padded)
                if auto_name:
                    row["name"] = auto_name
                    # ★ 关键：直接写入 session_state，强制覆盖 Streamlit 的 widget 缓存
                    st.session_state[f"pn_{i}"] = auto_name
                etf_prefix = ("50","51","15","16","55","56","58","59")
                row["type"] = "etf" if new_code_padded[:2] in etf_prefix else "stock"
                st.session_state[f"pt_{i}"] = row["type"]
                need_rerun = True
            elif new_code_padded != old_code:
                row["code"] = new_code_padded

            # 名称尚为空但代码已6位 → 补查一次
            if not row.get("name","") and len(row.get("code","")) == 6:
                auto_name = _lookup_name(row["code"])
                if auto_name:
                    row["name"] = auto_name
                    st.session_state[f"pn_{i}"] = auto_name
                    need_rerun = True

        with c2:
            # 直接用 session_state 中的值（已被上面强制写入）
            cur_name = st.session_state.get(f"pn_{i}", row.get("name", ""))
            row["name"] = st.text_input("", value=cur_name,
                                         key=f"pn_{i}", label_visibility="collapsed",
                                         placeholder="输入6位代码后自动识别")
        with c3:
            row["cost"] = st.number_input("", value=float(row.get("cost", 1.0)),
                                           key=f"pco_{i}", label_visibility="collapsed",
                                           min_value=0.001, step=0.01, format="%.3f")
        with c4:
            row["shares"] = st.number_input("", value=int(row.get("shares", 1000)),
                                             key=f"ps_{i}", label_visibility="collapsed",
                                             min_value=100, step=100)
        with c5:
            opts = ["etf", "stock", "lof"]
            idx  = opts.index(row.get("type", "etf")) if row.get("type", "etf") in opts else 0
            row["type"] = st.selectbox("", opts, index=idx,
                                        key=f"pt_{i}", label_visibility="collapsed")
        with c6:
            if st.button("✕", key=f"pd_{i}"):
                to_del.append(i)

    for i in reversed(to_del):
        st.session_state.pos_rows.pop(i)
    if to_del or need_rerun:
        st.rerun()

    # 操作按钮
    ba1, ba2, ba3 = st.columns([1, 1, 4])
    with ba1:
        if st.button("＋ 添加", key="pos_add"):
            st.session_state.pos_rows.append(
                {"code": "", "name": "", "cost": 1.0, "shares": 1000, "type": "etf"})
            st.rerun()
    with ba2:
        if st.button("📋 批量", key="pos_batch_btn"):
            st.session_state["pos_show_batch"] = not st.session_state.get("pos_show_batch", False)

    if st.session_state.get("pos_show_batch"):
        txt = st.text_area("批量输入（代码 成本 数量，每行一条）",
                            placeholder="159611 1.28 10000\n513160 1.10 5000\n000001 10.5 1000",
                            key="pos_batch_txt", height=90)
        if st.button("解析导入", key="pos_parse"):
            new = []
            for line in txt.strip().splitlines():
                parts = line.strip().split()
                if len(parts) >= 3:
                    try:
                        code = parts[0].strip()
                        etf_prefix = ("51", "50", "15", "16", "58", "56", "55", "59")
                        code6 = code.zfill(6)
                        new.append({
                            "code":   code6,
                            "name":   _lookup_name(code6),   # 批量导入时也自动查名
                            "cost":   float(parts[1]),
                            "shares": int(parts[2]),
                            "type":   "etf" if code[:2] in etf_prefix else "stock",
                        })
                    except Exception:
                        pass
            if new:
                st.session_state.pos_rows = new
                st.session_state["pos_show_batch"] = False
                st.rerun()

    st.markdown('<div style="height:12px"></div>', unsafe_allow_html=True)

    # ── 参数 ──────────────────────────────────────────────
    pa1, pa2 = st.columns([1, 1])
    with pa1:
        total_cap = st.number_input("总资金（元）", value=100000, step=10000, key="pos_capital")
    with pa2:
        hist_days = st.select_slider("历史天数", [90, 180, 252], value=180, key="pos_days")

    valid = [r for r in st.session_state.pos_rows if r.get("code", "").strip()]

    if st.button("🔍  分析持仓 & 生成调仓建议",
                 key="run_pos", type="primary", disabled=len(valid) == 0):
        with st.spinner(f"分析 {len(valid)} 只持仓（获取实时行情 + 计算因子）..."):
            try:
                import quant.patch_requests  # noqa
                from quant.position_advisor import Position, PositionAdvisor

                positions = [
                    Position(
                        code       = r["code"].strip().zfill(6),
                        name       = r.get("name", "") or r["code"],
                        cost       = float(r["cost"]),
                        shares     = int(r["shares"]),
                        asset_type = r.get("type", "etf"),
                    )
                    for r in valid
                ]

                advisor = PositionAdvisor(total_capital=total_cap)
                result  = advisor.analyze(positions, days=hist_days, verbose=False)
                st.session_state["pos_result"] = result
            except Exception as e:
                import traceback
                st.error(f"分析失败：{e}\n{traceback.format_exc()[-600:]}")
        st.rerun()

    if "pos_result" not in st.session_state:
        return

    # ── 展示结果 ──────────────────────────────────────────
    res  = st.session_state["pos_result"]
    port = res.get("portfolio", {})
    poss = res.get("positions", [])
    cands= res.get("candidates", [])
    summ = res.get("summary", "")

    # 摘要卡片
    h_sc    = port.get("health_score", 50)
    h_color = "#22c55e" if h_sc >= 70 else "#ef4444" if h_sc < 40 else "#f59e0b"
    pnl_c   = "#22c55e" if port.get("total_pnl", 0) >= 0 else "#ef4444"

    st.markdown('<div style="height:14px"></div>', unsafe_allow_html=True)
    st.markdown(
        f'<div style="background:rgba(168,85,247,0.06);border:1px solid rgba(168,85,247,0.2);'
        f'border-radius:8px;padding:12px 16px;margin-bottom:14px;white-space:pre-line;'
        f'font-size:13px;color:rgba(255,255,255,0.7);line-height:1.8">{summ}</div>',
        unsafe_allow_html=True,
    )

    # 组合指标行
    pm = st.columns(5)
    for col, (lb, v, c) in zip(pm, [
        ("组合健康度",  f'{h_sc:.0f}/100',                        h_color),
        ("总市值",      f'¥{port.get("total_market_value",0):,.0f}', "#fff"),
        ("总浮盈亏",    f'{port.get("total_pnl",0):+,.0f}元',         pnl_c),
        ("浮盈率",      f'{port.get("total_pnl_pct",0):+.2f}%',       pnl_c),
        ("平均量化分",  f'{port.get("avg_quant_score",50):.0f}',   _sc(port.get("avg_quant_score", 50))),
    ]):
        with col:
            st.markdown(_metric(lb, v, c), unsafe_allow_html=True)

    st.markdown('<div style="height:16px"></div>', unsafe_allow_html=True)

    # 持仓明细
    st.markdown('<div style="font-size:11px;color:rgba(255,255,255,0.25);text-transform:uppercase;'
                'letter-spacing:.06em;margin-bottom:8px">持仓明细 & 调仓建议</div>',
                unsafe_allow_html=True)

    ACTION_CFG = {
        "sell":   ("#ef4444", "rgba(239,68,68,0.08)",   "🔴 清仓"),
        "reduce": ("#f59e0b", "rgba(245,158,11,0.08)",  "🟡 减仓"),
        "hold":   ("#9ca3af", "rgba(156,163,175,0.04)", "⚪ 持有"),
        "add":    ("#22c55e", "rgba(34,197,94,0.08)",   "🟢 加仓"),
    }

    for p in sorted(poss,
                    key=lambda x: {"sell": 0, "reduce": 1, "add": 2, "hold": 3}.get(x.get("action", "hold"), 3)):
        action = p.get("action", "hold")
        ac, abg, alb = ACTION_CFG.get(action, ("#9ca3af", "rgba(156,163,175,0.04)", "⚪ 持有"))
        sc    = p.get("quant_score", 50)
        pnl   = p.get("pnl_pct", 0)
        pnl_c = "#22c55e" if pnl >= 0 else "#ef4444"
        reasons = " · ".join(p.get("reasons", [])[:2])
        ap    = p.get("action_pct", 0)
        ap_str= f'+{ap:.0%}' if ap > 0 else f'{ap:.0%}' if ap < 0 else ""
        risk_badge = {"high": '<span style="color:#ef4444;font-size:10px"> ⚠️高波动</span>',
                      "low":  '<span style="color:#22c55e;font-size:10px"> ✓低风险</span>',
                      "medium": ""}.get(p.get("risk_level", "medium"), "")

        with st.expander(
            f'{p.get("code","")}  {p.get("name","")}  ·  {alb}{" "+ap_str if ap_str else ""}  ·  '
            f'{pnl:+.1f}%  ·  量化{sc:.0f}分',
            expanded=(action in ("sell", "reduce"))
        ):
            d1, d2, d3, d4 = st.columns(4)
            with d1: st.markdown(_metric("成本价",   f'¥{p.get("cost",0):.3f}'), unsafe_allow_html=True)
            with d2: st.markdown(_metric("现价",     f'¥{p.get("current_price",0):.3f}'), unsafe_allow_html=True)
            with d3: st.markdown(_metric("浮盈亏",   f'{p.get("pnl_abs",0):+,.0f}元', pnl_c), unsafe_allow_html=True)
            with d4: st.markdown(_metric("持仓市值", f'¥{p.get("market_value",0):,.0f}'), unsafe_allow_html=True)

            r1, r2, r3, r4 = st.columns(4)
            with r1: st.markdown(_metric("量化分",   f'{sc:.0f}',                   _sc(sc)), unsafe_allow_html=True)
            with r2: st.markdown(_metric("年化波动", f'{p.get("volatility",0):.1f}%'), unsafe_allow_html=True)
            with r3: st.markdown(_metric("最大回撤", f'{p.get("max_drawdown",0):.1f}%', "#ef4444"), unsafe_allow_html=True)
            with r4: st.markdown(_metric("Beta",    f'{p.get("beta",1.0):.2f}'),    unsafe_allow_html=True)

            st.markdown(
                f'<div style="background:{abg};border:1px solid {ac}30;border-left:2px solid {ac};'
                f'border-radius:6px;padding:10px 14px;margin-top:10px">'
                f'<div style="font-size:13px;font-weight:700;color:{ac};margin-bottom:4px">'
                f'{alb}  {ap_str}</div>'
                f'<div style="font-size:12px;color:rgba(255,255,255,0.45)">{reasons}</div>'
                f'{risk_badge}</div>',
                unsafe_allow_html=True,
            )

    # 仓位分布
    weights = port.get("weights", {})
    if weights:
        st.markdown('<div style="height:14px"></div>', unsafe_allow_html=True)
        st.markdown('<div style="font-size:11px;color:rgba(255,255,255,0.25);text-transform:uppercase;'
                    'letter-spacing:.06em;margin-bottom:8px">仓位分布</div>', unsafe_allow_html=True)
        name_map = {p.get("code", ""): p.get("name", "") for p in poss}
        for code, w in sorted(weights.items(), key=lambda x: -x[1]):
            wc = "#ef4444" if w > 0.4 else "#22c55e" if w < 0.15 else "#3b82f6"
            st.markdown(
                f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:5px">'
                f'<div style="width:90px;font-size:11px;color:rgba(255,255,255,0.4)">'
                f'{code} {name_map.get(code,"")[:4]}</div>'
                f'<div style="flex:1">{_bar(w * 100, h=5)}</div>'
                f'<div style="width:44px;text-align:right;font-size:12px;font-weight:600;'
                f'color:{wc};font-variant-numeric:tabular-nums">{w:.1%}</div></div>',
                unsafe_allow_html=True,
            )

    # 推荐候选
    if cands:
        st.markdown('<div style="height:14px"></div>', unsafe_allow_html=True)
        st.markdown('<div style="font-size:11px;color:rgba(255,255,255,0.25);text-transform:uppercase;'
                    'letter-spacing:.06em;margin-bottom:8px">💡 推荐替换 / 补仓标的</div>',
                    unsafe_allow_html=True)
        cc = st.columns(min(len(cands), 5))
        for col, c in zip(cc, cands):
            sc   = c.get("score", 0)
            prem = c.get("premium")
            prem_s = f'溢价{prem:+.2f}%' if prem is not None else "溢价 —"
            prem_c = "#ef4444" if prem and prem > 2 else "#22c55e" if prem and prem < -0.5 else "rgba(255,255,255,0.3)"
            with col:
                st.markdown(
                    f'<div style="background:#111;border:1px solid rgba(255,255,255,0.06);'
                    f'border-top:1px solid #22c55e;border-radius:6px;padding:12px;text-align:center">'
                    f'<div style="font-size:11px;color:rgba(255,255,255,0.3)">{c.get("code","")}</div>'
                    f'<div style="font-size:13px;font-weight:600;color:#fff;margin:4px 0">{c.get("name","")}</div>'
                    f'<div style="font-size:20px;font-weight:700;color:#22c55e;font-variant-numeric:tabular-nums">'
                    f'{sc:.0f}</div>'
                    f'<div style="font-size:10px;color:rgba(255,255,255,0.25)">量化分</div>'
                    f'<div style="font-size:11px;color:{prem_c};margin-top:4px">{prem_s}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

    # 风险警告
    warnings = []
    if port.get("stop_loss_codes"):
        warnings.append(f'⚠️ 止损预警：{", ".join(port["stop_loss_codes"])}（浮亏超过-15%）')
    if port.get("overweight_codes"):
        warnings.append(f'⚠️ 仓位过重：{", ".join(port["overweight_codes"])}（单只超40%）')
    if port.get("hhi", 0) > 0.35:
        warnings.append(f'⚠️ 集中度过高（HHI={port["hhi"]:.2f}），建议分散至5只以上')
    if port.get("avg_correlation", 0) > 0.75:
        warnings.append(f'⚠️ 持仓相关性高（{port["avg_correlation"]:.2f}），分散化效果差')

    if warnings:
        st.markdown('<div style="height:10px"></div>', unsafe_allow_html=True)
        for w in warnings:
            st.markdown(
                f'<div style="background:rgba(239,68,68,0.05);border-left:2px solid #ef4444;'
                f'border-radius:4px;padding:8px 12px;margin-bottom:5px;'
                f'font-size:12px;color:#ef4444">{w}</div>',
                unsafe_allow_html=True,
            )
