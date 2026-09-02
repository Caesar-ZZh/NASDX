"""服务端安全契约（2026-08-27 审阅 P0 修复）：

1. SPA 静态托管防路径穿越（../ 编码变体 / 绝对路径注入 → 404，不泄露 dist 外文件）；
2. llm_cfg.merge_llm_cfg 的内置 key 锁定策略——换 baseURL 必须自带 key，
   但「前端不填任何配置」永远回退服务端默认 LLM（系统始终有可用模型）；
3. CORS 只允许 base_app 一层配置（VR_ALLOW_ORIGINS 白名单不被外层通配架空）；
4. auto-align workflow 不再定时触发（缺 LLM secrets 时每 4 小时空跑失败）。
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STOCK_SERVER = ROOT / "server" / "stock"
for p in (str(ROOT), str(STOCK_SERVER)):
    if p not in sys.path:
        sys.path.insert(0, p)

import llm_cfg  # noqa: E402


SERVER_CFG = {
    "provider": "",
    "baseURL": "https://apihub.agnes-ai.com/v1",
    "apiKey": "sk-server-side-key",
    "model": "agnes-2.5-flash",
}


class MergeLlmCfgContracts(unittest.TestCase):
    """内置 key 锁定策略：默认兜底必须保留，key 外泄路径必须堵死。"""

    def test_empty_request_falls_back_to_server_default(self):
        """前端什么都不填 → 完整服务端配置（系统始终有默认 LLM）。"""
        merged = llm_cfg.merge_llm_cfg(
            {"provider": "", "baseURL": "", "apiKey": "", "model": ""}, SERVER_CFG
        )
        self.assertEqual(merged["apiKey"], SERVER_CFG["apiKey"])
        self.assertEqual(merged["baseURL"], SERVER_CFG["baseURL"])
        self.assertEqual(merged["model"], SERVER_CFG["model"])

    def test_none_fields_are_treated_as_empty(self):
        merged = llm_cfg.merge_llm_cfg(
            {"provider": None, "baseURL": None, "apiKey": None, "model": None}, SERVER_CFG
        )
        self.assertEqual(merged["apiKey"], SERVER_CFG["apiKey"])
        self.assertEqual(merged["baseURL"], SERVER_CFG["baseURL"])

    def test_same_base_url_locks_api_key_to_server(self):
        """同 baseURL（前端可能传假 key）→ key 无条件服务端。"""
        merged = llm_cfg.merge_llm_cfg(
            {"baseURL": SERVER_CFG["baseURL"], "apiKey": "sk-fake", "model": "other-model"},
            SERVER_CFG,
        )
        self.assertEqual(merged["apiKey"], SERVER_CFG["apiKey"])
        self.assertEqual(merged["model"], "other-model")

    def test_base_url_trailing_slash_is_not_a_different_provider(self):
        merged = llm_cfg.merge_llm_cfg(
            {"baseURL": SERVER_CFG["baseURL"] + "/", "apiKey": "sk-fake"}, SERVER_CFG
        )
        self.assertEqual(merged["apiKey"], SERVER_CFG["apiKey"])

    def test_custom_base_url_with_own_key_is_respected(self):
        """换 provider 且自带完整凭证 → 尊重前端配置。"""
        req = {
            "baseURL": "https://api.deepseek.com",
            "apiKey": "sk-user-own-key",
            "model": "deepseek-chat",
        }
        merged = llm_cfg.merge_llm_cfg(req, SERVER_CFG)
        self.assertEqual(merged["apiKey"], "sk-user-own-key")
        self.assertEqual(merged["baseURL"], "https://api.deepseek.com")

    def test_custom_base_url_without_key_is_rejected(self):
        """P0 修复：换 baseURL 不带 key → 拒绝，绝不把服务端 key 发往任意 URL。"""
        with self.assertRaises(llm_cfg.LlmConfigError):
            llm_cfg.merge_llm_cfg(
                {"baseURL": "https://attacker.example", "apiKey": "", "model": "x"},
                SERVER_CFG,
            )

    def test_rejection_message_guides_users_to_default_path(self):
        try:
            llm_cfg.merge_llm_cfg({"baseURL": "https://attacker.example"}, SERVER_CFG)
        except llm_cfg.LlmConfigError as e:
            self.assertIn("API Key", str(e))
        else:
            self.fail("expected LlmConfigError")

    def test_no_server_config_passes_request_through(self):
        req = {"baseURL": "https://any.example", "apiKey": "", "model": "m"}
        self.assertEqual(llm_cfg.merge_llm_cfg(req, None), req)


try:
    import fastapi  # noqa: F401  仅探测服务端依赖是否就位
except ModuleNotFoundError:  # pragma: no cover - 取决于环境
    _FASTAPI_AVAILABLE = False
else:
    _FASTAPI_AVAILABLE = True


@unittest.skipUnless(
    _FASTAPI_AVAILABLE,
    "serve_spa 契约需要 fastapi/uvicorn，见 server/requirements.txt",
)
class SpaTraversalContracts(unittest.TestCase):
    """serve_spa 路径穿越防护。"""

    @classmethod
    def setUpClass(cls):
        import server.main as server_main  # noqa: E402  带副作用（sys.path/base_app 装配）
        cls.server_main = server_main

    def _serve(self, full_path: str):
        return asyncio.run(self.server_main.serve_spa(full_path))

    def test_parent_traversal_is_404(self):
        from fastapi import HTTPException

        for path in (
            "../package.json",
            "../../server/main.py",
            "..\\package.json",
            "assets/../../server/stock/llm_cfg.py",
        ):
            with self.subTest(path=path):
                with self.assertRaises(HTTPException) as ctx:
                    self._serve(path)
                self.assertEqual(ctx.exception.status_code, 404)

    def test_absolute_path_injection_is_404(self):
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            self._serve("C:/Windows/win.ini")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_unknown_api_path_is_404(self):
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            self._serve("api/does-not-exist")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_spa_deep_link_falls_back_to_index(self):
        from fastapi.responses import FileResponse

        if not self.server_main._DIST.exists():
            # CI 是全新检出，没有构建产物，此时深链返回「前端尚未构建」提示，
            # 属预期行为而非回归。穿越防护由上面几个用例覆盖，与构建状态无关。
            self.skipTest("frontend/dist 未构建，深链回退依赖真实 index.html")

        resp = self._serve("daily-review")
        self.assertIsInstance(resp, FileResponse)
        self.assertTrue(
            str(resp.path).startswith(str(self.server_main._DIST)),
            "fallback response must stay inside dist",
        )


class SingleCorsLayerContracts(unittest.TestCase):
    """CORS 只允许 base_app 一层：外层通配 + credentials 会架空 VR_ALLOW_ORIGINS 白名单。"""

    def test_main_module_does_not_add_cors_middleware(self):
        main_src = (ROOT / "server" / "main.py").read_text(encoding="utf-8")
        self.assertNotIn("CORSMiddleware", main_src)
        self.assertNotIn("allow_credentials", main_src)

    def test_base_app_keeps_origin_whitelist_configuration(self):
        base_src = (STOCK_SERVER / "base_app.py").read_text(encoding="utf-8")
        self.assertIn("CORSMiddleware", base_src)
        self.assertIn("VR_ALLOW_ORIGINS", base_src)

    def test_auto_align_workflow_has_no_schedule_trigger(self):
        wf = (ROOT / ".github" / "workflows" / "auto_align.yml").read_text(encoding="utf-8")
        self.assertNotIn("schedule:", wf)
        self.assertIn("workflow_dispatch:", wf)


def _cors_probe(cors_env_value: str, origin: str = "https://evil.example") -> str:
    """在子进程里以指定 VR_ALLOW_ORIGINS 建 app，返回预检的 ACAO 头。

    子进程隔离：base_app 在 import 时读环境变量挂 CORS 中间件，同一进程内
    reload 难以保证干净；每次探测各起一个解释器，互不污染。
    """
    import os
    import subprocess
    import textwrap

    code = textwrap.dedent(
        """
        import sys
        from pathlib import Path
        ROOT = Path(r"__ROOT__")
        ORIGIN = r"__ORIGIN__"
        for p in (str(ROOT), str(ROOT / "server" / "stock")):
            if p not in sys.path:
                sys.path.insert(0, p)
        from fastapi.testclient import TestClient
        import server.stock.base_app as base_app
        client = TestClient(base_app.app)
        pre = client.options(
            "/api/chat",
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": "POST",
            },
        )
        print(pre.headers.get("access-control-allow-origin", "<absent>"))
        """
    ).replace("__ROOT__", str(ROOT)).replace("__ORIGIN__", origin)
    env = {**os.environ, "VR_ALLOW_ORIGINS": cors_env_value}
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        cwd=str(ROOT),
    )
    if proc.returncode != 0:
        self_fail = f"probe failed rc={proc.returncode}: {proc.stderr[-400:]}"
        raise AssertionError(self_fail)
    return proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "<absent>"


@unittest.skipUnless(
    _FASTAPI_AVAILABLE,
    "CORS 行为契约需要 fastapi（TestClient），见 server/requirements.txt",
)
class CorsDefaultClosedContracts(unittest.TestCase):
    """CORS 默认收紧（2026-08-31）：同源部署不该给任何网站跨站读 API 的口子。"""

    def test_unset_env_mounts_no_cors(self):
        """默认（未设 VR_ALLOW_ORIGINS）→ 预检不回 ACAO，恶意站读不到响应。"""
        self.assertEqual(_cors_probe(""), "<absent>")

    def test_star_wildcard_must_be_explicit(self):
        """显式 "*" 才放开——隐式默认不再是 *。"""
        self.assertEqual(_cors_probe("*"), "*")

    def test_whitelist_echoes_listed_origin(self):
        """白名单内的 origin 正常回显。"""
        self.assertEqual(
            _cors_probe("https://good.example", origin="https://good.example"),
            "https://good.example",
        )

    def test_whitelist_rejects_unknown_origin(self):
        """白名单外的恶意 origin 拿不到 ACAO（浏览器拦截其跨站读取）。"""
        self.assertEqual(_cors_probe("https://good.example"), "<absent>")


if __name__ == "__main__":
    unittest.main()
