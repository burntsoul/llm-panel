# Ling 3.0 Flash infinite-output investigation

Date: 2026-08-17

Verdict: `ready_for_benchmark`

## Conclusion

The incident was a combination of model sampling degeneration, a valid grammar constraint, and
an unbounded server default:

1. Ling began repeating text while generating the string value of a Cline completion-report
   tool call. Newlines and `all` are both valid characters in that string.
2. Once the lazy PEG grammar sees `<tool_call>`, it must keep the output syntactically valid.
   EOG is not legal while an `<arg_value>` and the containing tool call are unfinished, so the
   grammar correctly masks EOG until the model emits the closing tags.
3. Cline omitted `max_tokens`. llama-server's inherited default was `--predict -1`, documented
   by the binary as infinity, so there was no final resource bound when the model failed to emit
   the closing tags.

PEG did not create the repeated tokens. It allowed them because an arbitrary string argument
must allow them. The exact stochastic trigger for Ling's degeneration was not retained in the
old logs and did not reproduce in ten bounded final-report trials. The deployed defaults were,
however, mismatched with Ling's published recommendations: llama.cpp used temperature `0.8` and
top-k `40` when the client omitted them, while Ling recommends `0.6` and `20` respectively.

The following suspected causes were excluded:

- **Tokenizer/EOG metadata:** the GGUF and official tokenizer both define EOS as token `156895`,
  `<|role_end|>`. llama.cpp's warning was a false positive: the loader inserted the configured
  EOS into `special_eog_ids` immediately after warning. A successful verbose generation ended
  with token `156895` and `stop_type=eos`.
- **Chat template:** the embedded template and the official `chat_template.jinja` have the same
  SHA-256, `b4ab3a1c8f748e6f874d9aea102333efe7ab82528e8ab81c2eb155851e8705c6`.
- **Reasoning configuration:** the template's default thinking path and automatic DeepSeek-style
  extraction produced valid reasoning plus tool calls. `--reasoning-preserve` was not involved.
- **llm-agent routing:** llm-agent copies and forwards the request body. It does not modify tool
  arguments; its llama.cpp-specific response normalization only removes thinking tags. Direct
  and routed probes behave identically.
- **The previous Bailing parser defect:** build `10698` retains commit `0266ebc` through fork
  commit `86d2ca6d`. Multi-argument direct/routed streaming and non-streaming tests remain clean.
- **Context extension as the incident trigger:** the context metadata was genuinely wrong, but
  the same completion-report path works at a roughly 1K-token prompt and the failure occurred
  during token generation, not template parsing. No evidence connected the repeated token
  sequence to a context boundary.

## Historical evidence

`logs/llm-agent.log` shows the affected Cline run used streaming requests with no output limit.
For the first long stall:

- `16:04:47`: request with 16 messages and `max_tokens=None`.
- `16:05:09`: the Ling slot was confirmed busy.
- `16:18:22`: the next request arrived, after the looping stream was stopped.

The slot therefore remained occupied for about 13 minutes. Other turns in the same workflow
also had multi-minute stalls. The old llama-server request log was overwritten by later profile
starts, and llm-agent intentionally did not log request bodies or generated content, so the
exact failed raw token stream and seed are unavailable.

Verbose bounded probes established the relevant parser behavior:

- `tool_choice=auto` selects `chat_format=peg-native` with a lazy grammar triggered by token
  `156896` (`<tool_call>`).
- The generated `attempt_completion.result` rule consumes arbitrary text until
  `</arg_value>`.
- A healthy final report emitted closing tags and then EOS token `156895`.

## Corrections deployed

### 1. Ling EOG recognition

Added `<|role_end|>` to llama.cpp's recognized EOG spellings. This does not change the effective
EOG set—the configured EOS was already added by the fallback sanity check—but it removes the
misleading tokenizer warning and marks the canonical Ling token directly.

- Previous fork commit: `86d2ca6db826b34b37fa80214f5f8605487be509`
- New fork commit: `e218133a` (`tokenizer: recognize Bailing role_end as EOG`)
- Previous build: `10697`
- New build: `10698`
- New binary SHA-256:
  `a053e34ddca6173d40da88b9c10b22d338467d878aea2445c6e5075cbb3a305e`
- New runtime:
  `/home/teemu/bin/llama-server-turboquant-b10269-1.5.1-ling-eog-e218133a`
- Previous build and runtime remain intact.

The source patch is preserved in `reports/ling-infinite-output/role-end-eog.patch`.

### 2. Correct GGUF context metadata

The official model config declares `max_position_embeddings=262144`, `rope_theta=6000000`, and
no extra RoPE scaling. Both GGUF shards were checked; only shard 1 contains the context field.
Its same-width `UINT32` value was changed in place:

```text
bailingmoe3.context_length: 131072 -> 262144
```

No tensor, tokenizer, template, quantization, or RoPE-base field changed. Startup now has neither
the `n_ctx_seq > n_ctx_train` warning nor the server's context-extension warning.

### 3. Documented sampling and output defaults

Ling's profile now supplies these defaults when a client omits them:

```text
--temp 0.6 --top-k 20 --predict 32768
```

Temperature and top-k are Ling's published recommendations. The 32K output bound is the model
authors' coding-evaluation setting, not an arbitrary stop string. Explicit per-request values
still override all three defaults.

A routed final-report request that omitted temperature, top-k, and max tokens reported the
effective server settings as temperature `0.6`, top-k `20`, and `n_predict=32768`; it completed
normally after 131 tokens with EOS `156895`.

## Validation

Pre-deployment diagnosis:

- Existing bounded five-step Cline smoke: pass.
- All-tools, automatic-choice final report at llama.cpp defaults: 5/5 pass.
- All-tools, automatic-choice final report at Ling's recommended sampling: 5/5 pass.
- No blank-line or repeated-`all` runs occurred in those ten bounded final turns.

Post-deployment regression matrix:

- Direct non-streaming exact two-argument calls: 5/5.
- Direct streaming exact reconstructed calls: 5/5.
- Direct multi-turn tool result: pass.
- Routed non-streaming exact two-argument calls: 5/5.
- Routed streaming exact reconstructed calls: 5/5.
- Routed multi-turn tool result: pass.
- Routed Cline-style read/write/edit/command/final-report workflow: pass.
- Omitted-limit routed final report: pass; effective limit 32768, EOS stop.
- llm-agent focused tests: 59/59.
- Startup health: `{"status":"ok"}`.
- Startup EOG/context warning check: clean.

The long benchmark was not run.

## Commands used

```bash
cmake --build build-ling-fix-cuda124 --config Release --target llama-server -j 14

python3 scripts/ling_tool_call_probe.py \
  --endpoint http://192.168.8.33:8082/v1 --mode both --count 5 --multi-turn
python3 scripts/ling_tool_call_probe.py \
  --endpoint http://192.168.8.36:8000/v1 --mode both --count 5 --multi-turn

python3 scripts/ling_completion_loop_probe.py \
  --endpoint http://192.168.8.33:8082/v1 --count 5 --max-tokens 512
python3 scripts/ling_completion_loop_probe.py \
  --endpoint http://192.168.8.33:8082/v1 --count 5 --max-tokens 512 \
  --temperature 0.6 --top-k 20
python3 scripts/ling_completion_loop_probe.py \
  --endpoint http://192.168.8.36:8000/v1 --count 1 --omit-max-tokens

.venv/bin/python -m unittest -v test_llama_cpp_provider test_llm_idle_activity
```

The GGUF field was changed with llama.cpp's bundled `gguf_set_metadata.py` after a dry run and a
controlled Ling-only stop.

## Rollback

1. Stop only profile `8570edb9aa75`.
2. Change `binary_path` back to
   `/home/teemu/bin/llama-server-turboquant-b10269-1.5.1-ling-parser-fix-86d2ca6d`.
3. Remove `--temp 0.6 --top-k 20 --predict 32768` from Ling's `extra_args` if the old defaults
   are specifically desired.
4. To reverse the metadata correction, run `gguf_set_metadata.py --force` on shard 1 with
   `bailingmoe3.context_length 131072`. This is not recommended because 262144 is canonical.
5. Start the profile and verify `/health`.

## Remaining risks

- The exact stochastic failing stream was not retained, and ten bounded attempts did not
  reproduce it. The diagnosis of model sampling degeneration is based on the emitted-token
  pattern and on the fact that neither PEG nor llm-agent generates token choices.
- A 32K cap prevents an infinite request but can still occupy this CPU-expert configuration for
  a long time in the worst case. No shorter arbitrary watchdog or stop sequence was added.
- The GGUF metadata edit is local. Re-downloading the AtomicChat shard can restore the incorrect
  131072 value unless the publisher updates the artifact.
- The EOG recognition commit is a narrow local fork change and should be upstreamed or carried
  into the next TurboQuant update.
- `GET /props` still displays `max_tokens=-1` in its base `default_generation_settings`, but a
  real omitted-limit request's verbose settings correctly show and enforce 32768.
- `--reasoning-preserve` remains disabled. The template supports it and the official model card
  uses it in one benchmark, but it was unrelated to this incident and was not required for a
  correct workflow. Test it separately before enabling it.

References:

- Official model card and sampling guidance:
  https://huggingface.co/inclusionAI/Ling-3.0-flash
- Official tokenizer configuration:
  https://huggingface.co/inclusionAI/Ling-3.0-flash/blob/main/tokenizer_config.json
- Official model configuration:
  https://huggingface.co/inclusionAI/Ling-3.0-flash/blob/main/config.json
- Official chat template:
  https://huggingface.co/inclusionAI/Ling-3.0-flash/blob/main/chat_template.jinja
- Preserved Bailing parser correction:
  https://github.com/ggml-org/llama.cpp/commit/0266ebca66bd95b7a85d37b8ca08ccf9812b85cc
