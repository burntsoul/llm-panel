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

    @patch("models.llm_server_up", return_value=True)
    @patch("models.requests.get")
    def test_private_backing_models_are_hidden_from_public_lists(self, get_mock, _up_mock):
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


if __name__ == "__main__":
    unittest.main()
