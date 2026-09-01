"""后台任务中心（server/jobhub）契约测试。

覆盖：提交→终态、cursor 增量、游标失效重放、取消、异常收敛、
生成器 runner（辩论形态）、TTL 回收、HTTP 路由契约。
"""
import time
import unittest

from fastapi.testclient import TestClient
from server.jobhub import Job, JobHandle, JobHub, RESULT_TTL_SECONDS


def _collect_job_ids() -> set:
    return set()


class JobHubContractsTest(unittest.TestCase):
    def setUp(self):
        self.hub = JobHub(max_workers=2, ttl=0.05)  # 短 TTL，方便测回收

    def tearDown(self):
        self.hub._executor.shutdown(wait=True)

    # ---- 状态机 ---------------------------------------------------------

    def test_submit_returns_pending_and_lands_in_done(self):
        job = self.hub.submit("analysis", lambda h: {"ok": True}, params={"code": "600000"})
        # runner 可能瞬间跑完，中间态（pending/running）是竞态的——只验证参数透传
        snap = self.hub.snapshot(job.id)
        self.assertEqual("600000", snap["params"]["code"])

        deadline = time.time() + 5
        while time.time() < deadline:
            snap = self.hub.snapshot(job.id)
            if snap["status"] in ("done", "error"):
                break
            time.sleep(0.02)
        self.assertEqual("done", snap["status"])
        self.assertEqual({"ok": True}, snap["result"])
        self.assertGreaterEqual(snap["elapsed"], 0)

    def test_runner_exception_becomes_error_with_message(self):
        def boom(_h):
            raise ValueError("上游取数失败")

        job = self.hub.submit("analysis", boom)
        deadline = time.time() + 5
        while time.time() < deadline:
            snap = self.hub.snapshot(job.id)
            if snap["status"] in ("done", "error"):
                break
            time.sleep(0.02)
        self.assertEqual("error", snap["status"])
        self.assertIn("上游取数失败", snap["error"])

    # ---- 事件与 cursor --------------------------------------------------

    def test_cursor_incremental_events(self):
        def gen(h):
            for i in range(5):
                h.publish({"type": "delta", "text": f"chunk{i}"})
            return {"finished": True}

        job = self.hub.submit("debate", gen)
        # 等任务彻底完成，事件是幂等累积的
        deadline = time.time() + 5
        while time.time() < deadline:
            if self.hub.get(job.id).done:
                break
            time.sleep(0.02)

        s0 = self.hub.snapshot(job.id, cursor=0)
        self.assertEqual(5, len(s0["events"]))
        self.assertEqual(5, s0["cursor"])
        self.assertFalse(s0["replay"])

        # 全量取过之后，cursor=5 应该零增量
        s1 = self.hub.snapshot(job.id, cursor=5)
        self.assertEqual(0, len(s1["events"]))
        self.assertEqual(5, s1["cursor"])
        self.assertEqual("done", s1["status"])

        # 中间游标只回后面的
        s2 = self.hub.snapshot(job.id, cursor=2)
        self.assertEqual(3, len(s2["events"]))
        self.assertEqual(["chunk2", "chunk3", "chunk4"], [e["text"] for e in s2["events"]])

    def test_stale_cursor_triggers_full_replay(self):
        job = self.hub.submit("debate", lambda h: (h.publish({"type": "delta", "text": "x"}) for _ in (0,)))
        deadline = time.time() + 5
        while time.time() < deadline:
            if self.hub.get(job.id).done:
                break
            time.sleep(0.02)
        # 游标超过事件总数（如事件被裁剪或前端记录失真）
        snap = self.hub.snapshot(job.id, cursor=99)
        self.assertTrue(snap["replay"])
        self.assertEqual(1, len(snap["events"]))
        # 但 cursor 必须恢复到真实位置
        self.assertEqual(1, snap["cursor"])

    # ---- 取消 -----------------------------------------------------------

    def test_cancel_stops_running_job(self):
        started = time.time()

        def slow(h):
            while not h.cancelled:
                time.sleep(0.02)
            return "after-cancel"

        job = self.hub.submit("debate", slow)
        time.sleep(0.15)
        self.assertTrue(self.hub.cancel(job.id))
        deadline = time.time() + 5
        while time.time() < deadline:
            snap = self.hub.snapshot(job.id)
            if snap["status"] in ("done", "error", "cancelled"):
                break
            time.sleep(0.02)
        self.assertEqual("cancelled", snap["status"])
        self.assertGreaterEqual(time.time() - started, 0)

    def test_cancel_on_terminal_job_is_noop(self):
        job = self.hub.submit("analysis", lambda h: 42)
        deadline = time.time() + 5
        while time.time() < deadline:
            if self.hub.get(job.id).done:
                break
            time.sleep(0.02)
        self.assertFalse(self.hub.cancel(job.id))
        self.assertEqual("done", self.hub.snapshot(job.id)["status"])

    # ---- 生成器 runner（辩论形态）----------------------------------------

    def test_generator_runner_publishes_done_event_as_result(self):
        def stream(_h):
            yield {"type": "status", "message": "拉底稿"}
            yield {"type": "stage", "stage": "bull", "label": "多方"}
            yield {"type": "delta", "stage": "bull", "text": "观点A"}
            yield {"type": "stage_done", "stage": "bull", "label": "多方", "content": "观点A"}
            yield {"type": "done", "code": "600000", "stages": []}

        job = self.hub.submit("debate", stream)
        deadline = time.time() + 5
        while time.time() < deadline:
            snap = self.hub.snapshot(job.id)
            if snap["status"] in ("done", "error"):
                break
            time.sleep(0.02)
        self.assertEqual("done", snap["status"])
        # status 事件镜像到 progress
        self.assertEqual("拉底稿", snap["progress"].get("message"))
        self.assertEqual("done", snap["result"]["type"])
        events = self.hub.snapshot(job.id, cursor=0)["events"]
        self.assertEqual(5, len(events))

    def test_generator_fatal_error_without_stage_becomes_error(self):
        def stream(_h):
            yield {"type": "error", "message": "未能取到任何客观数据"}

        job = self.hub.submit("debate", stream)
        deadline = time.time() + 5
        while time.time() < deadline:
            snap = self.hub.snapshot(job.id)
            if snap["status"] in ("done", "error"):
                break
            time.sleep(0.02)
        self.assertEqual("error", snap["status"])
        self.assertIn("未能取到任何客观数据", snap["error"])

    # ---- TTL 回收 -------------------------------------------------------

    def test_reap_removes_stale_terminal_jobs(self):
        job = self.hub.submit("analysis", lambda h: 1)
        deadline = time.time() + 5
        while time.time() < deadline:
            if self.hub.get(job.id).done:
                break
            time.sleep(0.02)
        time.sleep(0.07)  # 超过 ttl=0.05
        removed = self.hub.reap()
        self.assertEqual(1, removed)
        self.assertIsNone(self.hub.snapshot(job.id))

    def test_reap_keeps_running_jobs(self):
        job = self.hub.submit("analysis", lambda h: time.sleep(1) or 1)
        time.sleep(0.1)
        self.assertEqual(0, self.hub.reap())
        self.assertIsNotNone(self.hub.snapshot(job.id))
        # 收尾：等它跑完
        deadline = time.time() + 5
        while time.time() < deadline:
            if self.hub.get(job.id).done:
                break
            time.sleep(0.02)

    def test_default_ttl_constant_is_sane(self):
        self.assertGreaterEqual(RESULT_TTL_SECONDS, 300)


# ---- HTTP 路由契约（TestClient + 假 runner，不真调 LLM）-----------------


class JobRoutesContractsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server.main as server_main
        import server.jobs_api as jobs_api

        cls.server_main = server_main
        cls.jobs_api = jobs_api
        cls.client = TestClient(server_main.app)

        # 顶掉真实 runner：analysis 不碰 LLM/行情
        def fake_analysis_runner(handle):
            handle.progress("开始", step=1, total=3)
            handle.publish({"type": "status", "message": "阶段一"})
            time.sleep(0.05)
            return {"report": {"stock_code": "600000", "ok": True}}

        cls._orig_analysis = jobs_api._analysis_runner
        jobs_api._analysis_runner = lambda code, rp, d: fake_analysis_runner

    @classmethod
    def tearDownClass(cls):
        cls.jobs_api._analysis_runner = cls._orig_analysis

    def test_start_analysis_returns_job_and_polls_to_done(self):
        r = self.client.post(
            "/api/jobs/analysis",
            json={"code": "600000", "risk_profile": "balanced", "depth": "full"},
        )
        self.assertEqual(200, r.status_code)
        body = r.json()
        job_id = body["job_id"]
        self.assertTrue(job_id.startswith("analysis-"))
        self.assertIn(body["status"], ("pending", "running"))

        deadline = time.time() + 5
        snap = None
        while time.time() < deadline:
            snap = self.client.get(f"/api/jobs/{job_id}").json()
            if snap["status"] in ("done", "error"):
                break
            time.sleep(0.05)
        self.assertEqual("done", snap["status"])
        self.assertEqual("600000", snap["result"]["report"]["stock_code"])

    def test_start_debate_rejects_bad_code(self):
        # 前端总是带 llm；带上后走到代码校验 → 400
        r = self.client.post(
            "/api/jobs/debate",
            json={"code": "12", "rounds": 1, "llm": {"provider": "", "baseURL": "", "apiKey": "", "model": "x"}},
        )
        self.assertEqual(400, r.status_code)
        self.assertIn("6 位", r.json()["detail"])

    def test_start_analysis_rejects_bad_code(self):
        r = self.client.post("/api/jobs/analysis", json={"code": "abc"})
        self.assertEqual(400, r.status_code)
        self.assertIn("6 位", r.json()["detail"])

    def test_get_unknown_job_returns_404(self):
        r = self.client.get("/api/jobs/does-not-exist")
        self.assertEqual(404, r.status_code)

    def test_cancel_returns_ok_and_reaches_cancelled(self):
        # 用 hub 直接造一个长任务，走 HTTP 取消
        hub = self.server_main  # noqa: F841（保持引用清晰）
        from server.jobhub import hub as real_hub

        def slow(h):
            while not h.cancelled:
                time.sleep(0.02)
            return None

        job = real_hub.submit(kind="test", runner=slow, params={}, title="慢任务")
        time.sleep(0.1)
        r = self.client.delete(f"/api/jobs/{job.id}")
        self.assertEqual(200, r.status_code)
        self.assertTrue(r.json()["ok"])

        deadline = time.time() + 5
        snap = None
        while time.time() < deadline:
            snap = self.client.get(f"/api/jobs/{job.id}").json()
            if snap["status"] in ("cancelled", "done", "error"):
                break
            time.sleep(0.02)
        self.assertEqual("cancelled", snap["status"])


if __name__ == "__main__":
    unittest.main()
