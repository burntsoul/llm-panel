import unittest
from unittest.mock import Mock, patch

from config import settings
from ilo_fan import set_ilo_fan_min


class TestIloFan(unittest.TestCase):
    def test_xx_validation_range(self):
        result = set_ilo_fan_min(300)
        self.assertFalse(result["ok"])
        self.assertIn("between 0 and 255", result["error"])

    def test_xx_validation_type(self):
        result = set_ilo_fan_min("abc")
        self.assertFalse(result["ok"])
        self.assertIn("integer", result["error"])

    @patch("ilo_fan.shutil.which")
    def test_sshpass_missing(self, mock_which):
        mock_which.return_value = None
        with patch.object(settings, "ILO_HOST", "192.168.8.35"), patch.object(settings, "ILO_USER", "Administrator"), patch.object(settings, "ILO_PASSWORD", "secret"):
            result = set_ilo_fan_min(150)
        self.assertFalse(result["ok"])
        self.assertIn("sshpass", result["error"])

    @patch("ilo_fan.subprocess.run")
    @patch("ilo_fan.shutil.which")
    def test_success_blank_output_is_ok(self, mock_which, mock_run):
        mock_which.return_value = "/usr/bin/sshpass"
        proc = Mock()
        proc.returncode = 0
        proc.stdout = ""
        proc.stderr = ""
        mock_run.return_value = proc

        with patch.object(settings, "ILO_HOST", "192.168.8.35"), patch.object(settings, "ILO_USER", "Administrator"), patch.object(settings, "ILO_PASSWORD", "secret"), patch.object(settings, "ILO_FAN_PATCH_INDEX", 3):
            result = set_ilo_fan_min(150)
        self.assertTrue(result["ok"])
        self.assertEqual(result["xx"], 150)
        self.assertIn('fan p 3 min 150', result["command"])


if __name__ == "__main__":
    unittest.main()
