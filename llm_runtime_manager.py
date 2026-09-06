from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import fcntl
import json
import logging
import os
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Deque, Dict, Optional


logger = logging.getLogger("llm-agent.runtime")


class RuntimeRequestCancelled(Exception):
    """Raised when an operator cancels a queued or running request."""

    def __init__(self, request_id: str, message: str = "Request cancelled by operator") -> None:
        super().__init__(message)
        self.request_id = request_id


class RuntimeTargetError(Exception):
    """A definitive provider/model preparation failure."""


@dataclass(frozen=True)
class RuntimeTarget:
    provider: str
    model: str
    target_key: str
    base_url: str
    capacity: int = 1
    profile_id: Optional[str] = None
    workload: str = "generate"
    warning: Optional[str] = None

    def public_dict(self) -> Dict[str, Any]:
        return {
            "key": self.target_key,
            "provider": self.provider,
            "model": self.model,
            "profile_id": self.profile_id,
            "workload": self.workload,
            "capacity": max(1, int(self.capacity or 1)),
            "warning": self.warning,
        }


@dataclass
class _RequestRecord:
    request_id: str
    target: RuntimeTarget
    endpoint: str
    client_id: str
    stream: bool
    enqueued_at: dt.datetime
    enqueued_monotonic: float
    future: asyncio.Future
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    state: str = "queued"
    started_at: Optional[dt.datetime] = None
    started_monotonic: Optional[float] = None
    cancel_message: str = "Request cancelled by operator"


PrepareCallback = Callable[[Optional[RuntimeTarget], RuntimeTarget], Awaitable[Optional[Dict[str, Any]]]]
StopCallback = Callable[[RuntimeTarget], Awaitable[None]]
ForceStopCallback = Callable[[RuntimeTarget], Awaitable[None]]
ResetCallback = Callable[[], Awaitable[None]]
ReconcileCallback = Callable[[], Awaitable[Optional[RuntimeTarget]]]


async def _noop_prepare(_previous: Optional[RuntimeTarget], _target: RuntimeTarget) -> Optional[Dict[str, Any]]:
    return None


async def _noop_target(_target: RuntimeTarget) -> None:
    return None


async def _noop_reset() -> None:
    return None


async def _noop_reconcile() -> Optional[RuntimeTarget]:
    return None


class RuntimePermit:
    def __init__(self, scheduler: "InferenceScheduler", record: _RequestRecord) -> None:
        self._scheduler = scheduler
        self._record = record
        self._released = False

    @property
    def request_id(self) -> str:
        return self._record.request_id

    @property
    def target(self) -> RuntimeTarget:
        return self._record.target

    @property
    def cancelled(self) -> bool:
        return self._record.cancel_event.is_set()

    async def run(self, awaitable):
        """Run one upstream awaitable while remaining responsive to operator cancellation."""
        operation = asyncio.ensure_future(awaitable)
        cancelled = asyncio.create_task(self._record.cancel_event.wait())
        deadline_task = None
        try:
            from config import settings
            timeout = int(settings.SCHEDULER_GENERATION_TIMEOUT_SECONDS)
        except Exception:
            timeout = 0
        if timeout > 0 and self._record.started_monotonic is not None:
            remaining = max(0.0, timeout - (time.monotonic() - self._record.started_monotonic))
            deadline_task = asyncio.create_task(asyncio.sleep(remaining))
        try:
            waiters = {operation, cancelled}
            if deadline_task:
                waiters.add(deadline_task)
            done, _ = await asyncio.wait(
                waiters,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancelled in done and self._record.cancel_event.is_set():
                operation.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await operation
                raise RuntimeRequestCancelled(self.request_id, self._record.cancel_message)
            if deadline_task and deadline_task in done:
                operation.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await operation
                raise RuntimeTargetError("generation timeout exceeded")
            return await operation
        except asyncio.CancelledError:
            operation.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await operation
            raise
        finally:
            cancelled.cancel()
            if deadline_task:
                deadline_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cancelled
            if deadline_task:
                with contextlib.suppress(asyncio.CancelledError):
                    await deadline_task

    async def release(self, *, error: Optional[str] = None) -> None:
        if self._released:
            return
        self._released = True
        await self._scheduler.release(self.request_id, error=error)

    async def __aenter__(self) -> "RuntimePermit":
        return self

    async def __aexit__(self, exc_type, exc, _tb) -> None:
        await self.release(error=str(exc) if exc else None)


class InferenceScheduler:
    """FIFO, target-aware scheduler for one exclusive GPU runtime."""

    def __init__(self, lock_path: Optional[str] = None) -> None:
        self._condition = asyncio.Condition()
        self._queue: Deque[str] = deque()
        self._records: Dict[str, _RequestRecord] = {}
        self._targets: Dict[str, RuntimeTarget] = {}
        self._capacity_overrides: Dict[str, int] = {}
        self._paused_targets: set[str] = set()
        self._draining_targets: set[str] = set()
        self._invalidated_targets: set[str] = set()
        self._active_target_key: Optional[str] = None
        self._phase = "idle"
        self._last_error: Optional[str] = None
        self._last_transition: Optional[Dict[str, Any]] = None
        self._warnings: Deque[str] = deque(maxlen=20)
        self._history: Deque[Dict[str, Any]] = deque(maxlen=100)
        self._revision = 0
        self._dispatcher_task: Optional[asyncio.Task] = None
        self._transition_task: Optional[asyncio.Task] = None
        self._transition_target_key: Optional[str] = None
        self._stopping = False
        self._lock_path = Path(lock_path or os.getenv("LLM_RUNTIME_LOCK_PATH", "/tmp/llm-agent-runtime-manager.lock"))
        self._lock_handle = None
        self._prepare: PrepareCallback = _noop_prepare
        self._stop_target: StopCallback = _noop_target
        self._force_stop_target: ForceStopCallback = _noop_target
        self._reset: ResetCallback = _noop_reset
        self._reconcile: ReconcileCallback = _noop_reconcile

    def configure(
        self,
        *,
        prepare: PrepareCallback,
        stop_target: StopCallback,
        force_stop_target: ForceStopCallback,
        reset: ResetCallback,
        reconcile: ReconcileCallback,
    ) -> None:
        self._prepare = prepare
        self._stop_target = stop_target
        self._force_stop_target = force_stop_target
        self._reset = reset
        self._reconcile = reconcile

    def _acquire_process_lock(self) -> None:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self._lock_path.open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise RuntimeError(
                f"another llm-agent runtime scheduler owns {self._lock_path}; run exactly one uvicorn worker"
            ) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self._lock_handle = handle

    async def start(self) -> None:
        if self._dispatcher_task and not self._dispatcher_task.done():
            return
        self._acquire_process_lock()
        self._stopping = False
        self._phase = "reconciling"
        self._bump("scheduler_started")
        self._dispatcher_task = asyncio.create_task(self._dispatch_loop(), name="llm-runtime-dispatcher")
        self._transition_task = asyncio.create_task(self._run_reconcile(), name="llm-runtime-reconcile")

    async def _run_reconcile(self) -> None:
        try:
            reconciled = await self._reconcile()
            async with self._condition:
                if reconciled:
                    self._targets[reconciled.target_key] = reconciled
                    self._active_target_key = reconciled.target_key
                    self._phase = "ready"
                    if reconciled.warning:
                        self._warnings.append(reconciled.warning)
                else:
                    self._phase = "idle"
                self._bump("scheduler_reconciled", target=reconciled.target_key if reconciled else None)
                self._condition.notify_all()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            async with self._condition:
                self._last_error = f"startup reconciliation failed: {exc}"
                self._phase = "degraded"
                self._bump("scheduler_reconcile_failed", error=str(exc))
                self._condition.notify_all()
            logger.warning(self._last_error)

    async def stop(self) -> None:
        self._stopping = True
        async with self._condition:
            for request_id in list(self._queue):
                record = self._records.get(request_id)
                if not record:
                    continue
                record.cancel_event.set()
                record.cancel_message = "llm-agent is shutting down"
                record.state = "cancelled"
                if not record.future.done():
                    record.future.cancel()
            self._queue.clear()
            for record in self._running_for():
                record.cancel_message = "llm-agent is shutting down"
                record.cancel_event.set()
                record.state = "cancelling"
            self._condition.notify_all()
        tasks = [task for task in (self._transition_task, self._dispatcher_task) if task]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._transition_task = None
        self._dispatcher_task = None
        if self._lock_handle is not None:
            with contextlib.suppress(Exception):
                fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_UN)
                self._lock_handle.close()
            self._lock_handle = None

    def _bump(self, event: Optional[str] = None, **details: Any) -> None:
        self._revision += 1
        if event:
            row = {"at": dt.datetime.now(dt.timezone.utc).isoformat(), "event": event, **details}
            self._history.append(row)

    def has_work(self) -> bool:
        return bool(
            self._queue
            or any(record.state in {"running", "cancelling"} for record in self._records.values())
            or self._phase in {"switching", "starting", "loading", "unloading", "draining", "force_stopping", "resetting"}
        )

    async def register_target(self, target: RuntimeTarget) -> None:
        async with self._condition:
            self._targets[target.target_key] = target
            self._bump("target_registered", target=target.target_key)
            self._condition.notify_all()

    async def wait_until_inactive(self, target_key: str) -> None:
        """Wait without a deadline until a target has no work and is unloaded."""
        async with self._condition:
            while (
                self._active_target_key == target_key
                or self._transition_target_key == target_key
                or self._running_for(target_key)
            ):
                await self._condition.wait()

    async def acquire(
        self,
        target: RuntimeTarget,
        *,
        endpoint: str,
        client_id: str = "unknown",
        stream: bool = False,
    ) -> RuntimePermit:
        if not self._dispatcher_task or self._dispatcher_task.done():
            raise RuntimeTargetError("runtime scheduler is not running")
        loop = asyncio.get_running_loop()
        request_id = uuid.uuid4().hex[:16]
        record = _RequestRecord(
            request_id=request_id,
            target=target,
            endpoint=endpoint,
            client_id=client_id or "unknown",
            stream=bool(stream),
            enqueued_at=dt.datetime.now(dt.timezone.utc),
            enqueued_monotonic=time.monotonic(),
            future=loop.create_future(),
        )
        async with self._condition:
            self._targets[target.target_key] = target
            self._records[request_id] = record
            self._queue.append(request_id)
            self._bump("request_queued", request_id=request_id, target=target.target_key)
            self._condition.notify_all()
        try:
            try:
                from config import settings
                queue_timeout = int(settings.SCHEDULER_QUEUE_TIMEOUT_SECONDS)
            except Exception:
                queue_timeout = 0
            if queue_timeout > 0:
                await asyncio.wait_for(asyncio.shield(record.future), timeout=queue_timeout)
            else:
                await record.future
        except asyncio.TimeoutError as exc:
            record.future.cancel()
            await self.cancel_request(request_id, message="Queue wait timeout exceeded")
            raise RuntimeTargetError("queue wait timeout exceeded") from exc
        except asyncio.CancelledError:
            record.future.cancel()
            await self.cancel_request(request_id, message="Client disconnected while queued")
            raise
        if record.cancel_event.is_set():
            raise RuntimeRequestCancelled(request_id, record.cancel_message)
        return RuntimePermit(self, record)

    def _running_for(self, target_key: Optional[str] = None) -> list[_RequestRecord]:
        return [
            record
            for record in self._records.values()
            if record.state in {"running", "cancelling"}
            and (target_key is None or record.target.target_key == target_key)
        ]

    def _first_runnable(self) -> Optional[_RequestRecord]:
        for request_id in self._queue:
            record = self._records.get(request_id)
            if not record or record.state != "queued":
                continue
            if record.target.target_key in self._paused_targets:
                continue
            return record
        return None

    def _capacity_for(self, target: RuntimeTarget) -> int:
        return max(1, int(self._capacity_overrides.get(target.target_key, target.capacity or 1)))

    def _grant_for_active_locked(self, first: _RequestRecord) -> bool:
        target_key = first.target.target_key
        capacity = self._capacity_for(first.target)
        available = capacity - len(self._running_for(target_key))
        if available <= 0:
            return False
        changed = False
        for request_id in list(self._queue):
            record = self._records.get(request_id)
            if not record or record.state != "queued":
                with contextlib.suppress(ValueError):
                    self._queue.remove(request_id)
                continue
            if record.target.target_key in self._paused_targets:
                continue
            if record.target.target_key != target_key:
                break
            self._queue.remove(request_id)
            record.state = "running"
            record.started_at = dt.datetime.now(dt.timezone.utc)
            record.started_monotonic = time.monotonic()
            if not record.future.done():
                record.future.set_result(True)
            self._bump("request_started", request_id=request_id, target=target_key)
            changed = True
            available -= 1
            if available <= 0:
                break
        return changed

    async def _dispatch_loop(self) -> None:
        while not self._stopping:
            async with self._condition:
                changed = self._dispatch_locked()
                if not changed:
                    await self._condition.wait()

    def _dispatch_locked(self) -> bool:
        if self._phase in {"force_stopping", "resetting"}:
            return False
        if self._transition_task and not self._transition_task.done():
            return False
        if self._transition_task and self._transition_task.done():
            self._transition_task = None

        active_running = self._running_for(self._active_target_key) if self._active_target_key else []
        if self._active_target_key in self._invalidated_targets:
            if active_running:
                return False
            invalidated_key = self._active_target_key
            self._invalidated_targets.discard(invalidated_key)
            self._active_target_key = None
            self._phase = "idle"
            self._bump("target_invalidated", target=invalidated_key)
            return True
        if self._active_target_key in self._draining_targets and not active_running:
            target = self._targets.get(self._active_target_key)
            if target:
                self._phase = "draining"
                self._transition_target_key = target.target_key
                self._transition_task = asyncio.create_task(self._run_stop(target), name="llm-runtime-drain")
                self._bump("target_draining", target=target.target_key)
                return True

        first = self._first_runnable()
        if first is None:
            return False

        if self._active_target_key != first.target.target_key:
            if self._running_for():
                return False
            previous = self._targets.get(self._active_target_key or "")
            self._phase = "switching"
            self._transition_target_key = first.target.target_key
            self._transition_task = asyncio.create_task(
                self._run_transition(previous, first.target),
                name=f"llm-runtime-switch-{first.target.target_key}",
            )
            self._bump(
                "target_switch_started",
                previous=previous.target_key if previous else None,
                target=first.target.target_key,
            )
            return True

        self._phase = "ready"
        return self._grant_for_active_locked(first)

    async def _run_transition(self, previous: Optional[RuntimeTarget], target: RuntimeTarget) -> None:
        try:
            try:
                from config import settings
                startup_timeout = int(settings.SCHEDULER_STARTUP_TIMEOUT_SECONDS)
            except Exception:
                startup_timeout = 0
            if startup_timeout > 0:
                try:
                    result = await asyncio.wait_for(self._prepare(previous, target), timeout=startup_timeout) or {}
                except asyncio.TimeoutError as exc:
                    raise RuntimeTargetError("startup/readiness timeout exceeded") from exc
            else:
                result = await self._prepare(previous, target) or {}
            async with self._condition:
                observed = result.get("capacity")
                if observed is not None:
                    self._capacity_overrides[target.target_key] = max(1, min(int(target.capacity or 1), int(observed)))
                warning = result.get("warning")
                if warning:
                    self._warnings.append(str(warning))
                self._active_target_key = target.target_key
                self._invalidated_targets.discard(target.target_key)
                self._transition_target_key = None
                self._phase = "ready"
                self._last_error = None
                self._last_transition = {
                    "at": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "previous": previous.target_key if previous else None,
                    "target": target.target_key,
                    "status": "ready",
                }
                self._bump("target_ready", target=target.target_key)
                self._condition.notify_all()
        except asyncio.CancelledError:
            async with self._condition:
                if self._phase not in {"force_stopping", "resetting"}:
                    self._phase = "idle" if not self._active_target_key else "ready"
                if self._transition_target_key == target.target_key:
                    self._transition_target_key = None
                self._bump("target_switch_cancelled", target=target.target_key)
                self._condition.notify_all()
            raise
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            logger.error("Runtime target preparation failed target=%s error=%s", target.target_key, message)
            async with self._condition:
                self._phase = "failed"
                self._active_target_key = None
                if self._transition_target_key == target.target_key:
                    self._transition_target_key = None
                self._last_error = message
                self._last_transition = {
                    "at": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "previous": previous.target_key if previous else None,
                    "target": target.target_key,
                    "status": "failed",
                    "error": message,
                }
                for request_id in list(self._queue):
                    record = self._records.get(request_id)
                    if not record or record.target.target_key != target.target_key:
                        continue
                    self._queue.remove(request_id)
                    record.state = "failed"
                    if not record.future.done():
                        record.future.set_exception(RuntimeTargetError(message))
                    self._records.pop(request_id, None)
                self._bump("target_failed", target=target.target_key, error=message)
                self._condition.notify_all()

    async def _run_stop(self, target: RuntimeTarget) -> None:
        try:
            await self._stop_target(target)
            async with self._condition:
                if self._active_target_key == target.target_key:
                    self._active_target_key = None
                if self._transition_target_key == target.target_key:
                    self._transition_target_key = None
                self._phase = "idle"
                self._bump("target_drained", target=target.target_key)
                self._condition.notify_all()
        except asyncio.CancelledError:
            async with self._condition:
                if self._transition_target_key == target.target_key:
                    self._transition_target_key = None
                self._condition.notify_all()
            raise
        except Exception as exc:
            async with self._condition:
                self._phase = "failed"
                self._last_error = str(exc)
                if self._transition_target_key == target.target_key:
                    self._transition_target_key = None
                self._bump("target_drain_failed", target=target.target_key, error=str(exc))
                self._condition.notify_all()

    async def release(self, request_id: str, *, error: Optional[str] = None) -> None:
        async with self._condition:
            record = self._records.get(request_id)
            if not record or record.state not in {"running", "cancelling"}:
                return
            cancelled = record.cancel_event.is_set()
            record.state = "cancelled" if cancelled else ("failed" if error else "completed")
            if error:
                self._invalidated_targets.add(record.target.target_key)
                self._last_error = error
            self._bump(
                "request_finished",
                request_id=request_id,
                target=record.target.target_key,
                status=record.state,
                error=error,
            )
            self._records.pop(request_id, None)
            if (
                self._active_target_key == record.target.target_key
                and record.target.target_key in self._invalidated_targets
                and not self._running_for(record.target.target_key)
            ):
                self._invalidated_targets.discard(record.target.target_key)
                self._active_target_key = None
                self._phase = "idle"
                self._bump("target_invalidated", target=record.target.target_key)
            self._condition.notify_all()

    async def cancel_request(self, request_id: str, *, message: str = "Request cancelled by operator") -> list[str]:
        async with self._condition:
            record = self._records.get(request_id)
            if not record or record.state not in {"queued", "running", "cancelling"}:
                return []
            record.cancel_message = message
            record.cancel_event.set()
            if record.state == "queued":
                with contextlib.suppress(ValueError):
                    self._queue.remove(request_id)
                record.state = "cancelled"
                if not record.future.done():
                    record.future.set_exception(RuntimeRequestCancelled(request_id, message))
                self._records.pop(request_id, None)
            else:
                record.state = "cancelling"
            self._bump("request_cancelled", request_id=request_id, target=record.target.target_key)
            self._condition.notify_all()
            return [request_id]

    async def cancel_queued(self, target_key: Optional[str] = None) -> list[str]:
        affected: list[str] = []
        for request_id in list(self._queue):
            record = self._records.get(request_id)
            if record and (not target_key or record.target.target_key == target_key):
                affected.extend(await self.cancel_request(request_id))
        return affected

    async def set_paused(self, target_key: str, paused: bool) -> list[str]:
        async with self._condition:
            if paused:
                self._paused_targets.add(target_key)
            else:
                self._paused_targets.discard(target_key)
                self._draining_targets.discard(target_key)
            self._bump("target_paused" if paused else "target_resumed", target=target_key)
            self._condition.notify_all()
        return []

    async def drain_target(self, target_key: str) -> list[str]:
        async with self._condition:
            self._paused_targets.add(target_key)
            self._draining_targets.add(target_key)
            self._bump("target_drain_requested", target=target_key)
            self._condition.notify_all()
        return []

    async def force_stop(self, target_key: str) -> list[str]:
        target = self._targets.get(target_key)
        if not target:
            return []
        async with self._condition:
            self._paused_targets.add(target_key)
            self._phase = "force_stopping"
            self._bump("target_force_stop_started", target=target_key)
            self._condition.notify_all()
        affected = await self.cancel_queued(target_key)
        for record in list(self._running_for(target_key)):
            affected.extend(await self.cancel_request(record.request_id, message="Request cancelled by force-stop"))
        transition = self._transition_task
        if transition and not transition.done():
            transition.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await transition
        try:
            await self._force_stop_target(target)
        except Exception as exc:
            async with self._condition:
                self._phase = "failed"
                self._last_error = str(exc)
                self._bump("target_force_stop_failed", target=target_key, error=str(exc))
                self._condition.notify_all()
            raise
        async with self._condition:
            self._active_target_key = None
            self._invalidated_targets.discard(target_key)
            self._paused_targets.discard(target_key)
            self._draining_targets.discard(target_key)
            self._phase = "idle"
            self._bump("target_force_stopped", target=target_key)
            self._condition.notify_all()
        return list(dict.fromkeys(affected))

    async def emergency_reset(self) -> list[str]:
        async with self._condition:
            self._phase = "resetting"
            self._bump("emergency_reset_started")
            self._condition.notify_all()
        affected = await self.cancel_queued()
        for record in list(self._running_for()):
            affected.extend(await self.cancel_request(record.request_id, message="Request cancelled by emergency reset"))
        transition = self._transition_task
        if transition and not transition.done():
            transition.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await transition
        try:
            await self._reset()
        except Exception as exc:
            async with self._condition:
                self._phase = "failed"
                self._last_error = str(exc)
                self._bump("emergency_reset_failed", error=str(exc))
                self._condition.notify_all()
            raise
        async with self._condition:
            self._active_target_key = None
            self._invalidated_targets.clear()
            self._paused_targets.clear()
            self._draining_targets.clear()
            self._phase = "idle"
            self._last_error = None
            self._bump("emergency_reset_completed")
            self._condition.notify_all()
        return list(dict.fromkeys(affected))

    async def control(
        self,
        action: str,
        *,
        request_id: Optional[str] = None,
        target_key: Optional[str] = None,
    ) -> list[str]:
        logger.warning(
            "Runtime operator action action=%s request_id=%s target=%s",
            action,
            request_id or "none",
            target_key or "none",
        )
        if action == "cancel_request":
            if not request_id:
                raise ValueError("request_id is required")
            return await self.cancel_request(request_id)
        if action == "cancel_queued":
            return await self.cancel_queued(target_key)
        if action == "pause_target":
            if not target_key:
                raise ValueError("target_key is required")
            return await self.set_paused(target_key, True)
        if action == "resume_target":
            if not target_key:
                raise ValueError("target_key is required")
            return await self.set_paused(target_key, False)
        if action == "drain_target":
            if not target_key:
                raise ValueError("target_key is required")
            return await self.drain_target(target_key)
        if action == "force_stop_target":
            if not target_key:
                raise ValueError("target_key is required")
            return await self.force_stop(target_key)
        if action == "emergency_reset":
            return await self.emergency_reset()
        raise ValueError(f"unknown runtime action: {action}")

    def snapshot(self) -> Dict[str, Any]:
        now_mono = time.monotonic()
        queued_rows = []
        position = 0
        for request_id in self._queue:
            record = self._records.get(request_id)
            if not record or record.state != "queued":
                continue
            position += 1
            queued_rows.append(self._record_public(record, now_mono, position=position))
        running_rows = [self._record_public(record, now_mono) for record in self._running_for()]
        active = self._targets.get(self._active_target_key or "")
        active_row = active.public_dict() if active else None
        if active_row:
            active_row.update(
                {
                    "occupied": len(self._running_for(active.target_key)),
                    "capacity": self._capacity_for(active),
                    "paused": active.target_key in self._paused_targets,
                    "draining": active.target_key in self._draining_targets,
                }
            )
        target_keys = set(self._targets) | self._paused_targets | self._draining_targets
        targets = []
        for key in sorted(target_keys):
            target = self._targets.get(key)
            row = target.public_dict() if target else {"key": key}
            if target:
                row["capacity"] = self._capacity_for(target)
            row.update(
                {
                    "paused": key in self._paused_targets,
                    "draining": key in self._draining_targets,
                    "running": len(self._running_for(key)),
                    "queued": sum(1 for item in queued_rows if item["target_key"] == key),
                }
            )
            targets.append(row)
        pending_switches = []
        for row in queued_rows:
            key = row["target_key"]
            if key != self._active_target_key and key not in pending_switches:
                pending_switches.append(key)
        return {
            "revision": self._revision,
            "phase": self._phase,
            "accepting": bool(
                not self._stopping
                and self._dispatcher_task is not None
                and not self._dispatcher_task.done()
            ),
            "active_target": active_row,
            "running": running_rows,
            "queued": queued_rows,
            "targets": targets,
            "pending_switches": pending_switches,
            "warnings": list(self._warnings),
            "last_error": self._last_error,
            "last_transition": self._last_transition,
            "history": list(self._history),
        }

    @staticmethod
    def _record_public(record: _RequestRecord, now_mono: float, position: Optional[int] = None) -> Dict[str, Any]:
        started = record.started_monotonic
        elapsed = now_mono - (started if started is not None else record.enqueued_monotonic)
        wait_seconds = (
            (started - record.enqueued_monotonic)
            if started is not None
            else (now_mono - record.enqueued_monotonic)
        )
        run_seconds = (now_mono - started) if started is not None else None
        return {
            "request_id": record.request_id,
            "target_key": record.target.target_key,
            "provider": record.target.provider,
            "model": record.target.model,
            "endpoint": record.endpoint,
            "client_id": record.client_id,
            "stream": record.stream,
            "state": record.state,
            "position": position,
            "enqueued_at": record.enqueued_at.isoformat(),
            "started_at": record.started_at.isoformat() if record.started_at else None,
            "elapsed_seconds": round(max(0.0, elapsed), 3),
            "wait_seconds": round(max(0.0, wait_seconds), 3),
            "run_seconds": round(max(0.0, run_seconds), 3) if run_seconds is not None else None,
        }

    async def wait_for_revision(self, revision: int, timeout: float = 15.0) -> Dict[str, Any]:
        async with self._condition:
            if self._revision == revision:
                try:
                    await asyncio.wait_for(self._condition.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    pass
            return self.snapshot()


_scheduler = InferenceScheduler()


def get_runtime_scheduler() -> InferenceScheduler:
    return _scheduler


def cancelled_error_payload(exc: RuntimeRequestCancelled) -> Dict[str, Any]:
    return {
        "error": {
            "message": str(exc),
            "type": "request_cancelled",
            "request_id": exc.request_id,
        }
    }


def sse_cancelled_payload(exc: RuntimeRequestCancelled) -> bytes:
    return b"data: " + json.dumps(cancelled_error_payload(exc)).encode("utf-8") + b"\n\ndata: [DONE]\n\n"
