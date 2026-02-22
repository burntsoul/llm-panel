import unittest
from unittest.mock import Mock, patch

import requests

from config import settings
from gpu_telemetry import get_remote_glances_gpu_telemetry, normalize_glances_gpu_payload


class TestGpuTelemetry(unittest.TestCase):
    def test_one_gpu_payload_normal(self):
        payload = [
            {
                "gpu_id": "nvidia0",
                "name": "Tesla P40",
                "mem": 0.53,
                "proc": 17,
                "temperature": 42,
                "fan_speed": None,
            }
        ]

        result = normalize_glances_gpu_payload(payload, glances_gpu_id="nvidia0")
        self.assertTrue(result["telemetry_ok"])
        self.assertEqual(result["source"], "remote_glances")
        self.assertEqual(result["gpu_id"], "nvidia0")
        self.assertEqual(result["gpu_name"], "Tesla P40")
        self.assertEqual(result["gpu_temp_c"], 42.0)
        self.assertEqual(result["gpu_util_percent"], 17.0)
        self.assertAlmostEqual(result["gpu_mem_util_percent"], 53.0, places=4)
        self.assertIsNone(result["error"])

    def test_missing_temperature(self):
        payload = [{"gpu_id": "nvidia0", "name": "Tesla P40", "mem": 0.2, "proc": 0}]
        result = normalize_glances_gpu_payload(payload, glances_gpu_id="nvidia0")
        self.assertTrue(result["telemetry_ok"])
        self.assertIsNone(result["gpu_temp_c"])

    def test_mem_fraction(self):
        payload = [{"gpu_id": "nvidia0", "name": "Tesla P40", "mem": 0.5, "proc": 0}]
        result = normalize_glances_gpu_payload(payload, glances_gpu_id="nvidia0")
        self.assertEqual(result["gpu_mem_util_percent"], 50.0)

    def test_mem_percent(self):
        payload = [{"gpu_id": "nvidia0", "name": "Tesla P40", "mem": 72, "proc": 0}]
        result = normalize_glances_gpu_payload(payload, glances_gpu_id="nvidia0")
        self.assertEqual(result["gpu_mem_util_percent"], 72.0)

    def test_empty_list(self):
        result = normalize_glances_gpu_payload([], glances_gpu_id="nvidia0")
        self.assertFalse(result["telemetry_ok"])
        self.assertIn("empty", result["error"].lower())

    @patch("gpu_telemetry.requests.get")
    def test_timeout_path(self, mock_get):
        mock_get.side_effect = requests.Timeout("timed out")

        with patch.object(settings, "GLANCES_TIMEOUT_SECONDS", 0.1):
            result = get_remote_glances_gpu_telemetry()

        self.assertFalse(result["telemetry_ok"])
        self.assertIn("timed out", result["error"].lower())

    @patch("gpu_telemetry.requests.get")
    def test_first_entry_used_when_gpu_id_not_set(self, mock_get):
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = [
            {"gpu_id": "nvidia0", "name": "Tesla P40", "mem": 0.1, "proc": 5, "temperature": 33}
        ]
        mock_get.return_value = mock_response

        with patch.object(settings, "GLANCES_GPU_ID", ""):
            result = get_remote_glances_gpu_telemetry()

        self.assertTrue(result["telemetry_ok"])
        self.assertEqual(result["gpu_id"], "nvidia0")


if __name__ == "__main__":
    unittest.main()
