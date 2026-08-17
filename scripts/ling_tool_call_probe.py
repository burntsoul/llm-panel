#!/usr/bin/env python3
"""Probe OpenAI-compatible endpoints for Ling multi-argument tool calls."""

from __future__ import annotations

import argparse
import json
import pathlib
import time
import urllib.error
import urllib.request


EXPECTED_PATH = "/tmp/ling-tool-probe.txt"
EXPECTED_CONTENT = "line one\nline two"
MARKERS = ("<arg_key>", "<arg_value>", "<tool_call>", "</arg_key>", "</arg_value>", "</tool_call>")


def payload(model: str, stream: bool, messages: list[dict] | None = None) -> dict:
    return {
        "model": model,
        "messages": messages
        or [
            {
                "role": "user",
                "content": (
                    "Call write_file exactly once with path "
                    f"{EXPECTED_PATH} and content consisting of exactly two lines: "
                    "line one, then line two. Do not add commentary."
                ),
            }
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "Write exact text content to a file.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["path", "content"],
                        "additionalProperties": False,
                    },
                },
            }
        ],
        "tool_choice": "required",
        "temperature": 0,
        "seed": 42,
        "max_tokens": 256,
        "stream": stream,
    }


def post(endpoint: str, body: dict, timeout: float) -> tuple[dict, str]:
    request = urllib.request.Request(
        endpoint.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw), raw


def post_stream(endpoint: str, body: dict, timeout: float) -> tuple[dict, str]:
    request = urllib.request.Request(
        endpoint.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    raw_lines: list[str] = []
    calls: dict[int, dict[str, str]] = {}
    finish_reason = None
    with urllib.request.urlopen(request, timeout=timeout) as response:
        for encoded in response:
            line = encoded.decode("utf-8").rstrip("\r\n")
            raw_lines.append(line)
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            chunk = json.loads(line[6:])
            choice = chunk.get("choices", [{}])[0]
            finish_reason = choice.get("finish_reason") or finish_reason
            for item in choice.get("delta", {}).get("tool_calls") or []:
                index = int(item.get("index", 0))
                call = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                call["id"] += item.get("id") or ""
                function = item.get("function") or {}
                call["name"] += function.get("name") or ""
                call["arguments"] += function.get("arguments") or ""
    reconstructed = {
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {"name": call["name"], "arguments": call["arguments"]},
                        }
                        for _, call in sorted(calls.items())
                    ],
                },
            }
        ]
    }
    return reconstructed, "\n".join(raw_lines) + "\n"


def validate(response: dict) -> tuple[bool, str]:
    try:
        choice = response["choices"][0]
        calls = choice["message"]["tool_calls"]
        if choice.get("finish_reason") != "tool_calls":
            return False, f"finish_reason={choice.get('finish_reason')!r}"
        if len(calls) != 1:
            return False, f"tool_call_count={len(calls)}"
        function = calls[0]["function"]
        if function.get("name") != "write_file":
            return False, f"function={function.get('name')!r}"
        raw_arguments = function.get("arguments", "")
        arguments = json.loads(raw_arguments)
        if arguments.get("path") != EXPECTED_PATH:
            return False, f"path={arguments.get('path')!r}"
        if arguments.get("content") != EXPECTED_CONTENT:
            return False, f"content={arguments.get('content')!r}"
        if any(marker in raw_arguments for marker in MARKERS):
            return False, "markup leaked into arguments"
        return True, "exact"
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        return False, f"invalid response: {exc}"


def run_multiturn(endpoint: str, model: str, timeout: float) -> tuple[bool, str, list[str]]:
    first, first_raw = post(endpoint, payload(model, False), timeout)
    ok, detail = validate(first)
    if not ok:
        return False, f"initial tool call: {detail}", [first_raw]
    assistant = first["choices"][0]["message"]
    call_id = assistant["tool_calls"][0].get("id") or "call_ling_probe"
    messages = payload(model, False)["messages"] + [
        assistant,
        {
            "role": "tool",
            "tool_call_id": call_id,
            "content": "Wrote 17 bytes to /tmp/ling-tool-probe.txt successfully.",
        },
    ]
    followup = payload(model, False, messages)
    followup["tool_choice"] = "none"
    followup["max_tokens"] = 128
    second, second_raw = post(endpoint, followup, timeout)
    try:
        choice = second["choices"][0]
        content = choice["message"].get("content")
        if choice.get("finish_reason") not in ("stop", "length"):
            return False, f"final finish_reason={choice.get('finish_reason')!r}", [first_raw, second_raw]
        if not isinstance(content, str) or not content.strip():
            return False, "final assistant content is empty", [first_raw, second_raw]
        if any(marker in content for marker in MARKERS):
            return False, "markup leaked into final response", [first_raw, second_raw]
    except (KeyError, IndexError, TypeError) as exc:
        return False, f"invalid final response: {exc}", [first_raw, second_raw]
    return True, "tool result accepted; final assistant response valid", [first_raw, second_raw]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True, help="Base URL including /v1")
    parser.add_argument("--model", default="Ling-3.0-flash-AD-Q5_K_M")
    parser.add_argument("--mode", choices=("nonstream", "stream", "both"), default="both")
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--output-dir", type=pathlib.Path)
    parser.add_argument("--multi-turn", action="store_true")
    args = parser.parse_args()

    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
    modes = ("nonstream", "stream") if args.mode == "both" else (args.mode,)
    failures = 0
    for mode in modes:
        for index in range(1, args.count + 1):
            started = time.monotonic()
            try:
                if mode == "stream":
                    response, raw = post_stream(args.endpoint, payload(args.model, True), args.timeout)
                    suffix = "sse"
                else:
                    response, raw = post(args.endpoint, payload(args.model, False), args.timeout)
                    suffix = "json"
                ok, detail = validate(response)
            except (OSError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
                ok, detail, raw, suffix = False, str(exc), "", "txt"
            elapsed = time.monotonic() - started
            failures += int(not ok)
            print(f"{mode} {index}/{args.count}: {'PASS' if ok else 'FAIL'} ({elapsed:.2f}s) {detail}")
            if args.output_dir:
                (args.output_dir / f"{mode}-{index}.{suffix}").write_text(raw, encoding="utf-8")
    if args.multi_turn:
        started = time.monotonic()
        try:
            ok, detail, raws = run_multiturn(args.endpoint, args.model, args.timeout)
        except (OSError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            ok, detail, raws = False, str(exc), []
        failures += int(not ok)
        print(f"multi-turn: {'PASS' if ok else 'FAIL'} ({time.monotonic() - started:.2f}s) {detail}")
        if args.output_dir:
            for index, raw in enumerate(raws, 1):
                (args.output_dir / f"multi-turn-{index}.json").write_text(raw, encoding="utf-8")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
