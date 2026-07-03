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


if __name__ == "__main__":
    unittest.main()
