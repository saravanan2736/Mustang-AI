# Mustang AI — Milestone 5 Phase 0: Engine hardening report

Branch `m5-phase-0` (from `m5-plan`). Plan: `M5_AGENTIC_PLAN.md` §1.1 and Phase 0.

## 1. Problem

`llama-server` b12000 has experimental built-in agent tools: `read_file`, `file_glob_search`, `grep_search`, `exec_shell_command`, `write_file`, `edit_file`, `get_info`. They run inside the server with its full permissions, with no folder limit and no approval. They're off by default, but llama-server reads every option from `LLAMA_ARG_<NAME>` environment variables, so `LLAMA_ARG_TOOLS=all` or `LLAMA_ARG_AGENT=1` turns them on.

Measured before the fix, starting llama-server with `LLAMA_ARG_TOOLS=all` in its environment:
- `GET /tools` returns 200 with the tool list.
- The log shows `security: server tools (experimental) enabled - do not expose to untrusted environments`.

Exposure in Mustang AI:
- **Desktop app:** not exposed. The supervisor clears the backend's environment to an allowlist (`crates/mustang-supervisor`, `env_clear()`), so no `LLAMA_ARG_*` variable reaches the engine.
- **Browser dev mode:** exposed. The backend passed its whole environment to llama-server (`env={**os.environ, ...}`), so a variable set in the developer's shell would have turned the tools on.

The engine port is loopback-only and protected by the per-process API key, so only a client with that key could have used them. Still, they must never be on.

## 2. Change

`backend/app/core/llama_engine.py`:
- **`--no-agent` on every start.** An explicit command-line option takes precedence over its environment variable.
- **New `_engine_environment()`** builds llama-server's environment: the backend's own variables minus every `LLAMA_ARG_*`, plus the per-process `LLAMA_API_KEY`. Without the `LLAMA_ARG_TOOLS` variable there are no tools, since `--tools` defaults to none.

Nothing else changes. Other variables (for example `GGML_*` or CUDA settings) still pass through, and the desktop allowlist is untouched.

## 3. Tests

| Test | Result |
|---|---|
| `test_spawn_passes_api_key_by_env_and_hardening_flags` (extended: `--no-agent` on the command line) | pass; **fails on the old code** |
| `test_llama_arg_variables_never_reach_the_engine` (new): `LLAMA_ARG_TOOLS`, `LLAMA_ARG_AGENT`, `LLAMA_ARG_HOST` and an outside `LLAMA_API_KEY` set; checks none of the `LLAMA_ARG_*` reach the engine, the key is the engine's own, and unrelated variables pass | pass; **fails on the old code** |
| Real llama-server through `LlamaServerEngine`, with `LLAMA_ARG_TOOLS=all` and `LLAMA_ARG_AGENT=1` set | `/tools` → **403 "this feature is disabled"** (401 without the key); `cors_proxy_enabled: false`; chat works |
| Backend pytest, dev venv | **163 passed** (162 + 1 new) |
| Backend pytest `-m gpu` | **5 passed** |
| Switch timing (`switch_timing.py`) | cold 993 ms; switches 928 / 1431 / 1426 ms (unchanged from M4 Phase G) |
| Data guard (model + `backend\run\mustang.db` SHA-256) | unchanged |

Not run in this phase: a desktop rebuild and the embedded-runtime suite. The change is in the bundled backend source, so it ships with the next `scripts/build_release.py` build; the desktop app wasn't exposed to begin with (§1).

## 4. Release note line

> Hardening: the local engine never enables llama.cpp's experimental built-in agent tools, even if `LLAMA_ARG_*` variables are set in the environment.

## 5. Next

Phase A (tool-calling spike) awaits approval. The other owner decisions are listed in `M5_AGENTIC_PLAN.md` §8.
