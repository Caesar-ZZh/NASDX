"""反思审计层 —— 对已写好的投研分析做推理链审计。

职责：
- 接收一段文本 + 可选标题
- 调用 NASDX LLM 层做流式反思
- 产出五块结构化审计（数据支撑 / 缺乏支撑 / 数据使用问题 / 最脆弱一环 / 验证清单）
- 不给出买卖建议、不预测、不排名（守零标的红线）
"""
from __future__ import annotations

import json
from typing import Any, Iterator

from nasdx import llm

MAX_SOURCE_CHARS = 12000

REFLECT_SYSTEM_PROMPT = """你是一名严格的**研究审计员**。下面是一段已经写好的投研分析。
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


def run_reflection_stream(cfg: dict[str, Any], source: str, title: str = "") -> Iterator[dict[str, Any]]:
    """对一段分析做反思，yield NDJSON 事件（delta / done / error / status）。

    cfg 由调用方传入（llm.build_cfg 产物），包含 provider/baseURL/apiKey/model 等。
    运行时异常以内嵌 error 事件上报，不中断流。
    """
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
        {"role": "system", "content": REFLECT_SYSTEM_PROMPT},
        {"role": "user", "content": header + text + "\n\n请开始审计。"},
    ]

    buf: list[str] = []
    try:
        for ev in llm.stream(cfg, messages):
            if ev["type"] == "delta" and ev.get("text"):
                buf.append(ev["text"])
            yield ev
    except Exception as exc:  # noqa: BLE001
        yield {"type": "error", "message": f"反思失败：{exc}"}
        return

    yield {"type": "done", "content": "".join(buf).strip(), "truncated": truncated}


def run_reflection(cfg: dict[str, Any], source: str, title: str = "") -> dict[str, Any]:
    """同步版：攒完所有 delta 后一次性返回。用于非流式场景。"""
    parts: list[str] = []
    truncated = False
    for ev in run_reflection_stream(cfg, source, title):
        if ev["type"] == "delta":
            parts.append(ev.get("text", ""))
        elif ev["type"] == "status":
            truncated = True
    return {"content": "".join(parts).strip(), "truncated": truncated}
