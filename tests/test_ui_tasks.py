import threading
import unittest
import uuid

from nasdx.ui_tasks import (
    register_task,
    set_task_result,
    take_task_result,
    task_alive,
)


class UiTasksTest(unittest.TestCase):
    def test_result_survives_after_background_thread_finishes(self):
        release = threading.Event()
        task_id = f"test_{uuid.uuid4().hex}"
        thread = threading.Thread(target=release.wait, daemon=True)

        register_task(task_id, thread)
        thread.start()
        self.assertTrue(task_alive(task_id))

        expected = {"ok": True, "message": "done"}
        set_task_result(task_id, expected)
        release.set()
        thread.join(timeout=2)

        self.assertFalse(task_alive(task_id))
        self.assertEqual(take_task_result(task_id), expected)
        self.assertIsNone(take_task_result(task_id))


if __name__ == "__main__":
    unittest.main()
