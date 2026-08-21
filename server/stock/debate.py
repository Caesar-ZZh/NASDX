"""多空辩论 —— 让多个 AI 角色围绕同一份客观事实互相质疑。

为什么要这样设计（与常见「AI 交易 agent」框架的关键区别）：
开源的多 agent 金融框架（TradingAgents、ai-hedge-fund 等）最后都会落到一个 trader /
portfolio_manager 角色，输出「买/卖/持仓多少」。**本模块刻意不做那一层。**
这里的产物是「多方怎么说、空方怎么说、双方在哪儿真正分歧、要证伪各自需要什么证据」，
结论留给用户自己下。多视角本身就是产品——不是通往某个买卖建议的中间步骤。

流程：
  1. 事实底稿（不经 LLM，后端直接按固定清单拉客观数据）——保证多空双方吵的是同一份事实，
     谁也不能靠编数字赢，也不依赖模型「想起来」该调哪个工具。
  2. 多方研究员 / 空方研究员：各自只基于底稿立论。
  3. （可选）交叉反驳：各自看到对方论点后回应。
  4. 中立主持：只归纳分歧点与验证路径，不裁决谁对、不给操作建议。
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed

import chat
import cli_runtime
import tools

# 底稿抓取清单：覆盖「估值 / 财报 / 资金 / 事件 / 行业」五个面，与 chat.ANALYSIS_FRAMEWORK 对齐。
# 每项 (工具名, 额外参数, 小标题, 可并行)。任何一项挂了都不阻断，缺项会如实标注。
#
# ⚠️ 「可并行」不是随便标的：astock.em_get 的防封节流靠的是「上次请求时间戳 + sleep」而不是锁，
#    并发调用会让多个线程同时判定「不用等」从而击穿限流、有被东财封 IP 的风险。
#    所以凡是走 em_get 的（估值/资金流/两融/股东户数/解禁/板块）一律标 False 串行跑；
#    走腾讯、百度、同花顺、以及东财那几个不经 em_get 的直连接口才并行。
# 第 5 位 empty_ok = 「空结果是合法事实」还是「空结果等于没取到」。
#   False：这项数据每只股票都该有，空了就是数据源出问题 → 计入缺口。
#   True ：空可能是真实情况（真没解禁、非两融标的、小票没研报）→ 不算缺口，
#          但底稿里会写明「无记录」，跟「没取到」区分开。
_DOSSIER_SPEC: list[tuple[str, dict, str, bool, bool]] = [
    ("query_quote", {}, "实时行情", True, False),
    ("query_valuation", {}, "估值与一致预期", False, False),
    ("query_valuation_percentile", {}, "估值历史分位", True, False),
    ("query_financials", {}, "最新财报关键指标", True, False),
    ("query_kline", {"count": 60}, "近 60 日价格走势", True, False),
    ("query_fund_flow", {"days": 5}, "资金流向", False, False),
    ("query_margin", {}, "融资融券", False, True),
    ("query_holders", {}, "股东户数", False, True),
    ("query_announcements", {}, "近期公告", True, True),
    ("query_lockup", {}, "限售解禁", False, True),
    ("query_concepts", {}, "板块与概念归属", False, False),
    ("query_reports", {}, "近期研报", True, True),
    ("query_news", {}, "近期新闻", True, True),
]

_SECTION_CAP = 1800  # 单个小节注入上限，防止某项数据把整份底稿撑爆
_PARALLEL_WORKERS = 4

# 判空时要跳过的「元信息」字段：它们描述数据本身，不构成观测值。
# 少了这一步，`{"period":"近5年","metrics":{}}` 会因为 period 非空而被当成有数据。
_META_KEYS = {"period", "unit", "note", "code", "generated_at", "tracks", "total_cached"}

# 注意措辞：这类项返回空时，代码分不出「真的没有这类事件」还是「数据源临时不可用」，
# 所以不能断言「确实没有」——如实说明两种可能，并要求不得臆测。
NO_RECORD = "（未取到任何记录：可能确实没有此类事件，也可能是该数据源暂时不可用。两种情况都不得据此推断。）"


def _payload_empty(value) -> bool:
    """判断「有壳无肉」：剥掉元信息字段后没有任何实质观测值。

    只看顶层是不够的——上游失败时工具常返回带外壳的空结果，例如估值分位在两个请求
    都失败时返回 `{"period":"近5年","metrics":{}}`。若把它当成功，底稿会凭空多出一个
    空小节、还不进缺口列表，模型就可能对着空壳发挥。
    """
    if value is None or value == "" or value == [] or value == {}:
        return True
    if isinstance(value, list):
        return all(_payload_empty(x) for x in value)
    if isinstance(value, dict):
        for k, v in value.items():
            if k in _META_KEYS:
                continue
            if isinstance(v, (list, dict)):
                if not _payload_empty(v):
                    return False
            elif isinstance(v, bool):
                if v:
                    return False
            elif isinstance(v, (int, float)):
                if v:  # 0 视为无内容（如 total_blocks=0）
                    return False
            elif v:
                return False
        return True
    return False  # 非空标量


def _fetch_section(spec: tuple[str, dict, str, bool, bool], code: str) -> dict:
    """跑一项底稿数据，返回 {title, tool, data, ok}。"""
    name, extra, title, _par, empty_ok = spec
    args = {"codes": [code]} if name == "query_quote" else {"code": code, **extra}
    result = tools.exec_tool(name, args)

    if isinstance(result, dict) and result.get("error"):
        return {"title": title, "tool": name, "data": result, "ok": False}
    if _payload_empty(result):
        # 空是合法事实的项照常进底稿，但内容换成明确说明，免得模型把空壳当数据读；
        # 其余的算没取到，进缺口列表。
        if empty_ok:
            return {"title": title, "tool": name, "data": NO_RECORD, "ok": True}
        return {"title": title, "tool": name, "data": result, "ok": False}
    return {"title": title, "tool": name, "data": result, "ok": True}


def collect_dossier(code: str):
    """生成器：逐项 yield 进度事件，跑完 return 完整底稿。

    调用方用 `dossier = yield from collect_dossier(code)` 即可边推进度边拿结果——
    13 项串行要一分多钟，没有进度反馈的话前端就是一分钟白屏。
    """
    done: dict[str, dict] = {}
    total = len(_DOSSIER_SPEC)
    par = [s for s in _DOSSIER_SPEC if s[3]]
    seq = [s for s in _DOSSIER_SPEC if not s[3]]

    with ThreadPoolExecutor(max_workers=_PARALLEL_WORKERS) as ex:
        futures = {ex.submit(_fetch_section, s, code): s for s in par}
        for fut in as_completed(futures):
            spec = futures[fut]
            try:
                sec = fut.result()
            except Exception as e:  # noqa: BLE001 — 单项失败只记缺口
                sec = {"title": spec[2], "tool": spec[0], "data": {"error": str(e)}, "ok": False}
            done[sec["title"]] = sec
            yield {"type": "dossier_progress", "title": sec["title"], "ok": sec["ok"],
                   "loaded": len(done), "total": total}

    for spec in seq:  # 走 em_get 的，保持串行以尊重节流
        try:
            sec = _fetch_section(spec, code)
        except Exception as e:  # noqa: BLE001
            sec = {"title": spec[2], "tool": spec[0], "data": {"error": str(e)}, "ok": False}
        done[sec["title"]] = sec
        yield {"type": "dossier_progress", "title": sec["title"], "ok": sec["ok"],
               "loaded": len(done), "total": total}

    sections, missing = [], []
    for _n, _e, title, _p, _ok in _DOSSIER_SPEC:  # 按清单顺序还原，保证底稿可读性稳定
        sec = done.get(title)
        if sec and sec["ok"]:
            sections.append({"title": sec["title"], "tool": sec["tool"], "data": sec["data"]})
        else:
            missing.append(title)
    return {"code": code, "sections": sections, "missing": missing}


def build_dossier(code: str) -> dict:
    """同步版底稿（供测试与非流式调用）：跑完生成器取其返回值。"""
    gen = collect_dossier(code)
    try:
        while True:
            next(gen)
    except StopIteration as stop:
        return stop.value


def dossier_text(dossier: dict) -> str:
    """把底稿渲染成给模型看的纯文本。"""
    parts = [f"【客观事实底稿 · {dossier['code']}】", "以下全部为接口实时拉取的客观数据，不含任何观点：", ""]
    for s in dossier["sections"]:
        data = s["data"]
        # 「无记录」这类说明是给模型读的自然语言，别再套一层 JSON 引号
        body = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)[:_SECTION_CAP]
        parts.append(f"## {s['title']}（来源工具 {s['tool']}）\n{body}\n")
    if dossier["missing"]:
        parts.append(f"## 数据缺口\n以下数据本次未取到，立论时不得臆测：{('、'.join(dossier['missing']))}")
    return "\n".join(parts)


_COMMON_RULES = """
共同规则（必须遵守）：
- 只能使用底稿里的数据立论。底稿没有的数字一律不许编，需要但缺失的，明确写「该数据缺失」。
- 每条论点都要标出所依据的具体数据（写清数值），没有数据支撑的直觉判断要标注「无数据支撑」。
- 不预测股价涨跌与具体价位、不给买卖时机、不给目标价、不给仓位建议、不承诺收益。
- 用简洁中文，条目化输出。
"""

_ROLE_PROMPTS = {
    "bull": """你是一名**多方研究员**。基于底稿，找出支持这家公司基本面向好的证据，尽可能有力地立论。
输出格式：
1. **核心论点**（一句话）
2. **支撑证据**（3-5 条，每条：论据 + 依据的具体数据）
3. **这套逻辑成立的前提**（列出你的论点依赖哪些条件继续成立）
""" + _COMMON_RULES,

    "bear": """你是一名**空方研究员**。基于底稿，找出这家公司基本面与估值上的风险与疑点，尽可能有力地质疑。
输出格式：
1. **核心质疑**（一句话）
2. **风险证据**（3-5 条，每条：疑点 + 依据的具体数据）
3. **这套逻辑成立的前提**（列出你的质疑依赖哪些条件继续成立）
""" + _COMMON_RULES,

    "bull_rebut": """你是那名**多方研究员**。上面是空方的质疑。逐条回应：哪些质疑你承认（承认就明说），
哪些你认为有数据可以反驳（给出数据），哪些属于双方都没有数据、只能存疑。不要重复第一轮的论述。
""" + _COMMON_RULES,

    "bear_rebut": """你是那名**空方研究员**。上面是多方的论述。逐条回应：哪些论点你承认，
哪些你认为有数据可以反驳（给出数据），哪些属于双方都没有数据、只能存疑。不要重复第一轮的质疑。
""" + _COMMON_RULES,

    "referee": """你是**中立主持人**。你的任务不是裁决谁对谁错，更不是给出投资建议，而是把这场辩论的价值沉淀下来。

输出格式：
1. **双方共识**（多空都认可的事实，2-4 条）
2. **真正的分歧点**（3-5 条，每条写清：多方认为什么 / 空方认为什么 / **分歧的根源是数据不足还是对同一数据的解读不同**）
3. **验证清单**（针对每个分歧点，写清「要判定这条分歧，需要观察什么数据、去哪里看、什么时候能看到」）
4. **数据缺口**（这场讨论里缺了什么关键信息）

硬性要求：
- **绝对不要**给出结论倾向、买卖建议、目标价、评级或「更认同哪一方」的表述。
- 你的产出应当让读者自己有能力去继续验证，而不是替读者做决定。
- 用简洁中文。
""",
}

_STAGE_LABEL = {
    "bull": "多方研究员",
    "bear": "空方研究员",
    "bull_rebut": "多方反驳",
    "bear_rebut": "空方反驳",
    "referee": "中立主持 · 分歧与验证清单",
}


def _stage_plan(rounds: int) -> list[str]:
    if rounds >= 2:
        return ["bull", "bear", "bull_rebut", "bear_rebut", "referee"]
    return ["bull", "bear", "referee"]


def _build_messages(stage: str, facts: str, transcript: list[dict]) -> list[dict]:
    """给某个角色拼消息。底稿始终在 system 里；已产生的发言作为上下文喂进去。"""
    system = f"{_ROLE_PROMPTS[stage]}\n\n{facts}"
    user_parts = []
    for t in transcript:
        if stage == "bull" or (stage == "bear" and t["stage"] != "bull"):
            continue  # 首轮陈述：多方不看任何人，空方只看多方
        user_parts.append(f"【{_STAGE_LABEL[t['stage']]}的发言】\n{t['content']}")
    if not user_parts:
        return [{"role": "system", "content": system},
                {"role": "user", "content": "请基于底稿开始你的陈述。"}]
    return [{"role": "system", "content": system},
            {"role": "user", "content": "\n\n".join(user_parts) + "\n\n请按你的角色要求输出。"}]


def run_debate_stream(cfg: dict, code: str, rounds: int = 1):
    """跑一场辩论，yield NDJSON 事件。

    事件类型：dossier（底稿就绪）/ stage（角色开始）/ delta（增量文本）/
             stage_done（角色发言完成）/ done（全部完成）/ error
    """
    provider = str(cfg.get("provider", ""))
    is_cli = provider.startswith("cli-")

    yield {"type": "status", "message": "正在拉取客观事实底稿…"}
    dossier = yield from collect_dossier(code)
    # 只有「无记录」说明、没有一条真实数据时同样算取数失败——
    # 让多空基于一份全是「未取到」的底稿互相质疑毫无意义。
    if not any(not isinstance(s["data"], str) for s in dossier["sections"]):
        yield {"type": "error", "message": "未能取到任何客观数据，无法开始辩论（请检查代码是否正确、网络是否可达）"}
        return
    yield {"type": "dossier",
           "sections": [{"title": s["title"], "tool": s["tool"]} for s in dossier["sections"]],
           "missing": dossier["missing"]}

    facts = dossier_text(dossier)
    transcript: list[dict] = []

    for stage in _stage_plan(rounds):
        yield {"type": "stage", "stage": stage, "label": _STAGE_LABEL[stage]}
        messages = _build_messages(stage, facts, transcript)
        buf: list[str] = []
        try:
            if is_cli:
                content = cli_runtime.run_cli(provider[4:], messages[0]["content"], messages[-1]["content"])
                buf.append(content)
                yield {"type": "delta", "stage": stage, "text": content}
            else:
                # _call_llm_stream 返回的是上游 Response，需配 _iter_sse_deltas 解析 SSE
                resp = chat._call_llm_stream(cfg, messages, use_tools=False)
                for delta in chat._iter_sse_deltas(resp):
                    text = delta.get("content")
                    if text:
                        buf.append(text)
                        yield {"type": "delta", "stage": stage, "text": text}
        except Exception as e:  # noqa: BLE001 — 单个角色失败不该毁掉整场辩论
            # 必须补一个终态事件：前端按 stage_done 把该角色标记为完成，
            # 只发 error 的话这个角色会永远停在「生成中…」，并让「全部完成」判定不成立、
            # 连带后面能正常跑完的角色也存不进沉淀。
            yield {"type": "error", "stage": stage, "message": f"{_STAGE_LABEL[stage]}生成失败：{e}"}
            yield {"type": "stage_done", "stage": stage, "label": _STAGE_LABEL[stage],
                   "content": f"（本角色生成失败：{e}）", "failed": True}
            continue  # 失败内容不进 transcript——不能把错误信息当论据喂给后面的角色

        content = "".join(buf).strip()
        transcript.append({"stage": stage, "content": content})
        yield {"type": "stage_done", "stage": stage, "label": _STAGE_LABEL[stage], "content": content}

    yield {"type": "done", "code": code, "stages": transcript}
