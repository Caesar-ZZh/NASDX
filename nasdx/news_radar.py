"""资讯雷达数据层 —— 12 赛道 108 RSS 聚合 + 合规过滤。

移植自 Vibe-Research newsradar，并与 NASDX evidence 层（权威分/新鲜度）衔接。
纯标准库（urllib + xml.etree），零 key、零个股字段，守零标的红线。
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

try:
    from nasdx import evidence  # type: ignore
except ImportError:  # pragma: no cover
    evidence = None

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCES_FILE = os.path.join(HERE, "news_sources.json")
CACHE_DIR = os.path.join(HERE, ".cache")
CACHE_FILE = os.path.join(CACHE_DIR, "radar.json")

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
BEIJING = timezone(timedelta(hours=8))

# ── 合规红线关键词（小写） ───────────────────────────────────────────────────
DEFAULT_REDLINE = ["赌博", "赌场", "竞彩", "六合彩", "彩票预测", "加密", "BTC", "比特币",
                   "ETH", "以太坊", "色情", "色情片", "AV", "成人", "博彩"]


def _strip_html(s: str) -> str:
    """剥离 HTML 标签，合并空白。"""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()


def _local(tag: str) -> str:
    """提取 QName 局部名。"""
    return tag.split("}")[-1]


def _parse_dt(s: str) -> datetime | None:
    """解析各种时间格式，统一返回带时区的 datetime。"""
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s)
    except Exception:
        try:
            dt = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
        except Exception:
            return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _is_compliant(text: str, redline: list[str]) -> bool:
    """合规红线过滤：任意关键词命中则拒绝。"""
    lower = text.lower()
    return not any(str(k).lower() in lower for k in redline)


def _fetch_source(src: dict, per: int, cutoff: datetime | None, redline: list[str]) -> list[dict] | None:
    """抓取单个 RSS 源，返回 items 列表；出错返回 None。"""
    try:
        req = urllib.request.Request(
            src["url"],
            headers={
                "User-Agent": UA,
                "Accept": "application/rss+xml,application/atom+xml,application/xml,text/xml,*/*",
            },
        )
        with urllib.request.urlopen(req, timeout=14) as r:
            raw = r.read()
        # 部分 RSS 在 XML 声明前带换行/BOM；解析前去掉前导空白。
        root = ET.fromstring(raw.lstrip())
        out: list[dict] = []
        for n in [e for e in root.iter() if _local(e.tag) in ("item", "entry")]:
            if len(out) >= per:
                break
            d: dict[str, Any] = {
                "title": "",
                "url": "",
                "time": "",
                "ts": 0,
                "summary": "",
                "source": src["name"],
                "industry": src.get("hint", ""),
                "authority": src.get("authority", 0),
                "freshness": src.get("freshness", 1.0),
            }
            rawtime = ""
            for c in n:
                t = _local(c.tag)
                if t == "title" and not d["title"]:
                    d["title"] = (c.text or "").strip()
                elif t == "link" and not d["url"]:
                    d["url"] = c.get("href") or (c.text or "").strip()
                elif t in ("pubDate", "published", "updated", "date") and not rawtime:
                    rawtime = (c.text or "").strip()
                elif t in ("description", "summary", "content") and not d["summary"]:
                    d["summary"] = _strip_html(c.text or "")[:160]
            if not d["title"]:
                continue
            # 合规过滤
            blob = (d["title"] + " " + d["summary"]).lower()
            if not _is_compliant(blob, redline):
                continue
            dt = _parse_dt(rawtime)
            if dt is not None:
                if cutoff and dt < cutoff:
                    continue
                d["time"] = dt.astimezone(BEIJING).strftime("%m-%d %H:%M")
                d["ts"] = int(dt.timestamp())
                # 新鲜度补算：距现在的时间权重
                age_days = (datetime.now(timezone.utc) - dt).total_seconds() / 86400
                d["freshness"] = max(0.0, min(1.0, 1.0 - age_days / 14.0))
            else:
                d["time"] = "—"
            out.append(d)
        return out
    except Exception:
        return None


def _merge_evidence(items: list[dict], weights: dict[str, float]) -> list[dict]:
    """若有 evidence 层则计算综合得分（权威分 × 新鲜度）。"""
    if evidence is None:
        return items
    merged = []
    for it in items:
        it = dict(it)
        auth = it.get("authority", 0)
        fresh = it.get("freshness", 1.0)
        it["composite_score"] = round(
            auth * weights.get("authority", 0.0) + fresh * weights.get("freshness", 0.0),
            4,
        )
        merged.append(it)
    return merged


def fetch_radar(per_source: int | None = None, recent_days: int | None = None, force: bool = False) -> dict:
    """抓全部源，返回 12 赛道数据并落盘缓存（原子写）。"""
    cfg = json.load(open(SOURCES_FILE, encoding="utf-8"), object_hook=_cfg_hook)
    days = recent_days if recent_days is not None else cfg["fetch"]["recent_days"]
    per = per_source if per_source is not None else cfg["fetch"]["per_source"]
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    redline = [k.lower() for k in cfg.get("redline_keywords", DEFAULT_REDLINE)]
    weights = cfg.get("evidence_weights", {"authority": 0.6, "freshness": 0.4})

    byhint: dict[str, list[dict]] = {}
    for s in cfg["sources"]:
        hint = s.get("hint", "")
        byhint.setdefault(hint, []).append(s)

    industries: list[dict] = []
    tasks: list[tuple[int, dict]] = []
    for i, ind in enumerate(cfg["industries"]):
        pool = byhint.get(ind["key"], [])
        industries.append({
            "key": ind["key"],
            "name": ind["name"],
            "accent": ind.get("accent", "#64748b"),
            "total": len(pool),
            "items": [],
        })
        for s in pool:
            tasks.append((i, s))

    with ThreadPoolExecutor(max_workers=40) as ex:
        results = list(ex.map(lambda t: (t[0], _fetch_source(t[1], per, cutoff, redline)), tasks))

    failed = 0
    for idx, items in results:
        if items is None:
            failed += 1
            continue
        industries[idx]["items"].extend(items)

    # 按赛道内综合得分 × 时间倒序
    for ind in industries:
        ind["items"] = _merge_evidence(ind["items"], weights)
        ind["items"].sort(key=lambda x: (x.get("composite_score", 0), x.get("ts", 0)), reverse=True)
        # 截断 composite_score 用于展示
        for it in ind["items"]:
            it.pop("composite_score", None)

    data: dict[str, Any] = {
        "generated_at": datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M"),
        "recent_days": days,
        "industries": industries,
        "stats": {
            "industries": len(cfg["industries"]),
            "total_sources": len(cfg["sources"]),
            "failed_sources": failed,
            "total_items": sum(len(ind["items"]) for ind in industries),
        },
    }

    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = CACHE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, CACHE_FILE)  # 原子改名，防并发写坏缓存
    return data


def _cfg_hook(dct: dict[str, Any]) -> dict[str, Any]:
    """JSON 反序列化钩子：给每条源注入 industry hint 与默认 authority。"""
    if "url" in dct and "name" in dct:
        dct.setdefault("authority", 0.5)
        dct.setdefault("freshness", 1.0)
    return dct


def load_cache() -> dict | None:
    """读取缓存；若不存在或损坏返回 None。"""
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def skeleton() -> dict:
    """无缓存时返回赛道骨架（空 items），前端提示点刷新。"""
    cfg = json.load(open(SOURCES_FILE, encoding="utf-8"))
    byhint: dict[str, int] = {}
    for s in cfg["sources"]:
        byhint[s.get("hint", "")] = byhint.get(s.get("hint", ""), 0) + 1
    return {
        "generated_at": None,
        "recent_days": cfg.get("fetch", {}).get("recent_days", 7),
        "industries": [
            {
                "key": i["key"],
                "name": i["name"],
                "accent": i.get("accent", "#64748b"),
                "total": byhint.get(i["key"], 0),
                "items": [],
            }
            for i in cfg["industries"]
        ],
        "stats": {"industries": len(cfg["industries"]), "total_sources": len(cfg["sources"])},
    }


def get_radar(force: bool = False, per_source: int | None = None, recent_days: int | None = None) -> dict:
    """入口：优先读缓存，force=True 时强制刷新。"""
    if force:
        return fetch_radar(per_source=per_source, recent_days=recent_days)
    cached = load_cache()
    if cached is not None:
        return cached
    return skeleton()


def get_industry_summary() -> list[dict]:
    """返回各赛道摘要（不含正文），供概览页使用。"""
    radar = get_radar()
    summary = []
    for ind in radar.get("industries", []):
        summary.append({
            "key": ind["key"],
            "name": ind["name"],
            "accent": ind.get("accent", "#64748b"),
            "total_sources": ind["total"],
            "items_count": len(ind.get("items", [])),
            "latest_time": ind["items"][0]["time"] if ind.get("items") else None,
        })
    return summary
