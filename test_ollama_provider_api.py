from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import app as app_module


class TestOllamaProviderApi(unittest.TestCase):
    def test_settings_registers_ollama_provider_section(self):
        payload = app_module.api_settings_effective()
        section = payload["sections"]["provider_ollama"]

        self.assertEqual(section["title"], "Ollama provider")
        self.assertEqual(section["custom"], "provider_ollama")

    @patch("app.requests.post")
    def test_pull_request_calls_ollama_api(self, post_mock):
        response_mock = MagicMock()
        post_mock.return_value = response_mock

        response = app_module._ollama_pull_request("gemma4:latest")

        self.assertIs(response, response_mock)
        post_mock.assert_called_once()
        self.assertTrue(post_mock.call_args.args[0].endswith("/api/pull"))
        self.assertEqual(post_mock.call_args.kwargs["json"], {"model": "gemma4:latest", "stream": True})

    @patch("app.requests.delete")
    def test_delete_request_calls_ollama_api(self, delete_mock):
        response_mock = MagicMock()
        delete_mock.return_value = response_mock

        response = app_module._ollama_delete_request("gemma4:latest")

        self.assertIs(response, response_mock)
        delete_mock.assert_called_once()
        self.assertTrue(delete_mock.call_args.args[0].endswith("/api/delete"))
        self.assertEqual(delete_mock.call_args.kwargs["json"], {"model": "gemma4:latest"})

    @patch("app._ollama_delete_request")
    def test_delete_ollama_model_if_present_removes_private_backing_model(self, delete_request_mock):
        response_mock = MagicMock()
        response_mock.ok = True
        response_mock.status_code = 200
        delete_request_mock.return_value = response_mock

        removed, missing = app_module._delete_ollama_model_if_present("llm-agent/profile-gemma4-12b:latest")

        delete_request_mock.assert_called_once_with("llm-agent/profile-gemma4-12b:latest")
        self.assertTrue(removed)
        self.assertFalse(missing)

    @patch("app._ollama_delete_request")
    def test_delete_ollama_model_if_present_tolerates_missing_private_backing_model(self, delete_request_mock):
        response_mock = MagicMock()
        response_mock.ok = False
        response_mock.status_code = 404
        response_mock.json.return_value = {"error": "model not found"}
        response_mock.text = "model not found"
        delete_request_mock.return_value = response_mock

        removed, missing = app_module._delete_ollama_model_if_present("llm-agent/profile-gemma4-12b:latest")

        delete_request_mock.assert_called_once_with("llm-agent/profile-gemma4-12b:latest")
        self.assertFalse(removed)
        self.assertTrue(missing)


if __name__ == "__main__":
    unittest.main()
