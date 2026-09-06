import asyncio
import tempfile
import unittest
from pathlib import Path

from config import settings
from llm_runtime_manager import (
    InferenceScheduler,
    RuntimeRequestCancelled,
    RuntimeTarget,
    RuntimeTargetError,
    cancelled_error_payload,
    sse_cancelled_payload,
)


def target(name: str, capacity: int = 1) -> RuntimeTarget:
    return RuntimeTarget(
        provider="ollama",
        model=name,
        target_key=f"ollama:{name}",
        base_url="http://provider.invalid",
        capacity=capacity,
    )


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.transitions = []
        self.stops = []
        self.forced = []

        async def prepare(previous, wanted):
            self.transitions.append((previous.target_key if previous else None, wanted.target_key))
            return None

        async def stop(wanted):
            self.stops.append(wanted.target_key)

        async def force(wanted):
            self.forced.append(wanted.target_key)

        self.scheduler = InferenceScheduler(str(Path(self.temp.name) / "runtime.lock"))
        self.scheduler.configure(
            prepare=prepare,
            stop_target=stop,
            force_stop_target=force,
            reset=lambda: asyncio.sleep(0),
            reconcile=lambda: asyncio.sleep(0, result=None),
        )
        await self.scheduler.start()

    async def asyncTearDown(self):
        await self.scheduler.stop()
        self.temp.cleanup()

    async def test_capacity_one_serializes_same_target(self):
        first = await self.scheduler.acquire(target("a"), endpoint="/one")
        second_task = asyncio.create_task(self.scheduler.acquire(target("a"), endpoint="/two"))
        await asyncio.sleep(0.02)
        self.assertFalse(second_task.done())
        self.assertEqual(len(self.scheduler.snapshot()["queued"]), 1)
        await first.release()
        second = await asyncio.wait_for(second_task, 1)
        await second.release()

    async def test_capacity_allows_parallel_requests(self):
        first = await self.scheduler.acquire(target("a", 2), endpoint="/one")
        second = await asyncio.wait_for(
            self.scheduler.acquire(target("a", 2), endpoint="/two"), 1
        )
        self.assertEqual(self.scheduler.snapshot()["active_target"]["occupied"], 2)
        third_task = asyncio.create_task(self.scheduler.acquire(target("a", 2), endpoint="/three"))
        await asyncio.sleep(0.02)
        self.assertFalse(third_task.done())
        await first.release()
        third = await asyncio.wait_for(third_task, 1)
        await second.release()
        await third.release()

    async def test_failed_parallel_request_drains_siblings_before_reprepare(self):
        first = await self.scheduler.acquire(target("a", 2), endpoint="/one")
        second = await self.scheduler.acquire(target("a", 2), endpoint="/two")
        waiting = asyncio.create_task(self.scheduler.acquire(target("a", 2), endpoint="/three"))
        await first.release(error="connection lost")
        await asyncio.sleep(0.02)
        self.assertFalse(waiting.done())
        self.assertEqual(self.scheduler.snapshot()["active_target"]["occupied"], 1)
        await second.release()
        third = await asyncio.wait_for(waiting, 1)
        self.assertEqual([item[1] for item in self.transitions], ["ollama:a", "ollama:a"])
        await third.release()

    async def test_fifo_switch_barrier_prevents_active_target_jump(self):
        first_a = await self.scheduler.acquire(target("a", 2), endpoint="/a1")
        waiting_b = asyncio.create_task(self.scheduler.acquire(target("b"), endpoint="/b"))
        late_a = asyncio.create_task(self.scheduler.acquire(target("a", 2), endpoint="/a2"))
        await asyncio.sleep(0.02)
        self.assertFalse(late_a.done())
        await first_a.release()
        permit_b = await asyncio.wait_for(waiting_b, 1)
        self.assertFalse(late_a.done())
        await permit_b.release()
        permit_a = await asyncio.wait_for(late_a, 1)
        await permit_a.release()
        self.assertEqual(
            [item[1] for item in self.transitions],
            ["ollama:a", "ollama:b", "ollama:a"],
        )

    async def test_cancel_queued_returns_operator_cancellation(self):
        first = await self.scheduler.acquire(target("a"), endpoint="/one")
        waiting = asyncio.create_task(self.scheduler.acquire(target("a"), endpoint="/two"))
        await asyncio.sleep(0.02)
        request_id = self.scheduler.snapshot()["queued"][0]["request_id"]
        self.assertEqual(await self.scheduler.cancel_request(request_id), [request_id])
        with self.assertRaises(RuntimeRequestCancelled):
            await waiting
        await first.release()

    async def test_active_cancellation_closes_cooperative_operation(self):
        permit = await self.scheduler.acquire(target("a"), endpoint="/stream", stream=True)
        operation = asyncio.create_task(permit.run(asyncio.sleep(60)))
        await asyncio.sleep(0.01)
        await self.scheduler.cancel_request(permit.request_id)
        with self.assertRaises(RuntimeRequestCancelled):
            await asyncio.wait_for(operation, 1)
        await permit.release()

    async def test_pause_resume_and_drain(self):
        first = await self.scheduler.acquire(target("a"), endpoint="/one")
        await self.scheduler.set_paused("ollama:a", True)
        waiting = asyncio.create_task(self.scheduler.acquire(target("a"), endpoint="/two"))
        await first.release()
        await asyncio.sleep(0.02)
        self.assertFalse(waiting.done())
        await self.scheduler.set_paused("ollama:a", False)
        second = await asyncio.wait_for(waiting, 1)
        await self.scheduler.drain_target("ollama:a")
        await second.release()
        await asyncio.wait_for(self.scheduler.wait_until_inactive("ollama:a"), 1)
        self.assertEqual(self.stops, ["ollama:a"])

    async def test_drain_waits_for_an_inflight_target_transition(self):
        transition_started = asyncio.Event()
        allow_ready = asyncio.Event()

        async def slow_prepare(_previous, _target):
            transition_started.set()
            await allow_ready.wait()

        self.scheduler._prepare = slow_prepare
        waiting_request = asyncio.create_task(self.scheduler.acquire(target("loading"), endpoint="/one"))
        await asyncio.wait_for(transition_started.wait(), 1)
        await self.scheduler.drain_target("ollama:loading")
        waiting_for_drain = asyncio.create_task(self.scheduler.wait_until_inactive("ollama:loading"))
        await asyncio.sleep(0.02)
        self.assertFalse(waiting_for_drain.done())
        allow_ready.set()
        await asyncio.wait_for(waiting_for_drain, 1)
        self.assertEqual(self.stops, ["ollama:loading"])
        await self.scheduler.cancel_queued("ollama:loading")
        with self.assertRaises(RuntimeRequestCancelled):
            await waiting_request

    async def test_force_stop_cancels_running_and_queued_for_target(self):
        first = await self.scheduler.acquire(target("a"), endpoint="/one")
        waiting = asyncio.create_task(self.scheduler.acquire(target("a"), endpoint="/two"))
        await asyncio.sleep(0.02)
        affected_task = asyncio.create_task(self.scheduler.force_stop("ollama:a"))
        with self.assertRaises(RuntimeRequestCancelled):
            await waiting
        await first.release()
        affected = await asyncio.wait_for(affected_task, 1)
        self.assertEqual(len(affected), 2)
        self.assertEqual(self.forced, ["ollama:a"])

    async def test_cancel_queued_can_be_restricted_to_target(self):
        first = await self.scheduler.acquire(target("a"), endpoint="/one")
        waiting_a = asyncio.create_task(self.scheduler.acquire(target("a"), endpoint="/a"))
        waiting_b = asyncio.create_task(self.scheduler.acquire(target("b"), endpoint="/b"))
        await asyncio.sleep(0.02)
        affected = await self.scheduler.cancel_queued("ollama:a")
        self.assertEqual(len(affected), 1)
        with self.assertRaises(RuntimeRequestCancelled):
            await waiting_a
        self.assertFalse(waiting_b.done())
        await first.release()
        second = await asyncio.wait_for(waiting_b, 1)
        await second.release()

    async def test_emergency_reset_cancels_every_request_and_clears_state(self):
        first = await self.scheduler.acquire(target("a"), endpoint="/one")
        waiting = asyncio.create_task(self.scheduler.acquire(target("b"), endpoint="/two"))
        await asyncio.sleep(0.02)
        reset = asyncio.create_task(self.scheduler.emergency_reset())
        with self.assertRaises(RuntimeRequestCancelled):
            await waiting
        await first.release()
        affected = await asyncio.wait_for(reset, 1)
        self.assertEqual(len(affected), 2)
        snapshot = self.scheduler.snapshot()
        self.assertEqual(snapshot["phase"], "idle")
        self.assertIsNone(snapshot["active_target"])

    async def test_snapshot_is_sanitized(self):
        private_target = RuntimeTarget(
            provider="ollama",
            model="a",
            target_key="ollama:a",
            base_url="http://secret-user:secret-password@provider.invalid",
        )
        permit = await self.scheduler.acquire(
            private_target, endpoint="/v1/chat/completions", client_id="client", stream=True
        )
        encoded = repr(self.scheduler.snapshot()).lower()
        for forbidden in ("prompt", "messages", "authorization", "response content", "secret-password"):
            self.assertNotIn(forbidden, encoded)
        await permit.release()

    async def test_terminal_prepare_failure_fails_request(self):
        async def fail(_previous, _target):
            raise RuntimeTargetError("missing model artifact")

        self.scheduler._prepare = fail
        with self.assertRaisesRegex(RuntimeTargetError, "missing model artifact"):
            await self.scheduler.acquire(target("missing"), endpoint="/fail")

    async def test_observed_capacity_clamps_configured_capacity(self):
        async def observed(_previous, _target):
            return {"capacity": 1, "warning": "slot mismatch"}

        self.scheduler._prepare = observed
        first = await self.scheduler.acquire(target("a", 3), endpoint="/one")
        waiting = asyncio.create_task(self.scheduler.acquire(target("a", 3), endpoint="/two"))
        await asyncio.sleep(0.02)
        self.assertFalse(waiting.done())
        snapshot = self.scheduler.snapshot()
        self.assertEqual(snapshot["active_target"]["capacity"], 1)
        self.assertIn("slot mismatch", snapshot["warnings"])
        await first.release()
        second = await asyncio.wait_for(waiting, 1)
        await second.release()

    async def test_revision_wait_returns_new_snapshot(self):
        revision = self.scheduler.snapshot()["revision"]
        waiter = asyncio.create_task(self.scheduler.wait_for_revision(revision, timeout=1))
        await self.scheduler.register_target(target("events"))
        snapshot = await waiter
        self.assertGreater(snapshot["revision"], revision)

    def test_openai_cancellation_shapes(self):
        exc = RuntimeRequestCancelled("request-1")
        payload = cancelled_error_payload(exc)
        self.assertEqual(payload["error"]["type"], "request_cancelled")
        self.assertEqual(payload["error"]["request_id"], "request-1")
        self.assertTrue(sse_cancelled_payload(exc).endswith(b"data: [DONE]\n\n"))

    def test_timeout_defaults_are_infinite(self):
        self.assertEqual(settings.SCHEDULER_QUEUE_TIMEOUT_SECONDS, 0)
        self.assertEqual(settings.SCHEDULER_STARTUP_TIMEOUT_SECONDS, 0)
        self.assertEqual(settings.SCHEDULER_GENERATION_TIMEOUT_SECONDS, 0)


class ProcessLockTests(unittest.IsolatedAsyncioTestCase):
    async def test_second_scheduler_process_lock_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = str(Path(directory) / "runtime.lock")
            first = InferenceScheduler(lock)
            second = InferenceScheduler(lock)
            await first.start()
            try:
                with self.assertRaisesRegex(RuntimeError, "exactly one uvicorn worker"):
                    await second.start()
            finally:
                await first.stop()

    async def test_startup_reconciliation_adopts_verified_target(self):
        with tempfile.TemporaryDirectory() as directory:
            adopted = target("already-loaded", 2)
            scheduler = InferenceScheduler(str(Path(directory) / "runtime.lock"))
            scheduler.configure(
                prepare=lambda _old, _new: asyncio.sleep(0),
                stop_target=lambda _target: asyncio.sleep(0),
                force_stop_target=lambda _target: asyncio.sleep(0),
                reset=lambda: asyncio.sleep(0),
                reconcile=lambda: asyncio.sleep(0, result=adopted),
            )
            await scheduler.start()
            try:
                for _ in range(20):
                    if scheduler.snapshot()["phase"] != "reconciling":
                        break
                    await asyncio.sleep(0.01)
                snapshot = scheduler.snapshot()
                self.assertEqual(snapshot["phase"], "ready")
                self.assertEqual(snapshot["active_target"]["key"], adopted.target_key)
                self.assertEqual(snapshot["active_target"]["capacity"], 2)
            finally:
                await scheduler.stop()
