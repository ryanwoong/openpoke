import asyncio
import unittest

from server.execution_trace import TraceStore, trace_store, traced


class ExecutionTraceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        trace_store.clear()

    async def test_background_handoff_keeps_parent_after_parent_finishes(self):
        release = asyncio.Event()

        @traced("execution", lambda: ("Worker", {}))
        async def worker():
            await release.wait()
            return "done"

        @traced("interaction", lambda: ("User prompt", {}))
        async def interaction():
            return asyncio.create_task(worker())

        task = await interaction()
        await asyncio.sleep(0)
        root, child = trace_store.snapshot()
        self.assertEqual(root["status"], "completed")
        self.assertEqual(child["status"], "running")
        self.assertEqual(child["parent_id"], root["id"])
        self.assertEqual(child["run_id"], root["id"])
        release.set()
        await task
        self.assertEqual(trace_store.snapshot()[1]["status"], "completed")

    async def test_concurrent_runs_have_separate_context_and_thread_access_is_nested(self):
        @traced("access", lambda: ("Gmail", {}))
        def access():
            return {"successful": False, "error": "Unavailable"}

        @traced("interaction", lambda label: (label, {}))
        async def run(label):
            return await asyncio.to_thread(access)

        await asyncio.gather(run("first"), run("second"))
        spans = trace_store.snapshot()
        roots = [span for span in spans if span["parent_id"] is None]
        self.assertEqual(len(roots), 2)
        for root in roots:
            children = [span for span in spans if span["parent_id"] == root["id"]]
            self.assertEqual(len(children), 1)
            self.assertEqual(children[0]["run_id"], root["id"])
            self.assertEqual(children[0]["status"], "failed")

    async def test_cancellation_and_exception_are_terminal_and_context_is_restored(self):
        @traced("execution", lambda: ("Timed worker", {}))
        async def worker():
            await asyncio.Future()

        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(worker(), timeout=0.01)
        self.assertEqual(trace_store.snapshot()[0]["status"], "cancelled")

        @traced("tool", lambda: ("Broken", {}))
        def broken():
            raise ValueError("broken")

        with self.assertRaises(ValueError):
            broken()
        span = trace_store.snapshot()[1]
        self.assertEqual(span["status"], "failed")
        self.assertIsNone(span["parent_id"])

    async def test_sensitive_fields_and_model_reasoning_are_omitted(self):
        @traced("model", lambda: ("OpenRouter", {"api_key": "private", "nested": {"Authorization": "private"}}))
        async def request():
            return {"model": "test", "usage": {"total_tokens": 5}, "choices": [{"message": {"reasoning": "private"}}]}

        await request()
        span = trace_store.snapshot()[0]
        self.assertEqual(span["input"]["api_key"], "[redacted]")
        self.assertEqual(span["input"]["nested"]["Authorization"], "[redacted]")
        self.assertEqual(span["output"], {"model": "test", "usage": {"total_tokens": 5}})

    def test_retention_evicts_whole_completed_runs_and_clear_removes_data(self):
        store = TraceStore(limit=2)
        root = store.start("interaction", "first", {})
        child = store.start("tool", "child", {}, root)
        store.finish(root, "completed", None)
        store.finish(child, "completed", None)
        newest = store.start("interaction", "second", {})
        self.assertEqual([span["id"] for span in store.snapshot()], [newest["id"]])
        store.clear()
        self.assertEqual(store.snapshot(), [])

    def test_background_work_after_clear_starts_a_visible_run(self):
        store = TraceStore()
        old = store.start("interaction", "cleared", {})
        store.clear()
        new = store.start("execution", "late background work", {}, old)
        self.assertIsNone(new["parent_id"])
        self.assertEqual(new["run_id"], new["id"])

    async def test_blocking_execution_tool_keeps_event_loop_responsive(self):
        import threading
        from server.agents.execution_agent.runtime import ExecutionAgentRuntime

        release = threading.Event()
        entered = threading.Event()

        def blocking_tool():
            entered.set()
            return {"released": release.wait(timeout=1)}

        runtime = ExecutionAgentRuntime.__new__(ExecutionAgentRuntime)
        runtime.tool_registry = {"blocking": blocking_tool}
        task = asyncio.create_task(runtime._execute_tool("blocking", {}))
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.001)
        try:
            self.assertTrue(entered.is_set())
            self.assertFalse(task.done())
            self.assertEqual(trace_store.snapshot()[0]["status"], "running")
        finally:
            release.set()
        success, result = await task
        self.assertTrue(success)
        self.assertTrue(result["released"])
