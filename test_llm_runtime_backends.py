import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from config import settings
from llm_runtime_backends import RuntimeBackendController
from llm_runtime_manager import RuntimeTarget, RuntimeTargetError


class RuntimeBackendTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.controller = RuntimeBackendController()
        self.controller.poll_seconds = 0.001

    @staticmethod
    def llama_target():
        return RuntimeTarget(
            provider="llama_cpp",
            model="served-model",
            target_key="llama_cpp:profile-1",
            base_url="http://llama.invalid",
            capacity=2,
            profile_id="profile-1",
        )

    @staticmethod
    def ollama_target():
        return RuntimeTarget(
            provider="ollama",
            model="model:latest",
            target_key="ollama:model:latest",
            base_url="http://ollama.invalid",
        )

    @staticmethod
    async def inline_to_thread(function, *args, **kwargs):
        """Keep mocked provider calls on the test loop; no executor lifecycle is needed."""
        return function(*args, **kwargs)

    async def test_llama_prepare_unloads_ollama_and_stops_other_profiles_before_start(self):
        profile = {"id": "profile-1", "served_model_id": "served-model", "parallel": 2}
        self.controller._vm_ready = AsyncMock()
        self.controller._wait_ollama = AsyncMock()
        self.controller._unload_ollama = AsyncMock(return_value=True)
        self.controller._stop_all_llama = AsyncMock()
        self.controller._wait_llama_ready = AsyncMock(return_value={"capacity": 2})
        with patch("llm_runtime_backends.asyncio.to_thread", side_effect=self.inline_to_thread), patch(
            "llm_runtime_backends.llama_cpp_provider.find_profile", return_value=profile
        ), patch(
            "llm_runtime_backends.llama_cpp_provider.status_for_profile", return_value={"status": "stopped"}
        ), patch("llm_runtime_backends.llama_cpp_provider.start_profile") as start:
            result = await self.controller.prepare(None, self.llama_target())
        self.controller._unload_ollama.assert_awaited_once_with()
        self.controller._stop_all_llama.assert_awaited_once_with(except_profile_id="profile-1")
        start.assert_called_once_with("profile-1", cleanup_runtime=False)
        self.assertEqual(result["capacity"], 2)

    async def test_ollama_prepare_stops_llama_and_preloads_missing_model(self):
        self.controller._vm_ready = AsyncMock()
        self.controller._stop_all_llama = AsyncMock()
        self.controller._wait_ollama = AsyncMock()
        self.controller._unload_ollama = AsyncMock(return_value=True)
        self.controller._loaded_ollama_models = AsyncMock(return_value=[])
        self.controller._preload_ollama = AsyncMock()
        wanted = self.ollama_target()
        await self.controller.prepare(None, wanted)
        self.controller._stop_all_llama.assert_awaited_once_with()
        self.controller._unload_ollama.assert_awaited_once_with(keep=wanted.model)
        self.controller._preload_ollama.assert_awaited_once_with(wanted)

    async def test_ollama_preload_retries_transient_readiness_response(self):
        transient = MagicMock(is_success=False, status_code=503, text="loading")
        ready = MagicMock(is_success=True, status_code=200, text="")
        self.controller._ollama_request = AsyncMock(side_effect=[transient, ready])
        self.controller._loaded_ollama_models = AsyncMock(return_value=["model:latest"])
        await self.controller._preload_ollama(self.ollama_target())
        self.assertEqual(self.controller._ollama_request.await_count, 2)

    async def test_ollama_preload_surfaces_terminal_oom_response(self):
        oom = MagicMock(is_success=False, status_code=500, text="CUDA out of memory")
        self.controller._ollama_request = AsyncMock(return_value=oom)
        with self.assertRaisesRegex(RuntimeTargetError, "CUDA out of memory"):
            await self.controller._preload_ollama(self.ollama_target())

    async def test_force_stop_restarts_ollama_when_unload_cannot_clear(self):
        self.controller._wait_ollama = AsyncMock()
        self.controller._unload_ollama_model = AsyncMock(side_effect=[False, True])
        self.controller._restart_ollama = AsyncMock()
        await self.controller.force_stop_target(self.ollama_target())
        self.controller._restart_ollama.assert_awaited_once_with()
        self.controller._unload_ollama_model.assert_awaited_with("model:latest", attempts=3)

    async def test_windows_gpu_exclusivity_is_terminal(self):
        original = settings.ENFORCE_EXCLUSIVE_VMS
        settings.ENFORCE_EXCLUSIVE_VMS = True
        try:
            with patch("llm_runtime_backends.asyncio.to_thread", side_effect=self.inline_to_thread), patch(
                "llm_runtime_backends.get_vm_status", return_value="running"
            ):
                with self.assertRaisesRegex(RuntimeTargetError, "Windows VM"):
                    await self.controller._vm_ready(require_ssh=False)
        finally:
            settings.ENFORCE_EXCLUSIVE_VMS = original

    async def test_reconcile_retries_unknown_process_state_before_adopting_target(self):
        profile = {
            "id": "profile-1",
            "served_model_id": "served-model",
            "parallel": 2,
            "port": 8081,
        }
        response = MagicMock(is_success=True)
        response.json.return_value = {"models": []}
        self.controller._vm_ready = AsyncMock()
        self.controller._ollama_request = AsyncMock(return_value=response)
        self.controller._wait_llama_ready = AsyncMock(return_value={"capacity": 2})
        with patch("llm_runtime_backends.asyncio.to_thread", side_effect=self.inline_to_thread), patch(
            "llm_runtime_backends.get_vm_status", return_value="running"
        ), patch("llm_runtime_backends.llama_cpp_provider.list_profiles", return_value=[profile]), patch(
            "llm_runtime_backends.llama_cpp_provider.status_for_profile",
            side_effect=[{"status": "unknown"}, {"status": "running"}],
        ) as status_for_profile:
            result = await self.controller.reconcile()
        self.assertEqual(status_for_profile.call_count, 2)
        self.assertEqual(result.target_key, "llama_cpp:profile-1")
        self.assertEqual(result.capacity, 2)

    async def test_reconcile_drains_conflicting_llama_and_ollama_targets(self):
        profile = {
            "id": "profile-1",
            "served_model_id": "served-model",
            "parallel": 1,
            "port": 8081,
        }
        response = MagicMock(is_success=True)
        response.json.return_value = {"models": [{"name": "other-model"}]}
        self.controller._vm_ready = AsyncMock()
        self.controller._ollama_request = AsyncMock(return_value=response)
        self.controller._stop_llama_profile = AsyncMock()
        self.controller._wait_ollama = AsyncMock()
        self.controller._unload_ollama = AsyncMock(return_value=True)
        with patch("llm_runtime_backends.asyncio.to_thread", side_effect=self.inline_to_thread), patch(
            "llm_runtime_backends.get_vm_status", return_value="running"
        ), patch("llm_runtime_backends.llama_cpp_provider.list_profiles", return_value=[profile]), patch(
            "llm_runtime_backends.llama_cpp_provider.status_for_profile", return_value={"status": "running"}
        ):
            result = await self.controller.reconcile()
        self.assertIsNone(result)
        self.controller._stop_llama_profile.assert_awaited_once_with(profile)
        self.controller._unload_ollama.assert_awaited_once_with()

    async def test_reconcile_marks_profiles_stopped_when_vm_is_off(self):
        profile = {"id": "profile-1", "served_model_id": "served-model", "port": 8081}
        response = MagicMock(is_success=False)
        self.controller._ollama_request = AsyncMock(return_value=response)
        with patch("llm_runtime_backends.asyncio.to_thread", side_effect=self.inline_to_thread), patch(
            "llm_runtime_backends.get_vm_status", return_value="stopped"
        ), patch("llm_runtime_backends.llama_cpp_provider.list_profiles", return_value=[profile]), patch(
            "llm_runtime_backends.llama_cpp_provider.mark_profile_stopped"
        ) as mark_stopped:
            result = await self.controller.reconcile()
        self.assertIsNone(result)
        mark_stopped.assert_called_once_with("profile-1")

    def test_model_advertisement_requires_expected_id(self):
        self.assertTrue(self.controller._models_advertise({"data": [{"id": "wanted"}]}, "wanted"))
        self.assertFalse(self.controller._models_advertise({"data": [{"id": "other"}]}, "wanted"))
