import unittest
from unittest.mock import Mock, patch
import subprocess

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
        self.assertEqual(result["exit_code"], 0)

    @patch("ilo_fan.subprocess.run")
    @patch("ilo_fan.shutil.which")
    def test_sshpass_env_and_shell_false(self, mock_which, mock_run):
        mock_which.return_value = "/usr/bin/sshpass"
        proc = Mock()
        proc.returncode = 0
        proc.stdout = ""
        proc.stderr = ""
        mock_run.return_value = proc

        with patch.object(settings, "ILO_HOST", "192.168.8.35"), patch.object(settings, "ILO_USER", "Administrator"), patch.object(settings, "ILO_PASSWORD", "my-password"), patch.object(settings, "ILO_FAN_PATCH_INDEX", 3):
            set_ilo_fan_min(120)

        args, kwargs = mock_run.call_args
        self.assertIsInstance(args[0], list)
        self.assertEqual(args[0][0], "sshpass")
        self.assertEqual(args[0][1], "-e")
        self.assertFalse(kwargs["shell"])
        self.assertIn("SSHPASS", kwargs["env"])
        self.assertEqual(kwargs["env"]["SSHPASS"], "my-password")

    @patch("ilo_fan.subprocess.run")
    @patch("ilo_fan.shutil.which")
    def test_error_classification_auth_failure(self, mock_which, mock_run):
        mock_which.return_value = "/usr/bin/sshpass"
        proc = Mock()
        proc.returncode = 255
        proc.stdout = ""
        proc.stderr = "Permission denied, please try again."
        mock_run.return_value = proc

        with patch.object(settings, "ILO_HOST", "192.168.8.35"), patch.object(settings, "ILO_USER", "Administrator"), patch.object(settings, "ILO_PASSWORD", "secret"):
            result = set_ilo_fan_min(100)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "auth_failure")
        self.assertEqual(result["exit_code"], 255)

    @patch("ilo_fan.subprocess.run")
    @patch("ilo_fan.shutil.which")
    def test_error_classification_host_key(self, mock_which, mock_run):
        mock_which.return_value = "/usr/bin/sshpass"
        proc = Mock()
        proc.returncode = 255
        proc.stdout = ""
        proc.stderr = "Host key verification failed."
        mock_run.return_value = proc

        with patch.object(settings, "ILO_HOST", "192.168.8.35"), patch.object(settings, "ILO_USER", "Administrator"), patch.object(settings, "ILO_PASSWORD", "secret"):
            result = set_ilo_fan_min(100)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "host_key_verification")

    @patch("ilo_fan.subprocess.run")
    @patch("ilo_fan.shutil.which")
    def test_error_classification_timeout(self, mock_which, mock_run):
        mock_which.return_value = "/usr/bin/sshpass"
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["ssh"], timeout=5)

        with patch.object(settings, "ILO_HOST", "192.168.8.35"), patch.object(settings, "ILO_USER", "Administrator"), patch.object(settings, "ILO_PASSWORD", "secret"):
            result = set_ilo_fan_min(90)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "timeout_or_refused")


if __name__ == "__main__":
    unittest.main()
