# Ling 3.0 Flash multi-argument tool-call fix

Date: 2026-08-17

Verdict: `ready_for_benchmark`

## Root cause

The deployed AtomicBot TurboQuant fork was built from `cd560939087c95b93a1f30a95603d6b079436952`
(`b10269-1.5.1`) on August 6, before upstream llama.cpp commit
`0266ebca66bd95b7a85d37b8ca08ccf9812b85cc` (`common: fix Bailing V3 tool argument
parsing`). The missing code is in `common/chat-diff-analyzer.cpp`, not llm-agent.

For the Bailing V3 `TAG_WITH_TAGGED` format, template analysis left whitespace attached to
the detected argument value suffix and did not permit whitespace between adjacent tags. The
PEG-native parser therefore consumed the next `<arg_key>`/`<arg_value>` sequence into the
first string value. The upstream fix trims `analysis.tools.arguments.value_suffix` and sets
`analysis.tools.arguments.tolerate_intertag_whitespace = true` for templates containing
`Bailing V3 chat template`.

The old binary reproduced the exact corrupt path through both direct llama-server and
llm-agent endpoints. No llm-agent response repair was added.

## Builds and deployment

- Old Ling build: `cd560939087c95b93a1f30a95603d6b079436952`, build 10696,
  SHA-256 `a7402b52bf0740ffa5e93b1d41b5ecf874ac741cb5695b10bcd653ef56c47a38`.
- Upstream fix: `0266ebca66bd95b7a85d37b8ca08ccf9812b85cc`.
- Replacement fork commit: `86d2ca6db826b34b37fa80214f5f8605487be509`, build 10697,
  SHA-256 `4f9dcd23bb9d8b8c59a67a0ab8e4e50d8e9b7d4dbd3b750ad36f9433bfe932e3`.
- Source branch: `/home/teemu/src/atomic-llama-cpp-turboquant-ling-fix`,
  `ling-bailing-parser-fix`, clean.
- Packaged runtime:
  `/home/teemu/opt/llama-turboquant-b10269-1.5.1-ling-parser-fix-86d2ca6d-cuda-12.4`.
- Profile symlink:
  `/home/teemu/bin/llama-server-turboquant-b10269-1.5.1-ling-parser-fix-86d2ca6d`.
- Original binary/package retained unchanged.

The CUDA build follows the fork's CUDA 12.4 release recipe, including dynamic backends, all
CPU variants, and `61-virtual;75-real;86-real;89-real`. The replacement contains the same
Pascal PTX fallback needed by the Tesla P40. Quantization, tensor placement, KV precision,
model, context, and trained chat template were not changed.

## Validation

| Test | Result |
| --- | --- |
| Old direct non-streaming baseline | 0/1; exact corruption reproduced |
| Old routed non-streaming baseline | 0/1; identical corruption reproduced |
| Patched direct non-streaming | 5/5 exact |
| Patched direct streaming reconstruction | 5/5 exact |
| Patched routed non-streaming | 5/5 exact |
| Patched routed streaming reconstruction | 5/5 exact |
| Routed tool-result multi-turn | pass; valid final assistant response |
| Cline-style read/create/edit/command/report | 5/5 steps and final file verification pass |
| Upstream chat auto-parser regression suite | 1/1 pass |
| llm-agent focused unit tests | 59/59 pass |
| Qwen routed smoke | HTTP 200, one valid choice |
| Laguna routed smoke | HTTP 200, one valid choice |
| Final packaged Ling routed exact call | pass |

Every counted Ling acceptance response used `tool_choice: "required"`, returned exactly one
`write_file` call with `finish_reason: "tool_calls"`, valid JSON arguments, exact path
`/tmp/ling-tool-probe.txt`, exact content `line one\nline two`, and no tagged-tool markup.

The patched diagnostic launch selected `chat format: peg-native` for tool requests. `/props`
still reports the expected idle defaults (`chat_format: Content-only`, `reasoning_format:
none`) and all tool capability flags, including object arguments and parallel calls.

Raw bounded responses and SSE captures are stored below this directory. No API keys or
authorization headers are present.

## Commands used

The essential build and test commands were:

```sh
git fetch https://github.com/ggml-org/llama.cpp.git 0266ebca66bd95b7a85d37b8ca08ccf9812b85cc
git cherry-pick 0266ebca66bd95b7a85d37b8ca08ccf9812b85cc

cmake -S . -B build-ling-fix-cpu -DCMAKE_BUILD_TYPE=Release -DGGML_CUDA=OFF -DLLAMA_BUILD_TESTS=ON
cmake --build build-ling-fix-cpu --target test-chat-auto-parser -j 14
ctest --test-dir build-ling-fix-cpu --output-on-failure -R test-chat-auto-parser

cmake -S . -B build-ling-fix-cuda124 \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_RPATH='$ORIGIN' \
  -DCMAKE_BUILD_WITH_INSTALL_RPATH=ON -DGGML_BACKEND_DL=ON \
  -DGGML_NATIVE=OFF -DGGML_CPU_ALL_VARIANTS=ON -DGGML_CUDA=ON \
  -DGGML_CUDA_CUB_3DOT2=ON \
  -DCMAKE_CUDA_ARCHITECTURES='61-virtual;75-real;86-real;89-real' \
  -DLLAMA_CURL=OFF -DLLAMA_OPENSSL=OFF -DLLAMA_BUILD_SERVER=ON \
  -DLLAMA_BUILD_TOOLS=ON -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF
cmake --build build-ling-fix-cuda124 --config Release --target llama-server -j 14

python3 scripts/ling_tool_call_probe.py --endpoint http://192.168.8.33:8082/v1 --mode both --count 5
python3 scripts/ling_tool_call_probe.py --endpoint http://192.168.8.36:8000/v1 --mode both --count 5
python3 scripts/ling_cline_smoke.py --endpoint http://192.168.8.36:8000/v1
.venv/bin/python -m unittest -v test_llama_cpp_provider test_llm_idle_activity
```

## Rollback

1. Stop only profile `8570edb9aa75` through the llm-agent profile stop endpoint.
2. Change its `binary_path` back to
   `/home/teemu/bin/llama-server-turboquant-b10269-1.5.1`.
3. Start the same profile and verify `/health` on port 8082.

The old symlink, old packaged build, model files, and cache are intact, so rollback does not
require a rebuild or download.

## Remaining risks

- This is the exact narrow upstream parser commit cherry-picked onto the deployed TurboQuant
  release, not a wholesale fork upgrade. A future TurboQuant update should include the merged
  upstream Bailing support and rerun these tests.
- The existing 262144 configured context exceeds the GGUF's 131072 training context. That
  warning is unchanged and intentionally outside this fix.
- The existing tokenizer EOS warning is unchanged and showed no connection to tool boundaries.
- `--reasoning-preserve` remains disabled. The template advertises support, but the option was
  not needed for this fix and was not included in the acceptance matrix. Test it separately
  before restoring it for benchmark runs.
- The long coding-workflow benchmark was not run. The serving stack is ready for it.
