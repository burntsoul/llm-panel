#!/usr/bin/env python3
"""Reproduce Ling's Cline final-report turn with bounded generation."""

from __future__ import annotations

import argparse
import json
import pathlib
import time

from ling_cline_smoke import TOOLS, execute, expected_prompt, tool_schema
from ling_tool_call_probe import MARKERS, post


SETUP_STEPS = ("read_file", "write_to_file", "replace_in_file", "execute_command")


def all_tool_schemas() -> list[dict]:
    schemas: list[dict] = []
    for name in TOOLS:
        schemas.extend(tool_schema(name))
    return schemas


def completed_call(response: dict, expected_name: str) -> tuple[dict, dict]:
    choice = response["choices"][0]
    calls = choice["message"]["tool_calls"]
    if choice.get("finish_reason") != "tool_calls" or len(calls) != 1:
        raise ValueError(
            f"finish_reason={choice.get('finish_reason')!r}, tool_call_count={len(calls)}"
        )
    call = calls[0]
    function = call["function"]
    if function.get("name") != expected_name:
        raise ValueError(f"expected {expected_name}, got {function.get('name')!r}")
    raw_arguments = function.get("arguments", "")
    if any(marker in raw_arguments for marker in MARKERS):
        raise ValueError("tool markup leaked into arguments")
    return choice, json.loads(raw_arguments)


def build_history(endpoint: str, model: str, timeout: float) -> list[dict]:
    messages: list[dict] = [
        {
            "role": "user",
            "content": (
                "Complete a Cline-style workflow one tool at a time: read the source, create the "
                "output with alpha, edit alpha to beta, run cat on the output, verify everything, "
                "and submit an implementation report."
            ),
        }
    ]
    for index, name in enumerate(SETUP_STEPS, 1):
        messages.append({"role": "user", "content": expected_prompt(name)})
        body = {
            "model": model,
            "messages": messages,
            "tools": tool_schema(name),
            "tool_choice": "required",
            "temperature": 0,
            "seed": 42,
            "max_tokens": 256,
            "stream": False,
        }
        started = time.monotonic()
        response, _ = post(endpoint, body, timeout)
        choice, arguments = completed_call(response, name)
        result = execute(name, arguments)
        call = choice["message"]["tool_calls"][0]
        messages.extend(
            [
                choice["message"],
                {
                    "role": "tool",
                    "tool_call_id": call.get("id") or f"call_setup_{index}",
                    "content": result,
                },
            ]
        )
        print(f"setup {index}/4 {name}: PASS ({time.monotonic() - started:.2f}s)")
    messages.append(
        {
            "role": "user",
            "content": (
                "All verification passes. Submit the implementation report now using "
                "attempt_completion. Include a concise summary and the verification result."
            ),
        }
    )
    return messages


def repetition_summary(response: dict) -> str:
    choice = (response.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    pieces: list[str] = []
    content = message.get("content")
    if isinstance(content, str):
        pieces.append(content)
    for call in message.get("tool_calls") or []:
        arguments = (call.get("function") or {}).get("arguments")
        if isinstance(arguments, str):
            pieces.append(arguments)
    text = "\n".join(pieces)
    lines = text.splitlines()
    max_blank_run = 0
    blank_run = 0
    max_all_run = 0
    all_run = 0
    for line in lines:
        if line.strip():
            blank_run = 0
        else:
            blank_run += 1
            max_blank_run = max(max_blank_run, blank_run)
        if line.strip().lower() == "all":
            all_run += 1
            max_all_run = max(max_all_run, all_run)
        else:
            all_run = 0
    return (
        f"chars={len(text)}, lines={len(lines)}, "
        f"max_blank_run={max_blank_run}, max_all_run={max_all_run}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True, help="Base URL including /v1")
    parser.add_argument("--model", default="Ling-3.0-flash-AD-Q5_K_M")
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--omit-max-tokens", action="store_true")
    parser.add_argument("--temperature", type=float)
    parser.add_argument("--top-k", type=int)
    parser.add_argument("--tool-choice", choices=("auto", "required"), default="auto")
    parser.add_argument("--seed-start", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--output-dir", type=pathlib.Path)
    args = parser.parse_args()
    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)

    messages = build_history(args.endpoint, args.model, args.timeout)
    failures = 0
    for index in range(args.count):
        seed = args.seed_start + index
        response: dict | None = None
        raw = ""
        body = {
            "model": args.model,
            "messages": messages,
            "tools": all_tool_schemas(),
            "tool_choice": args.tool_choice,
            "seed": seed,
            "stream": False,
            "verbose": True,
            "return_tokens": True,
            "logprobs": True,
            "top_logprobs": 5,
        }
        if not args.omit_max_tokens:
            body["max_tokens"] = args.max_tokens
        if args.temperature is not None:
            body["temperature"] = args.temperature
        if args.top_k is not None:
            body["top_k"] = args.top_k
        started = time.monotonic()
        try:
            response, raw = post(args.endpoint, body, args.timeout)
            _, arguments = completed_call(response, "attempt_completion")
            report = arguments.get("result")
            if set(arguments) != {"result"} or not isinstance(report, str) or not report.strip():
                raise ValueError(f"invalid attempt_completion arguments: {arguments!r}")
            ok = True
            detail = f"exact; {repetition_summary(response)}"
        except Exception as exc:
            ok = False
            detail = f"{exc}; {repetition_summary(response) if response else 'no response'}"
        failures += int(not ok)
        print(
            f"final {index + 1}/{args.count} seed={seed}: "
            f"{'PASS' if ok else 'FAIL'} ({time.monotonic() - started:.2f}s) {detail}"
        )
        if args.output_dir:
            (args.output_dir / f"final-seed-{seed}.json").write_text(raw, encoding="utf-8")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
