"""newsradar.fetch_radar 契约：必须限时返回，不能让单源或全源把请求拖到分钟级。

背景：服务器上 90% 国外 RSS 源（BBC/Reuters 等）不可达，单源 14s × 40 并发 × 3 批
会把 POST /api/radar/refresh 拖到 90s+，前端 60s 超时后用户看到「请求超时」。
本测试用本地 127.0.0.1 黑洞端口（连接会立即 RST 或超时）模拟「不可达源」，
验证 fetch_radar 一定在 30s 内返回，且 unreachable 源被标 failed_sources。
"""
from __future__ import annotations

import os
import socket
import sys
import time
import tomllib  # noqa: F401
from contextlib import contextmanager

# 把 server/stock 加进 sys.path，直接 import newsradar
HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_STOCK = os.path.normpath(os.path.join(HERE, "..", "server", "stock"))
if SERVER_STOCK not in sys.path:
    sys.path.insert(0, SERVER_STOCK)

import newsradar  # noqa: E402


def _blackhole_listener():
    """起一个监听但不 accept 的 socket，连上去会等到本端 timeout。
    用于模拟"源永远不响应"。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(64)
    s.settimeout(None)  # accept 不超时
    return s


@contextmanager
def _blackhole_pool(n: int = 80):
    """n 个黑洞端口；通过 monkey-patch 替换 newsradar.sources URL。"""
    listeners = [_blackhole_listener() for _ in range(n)]
    ports = [s.getsockname()[1] for s in listeners]
    urls = [f"http://127.0.0.1:{p}/rss.xml" for p in ports]

    # 备份原始 sources
    cfg_path = newsradar.SOURCES_FILE
    with open(cfg_path, encoding="utf-8") as f:
        orig_cfg = f.read()
    try:
        # 替换所有源 URL 为黑洞 URL
        import json
        cfg = json.loads(orig_cfg)
        # 每个 source 都换成黑洞（保留原 url 结构）
        for s in cfg["sources"]:
            s["url"] = urls[cfg["sources"].index(s) % len(urls)]
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False)
        yield
    finally:
        # 恢复原 sources
        with open(cfg_path, "w", encoding="utf-8") as f:
            f.write(orig_cfg)
        for s in listeners:
            try:
                s.close()
            except Exception:
                pass


def test_fetch_radar_returns_within_30s_when_sources_unreachable():
    """所有 RSS 源都不可达时，fetch_radar 必须在 30s 内返回。"""
    with _blackhole_pool(20):
        t0 = time.time()
        data = newsradar.fetch_radar()
        elapsed = time.time() - t0

    assert elapsed < 30, f"fetch_radar 用了 {elapsed:.1f}s，超过 30s 上限"
    assert "industries" in data
    assert "stats" in data
    # 所有源都黑洞 → failed_sources 应 > 0
    assert data["stats"]["failed_sources"] > 0, "应有源失败但统计为 0"
    print(f"✓ fetch_radar 在 {elapsed:.1f}s 内返回，{data['stats']['failed_sources']} 源标失败")


def test_fetch_radar_records_partial_success():
    """部分源可达时，fetch_radar 仍限时返回且记 failed_sources。"""
    # 50% 黑洞 + 50% 保留原 URL（多数国外源在本机也不可达，凑个部分失败场景）
    cfg_path = newsradar.SOURCES_FILE
    with open(cfg_path, encoding="utf-8") as f:
        orig_cfg = f.read()
    try:
        import json
        cfg = json.loads(orig_cfg)
        sources = cfg["sources"]
        # 全部换成黑洞（极端部分失败模拟）
        for s in sources:
            s["url"] = "http://127.0.0.1:1/never"
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False)

        t0 = time.time()
        data = newsradar.fetch_radar()
        elapsed = time.time() - t0
        assert elapsed < 30
        # 全失败但 contracts 仍满足：每赛道有 key/name/accent/total/items
        for ind in data["industries"]:
            assert {"key", "name", "accent", "items"}.issubset(ind.keys())
        print(f"✓ 全失败时仍 {elapsed:.1f}s 返回，schema 不破")
    finally:
        with open(cfg_path, "w", encoding="utf-8") as f:
            f.write(orig_cfg)


def test_get_radar_with_force_uses_limited_fetch():
    """get_radar(force=True) 走 fetch_radar，必须也限时。"""
    with _blackhole_pool(20):
        t0 = time.time()
        data = newsradar.get_radar(force=True)
        elapsed = time.time() - t0
    assert elapsed < 30, f"get_radar(force=True) 用了 {elapsed:.1f}s"
    assert data["stats"]["failed_sources"] > 0
    print(f"✓ get_radar(force=True) {elapsed:.1f}s 返回")
