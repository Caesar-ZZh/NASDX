"""反思层 —— 对一段已经写好的分析做「推理审计」。

投研里最贵的错误不是算错数字，是**把推测当成了事实**、并在此之上继续推理。
这个模块不产出新观点，只回头审一遍已有的分析：哪些话有数据撑着、哪些是脑补、
一旦结论错了最可能错在哪一环、什么信号出现就说明它站不住。

产物是「怎么继续验证」，不是「结论对不对」——所以它天然不涉及荐股，
也正好补上「多空辩论」之后、「沉淀成研究记录」之前缺的那一步。
"""

from __future__ import annotations

import chat
import cli_runtime

MAX_SOURCE_CHARS = 12000  # 待审文本上限，超出截断（反思本身不该把上下文吃光）

REFLECT_PROMPT = """你是一名严格的**研究审计员**。下面是一段已经写好的投研分析。
你的任务不是重写它、也不是评价结论对错，而是审计它的推理过程。

按下面五块输出，每块都要具体、对应到原文的话，不要泛泛而谈：

1. **有数据支撑的部分**：原文哪些判断确实建立在具体数据上（引用那句话 + 它依据的数据）。
2. **缺乏支撑的部分**：哪些判断是推测、类比、常识或情绪，却被当成了事实往下推。逐条点出原句。
3. **数据使用问题**：是否存在——用错口径（如把单季当全年、把流通市值当总市值）、
   拿孤立数字下结论（缺同比/环比/同业对照）、幸存者偏差、把相关当因果、时间口径不一致。没有就写「未发现」。
4. **最脆弱的一环**：如果这段分析最终被证明是错的，最可能错在哪里？为什么是这一环？
5. **验证清单**：要检验上面的疑点，具体该看什么数据、去哪看、多久能看到结果（3-5 条，可执行）。

硬性要求：
- 不要给出你自己的投资判断、买卖建议、目标价或评级。
- 不要因为结论听起来合理就放过它——你的价值在于挑出「听起来合理但没有依据」的部分。
- 用简洁中文，条目化。
"""


def run_reflection_stream(cfg: dict, source: str, title: str = ""):
    """对一段分析做反思，yield NDJSON 事件（delta / done / error）。"""
    text = (source or "").strip()
    if not text:
        yield {"type": "error", "message": "没有可反思的内容"}
        return
    truncated = len(text) > MAX_SOURCE_CHARS
    if truncated:
        text = text[:MAX_SOURCE_CHARS]
        yield {"type": "status", "message": f"原文较长，已截取前 {MAX_SOURCE_CHARS} 字进行审计"}

    header = f"【待审分析{('：' + title) if title else ''}】\n"
    messages = [
        {"role": "system", "content": REFLECT_PROMPT},
        {"role": "user", "content": header + text + "\n\n请开始审计。"},
    ]

    provider = str(cfg.get("provider", ""))
    buf: list[str] = []
    try:
        if provider.startswith("cli-"):
            content = cli_runtime.run_cli(provider[4:], REFLECT_PROMPT, messages[-1]["content"])
            buf.append(content)
            yield {"type": "delta", "text": content}
        else:
            resp = chat._call_llm_stream(cfg, messages, use_tools=False)
            for delta in chat._iter_sse_deltas(resp):
                piece = delta.get("content")
                if piece:
                    buf.append(piece)
                    yield {"type": "delta", "text": piece}
    except Exception as e:  # noqa: BLE001 — 运行时错误以流内事件上报
        yield {"type": "error", "message": f"反思失败：{e}"}
        return

    yield {"type": "done", "content": "".join(buf).strip(), "truncated": truncated}
