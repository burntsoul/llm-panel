from __future__ import annotations

import asyncio
import datetime
import time
import unittest
from unittest.mock import Mock, patch

import llama_cpp_provider
import llm_server


async def _inline_to_thread(function, /, *args, **kwargs):
    """Keep unit tests deterministic without creating executor threads."""
    return function(*args, **kwargs)


class TestLlmIdleActivity(unittest.TestCase):
    def setUp(self):
        self.old_last_activity = llm_server._last_activity
        self.old_slot_activity = dict(llm_server._slot_activity)
        self.old_cpu_total = llm_server._last_cpu_total

    def tearDown(self):
        llm_server._last_activity = self.old_last_activity
        llm_server._slot_activity = self.old_slot_activity
        llm_server._last_cpu_total = self.old_cpu_total

    def _run(self, coroutine):
        with patch("llm_server.asyncio.to_thread", new=_inline_to_thread):
            return asyncio.run(coroutine)

    def _set_slot_state(
        self,
        state: str,
        *,
        checked_at: float | None = None,
        active_slots: int = 0,
    ) -> None:
        llm_server._slot_activity = {
            "state": state,
            "profile_id": "ling-profile" if state != llama_cpp_provider.SLOT_STATE_NO_SERVER else None,
            "served_model_id": "ling-local" if state != llama_cpp_provider.SLOT_STATE_NO_SERVER else None,
            "active_slots": active_slots,
            "total_slots": 1 if state != llama_cpp_provider.SLOT_STATE_NO_SERVER else 0,
            "error": "probe failed" if state == llama_cpp_provider.SLOT_STATE_UNKNOWN else None,
            "checked_at_monotonic": time.monotonic() if checked_at is None else checked_at,
        }

    def _slot_result(self, state: str, active_slots: int = 0):
        return {
            "state": state,
            "profile_id": "ling-profile" if state != llama_cpp_provider.SLOT_STATE_NO_SERVER else None,
            "served_model_id": "ling-local" if state != llama_cpp_provider.SLOT_STATE_NO_SERVER else None,
            "active_slots": active_slots,
            "total_slots": 1 if state != llama_cpp_provider.SLOT_STATE_NO_SERVER else 0,
            "error": "probe failed" if state == llama_cpp_provider.SLOT_STATE_UNKNOWN else None,
        }

    @patch.object(llm_server.settings, "CPU_BUSY_THRESHOLD_FOR_IDLE", 20.0)
    def test_poller_refreshes_for_busy_slots_even_with_low_cpu(self):
        previous = datetime.datetime.utcnow() - datetime.timedelta(minutes=30)
        llm_server._last_activity = previous

        with patch(
            "llm_server.llama_cpp_provider.probe_active_profile_slots",
            return_value=self._slot_result(llama_cpp_provider.SLOT_STATE_BUSY, active_slots=1),
        ), patch("llm_server.get_llm_server_cpu_total", return_value=7.1):
            result = self._run(llm_server.poll_llm_activity_once())

        self.assertEqual(result["state"], llama_cpp_provider.SLOT_STATE_BUSY)
        self.assertGreater(llm_server._last_activity, previous)
        self.assertEqual(llm_server._last_cpu_total, 7.1)

    @patch.object(llm_server.settings, "CPU_BUSY_THRESHOLD_FOR_IDLE", 20.0)
    def test_poller_refreshes_for_loading_or_high_cpu(self):
        for state, cpu in (
            (llama_cpp_provider.SLOT_STATE_LOADING, 0.0),
            (llama_cpp_provider.SLOT_STATE_IDLE, 25.0),
        ):
            with self.subTest(state=state, cpu=cpu):
                previous = datetime.datetime.utcnow() - datetime.timedelta(minutes=30)
                llm_server._last_activity = previous
                with patch(
                    "llm_server.llama_cpp_provider.probe_active_profile_slots",
                    return_value=self._slot_result(state),
                ), patch("llm_server.get_llm_server_cpu_total", return_value=cpu):
                    self._run(llm_server.poll_llm_activity_once())
                self.assertGreater(llm_server._last_activity, previous)

    @patch.object(llm_server.settings, "CPU_BUSY_THRESHOLD_FOR_IDLE", 20.0)
    def test_poller_does_not_refresh_for_idle_slots_and_low_cpu(self):
        previous = datetime.datetime.utcnow() - datetime.timedelta(minutes=30)
        llm_server._last_activity = previous

        with patch(
            "llm_server.llama_cpp_provider.probe_active_profile_slots",
            return_value=self._slot_result(llama_cpp_provider.SLOT_STATE_IDLE),
        ), patch("llm_server.get_llm_server_cpu_total", return_value=7.1):
            self._run(llm_server.poll_llm_activity_once())

        self.assertEqual(llm_server._last_activity, previous)

    @patch.object(llm_server.settings, "CPU_BUSY_THRESHOLD_FOR_IDLE", 20.0)
    def test_poller_keeps_unknown_fail_safe_without_refreshing_timer(self):
        previous = datetime.datetime.utcnow() - datetime.timedelta(minutes=30)
        llm_server._last_activity = previous

        with patch(
            "llm_server.llama_cpp_provider.probe_active_profile_slots",
            return_value=self._slot_result(llama_cpp_provider.SLOT_STATE_UNKNOWN),
        ), patch("llm_server.get_llm_server_cpu_total", return_value=None):
            self._run(llm_server.poll_llm_activity_once())

        self.assertEqual(llm_server._last_activity, previous)
        self.assertTrue(llm_server._slot_activity_blocks_shutdown())

    def test_busy_loading_unknown_and_stale_states_block_shutdown(self):
        lease_manager = Mock()
        lease_manager.get_active_leases.return_value = []

        for state in (
            llama_cpp_provider.SLOT_STATE_BUSY,
            llama_cpp_provider.SLOT_STATE_LOADING,
            llama_cpp_provider.SLOT_STATE_UNKNOWN,
        ):
            with self.subTest(state=state):
                self._set_slot_state(state, active_slots=1 if state == llama_cpp_provider.SLOT_STATE_BUSY else 0)
                llm_server._last_activity = datetime.datetime.utcnow() - datetime.timedelta(seconds=120)
                with patch.object(llm_server.settings, "LLM_IDLE_SECONDS", 60), patch(
                    "llm_server.get_maintenance_mode", return_value=False
                ), patch("llm_server.get_vm_status", return_value="running"), patch(
                    "lease.get_lease_manager", return_value=lease_manager
                ), patch(
                    "llm_server.llama_cpp_provider.probe_active_profile_slots"
                ) as final_probe, patch("llm_server.shutdown_vm") as shutdown:
                    self.assertFalse(self._run(llm_server.run_idle_shutdown_check()))
                final_probe.assert_not_called()
                shutdown.assert_not_called()

        self._set_slot_state(
            llama_cpp_provider.SLOT_STATE_IDLE,
            checked_at=time.monotonic() - llm_server._SLOT_STALE_SECONDS - 1,
        )
        llm_server._last_activity = datetime.datetime.utcnow() - datetime.timedelta(seconds=120)
        with patch.object(llm_server.settings, "LLM_IDLE_SECONDS", 60), patch(
            "llm_server.get_maintenance_mode", return_value=False
        ), patch("llm_server.get_vm_status", return_value="running"), patch(
            "lease.get_lease_manager", return_value=lease_manager
        ), patch("llm_server.llama_cpp_provider.probe_active_profile_slots") as final_probe, patch(
            "llm_server.shutdown_vm"
        ) as shutdown:
            self.assertFalse(self._run(llm_server.run_idle_shutdown_check()))
        final_probe.assert_not_called()
        shutdown.assert_not_called()

    @patch.object(llm_server.settings, "CPU_BUSY_THRESHOLD_FOR_IDLE", 20.0)
    def test_definitive_idle_or_no_server_allows_shutdown_after_timeout(self):
        lease_manager = Mock()
        lease_manager.get_active_leases.return_value = []

        for state in (
            llama_cpp_provider.SLOT_STATE_IDLE,
            llama_cpp_provider.SLOT_STATE_NO_SERVER,
        ):
            with self.subTest(state=state):
                self._set_slot_state(state)
                llm_server._last_activity = datetime.datetime.utcnow() - datetime.timedelta(seconds=120)
                with patch.object(llm_server.settings, "LLM_IDLE_SECONDS", 60), patch(
                    "llm_server.get_maintenance_mode", return_value=False
                ), patch("llm_server.get_vm_status", return_value="running"), patch(
                    "lease.get_lease_manager", return_value=lease_manager
                ), patch("llm_server.get_llm_server_cpu_total", return_value=7.1), patch(
                    "llm_server.llama_cpp_provider.probe_active_profile_slots",
                    return_value=self._slot_result(state),
                ) as final_probe, patch(
                    "llm_server.shutdown_vm", return_value=(True, "shutdown requested")
                ) as shutdown:
                    self.assertTrue(self._run(llm_server.run_idle_shutdown_check()))
                final_probe.assert_called_once_with(
                    llm_server.settings.LLAMA_CPP_SLOT_PROBE_TIMEOUT_SECONDS
                )
                shutdown.assert_called_once_with(llm_server.settings.LLM_VM_ID, wait_stopped=False)

    @patch.object(llm_server.settings, "CPU_BUSY_THRESHOLD_FOR_IDLE", 20.0)
    def test_final_probe_prevents_shutdown_when_slot_becomes_busy_or_loading(self):
        lease_manager = Mock()
        lease_manager.get_active_leases.return_value = []

        for state in (
            llama_cpp_provider.SLOT_STATE_BUSY,
            llama_cpp_provider.SLOT_STATE_LOADING,
        ):
            with self.subTest(state=state):
                self._set_slot_state(llama_cpp_provider.SLOT_STATE_IDLE)
                llm_server._last_activity = datetime.datetime.utcnow() - datetime.timedelta(seconds=120)
                with patch.object(llm_server.settings, "LLM_IDLE_SECONDS", 60), patch(
                    "llm_server.get_maintenance_mode", return_value=False
                ), patch("llm_server.get_vm_status", return_value="running"), patch(
                    "lease.get_lease_manager", return_value=lease_manager
                ), patch("llm_server.get_llm_server_cpu_total", return_value=7.1), patch(
                    "llm_server.llama_cpp_provider.probe_active_profile_slots",
                    return_value=self._slot_result(
                        state,
                        active_slots=1 if state == llama_cpp_provider.SLOT_STATE_BUSY else 0,
                    ),
                ), patch("llm_server.shutdown_vm") as shutdown:
                    self.assertFalse(self._run(llm_server.run_idle_shutdown_check()))

                shutdown.assert_not_called()
                self.assertGreater(
                    llm_server._last_activity,
                    datetime.datetime.utcnow() - datetime.timedelta(seconds=5),
                )

    @patch.object(llm_server.settings, "CPU_BUSY_THRESHOLD_FOR_IDLE", 20.0)
    def test_supplemental_cpu_prevents_shutdown_for_untracked_server(self):
        lease_manager = Mock()
        lease_manager.get_active_leases.return_value = []
        self._set_slot_state(llama_cpp_provider.SLOT_STATE_NO_SERVER)
        llm_server._last_activity = datetime.datetime.utcnow() - datetime.timedelta(seconds=120)

        with patch.object(llm_server.settings, "LLM_IDLE_SECONDS", 60), patch(
            "llm_server.get_maintenance_mode", return_value=False
        ), patch("llm_server.get_vm_status", return_value="running"), patch(
            "lease.get_lease_manager", return_value=lease_manager
        ), patch("llm_server.get_llm_server_cpu_total", return_value=25.0), patch(
            "llm_server.llama_cpp_provider.probe_active_profile_slots"
        ) as final_probe, patch("llm_server.shutdown_vm") as shutdown:
            self.assertFalse(self._run(llm_server.run_idle_shutdown_check()))

        final_probe.assert_not_called()
        shutdown.assert_not_called()

    def test_lease_and_maintenance_holds_prevent_shutdown(self):
        self._set_slot_state(llama_cpp_provider.SLOT_STATE_IDLE)

        for maintenance, leases in ((True, []), (False, [object()])):
            with self.subTest(maintenance=maintenance, leases=len(leases)):
                llm_server._last_activity = datetime.datetime.utcnow() - datetime.timedelta(seconds=120)
                lease_manager = Mock()
                lease_manager.get_active_leases.return_value = leases
                with patch.object(llm_server.settings, "LLM_IDLE_SECONDS", 60), patch(
                    "llm_server.get_maintenance_mode", return_value=maintenance
                ), patch("lease.get_lease_manager", return_value=lease_manager), patch(
                    "llm_server.shutdown_vm"
                ) as shutdown:
                    self.assertFalse(self._run(llm_server.run_idle_shutdown_check()))
                shutdown.assert_not_called()


if __name__ == "__main__":
    unittest.main()
