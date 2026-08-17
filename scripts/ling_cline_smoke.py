#!/usr/bin/env python3
"""Run a bounded Cline-style tool workflow against a Ling endpoint."""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import time

from ling_tool_call_probe import MARKERS, post


SOURCE = pathlib.Path("/tmp/ling-cline-smoke-source.txt")
TARGET = pathlib.Path("/tmp/ling-cline-smoke-output.txt")


TOOLS = {
    "read_file": {
        "description": "Read a UTF-8 text file.",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
    "write_to_file": {
        "description": "Write exact UTF-8 content to a file.",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"],
    },
    "replace_in_file": {
        "description": "Replace exact text in a UTF-8 file.",
        "properties": {
            "path": {"type": "string"},
            "old_text": {"type": "string"},
            "new_text": {"type": "string"},
        },
        "required": ["path", "old_text", "new_text"],
    },
    "execute_command": {
        "description": "Run a shell command.",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    },
    "attempt_completion": {
        "description": "Submit the implementation report.",
        "properties": {"result": {"type": "string"}},
        "required": ["result"],
    },
}


def tool_schema(name: str) -> list[dict]:
    definition = TOOLS[name]
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": definition["description"],
                "parameters": {
                    "type": "object",
                    "properties": definition["properties"],
                    "required": definition["required"],
                    "additionalProperties": False,
                },
            },
        }
    ]


def expected_prompt(name: str) -> str:
    prompts = {
        "read_file": f"Call read_file for exactly {SOURCE}.",
        "write_to_file": f"Call write_to_file for exactly {TARGET} with content exactly alpha followed by a newline.",
        "replace_in_file": f"Call replace_in_file for exactly {TARGET}, replacing alpha with beta.",
        "execute_command": f"Call execute_command with exactly: cat {TARGET}",
        "attempt_completion": "Call attempt_completion with a short report that the file workflow succeeded.",
    }
    return prompts[name]


def execute(name: str, arguments: dict) -> str:
    if name == "read_file":
        if arguments != {"path": str(SOURCE)}:
            raise ValueError(f"unexpected read arguments: {arguments!r}")
        return SOURCE.read_text(encoding="utf-8")
    if name == "write_to_file":
        if arguments != {"path": str(TARGET), "content": "alpha\n"}:
            raise ValueError(f"unexpected write arguments: {arguments!r}")
        TARGET.write_text(arguments["content"], encoding="utf-8")
        return "File created. Next, replace alpha with beta."
    if name == "replace_in_file":
        expected = {"path": str(TARGET), "old_text": "alpha", "new_text": "beta"}
        if arguments != expected:
            raise ValueError(f"unexpected edit arguments: {arguments!r}")
        content = TARGET.read_text(encoding="utf-8")
        TARGET.write_text(content.replace("alpha", "beta", 1), encoding="utf-8")
        return "File edited. Next, run the requested cat command."
    if name == "execute_command":
        expected = f"cat {TARGET}"
        if arguments != {"command": expected}:
            raise ValueError(f"unexpected command arguments: {arguments!r}")
        result = subprocess.run(["cat", str(TARGET)], check=True, capture_output=True, text=True)
        return f"Command succeeded with output: {result.stdout!r}. Next, submit the report."
    if name == "attempt_completion":
        report = arguments.get("result")
        if set(arguments) != {"result"} or not isinstance(report, str) or not report.strip():
            raise ValueError(f"unexpected report arguments: {arguments!r}")
        return report
    raise ValueError(f"unsupported tool: {name}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True, help="Base URL including /v1")
    parser.add_argument("--model", default="Ling-3.0-flash-AD-Q5_K_M")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--output-dir", type=pathlib.Path)
    args = parser.parse_args()
    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)

    SOURCE.write_text("source text\n", encoding="utf-8")
    messages: list[dict] = [
        {
            "role": "user",
            "content": (
                "Complete a Cline-style workflow one tool at a time: read the source, create the output "
                "with alpha, edit alpha to beta, run cat on the output, and submit a report."
            ),
        }
    ]
    steps = ("read_file", "write_to_file", "replace_in_file", "execute_command", "attempt_completion")
    for index, expected_name in enumerate(steps, 1):
        messages.append({"role": "user", "content": expected_prompt(expected_name)})
        body = {
            "model": args.model,
            "messages": messages,
            "tools": tool_schema(expected_name),
            "tool_choice": "required",
            "temperature": 0,
            "seed": 42,
            "max_tokens": 256,
            "stream": False,
        }
        started = time.monotonic()
        response, raw = post(args.endpoint, body, args.timeout)
        if args.output_dir:
            (args.output_dir / f"step-{index}-{expected_name}.json").write_text(raw, encoding="utf-8")
        try:
            choice = response["choices"][0]
            calls = choice["message"]["tool_calls"]
            if choice.get("finish_reason") != "tool_calls" or len(calls) != 1:
                raise ValueError("response did not contain exactly one completed tool call")
            call = calls[0]
            function = call["function"]
            if function.get("name") != expected_name:
                raise ValueError(f"expected {expected_name}, got {function.get('name')!r}")
            raw_arguments = function.get("arguments", "")
            if any(marker in raw_arguments for marker in MARKERS):
                raise ValueError("tool markup leaked into arguments")
            arguments = json.loads(raw_arguments)
            result = execute(expected_name, arguments)
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            print(f"step {index}/5 {expected_name}: FAIL {exc}")
            return 1
        print(f"step {index}/5 {expected_name}: PASS ({time.monotonic() - started:.2f}s)")
        messages.extend(
            [
                choice["message"],
                {
                    "role": "tool",
                    "tool_call_id": call.get("id") or f"call_{index}",
                    "content": result,
                },
            ]
        )

    if TARGET.read_text(encoding="utf-8") != "beta\n":
        print("final file verification: FAIL")
        return 1
    print("final file verification: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
