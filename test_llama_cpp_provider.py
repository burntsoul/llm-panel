from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as app_module
import llama_cpp_provider
import models


class JsonRequest:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


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
        self.assertIn("--cache-ram", args)
        self.assertIn("8192", args)
        self.assertIn("--cache-idle-slots", args)
        self.assertIn("--ctx-checkpoints", args)
        self.assertIn("32", args)
        self.assertIn("--checkpoint-min-step", args)
        self.assertIn("256", args)
        self.assertIn("--slot-save-path", args)
        self.assertNotIn("--prompt-cache", args)
        self.assertIn("--no-mmap", args)

    def test_profile_can_override_provider_binary(self):
        llama_cpp_provider.update_provider_settings(
            {"binary_path": "/usr/local/bin/llama-server"}
        )
        profile = llama_cpp_provider.upsert_profile(
            {
                "served_model_id": "ling-local",
                "gguf_path": "/models/llama/ling.gguf",
                "binary_path": "/home/teemu/bin/llama-server-turboquant",
            }
        )

        args = llama_cpp_provider.build_llama_server_args(profile)

        self.assertEqual(args[0], "/home/teemu/bin/llama-server-turboquant")
        self.assertEqual(
            llama_cpp_provider.profile_binary_path(profile),
            "/home/teemu/bin/llama-server-turboquant",
        )

    def test_profile_without_binary_override_inherits_provider_binary(self):
        llama_cpp_provider.update_provider_settings(
            {"binary_path": "/usr/local/bin/llama-server"}
        )
        profile = llama_cpp_provider.upsert_profile(
            {
                "served_model_id": "qwen-local",
                "gguf_path": "/models/llama/qwen.gguf",
            }
        )

        self.assertEqual(
            llama_cpp_provider.build_llama_server_args(profile)[0],
            "/usr/local/bin/llama-server",
        )

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

    def test_profile_update_changes_prompt_cache_ram(self):
        profile = llama_cpp_provider.upsert_profile(
            {
                "served_model_id": "qwen-local:planner",
                "gguf_path": "/models/llama/qwen.gguf",
                "cache_enabled": True,
                "cache_ram": 8192,
            }
        )

        updated = llama_cpp_provider.upsert_profile(
            {
                "served_model_id": "qwen-local:planner",
                "gguf_path": "/models/llama/qwen.gguf",
                "cache_enabled": True,
                "cache_ram": "16384",
            },
            profile_id=profile["id"],
        )

        self.assertEqual(updated["cache_ram"], 16384)

    def test_profile_update_requires_existing_profile_id(self):
        with self.assertRaises(KeyError):
            llama_cpp_provider.upsert_profile(
                {
                    "served_model_id": "qwen-local:planner",
                    "gguf_path": "/models/llama/qwen.gguf",
                },
                profile_id="missing-profile",
            )

        self.assertEqual(llama_cpp_provider.list_profiles(), [])

    def test_profile_update_api_persists_to_existing_profile(self):
        profile = llama_cpp_provider.upsert_profile(
            {
                "served_model_id": "qwen-local:planner",
                "gguf_path": "/models/llama/qwen.gguf",
                "ctx_size": 4096,
                "cache_enabled": False,
                "extra_args": ["--jinja"],
            }
        )
        payload = {
            "served_model_id": "qwen-local:planner-renamed",
            "gguf_path": "/models/llama/qwen.gguf",
            "port": 8099,
            "ctx_size": 8192,
            "cache_enabled": True,
            "cache_path": "/models/llama/.llm-agent-cache/qwen-local-planner-renamed",
            "cache_ram": 16384,
            "extra_args": ["--jinja", "--reasoning-format", "deepseek"],
        }

        with patch("app.llama_cpp_provider.status_for_profile", side_effect=lambda p: p), patch(
            "app.get_model_table_status",
            return_value=[],
        ):
            response = asyncio.run(app_module.api_llama_cpp_update_profile(profile["id"], JsonRequest(payload)))

        self.assertEqual(response["profile"]["id"], profile["id"])
        saved = llama_cpp_provider.find_profile(profile["id"])
        self.assertIsNotNone(saved)
        self.assertEqual(saved["served_model_id"], "qwen-local:planner-renamed")
        self.assertEqual(saved["port"], 8099)
        self.assertEqual(saved["ctx_size"], 8192)
        self.assertTrue(saved["cache_enabled"])
        self.assertEqual(saved["cache_ram"], 16384)
        self.assertEqual(saved["extra_args"], ["--jinja", "--reasoning-format", "deepseek"])
        self.assertEqual(len(llama_cpp_provider.list_profiles()), 1)

    def test_profile_update_api_rejects_missing_profile_id(self):
        with patch("app.llama_cpp_provider.status_for_profile", side_effect=lambda p: p), patch(
            "app.get_model_table_status",
            return_value=[],
        ):
            with self.assertRaises(app_module.HTTPException) as ctx:
                asyncio.run(
                    app_module.api_llama_cpp_update_profile(
                        "missing-profile",
                        JsonRequest(
                            {
                                "served_model_id": "qwen-local:planner",
                                "gguf_path": "/models/llama/qwen.gguf",
                            }
                        ),
                    )
                )

        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(llama_cpp_provider.list_profiles(), [])

    def test_profile_update_api_rejects_non_object_payload(self):
        profile = llama_cpp_provider.upsert_profile(
            {
                "served_model_id": "qwen-local:planner",
                "gguf_path": "/models/llama/qwen.gguf",
            }
        )

        with self.assertRaises(app_module.HTTPException) as ctx:
            asyncio.run(app_module.api_llama_cpp_update_profile(profile["id"], JsonRequest([])))

        self.assertEqual(ctx.exception.status_code, 400)

    def test_llama_cpp_routing_start_policy_keeps_running_profile_alive(self):
        self.assertFalse(app_module._should_start_llama_cpp_profile({"status": "running"}))
        self.assertTrue(app_module._should_start_llama_cpp_profile({"status": "stopped"}))
        self.assertTrue(app_module._should_start_llama_cpp_profile({"status": "unknown"}))

    def test_llama_cpp_readiness_timeout_policy(self):
        self.assertIsNone(app_module._llama_cpp_readiness_timeout(did_start=True))
        self.assertEqual(app_module._llama_cpp_readiness_timeout(did_start=False), 5)

    def test_llama_cpp_request_options_enable_cache_prompt(self):
        payload = {"model": "qwen-local:planner"}

        app_module._apply_llama_cpp_request_options(payload, {"cache_enabled": True})

        self.assertTrue(payload["cache_prompt"])

    def test_llama_cpp_request_options_preserve_explicit_cache_prompt(self):
        payload = {"model": "qwen-local:planner", "cache_prompt": False}

        app_module._apply_llama_cpp_request_options(payload, {"cache_enabled": True})

        self.assertFalse(payload["cache_prompt"])

    def test_llama_cpp_cached_tokens_are_mirrored_to_openai_usage(self):
        payload = {
            "model": "qwen-local:planner",
            "timings": {"cache_n": 512},
            "usage": {"prompt_tokens_details": {"cached_tokens": 0}},
        }

        rewritten = app_module._rewrite_upstream_json(
            payload,
            "qwen-local:planner",
            "qwen-local:planner",
            "llama_cpp",
        )

        self.assertEqual(rewritten["usage"]["prompt_tokens_details"]["cached_tokens"], 512)

    def test_llama_cpp_response_strips_dangling_thinking_tags_from_content(self):
        payload = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": (
                            "I did not use a tool in my previous response.\n"
                            "</thinking>\n\n"
                            "Now let me create the tests:\n\n"
                            "<write_to_file><path>tests/test_example.py</path></write_to_file>"
                        ),
                    }
                }
            ]
        }

        rewritten = app_module._rewrite_upstream_json(payload, "qwen-local:planner", "qwen-local:planner", "llama_cpp")

        content = rewritten["choices"][0]["message"]["content"]
        self.assertNotIn("</thinking>", content)
        self.assertIn("<write_to_file>", content)

    def test_llama_cpp_response_strips_think_tags_from_stream_delta(self):
        line = (
            b'data: {"choices":[{"delta":{"content":"<think>hidden</think>'
            b'<write_to_file><path>x</path></write_to_file>"}}]}\n'
        )

        rewritten = app_module._rewrite_sse_line(line, "qwen-local:planner", "qwen-local:planner", "llama_cpp")

        self.assertNotIn(b"<think>", rewritten)
        self.assertNotIn(b"</think>", rewritten)
        self.assertIn(b"<write_to_file>", rewritten)

    def test_llama_cpp_response_strips_thinking_tags_split_across_stream_chunks(self):
        async def chunks():
            yield b'data: {"choices":[{"index":0,"delta":{"content":"</thi"}}]}\n'
            yield b'data: {"choices":[{"index":0,"delta":{"content":"nking><write_to_file>"}}]}\n'

        async def collect():
            result = []
            async for item in app_module._rewrite_upstream_sse_chunks(
                chunks(),
                "qwen-local:planner",
                "qwen-local:planner",
                "llama_cpp",
            ):
                result.append(item)
            return b"".join(result)

        rewritten = asyncio.run(collect())

        self.assertNotIn(b"</thinking>", rewritten)
        self.assertNotIn(b"</thi", rewritten)
        self.assertIn(b"<write_to_file>", rewritten)

    def test_llama_cpp_response_strips_thinking_tags_from_completion_text(self):
        payload = {"choices": [{"text": "</thinking>\ndef foo():\n    pass\n"}]}

        rewritten = app_module._rewrite_upstream_json(payload, "qwen-local:planner", "qwen-local:planner", "llama_cpp")

        self.assertEqual(rewritten["choices"][0]["text"], "\ndef foo():\n    pass\n")

    @patch.object(app_module.settings, "ENFORCE_EXCLUSIVE_VMS", True)
    @patch("app.llama_cpp_provider.run_ssh", return_value=(True, "llm-agent-ssh-ok"))
    @patch("app.start_vm")
    @patch("app.get_vm_status", side_effect=["stopped", "stopped"])
    def test_llama_cpp_host_wake_starts_llm_vm(self, get_vm_status, start_vm, _ssh):
        start_vm.return_value = (True, "started")

        ok, message = app_module._ensure_llama_cpp_host_running()

        self.assertTrue(ok, message)
        get_vm_status.assert_any_call(app_module.settings.WINDOWS_VM_ID)
        get_vm_status.assert_any_call(app_module.settings.LLM_VM_ID)
        start_vm.assert_called_once_with(app_module.settings.LLM_VM_ID, wait_running=True, timeout_s=90)

    @patch.object(app_module.settings, "ENFORCE_EXCLUSIVE_VMS", True)
    @patch("app.llama_cpp_provider.run_ssh", return_value=(True, "llm-agent-ssh-ok"))
    @patch("app.start_vm")
    @patch("app.get_vm_status", side_effect=["stopped", "running"])
    def test_llama_cpp_host_wake_skips_start_when_running(self, _get_vm_status, start_vm, _ssh):
        ok, message = app_module._ensure_llama_cpp_host_running()

        self.assertTrue(ok, message)
        start_vm.assert_not_called()

    @patch.object(app_module.settings, "ENFORCE_EXCLUSIVE_VMS", True)
    @patch("app.start_vm")
    @patch("app.get_vm_status", return_value="running")
    def test_llama_cpp_host_wake_respects_windows_exclusivity(self, _get_vm_status, start_vm):
        ok, message = app_module._ensure_llama_cpp_host_running()

        self.assertFalse(ok)
        self.assertIn("Windows VM is running", message)
        start_vm.assert_not_called()

    def test_runtime_cleanup_skips_hf_download_pid(self):
        command = llama_cpp_provider._cleanup_runtime_pids_remote(
            llama_cpp_provider.get_provider_settings(),
            port=8081,
        )

        self.assertIn("hf-download.pid", command)
        self.assertIn("llama-server", command)
        self.assertIn("fuser -n tcp 8081", command)

    @patch("llama_cpp_provider.run_ssh")
    def test_get_profile_logs_uses_requested_tail_lines(self, run_ssh_mock):
        profile = llama_cpp_provider.upsert_profile(
            {
                "served_model_id": "qwen-local:planner",
                "gguf_path": "/models/llama/qwen.gguf",
            }
        )
        run_ssh_mock.return_value = True, "log lines"

        logs = llama_cpp_provider.get_profile_logs(profile["id"], lines=777)

        self.assertEqual(logs, "log lines")
        self.assertIn("tail -n 777", run_ssh_mock.call_args.args[0])

    def test_api_llama_cpp_profile_log_lines_are_clamped(self):
        self.assertEqual(app_module._clamp_llama_cpp_log_lines(5), 50)
        self.assertEqual(app_module._clamp_llama_cpp_log_lines(500), 500)
        self.assertEqual(app_module._clamp_llama_cpp_log_lines(5000), 2000)

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

    def test_hf_download_expands_complete_multipart_set(self):
        filenames = llama_cpp_provider.expand_hf_filenames(
            "UD-Q4_K_XL/Laguna-S-2.1-UD-Q4_K_XL-00002-of-00003.gguf"
        )

        self.assertEqual(
            filenames,
            [
                "UD-Q4_K_XL/Laguna-S-2.1-UD-Q4_K_XL-00001-of-00003.gguf",
                "UD-Q4_K_XL/Laguna-S-2.1-UD-Q4_K_XL-00002-of-00003.gguf",
                "UD-Q4_K_XL/Laguna-S-2.1-UD-Q4_K_XL-00003-of-00003.gguf",
            ],
        )
        self.assertEqual(
            llama_cpp_provider.expand_hf_filenames("model-Q4_K_M.gguf"),
            ["model-Q4_K_M.gguf"],
        )
        with self.assertRaises(ValueError):
            llama_cpp_provider.expand_hf_filenames("model-00004-of-00003.gguf")

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

    def test_hf_download_command_builder_includes_all_multipart_shards(self):
        command = llama_cpp_provider.build_hf_download_command(
            "unsloth/Laguna-S-2.1-GGUF",
            "UD-Q4_K_XL/Laguna-S-2.1-UD-Q4_K_XL-00002-of-00003.gguf",
        )

        self.assertEqual(len(command["filenames"]), 3)
        self.assertEqual(len(command["target_paths"]), 3)
        for shard in range(1, 4):
            self.assertIn(
                f"Laguna-S-2.1-UD-Q4_K_XL-{shard:05d}-of-00003.gguf",
                command["command"],
            )

    @patch("llama_cpp_provider.run_ssh")
    def test_hf_download_completion_requires_every_multipart_shard(self, run_ssh_mock):
        run_ssh_mock.return_value = True, "completed"
        target_paths = [
            f"/models/llama/model-{shard:05d}-of-00003.gguf"
            for shard in range(1, 4)
        ]

        refreshed = llama_cpp_provider.refresh_download_job_status(
            {
                "status": "running",
                "pid_path": "/tmp/hf-download.pid",
                "target_path": target_paths[0],
                "target_paths": target_paths,
            }
        )

        self.assertEqual(refreshed["status"], "completed")
        remote = run_ssh_mock.call_args.args[0]
        for path in target_paths:
            self.assertIn(path, remote)
        self.assertEqual(remote.count("test -f /models/llama/model-"), 3)

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
                "ctx_size": 32768,
            }
        )

        with patch("models._get_raw_models", return_value=[]), patch("models._load_meta", return_value={}), patch(
            "models._get_ollama_contexts", return_value={}
        ):
            data = models.get_models_openai_format()

        entry = next(item for item in data if item["id"] == "qwen-local:planner")
        self.assertEqual(entry["context_length"], 32768)
        self.assertEqual(entry["max_input_tokens"], 32768)
        self.assertEqual(entry["n_ctx"], 32768)

    def test_llama_cpp_runtime_context_overrides_profile(self):
        profile = llama_cpp_provider.upsert_profile(
            {
                "served_model_id": "qwen-local:planner",
                "gguf_path": "/models/llama/qwen.gguf",
                "ctx_size": 32768,
            }
        )
        response = unittest.mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"default_generation_settings": {"n_ctx": 65536}}

        with patch("models._get_raw_models", return_value=[]), patch("models._load_meta", return_value={}), patch(
            "models._get_ollama_contexts", return_value={}
        ), patch("models.llama_cpp_provider.get_active_profile", return_value=profile), patch(
            "models.requests.get", return_value=response
        ):
            data = models.get_models_openai_format()

        entry = next(item for item in data if item["id"] == "qwen-local:planner")
        self.assertEqual(entry["context_length"], 65536)

    def test_llama_cpp_profile_context_is_per_slot_and_zero_is_omitted(self):
        llama_cpp_provider.upsert_profile(
            {
                "served_model_id": "parallel-model",
                "gguf_path": "/models/llama/parallel.gguf",
                "ctx_size": 65536,
                "parallel": 4,
            }
        )
        llama_cpp_provider.upsert_profile(
            {
                "served_model_id": "auto-context-model",
                "gguf_path": "/models/llama/auto.gguf",
                "ctx_size": 0,
            }
        )

        with patch("models._get_raw_models", return_value=[]), patch("models._load_meta", return_value={}), patch(
            "models._get_ollama_contexts", return_value={}
        ):
            data = models.get_models_openai_format()

        entries = {item["id"]: item for item in data}
        self.assertEqual(entries["parallel-model"]["context_length"], 16384)
        self.assertNotIn("context_length", entries["auto-context-model"])
        self.assertNotIn("max_input_tokens", entries["auto-context-model"])
        self.assertNotIn("n_ctx", entries["auto-context-model"])

    def test_llama_cpp_props_failure_falls_back_to_profile(self):
        profile = {
            "id": "profile-1",
            "served_model_id": "qwen-local:planner",
            "port": 8081,
            "ctx_size": 32768,
            "parallel": 2,
        }

        with patch("models.requests.get", side_effect=RuntimeError("offline")):
            runtime = models._fetch_llama_cpp_runtime_context(profile)

        self.assertIsNone(runtime)
        self.assertEqual(models._llama_cpp_profile_context(profile), 16384)


if __name__ == "__main__":
    unittest.main()
