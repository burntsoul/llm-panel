from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as app_module
import llama_cpp_provider
import models


class TestLlamaCppProvider(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = llama_cpp_provider.PROVIDER_CONFIG_PATH
        llama_cpp_provider.PROVIDER_CONFIG_PATH = Path(self.tmp.name) / "llama_cpp.json"

    def tearDown(self):
        llama_cpp_provider.PROVIDER_CONFIG_PATH = self.old_path
        self.tmp.cleanup()

    def test_settings_registers_llama_cpp_provider_section(self):
        payload = app_module.api_settings_effective()
        section = payload["sections"]["provider_llama_cpp"]

        self.assertEqual(section["title"], "llama.cpp provider")
        self.assertEqual(section["custom"], "provider_llama_cpp")

    def test_safe_settings_do_not_expose_ssh_key(self):
        llama_cpp_provider.update_provider_settings(
            {
                "ssh_enabled": True,
                "ssh_host": "192.168.8.33",
                "ssh_user": "teemu",
                "ssh_key": "/home/teemu/.ssh/id_ed25519",
            }
        )

        safe = llama_cpp_provider.get_provider_settings_safe()

        self.assertEqual(safe["ssh_key"], "")
        self.assertTrue(safe["ssh_key_configured"])

    def test_safe_settings_mask_and_preserve_hf_token(self):
        llama_cpp_provider.update_provider_settings({"hf_token": "hf_secret"})

        safe = llama_cpp_provider.get_provider_settings_safe()

        self.assertEqual(safe["hf_token"], "")
        self.assertTrue(safe["hf_token_configured"])

        llama_cpp_provider.update_provider_settings({"hf_token": ""})
        raw = llama_cpp_provider.get_provider_settings()
        self.assertEqual(raw["hf_token"], "hf_secret")

    def test_parse_artifact_scan(self):
        rows = llama_cpp_provider.parse_artifact_scan(
            "/models/llama/a.gguf\t1024\n"
            "/models/llama/nested/b.gguf\t2048\n"
            "bad line\n"
        )

        self.assertEqual([row["path"] for row in rows], ["/models/llama/a.gguf", "/models/llama/nested/b.gguf"])
        self.assertEqual(rows[0]["size"], 1024)

    def test_profile_create_and_command_builder(self):
        profile = llama_cpp_provider.upsert_profile(
            {
                "served_model_id": "qwen-local:planner",
                "gguf_path": "/models/llama/qwen.gguf",
                "port": 8087,
                "ctx_size": 262144,
                "n_gpu_layers": 99,
                "flash_attn": True,
                "cache_enabled": True,
                "extra_args": ["--no-mmap"],
            }
        )

        args = llama_cpp_provider.build_llama_server_args(profile)

        self.assertIn("--model", args)
        self.assertIn("/models/llama/qwen.gguf", args)
        self.assertIn("--alias", args)
        self.assertIn("qwen-local:planner", args)
        self.assertIn("--ctx-size", args)
        self.assertIn("262144", args)
        self.assertIn("--flash-attn", args)
        self.assertIn("--cache-prompt", args)
        self.assertIn("--slot-save-path", args)
        self.assertNotIn("--prompt-cache", args)
        self.assertIn("--no-mmap", args)

    def test_profile_update_enables_prompt_cache(self):
        profile = llama_cpp_provider.upsert_profile(
            {
                "served_model_id": "qwen-local:planner",
                "gguf_path": "/models/llama/qwen.gguf",
                "cache_enabled": False,
            }
        )

        updated = llama_cpp_provider.upsert_profile(
            {
                "served_model_id": "qwen-local:planner",
                "gguf_path": "/models/llama/qwen.gguf",
                "cache_enabled": "on",
                "cache_path": "",
            },
            profile_id=profile["id"],
        )

        self.assertTrue(updated["cache_enabled"])
        self.assertTrue(updated["cache_path"].endswith("/qwen-local-planner"))

        disabled = llama_cpp_provider.upsert_profile(
            {
                "served_model_id": "qwen-local:planner",
                "gguf_path": "/models/llama/qwen.gguf",
                "cache_enabled": "false",
                "cache_path": "",
            },
            profile_id=profile["id"],
        )

        self.assertFalse(disabled["cache_enabled"])

    def test_llama_cpp_routing_start_policy_keeps_running_profile_alive(self):
        self.assertFalse(app_module._should_start_llama_cpp_profile({"status": "running"}))
        self.assertTrue(app_module._should_start_llama_cpp_profile({"status": "stopped"}))
        self.assertTrue(app_module._should_start_llama_cpp_profile({"status": "unknown"}))

    def test_runtime_cleanup_skips_hf_download_pid(self):
        command = llama_cpp_provider._cleanup_runtime_pids_remote(
            llama_cpp_provider.get_provider_settings(),
            port=8081,
        )

        self.assertIn("hf-download.pid", command)
        self.assertIn("llama-server", command)
        self.assertIn("fuser -n tcp 8081", command)

    @patch("llama_cpp_provider.run_ssh")
    def test_start_profile_runs_runtime_cleanup_before_launch(self, run_ssh_mock):
        profile = llama_cpp_provider.upsert_profile(
            {
                "served_model_id": "qwen-local:planner",
                "gguf_path": "/models/llama/qwen.gguf",
                "port": 8081,
            }
        )
        run_ssh_mock.return_value = True, "1234"

        llama_cpp_provider.start_profile(profile["id"])

        commands = [call.args[0] for call in run_ssh_mock.call_args_list]
        self.assertTrue(any("fuser -n tcp 8081" in command for command in commands))
        self.assertTrue(any("nohup" in command for command in commands))

    def test_profile_rejects_path_outside_model_dir(self):
        with self.assertRaises(ValueError):
            llama_cpp_provider.upsert_profile(
                {
                    "served_model_id": "bad:path",
                    "gguf_path": "/tmp/model.gguf",
                }
            )

    @patch("llama_cpp_provider.run_ssh")
    def test_delete_artifact_is_constrained_to_model_dir(self, run_ssh_mock):
        run_ssh_mock.return_value = True, ""

        result = llama_cpp_provider.delete_artifact("/models/llama/qwen.gguf")

        self.assertTrue(result["deleted"])
        self.assertIn("rm --", run_ssh_mock.call_args.args[0])
        with self.assertRaises(ValueError):
            llama_cpp_provider.delete_artifact("/tmp/qwen.gguf")

    def test_hf_download_validation_and_target_path(self):
        self.assertEqual(
            llama_cpp_provider.hf_download_target_path(
                "Qwen/Qwen3.6-35B-A3B-GGUF",
                "Qwen3.6-35B-A3B-Q4_K_M.gguf",
            ),
            "/models/llama/Qwen3.6-35B-A3B-GGUF/Qwen3.6-35B-A3B-Q4_K_M.gguf",
        )

        with self.assertRaises(ValueError):
            llama_cpp_provider.validate_hf_repo_id("missing-owner")
        with self.assertRaises(ValueError):
            llama_cpp_provider.validate_hf_filename("../model.gguf")
        with self.assertRaises(ValueError):
            llama_cpp_provider.validate_hf_filename("model.bin")

    def test_hf_download_command_builder(self):
        llama_cpp_provider.update_provider_settings({"hf_token": "hf_secret"})

        command = llama_cpp_provider.build_hf_download_command(
            "Qwen/Qwen3.6-35B-A3B-GGUF",
            "Qwen3.6-35B-A3B-Q4_K_M.gguf",
        )

        self.assertIn('"$hf_cmd" download', command["command"])
        self.assertIn("HF_TOKEN=hf_secret", command["command"])
        self.assertIn("--local-dir", command["command"])
        self.assertEqual(command["target_dir"], "/models/llama/Qwen3.6-35B-A3B-GGUF")
        self.assertTrue(command["target_path"].endswith(".gguf"))
        self.assertIn("nohup", command["remote"])
        self.assertIn("command -v hf", command["remote"])

    @patch("llama_cpp_provider.run_ssh")
    def test_hf_download_rejects_second_active_job(self, run_ssh_mock):
        run_ssh_mock.return_value = True, "1234"

        job = llama_cpp_provider.start_hf_download(
            "Qwen/Qwen3.6-35B-A3B-GGUF",
            "Qwen3.6-35B-A3B-Q4_K_M.gguf",
        )
        self.assertEqual(job["status"], "running")

        run_ssh_mock.return_value = True, "running"
        with self.assertRaises(RuntimeError):
            llama_cpp_provider.start_hf_download(
                "Qwen/Other-GGUF",
                "Other-Q4_K_M.gguf",
            )

    def test_openai_models_include_llama_cpp_profiles(self):
        llama_cpp_provider.upsert_profile(
            {
                "served_model_id": "qwen-local:planner",
                "gguf_path": "/models/llama/qwen.gguf",
            }
        )

        with patch("models._get_raw_models", return_value=[]), patch("models._load_meta", return_value={}):
            data = models.get_models_openai_format()

        ids = {item["id"] for item in data}
        self.assertIn("qwen-local:planner", ids)


if __name__ == "__main__":
    unittest.main()
