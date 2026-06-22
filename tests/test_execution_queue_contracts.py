import unittest

from nasdx.execution_queue import build_execution_queue


class ExecutionQueueContractTests(unittest.TestCase):
    def test_refresh_required_route_still_has_intraday_guard(self):
        plan = {
            "action_gate": "refresh_required",
            "risk_profile": "balanced",
            "allocation": {"max_total": "0%-10%"},
        }
        audits = [
            {
                "code": "512890",
                "candidate": "512890 红利低波ETF华泰柏瑞",
                "status_code": "refresh_data",
                "audit_status": "先修数据",
            }
        ]

        queue = build_execution_queue(plan, audits)
        stages = {item["stage"] for item in queue}
        intraday = [item for item in queue if item["stage"] == "盘中"]

        self.assertTrue({"盘前", "盘中", "盘后"}.issubset(stages))
        self.assertTrue(intraday)
        self.assertIn("不新增", intraday[0]["action"])
        self.assertIn("数据闸门", intraday[0]["blocker"])


if __name__ == "__main__":
    unittest.main()
