import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from starlette.requests import Request

import app as app_module
import lease_api
from llm_runtime_manager import RuntimeRequestCancelled, RuntimeTarget


def make_request(path: str, payload: dict, *, headers=None) -> Request:
    body = json.dumps(payload).encode("utf-8")
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    raw_headers = [
        (str(key).lower().encode("latin-1"), str(value).encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": raw_headers,
            "client": ("192.0.2.10", 12345),
            "server": ("agent.test", 8000),
        },
        receive,
    )


class RuntimeApiContractTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def target() -> RuntimeTarget:
        return RuntimeTarget(
            provider="ollama",
            model="model:latest",
            target_key="ollama:model:latest",
            base_url="http://provider.invalid",
        )

    async def test_queued_stream_cancellation_returns_openai_409(self):
        request = make_request(
            "/v1/chat/completions",
            {"model": "model:latest", "messages": [{"role": "user", "content": "private"}], "stream": True},
        )
        resolved = ("model:latest", "model:latest", "http://provider.invalid", "ollama", self.target())
        with patch.object(app_module, "_resolve_provider_for_payload", AsyncMock(return_value=resolved)), patch.object(
            app_module.runtime_scheduler,
            "acquire",
            AsyncMock(side_effect=RuntimeRequestCancelled("queued-1")),
        ):
            response = await app_module.chat_completions(request)
        self.assertEqual(response.status_code, 409)
        payload = json.loads(response.body)
        self.assertEqual(payload["error"]["type"], "request_cancelled")
        self.assertEqual(payload["error"]["request_id"], "queued-1")

    async def test_proxy_inference_requires_explicit_model(self):
        request = make_request("/v1/proxy/api/chat", {"messages": []})
        with self.assertRaises(HTTPException) as raised:
            await lease_api._proxy_forward("POST", "api/chat", request, None)
        self.assertEqual(raised.exception.status_code, 400)

    async def test_lease_advertises_agent_not_provider_url(self):
        request = make_request(
            "/v1/lease",
            {"client_id": "contract-test", "purpose": "chat", "ttl_seconds": 60},
        )
        manager = MagicMock()
        manager.create_lease.return_value = SimpleNamespace(lease_id="lease-1")
        with patch.object(lease_api, "verify_token", return_value=True), patch.object(
            lease_api, "get_lease_manager", return_value=manager
        ):
            response = await lease_api.create_lease(request, authorization=None)
        payload = json.loads(response.body)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["llm_base_url"], "http://agent.test:8000")

    async def test_queue_api_exposes_only_sanitized_scheduler_snapshot(self):
        snapshot = await app_module.api_runtime_queue()
        encoded = json.dumps(snapshot).lower()
        self.assertNotIn("prompt", encoded)
        self.assertNotIn("messages", encoded)
        self.assertIn("phase", snapshot)
        self.assertIn("revision", snapshot)

    async def test_runtime_events_begin_with_snapshot_and_emit_heartbeat(self):
        request = MagicMock()
        request.is_disconnected = AsyncMock(side_effect=[False, True])
        current = app_module.runtime_scheduler.snapshot()
        with patch.object(
            app_module.runtime_scheduler, "wait_for_revision", AsyncMock(return_value=current)
        ):
            response = await app_module.api_runtime_events(request)
            iterator = response.body_iterator.__aiter__()
            initial = await iterator.__anext__()
            heartbeat = await iterator.__anext__()
            await iterator.aclose()
        self.assertIn("event: snapshot", initial)
        self.assertIn("event: heartbeat", heartbeat)


if __name__ == "__main__":
    unittest.main()
