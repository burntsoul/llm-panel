# test_lease_integration.py
"""
Integration tests for lease API and proxy endpoints.

Requires the FastAPI app to be running locally.
Run with: python test_lease_integration.py

Or with pytest: python -m pytest test_lease_integration.py -v
"""

import unittest
import asyncio
import json
from unittest.mock import patch, MagicMock, AsyncMock
import httpx

from fastapi.testclient import TestClient

import app as app_module
from config import settings


class TestLeaseAPI(unittest.TestCase):
    """Integration tests for the lease API endpoints."""

    @classmethod
    def setUpClass(cls):
        """Set up test fixtures."""
        # Create a test client
        cls.client = TestClient(app_module.app)
        cls.token = "test-token"

        # Mock the LLM server to be "ready"
        cls.llm_ready_patcher = patch(
            "lease_api.is_llm_ready",
            return_value=True,
        )
        cls.llm_ready_mock = cls.llm_ready_patcher.start()

        # Mock ensure_llm_running_and_ready
        cls.ensure_llm_patcher = patch(
            "lease_api.ensure_llm_running_and_ready",
            new_callable=AsyncMock,
            return_value=True,
        )
        cls.ensure_llm_mock = cls.ensure_llm_patcher.start()

        # Mock VM status
        cls.vm_status_patcher = patch(
            "lease_api.get_vm_status",
            return_value="running",
        )
        cls.vm_status_mock = cls.vm_status_patcher.start()

        # Temporarily set a token for testing
        cls.original_token = settings.LLM_AGENT_TOKEN
        settings.LLM_AGENT_TOKEN = cls.token

    @classmethod
    def tearDownClass(cls):
        """Clean up patches."""
        cls.llm_ready_patcher.stop()
        cls.ensure_llm_patcher.stop()
        cls.vm_status_patcher.stop()
        settings.LLM_AGENT_TOKEN = cls.original_token

    def _headers(self, token=None):
        """Build auth headers."""
        if token is None:
            token = self.token
        return {"Authorization": f"Bearer {token}"}

    def test_create_lease_success(self):
        """Test creating a lease successfully."""
        response = self.client.post(
            "/v1/lease",
            json={
                "client_id": "test-client",
                "purpose": "testing",
                "ttl_seconds": 3600,
            },
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertIn("lease_id", data)
        self.assertEqual(data["status"], "ready")
        self.assertIn("llm_base_url", data)
        self.lease_id = data["lease_id"]

    def test_create_lease_missing_auth(self):
        """Test creating a lease without auth fails."""
        response = self.client.post(
            "/v1/lease",
            json={
                "client_id": "test-client",
                "purpose": "testing",
                "ttl_seconds": 3600,
            },
        )

        self.assertEqual(response.status_code, 401)

    def test_create_lease_invalid_auth(self):
        """Test creating a lease with invalid auth fails."""
        response = self.client.post(
            "/v1/lease",
            json={
                "client_id": "test-client",
                "purpose": "testing",
                "ttl_seconds": 3600,
            },
            headers=self._headers(token="wrong-token"),
        )

        self.assertEqual(response.status_code, 401)

    def test_create_lease_missing_fields(self):
        """Test creating a lease with missing fields fails."""
        response = self.client.post(
            "/v1/lease",
            json={"client_id": "test-client"},
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 400)

    def test_get_lease(self):
        """Test getting a lease."""
        # Create a lease first
        response = self.client.post(
            "/v1/lease",
            json={
                "client_id": "test-client",
                "purpose": "testing",
                "ttl_seconds": 3600,
            },
            headers=self._headers(),
        )
        lease_id = response.json()["lease_id"]

        # Get the lease
        response = self.client.get(
            f"/v1/lease/{lease_id}",
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["lease_id"], lease_id)
        self.assertEqual(data["client_id"], "test-client")
        self.assertEqual(data["status"], "ready")

    def test_get_nonexistent_lease(self):
        """Test getting a nonexistent lease fails."""
        response = self.client.get(
            "/v1/lease/nonexistent",
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 404)

    def test_refresh_lease(self):
        """Test refreshing a lease."""
        # Create a lease first
        response = self.client.post(
            "/v1/lease",
            json={
                "client_id": "test-client",
                "purpose": "testing",
                "ttl_seconds": 3600,
            },
            headers=self._headers(),
        )
        lease_id = response.json()["lease_id"]

        # Refresh the lease
        response = self.client.post(
            f"/v1/lease/{lease_id}/refresh",
            json={"ttl_seconds": 7200},
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["lease_id"], lease_id)
        self.assertEqual(data["ttl_seconds"], 7200)

    def test_refresh_nonexistent_lease(self):
        """Test refreshing a nonexistent lease fails."""
        response = self.client.post(
            "/v1/lease/nonexistent/refresh",
            json={},
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 404)

    def test_release_lease(self):
        """Test releasing a lease."""
        # Create a lease first
        response = self.client.post(
            "/v1/lease",
            json={
                "client_id": "test-client",
                "purpose": "testing",
                "ttl_seconds": 3600,
            },
            headers=self._headers(),
        )
        lease_id = response.json()["lease_id"]

        # Release the lease
        response = self.client.post(
            f"/v1/lease/{lease_id}/release",
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 200)

        # Verify it's gone
        response = self.client.get(
            f"/v1/lease/{lease_id}",
            headers=self._headers(),
        )
        self.assertEqual(response.status_code, 404)

    def test_health_check(self):
        """Test health check endpoint."""
        response = self.client.get(
            "/v1/health",
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("ok", data)
        self.assertIn("vm_state", data)
        self.assertIn("llm_ready", data)
        self.assertIn("active_leases", data)

    def test_proxy_requires_auth(self):
        """Test proxy endpoint requires auth."""
        response = self.client.get("/v1/proxy/api/tags")
        self.assertEqual(response.status_code, 401)

    def test_proxy_requires_llm_ready(self):
        """Test proxy requires LLM to be ready."""
        with patch("lease_api.is_llm_ready", return_value=False):
            response = self.client.get(
                "/v1/proxy/api/tags",
                headers=self._headers(),
            )
            self.assertEqual(response.status_code, 503)

    @patch("lease_api.httpx.AsyncClient")
    def test_proxy_forward_request(self, mock_client_class):
        """Test proxy forwards requests correctly."""
        # Mock the HTTP client
        payload = b'{"tags":[]}'
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"tags": []}
        mock_response.headers = {"content-type": "application/json", "content-length": str(len(payload))}
        mock_response.text = payload.decode("utf-8")
        mock_response.content = payload

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.request = AsyncMock(return_value=mock_response)

        mock_client_class.return_value = mock_client

        response = self.client.get(
            "/v1/proxy/api/tags",
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, payload)
        mock_client.request.assert_called_once()
        mock_client_class.assert_called_once_with(
            timeout=settings.PROXY_UPSTREAM_TIMEOUT_SECONDS
        )

    @patch("lease_api.httpx.AsyncClient")
    def test_proxy_nonstream_json_passthrough_and_header_sanitization(self, mock_client_class):
        """Test proxy preserves JSON bytes and strips framing/hop-by-hop headers."""
        payload = b'{"ok":true,"result":"pass-through"}'
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {
            "content-type": "application/json",
            "content-length": str(len(payload)),
            "transfer-encoding": "chunked",
            "content-encoding": "gzip",
            "connection": "keep-alive",
            "server": "ollama",
            "date": "Thu, 01 Jan 1970 00:00:00 GMT",
            "x-upstream-trace-id": "abc-123",
        }
        mock_response.content = payload
        mock_response.text = payload.decode("utf-8")

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.request = AsyncMock(return_value=mock_response)
        mock_client_class.return_value = mock_client

        response = self.client.get(
            "/v1/proxy/api/tags",
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, payload)
        self.assertEqual(response.headers.get("content-type"), "application/json")
        self.assertEqual(response.headers.get("x-upstream-trace-id"), "abc-123")
        self.assertIsNone(response.headers.get("transfer-encoding"))
        self.assertIsNone(response.headers.get("content-encoding"))
        self.assertIsNone(response.headers.get("connection"))
        self.assertIsNone(response.headers.get("server"))
        self.assertIsNone(response.headers.get("date"))

    @patch("lease_api.httpx.AsyncClient")
    def test_proxy_non_json_passthrough(self, mock_client_class):
        """Test proxy forwards non-JSON payloads without JSON parsing."""
        payload = b"not-json-response"
        mock_response = MagicMock()
        mock_response.status_code = 502
        mock_response.headers = {
            "content-type": "text/plain; charset=utf-8",
            "content-length": str(len(payload)),
        }
        mock_response.content = payload
        mock_response.text = payload.decode("utf-8")

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.request = AsyncMock(return_value=mock_response)
        mock_client_class.return_value = mock_client

        response = self.client.get(
            "/v1/proxy/api/tags",
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.content, payload)
        self.assertEqual(
            response.headers.get("content-type"),
            "text/plain; charset=utf-8",
        )

    @patch("lease_api.httpx.AsyncClient")
    def test_proxy_timeout_returns_504(self, mock_client_class):
        """Test proxy timeout is mapped to 504."""
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.request = AsyncMock(side_effect=httpx.TimeoutException("request timed out"))
        mock_client_class.return_value = mock_client

        response = self.client.get(
            "/v1/proxy/api/tags",
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 504)
        data = response.json()
        self.assertEqual(data.get("detail"), "LLM server request timeout")

    @patch("lease_api.httpx.AsyncClient")
    def test_proxy_with_lease_id(self, mock_client_class):
        """Test proxy with lease ID header."""
        # Create a lease first
        response = self.client.post(
            "/v1/lease",
            json={
                "client_id": "test-client",
                "purpose": "testing",
                "ttl_seconds": 3600,
            },
            headers=self._headers(),
        )
        lease_id = response.json()["lease_id"]

        # Mock the HTTP client
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"tags": []}
        mock_response.headers = {"content-type": "application/json"}
        mock_response.text = "{}"
        mock_response.content = b"{}"

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.request = AsyncMock(return_value=mock_response)

        mock_client_class.return_value = mock_client

        # Proxy with lease ID
        response = self.client.get(
            "/v1/proxy/api/tags",
            headers={
                **self._headers(),
                "X-Lease-Id": lease_id,
            },
        )

        self.assertEqual(response.status_code, 200)

    @patch("lease_api.httpx.AsyncClient")
    def test_proxy_with_invalid_lease_id(self, mock_client_class):
        """Test proxy with invalid lease ID fails."""
        response = self.client.get(
            "/v1/proxy/api/tags",
            headers={
                **self._headers(),
                "X-Lease-Id": "nonexistent-lease",
            },
        )

        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
