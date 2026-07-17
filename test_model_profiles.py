from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import models


class TestModelProfiles(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.meta_path = Path(self.tmpdir.name) / "model_meta.json"
        self.original_meta_file = models._MODEL_META_FILE
        models._MODEL_META_FILE = str(self.meta_path)
        models.invalidate_model_cache()

    def tearDown(self):
        models._MODEL_META_FILE = self.original_meta_file
        models.invalidate_model_cache()
        self.tmpdir.cleanup()

    def _write_meta(self, data):
        self.meta_path.write_text(json.dumps(data), encoding="utf-8")
        models.invalidate_model_cache()

    @patch("models.llama_cpp_provider.list_profiles", return_value=[])
    @patch("models.llm_server_up", return_value=True)
    @patch("models.requests.get")
    def test_private_backing_models_are_hidden_from_public_lists(self, get_mock, _up_mock, _llama_mock):
        backing = models.profile_backing_model_name("gemma4:12b")
        self._write_meta(
            {
                "gemma4:12b": {
                    "source": "local",
                    "device": "gpu",
                    "available": False,
                    "profile_enabled": True,
                    "profile_backing_model": backing,
                    "profile_base_model": "gemma4:12b",
                    "profile_parameters": {"num_ctx": 32768},
                    "profile_status": "active",
                }
            }
        )
        get_mock.return_value.raise_for_status.return_value = None
        get_mock.return_value.json.return_value = {"models": [{"name": backing}]}

        rows = models.get_model_table_status()
        openai_models = models.get_models_openai_format()

        self.assertEqual([row["id"] for row in rows], ["gemma4:12b"])
        self.assertTrue(rows[0]["present_now"])
        self.assertFalse(rows[0]["base_present_now"])
        self.assertEqual([row["id"] for row in openai_models], ["gemma4:12b"])

    @patch("models.llm_server_up", return_value=True)
    @patch("models.requests.get")
    def test_public_model_resolves_to_backing_when_present(self, get_mock, _up_mock):
        backing = models.profile_backing_model_name("qwen2.5-coder:7b")
        self._write_meta(
            {
                "qwen2.5-coder:7b": {
                    "profile_enabled": True,
                    "profile_backing_model": backing,
                    "profile_base_model": "qwen2.5-coder:7b",
                    "profile_parameters": {"num_ctx": 24576},
                }
            }
        )
        get_mock.return_value.raise_for_status.return_value = None
        get_mock.return_value.json.return_value = {"models": [{"name": backing}]}

        self.assertEqual(models.resolve_model_for_upstream("qwen2.5-coder:7b"), backing)

    @patch("models.llm_server_up", return_value=True)
    @patch("models.requests.get")
    def test_public_model_falls_back_when_backing_missing(self, get_mock, _up_mock):
        backing = models.profile_backing_model_name("qwen2.5-coder:7b")
        self._write_meta(
            {
                "qwen2.5-coder:7b": {
                    "profile_enabled": True,
                    "profile_backing_model": backing,
                    "profile_base_model": "qwen2.5-coder:7b",
                    "profile_parameters": {"num_ctx": 24576},
                }
            }
        )
        get_mock.return_value.raise_for_status.return_value = None
        get_mock.return_value.json.return_value = {"models": [{"name": "qwen2.5-coder:7b"}]}

        self.assertEqual(models.resolve_model_for_upstream("qwen2.5-coder:7b"), "qwen2.5-coder:7b")

    @patch("models.llama_cpp_provider.list_profiles", return_value=[])
    @patch("models.llm_server_up", return_value=True)
    @patch("models.requests.get")
    def test_alias_public_status_uses_raw_ollama_name_for_presence(self, get_mock, _up_mock, _llama_mock):
        self._write_meta(
            {
                "gemma4:12b": {
                    "source": "local",
                    "device": "gpu",
                    "alias": "coding-planner",
                }
            }
        )
        get_mock.return_value.raise_for_status.return_value = None
        get_mock.return_value.json.return_value = {"models": [{"name": "gemma4:12b"}]}

        rows = models.get_model_table_status()

        self.assertEqual([row["id"] for row in rows], ["coding-planner"])
        self.assertEqual(rows[0]["raw_model_name"], "gemma4:12b")
        self.assertTrue(rows[0]["present_now"])
        self.assertTrue(rows[0]["base_present_now"])

    @patch("models.llm_server_up", return_value=True)
    @patch("models.requests.get")
    def test_ollama_provider_status_uses_raw_names_when_alias_exists(self, get_mock, _up_mock):
        self._write_meta(
            {
                "gemma4:12b": {
                    "source": "local",
                    "device": "gpu",
                    "alias": "coding-planner",
                }
            }
        )
        get_mock.return_value.raise_for_status.return_value = None
        get_mock.return_value.json.return_value = {"models": [{"name": "gemma4:12b"}]}

        rows = models.get_ollama_provider_model_status()

        self.assertEqual([row["id"] for row in rows], ["gemma4:12b"])
        self.assertEqual(rows[0]["alias"], "coding-planner")
        self.assertEqual(rows[0]["display_id"], "coding-planner")
        self.assertTrue(rows[0]["present_now"])

    @patch("models.llama_cpp_provider.list_profiles", return_value=[])
    @patch("models._get_ollama_show_contexts")
    @patch("models._fetch_ollama_runtime_contexts", return_value={})
    @patch("models._get_raw_models")
    def test_openai_models_advertise_ollama_profile_context(
        self, raw_mock, _runtime_mock, show_mock, _profiles_mock
    ):
        raw_mock.return_value = [{"name": "gemma4:12b", "digest": "abc"}]
        self._write_meta(
            {
                "gemma4:12b": {
                    "alias": "coding-planner",
                    "profile_enabled": True,
                    "profile_backing_model": models.profile_backing_model_name("gemma4:12b"),
                    "profile_base_model": "gemma4:12b",
                    "profile_parameters": {"num_ctx": 32768},
                }
            }
        )

        data = models.get_models_openai_format()

        self.assertEqual(data[0]["id"], "coding-planner")
        self.assertEqual(data[0]["context_length"], 32768)
        self.assertEqual(data[0]["max_input_tokens"], 32768)
        self.assertEqual(data[0]["n_ctx"], 32768)
        show_mock.assert_not_called()

    @patch("models.llama_cpp_provider.list_profiles", return_value=[])
    @patch("models._get_ollama_show_contexts")
    @patch("models._fetch_ollama_runtime_contexts")
    @patch("models._get_raw_models")
    def test_loaded_ollama_backing_context_overrides_profile(
        self, raw_mock, runtime_mock, show_mock, _profiles_mock
    ):
        backing = models.profile_backing_model_name("gemma4:12b")
        raw_mock.return_value = [{"name": "gemma4:12b", "digest": "abc"}]
        runtime_mock.return_value = {backing: 65536}
        self._write_meta(
            {
                "gemma4:12b": {
                    "profile_enabled": True,
                    "profile_backing_model": backing,
                    "profile_base_model": "gemma4:12b",
                    "profile_parameters": {"num_ctx": 32768},
                }
            }
        )

        data = models.get_models_openai_format()

        self.assertEqual(data[0]["context_length"], 65536)
        show_mock.assert_not_called()

    @patch("models.llama_cpp_provider.list_profiles", return_value=[])
    @patch("models._get_ollama_show_contexts", return_value={"plain:latest": 131072})
    @patch("models._fetch_ollama_runtime_contexts", return_value={})
    @patch("models._get_raw_models", return_value=[{"name": "plain:latest", "digest": "abc"}])
    def test_openai_models_use_ollama_show_context_fallback(
        self, _raw_mock, _runtime_mock, _show_mock, _profiles_mock
    ):
        data = models.get_models_openai_format()

        self.assertEqual(data[0]["context_length"], 131072)
        self.assertEqual(data[0]["max_input_tokens"], 131072)
        self.assertEqual(data[0]["n_ctx"], 131072)

    def test_ollama_show_context_prefers_num_ctx_and_rejects_invalid_values(self):
        payload = {
            "parameters": "temperature 0.7\nnum_ctx 8192",
            "model_info": {
                "general.architecture": "gemma3",
                "gemma3.context_length": 131072,
            },
        }

        self.assertEqual(models._parse_ollama_show_context(payload), 8192)
        self.assertEqual(
            models._parse_ollama_show_context(
                {"model_info": {"general.architecture": "gemma3", "gemma3.context_length": 131072}}
            ),
            131072,
        )
        for value in (None, 0, -1, True, "invalid", 1.5):
            self.assertIsNone(models._positive_int(value))

    @patch("models.llm_server_up", return_value=True)
    @patch("models._fetch_ollama_show_context", return_value=4096)
    def test_ollama_show_context_is_cached_by_model_digest(self, fetch_mock, _up_mock):
        rows = [{"name": "plain:latest", "digest": "abc"}]

        first = models._get_ollama_show_contexts(rows)
        second = models._get_ollama_show_contexts(rows)

        self.assertEqual(first, {"plain:latest": 4096})
        self.assertEqual(second, first)
        fetch_mock.assert_called_once_with("plain:latest")

    @patch("models.llm_server_up", return_value=True)
    @patch("models.requests.get", side_effect=RuntimeError("offline"))
    def test_ollama_runtime_context_failure_is_nonfatal(self, _get_mock, _up_mock):
        self.assertEqual(models._fetch_ollama_runtime_contexts(), {})


if __name__ == "__main__":
    unittest.main()
