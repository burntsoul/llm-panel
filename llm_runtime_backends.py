from __future__ import annotations

import asyncio
import json
import logging
import shlex
from typing import Any, Dict, Optional

import httpx

from config import settings
import llama_cpp_provider
from llm_runtime_manager import RuntimeTarget, RuntimeTargetError
from llm_server import touch_activity
from proxmox import get_vm_status, start_vm


logger = logging.getLogger("llm-agent.runtime.backends")


def resolve_runtime_target(public_model: str, workload: str = "generate") -> tuple[RuntimeTarget, str, str]:
    """Resolve a public model name to a scheduler target and upstream model."""
    from models import get_scheduler_capacity, resolve_model_for_upstream

    public_model = str(public_model or "").strip()
    if not public_model:
        raise ValueError("model field is required")
    profile = llama_cpp_provider.find_profile_by_model(public_model)
    if profile:
        profile_id = str(profile["id"])
        capacity = max(1, int(profile.get("parallel") or 1))
        target = RuntimeTarget(
            provider="llama_cpp",
            model=public_model,
            target_key=f"llama_cpp:{profile_id}",
            base_url=llama_cpp_provider.profile_base_url(profile),
            capacity=capacity,
            profile_id=profile_id,
            workload=workload,
        )
        return target, public_model, public_model
    upstream = resolve_model_for_upstream(public_model)
    target = RuntimeTarget(
        provider="ollama",
        model=upstream,
        target_key=f"ollama:{upstream}",
        base_url=settings.LLM_SERVER_BASE,
        capacity=get_scheduler_capacity(public_model),
        workload=workload,
    )
    return target, public_model, upstream


class RuntimeBackendController:
    """Owns every provider start, stop, unload, preload and readiness decision."""

    def __init__(self) -> None:
        self.poll_seconds = max(0.2, float(settings.LLM_POLL_INTERVAL))

    async def ensure_ollama_available(self) -> None:
        """Wake the host and Ollama daemon for non-inference management calls."""
        await self._wait_ollama()

    async def _vm_ready(self, *, require_ssh: bool) -> None:
        if require_ssh and not bool(llama_cpp_provider.get_provider_settings().get("ssh_enabled")):
            raise RuntimeTargetError("llama.cpp runtime coordination requires provider SSH to be enabled")
        start_requested = False
        while True:
            if settings.ENFORCE_EXCLUSIVE_VMS:
                try:
                    windows = await asyncio.to_thread(get_vm_status, settings.WINDOWS_VM_ID)
                except Exception:
                    await asyncio.sleep(self.poll_seconds)
                    continue
                if windows == "running":
                    raise RuntimeTargetError("Windows VM is running and owns the GPU")
            try:
                llm_state = await asyncio.to_thread(get_vm_status, settings.LLM_VM_ID)
            except Exception:
                await asyncio.sleep(self.poll_seconds)
                continue
            if llm_state != "running":
                if not start_requested:
                    ok, message = await asyncio.to_thread(start_vm, settings.LLM_VM_ID, False)
                    if not ok:
                        raise RuntimeTargetError(f"LLM VM start failed: {message}")
                    start_requested = True
                await asyncio.sleep(self.poll_seconds)
                continue
            if not require_ssh:
                touch_activity()
                return
            ok, _ = await asyncio.to_thread(llama_cpp_provider.run_ssh, "printf llm-agent-ssh-ok", 5)
            if ok:
                touch_activity()
                return
            await asyncio.sleep(self.poll_seconds)

    async def _ollama_request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        timeout = httpx.Timeout(10.0, connect=3.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.request(method, f"{settings.LLM_SERVER_BASE.rstrip('/')}{path}", **kwargs)

    async def _wait_ollama(self) -> None:
        await self._vm_ready(require_ssh=False)
        start_requested = False
        while True:
            try:
                response = await self._ollama_request("GET", "/api/tags")
                if response.is_success:
                    return
                if response.status_code not in {502, 503, 504}:
                    raise RuntimeTargetError(f"Ollama readiness failed: HTTP {response.status_code}: {response.text[:500]}")
            except RuntimeTargetError:
                raise
            except (httpx.TransportError, httpx.TimeoutException):
                pass
            ssh_enabled = bool(llama_cpp_provider.get_provider_settings().get("ssh_enabled"))
            if not start_requested and ssh_enabled:
                await self._vm_ready(require_ssh=True)
                command = f"{shlex.quote(settings.OLLAMA_SYSTEMCTL_PATH)} start {shlex.quote(settings.OLLAMA_SERVICE_NAME)}"
                ok, output = await asyncio.to_thread(llama_cpp_provider.run_ssh, command, 30)
                if not ok:
                    raise RuntimeTargetError(f"Ollama startup command failed: {output}")
                start_requested = True
            await asyncio.sleep(self.poll_seconds)

    async def _loaded_ollama_models(self, *, retry: bool = True) -> Optional[list[str]]:
        while True:
            try:
                response = await self._ollama_request("GET", "/api/ps")
                if response.is_success:
                    payload = response.json()
                    return [
                        str(row.get("name") or row.get("model"))
                        for row in payload.get("models", [])
                        if row.get("name") or row.get("model")
                    ]
                if response.status_code not in {502, 503, 504}:
                    raise RuntimeTargetError(f"Ollama process query failed: HTTP {response.status_code}: {response.text[:500]}")
            except RuntimeTargetError:
                raise
            except (httpx.TransportError, httpx.TimeoutException, ValueError):
                pass
            if not retry:
                return None
            await asyncio.sleep(self.poll_seconds)

    async def _unload_ollama(self, keep: Optional[str] = None, *, attempts: Optional[int] = None) -> bool:
        count = 0
        while True:
            loaded = await self._loaded_ollama_models(retry=attempts is None)
            if loaded is None:
                count += 1
                if attempts is not None and count >= attempts:
                    return False
                await asyncio.sleep(self.poll_seconds)
                continue
            unwanted = [name for name in loaded if not keep or name != keep]
            if not unwanted:
                return True
            for name in unwanted:
                try:
                    response = await self._ollama_request(
                        "POST", "/api/generate", json={"model": name, "keep_alive": 0, "stream": False}
                    )
                except (httpx.TransportError, httpx.TimeoutException):
                    continue
                if not response.is_success and response.status_code not in {502, 503, 504}:
                    raise RuntimeTargetError(f"Ollama unload failed for {name}: HTTP {response.status_code}: {response.text[:500]}")
            count += 1
            if attempts is not None and count >= attempts:
                return False
            await asyncio.sleep(self.poll_seconds)

    async def _unload_ollama_model(self, model: str, *, attempts: int = 3) -> bool:
        for _ in range(max(1, attempts)):
            loaded = await self._loaded_ollama_models(retry=False)
            if loaded is not None and model not in loaded:
                return True
            try:
                response = await self._ollama_request(
                    "POST", "/api/generate", json={"model": model, "keep_alive": 0, "stream": False}
                )
                if not response.is_success and response.status_code not in {404, 502, 503, 504}:
                    raise RuntimeTargetError(
                        f"Ollama unload failed for {model}: HTTP {response.status_code}: {response.text[:500]}"
                    )
            except (httpx.TransportError, httpx.TimeoutException):
                pass
            await asyncio.sleep(self.poll_seconds)
        loaded = await self._loaded_ollama_models(retry=False)
        return loaded is not None and model not in loaded

    async def _stop_llama_profile(self, profile: Dict[str, Any]) -> None:
        profile_id = str(profile["id"])
        status = await asyncio.to_thread(llama_cpp_provider.status_for_profile, profile, 5)
        if status.get("status") == "stopped":
            port_state = await asyncio.to_thread(llama_cpp_provider.profile_port_state, profile, 5)
            if port_state is False:
                await asyncio.to_thread(llama_cpp_provider.finalize_graceful_stop, profile_id)
                return
            if port_state is True:
                try:
                    await asyncio.to_thread(llama_cpp_provider.request_graceful_port_stop, profile)
                except Exception as exc:
                    raise RuntimeTargetError(
                        f"llama.cpp PID for {profile_id} is gone but port {profile.get('port')} "
                        f"could not be stopped safely: {exc}"
                    ) from exc
        if status.get("status") == "unknown":
            raise RuntimeTargetError(f"Unable to verify llama.cpp profile {profile_id} before stopping")
        await asyncio.to_thread(llama_cpp_provider.request_graceful_stop, profile_id)
        while True:
            status = await asyncio.to_thread(llama_cpp_provider.status_for_profile, profile, 5)
            if status.get("status") == "stopped":
                port_state = await asyncio.to_thread(llama_cpp_provider.profile_port_state, profile, 5)
                if port_state is False:
                    await asyncio.to_thread(llama_cpp_provider.finalize_graceful_stop, profile_id)
                    return
            await asyncio.sleep(self.poll_seconds)

    async def _stop_all_llama(self, except_profile_id: Optional[str] = None) -> None:
        profiles = llama_cpp_provider.list_profiles()
        if not profiles:
            return
        await self._vm_ready(require_ssh=True)
        for profile in profiles:
            if str(profile.get("id")) == except_profile_id:
                continue
            status = await asyncio.to_thread(llama_cpp_provider.status_for_profile, profile, 5)
            while status.get("status") == "unknown":
                await asyncio.sleep(self.poll_seconds)
                status = await asyncio.to_thread(llama_cpp_provider.status_for_profile, profile, 5)
            if status.get("status") == "running":
                await self._stop_llama_profile(profile)

    @staticmethod
    def _models_advertise(payload: Any, expected: str) -> bool:
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        return any(isinstance(row, dict) and str(row.get("id")) == expected for row in rows)

    async def _wait_llama_ready(self, profile: Dict[str, Any]) -> Dict[str, Any]:
        base = llama_cpp_provider.profile_base_url(profile).rstrip("/")
        expected = str(profile.get("served_model_id") or "")
        while True:
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0)) as client:
                    response = await client.get(f"{base}/v1/models")
                if response.is_success and self._models_advertise(response.json(), expected):
                    await asyncio.to_thread(llama_cpp_provider.mark_profile_running, str(profile["id"]))
                    break
                if response.is_success:
                    raise RuntimeTargetError(f"llama.cpp advertised the wrong model; expected {expected}")
            except RuntimeTargetError:
                raise
            except (httpx.TransportError, httpx.TimeoutException, ValueError):
                pass
            status = await asyncio.to_thread(llama_cpp_provider.status_for_profile, profile, 5)
            if status.get("status") == "stopped":
                logs = await asyncio.to_thread(llama_cpp_provider.get_profile_logs, str(profile["id"]), 80)
                raise RuntimeTargetError(f"llama-server exited while loading {expected}: {logs[-2000:]}")
            await asyncio.sleep(self.poll_seconds)

        observed: Optional[int] = None
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0)) as client:
                response = await client.get(f"{base}/slots")
            if response.is_success:
                slots = response.json()
                if isinstance(slots, list):
                    observed = len(slots)
                elif isinstance(slots, dict) and isinstance(slots.get("slots"), list):
                    observed = len(slots["slots"])
        except Exception:
            pass
        configured = max(1, int(profile.get("parallel") or 1))
        result: Dict[str, Any] = {}
        if observed:
            result["capacity"] = observed
            if observed != configured:
                result["warning"] = (
                    f"llama.cpp profile {profile['id']} configured parallel={configured}, "
                    f"but /slots reports {observed}; capacity was clamped"
                )
        return result

    async def _preload_ollama(self, target: RuntimeTarget) -> None:
        if target.workload == "embed":
            path, payload = "/api/embed", {"model": target.model, "input": "", "keep_alive": -1}
        else:
            path, payload = "/api/generate", {"model": target.model, "prompt": "", "stream": False, "keep_alive": -1}
        while True:
            try:
                response = await self._ollama_request("POST", path, json=payload)
            except (httpx.TransportError, httpx.TimeoutException):
                await asyncio.sleep(self.poll_seconds)
                continue
            if response.is_success:
                break
            if response.status_code in {502, 503, 504}:
                await asyncio.sleep(self.poll_seconds)
                continue
            raise RuntimeTargetError(
                f"Ollama could not load {target.model}: HTTP {response.status_code}: {response.text[:1000]}"
            )
        loaded = await self._loaded_ollama_models()
        assert loaded is not None
        if target.model not in loaded:
            raise RuntimeTargetError(f"Ollama did not report {target.model} as loaded after preload")

    async def prepare(self, _previous: Optional[RuntimeTarget], target: RuntimeTarget) -> Optional[Dict[str, Any]]:
        touch_activity()
        if target.provider == "llama_cpp":
            await self._vm_ready(require_ssh=True)
            await self._wait_ollama()
            await self._unload_ollama()
            await self._stop_all_llama(except_profile_id=target.profile_id)
            profile = llama_cpp_provider.find_profile(str(target.profile_id or ""))
            if not profile:
                raise RuntimeTargetError(f"llama.cpp profile is missing: {target.profile_id}")
            status = await asyncio.to_thread(llama_cpp_provider.status_for_profile, profile, 5)
            if status.get("status") != "running":
                try:
                    await asyncio.to_thread(
                        llama_cpp_provider.start_profile,
                        str(profile["id"]),
                        cleanup_runtime=False,
                    )
                except Exception as exc:
                    raise RuntimeTargetError(f"llama.cpp startup command failed: {exc}") from exc
            return await self._wait_llama_ready(profile)

        if target.provider == "ollama":
            await self._vm_ready(require_ssh=False)
            await self._stop_all_llama()
            await self._wait_ollama()
            await self._unload_ollama(keep=target.model)
            loaded = await self._loaded_ollama_models()
            if loaded is None or target.model not in loaded:
                await self._preload_ollama(target)
            return None
        raise RuntimeTargetError(f"unsupported runtime provider: {target.provider}")

    async def stop_target(self, target: RuntimeTarget) -> None:
        if target.provider == "llama_cpp" and target.profile_id:
            profile = llama_cpp_provider.find_profile(target.profile_id)
            if profile:
                await self._stop_llama_profile(profile)
        elif target.provider == "ollama":
            await self._wait_ollama()
            await self._unload_ollama()

    async def _restart_ollama(self) -> None:
        await self._vm_ready(require_ssh=True)
        command = f"{shlex.quote(settings.OLLAMA_SYSTEMCTL_PATH)} restart {shlex.quote(settings.OLLAMA_SERVICE_NAME)}"
        ok, output = await asyncio.to_thread(llama_cpp_provider.run_ssh, command, 30)
        if not ok:
            raise RuntimeTargetError(f"Ollama service restart failed: {output}")
        await self._wait_ollama()

    async def force_stop_target(self, target: RuntimeTarget) -> None:
        if target.provider == "llama_cpp" and target.profile_id:
            await asyncio.to_thread(llama_cpp_provider.force_stop_profile, target.profile_id)
            return
        await self._wait_ollama()
        try:
            cleared = await self._unload_ollama_model(target.model, attempts=3)
        except RuntimeTargetError:
            cleared = False
        if not cleared:
            await self._restart_ollama()
            if not await self._unload_ollama_model(target.model, attempts=3):
                raise RuntimeTargetError(f"Ollama model {target.model} remained loaded after service restart")

    async def reset(self) -> None:
        profiles = llama_cpp_provider.list_profiles()
        await self._vm_ready(require_ssh=bool(profiles))
        if profiles:
            await asyncio.to_thread(llama_cpp_provider.cleanup_llama_cpp_runtime)
            for profile in profiles:
                await asyncio.to_thread(llama_cpp_provider.mark_profile_stopped, str(profile["id"]))
        await self._wait_ollama()
        try:
            cleared = await self._unload_ollama(attempts=3)
        except RuntimeTargetError:
            cleared = False
        if not cleared:
            await self._restart_ollama()
            if not await self._unload_ollama(attempts=3):
                raise RuntimeTargetError("Ollama models remained loaded after emergency restart")

    async def reconcile(self) -> Optional[RuntimeTarget]:
        running = []
        orphaned = []
        profiles = llama_cpp_provider.list_profiles()
        if profiles:
            while True:
                try:
                    vm_state = await asyncio.to_thread(get_vm_status, settings.LLM_VM_ID)
                except Exception as exc:
                    logger.warning("LLM VM startup reconciliation probe failed: %s", exc)
                    await asyncio.sleep(self.poll_seconds)
                    continue
                if vm_state in {"running", "stopped"}:
                    break
                await asyncio.sleep(self.poll_seconds)
            if vm_state == "running":
                await self._vm_ready(require_ssh=True)
            else:
                for profile in profiles:
                    await asyncio.to_thread(llama_cpp_provider.mark_profile_stopped, str(profile["id"]))
                profiles = []

        for profile in profiles:
            while True:
                status = await asyncio.to_thread(llama_cpp_provider.status_for_profile, profile, 3)
                if status.get("status") != "unknown":
                    break
                await asyncio.sleep(self.poll_seconds)
            if status.get("status") == "running":
                running.append(profile)
            else:
                while True:
                    port_state = await asyncio.to_thread(llama_cpp_provider.profile_port_state, profile, 3)
                    if port_state is not None:
                        break
                    await asyncio.sleep(self.poll_seconds)
                if port_state is True:
                    orphaned.append(profile)
        loaded: list[str] = []
        try:
            response = await self._ollama_request("GET", "/api/ps")
            if response.is_success:
                loaded = [str(row.get("name") or row.get("model")) for row in response.json().get("models", [])]
        except Exception:
            pass
        running_ports = {int(profile.get("port") or 0) for profile in running}
        orphaned = [profile for profile in orphaned if int(profile.get("port") or 0) not in running_ports]
        if len(running) > 1 or bool(running and loaded) or len(loaded) > 1 or orphaned:
            logger.warning("Conflicting runtime state found at startup; draining it before dispatch")
            for profile in running:
                await self._stop_llama_profile(profile)
            orphan_ports = sorted({int(profile.get("port") or 0) for profile in orphaned})
            for port in orphan_ports:
                candidates = [profile for profile in orphaned if int(profile.get("port") or 0) == port]
                last_error: Optional[Exception] = None
                for profile in candidates:
                    try:
                        await self._stop_llama_profile(profile)
                        last_error = None
                        break
                    except RuntimeTargetError as exc:
                        last_error = exc
                if last_error:
                    raise last_error
            if loaded:
                await self._wait_ollama()
                await self._unload_ollama()
            return None
        if len(running) == 1 and not loaded:
            profile = running[0]
            ready = await self._wait_llama_ready(profile)
            configured_capacity = max(1, int(profile.get("parallel") or 1))
            observed_capacity = int(ready.get("capacity") or configured_capacity)
            if ready.get("warning"):
                logger.warning("%s", ready["warning"])
            return RuntimeTarget(
                provider="llama_cpp",
                model=str(profile.get("served_model_id")),
                target_key=f"llama_cpp:{profile['id']}",
                base_url=llama_cpp_provider.profile_base_url(profile),
                capacity=max(1, min(configured_capacity, observed_capacity)),
                profile_id=str(profile["id"]),
                warning=str(ready.get("warning")) if ready.get("warning") else None,
            )
        if len(loaded) == 1 and not running:
            from models import get_scheduler_capacity
            model = loaded[0]
            return RuntimeTarget(
                provider="ollama", model=model, target_key=f"ollama:{model}",
                base_url=settings.LLM_SERVER_BASE, capacity=get_scheduler_capacity(model)
            )
        return None
