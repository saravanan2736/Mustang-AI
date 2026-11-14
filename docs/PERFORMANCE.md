# Performance

How Mustang AI was tuned for small GPUs, what was measured, and what turned out not to matter. All numbers are from
one machine (RTX 5050 Laptop GPU, 8 GB; Core i7-13620H; 24 GB RAM; llama.cpp b12000 CUDA) with Qwen3.5 4B, a
Qwen3.5 9B fine-tune (Q4_K_M) and Gemma 4 E4B. They are single runs of a sampled model unless a table says otherwise,
so treat differences under about 10% as noise.

## Method

`scripts/bench_engine.py` starts `llama-server` itself with any flag set and runs fixed workloads (temperature 0, fixed
seed): `decode` (generation speed), `prefill` (a ~6k-token prompt, twice, to see prompt-cache reuse), `edit` (rewrite a
150-line file with one rename), and `tools` (the app's real tool list and agent prompt, 21 requests: is the right tool
called with valid arguments, how many prompt tokens the tools cost, how long a call takes).

```powershell
backend\.venv\Scripts\python.exe scripts\bench_engine.py --model models\<file>.gguf --label test --ctx 8192 -- -np 1 -fit on --flash-attn on --spec-type ngram-mod
```

Everything after `--` goes to llama-server as is, so two flag sets can be compared on the same model and workloads.
End-to-end numbers come from four real tasks run through the app's backend on a fresh copy of a small buggy Python
project: explore it, fix three failing tests, rename a function across files, write and compile a C program with gcc.
Each result was checked independently (tests run, old name gone, program output compared), not taken from the model.

## What was adopted

| Change | Measured effect | Cost |
|---|---|---|
| **N-gram speculative decoding** (`--spec-type ngram-mod`) | Rewriting a file the model has just read: 4B **15.8 s to 2.5 s**, 9B **25.4 s to 3.6 s**. Output identical. Normal text unchanged (about 77 tok/s on the 4B) | none (no extra VRAM) |
| **Thinking off while the agent works** (setting: Auto / Off / Always on) | A tool call: 9B **2.04 s to 1.30 s**, 4B 1.36 s to 0.79 s; accuracy equal (9/10 and 21/21 on the wider test) | the model reasons less on hard tool problems |
| **Thinking budget** (`--reasoning-budget 768`) | Three plain questions on the 9B: with no limit all three used the whole 1,500-token reply on thinking and **got no answer** (about 30 s each); with 768 all three were answered (17 to 22 s) | thinking is cut off after 768 tokens |
| **`--fit` instead of forcing every layer onto the GPU** | On this card everything fits, so no speed change. When memory is short, layers that do not fit run on the CPU instead of the load failing: the 9B with about 1.9 GB of video memory available still runs, at about 7 tok/s | see "Not measured" |
| **One slot** (`-np 1`) | 150 MB less VRAM on the 4B; same speed | none (single user) |
| **Shorter tool descriptions and agent instructions** | 2,134 to 1,821 prompt tokens (Qwen), 1,800 to 1,475 (Gemma); tool-call accuracy unchanged on all three models | none measured |
| **Task list at the end of the user's message** (not in the system message) | The prompt prefix no longer changes during a run, so llama.cpp's prompt cache is never invalidated by `update_tasks` | none |
| **Per-request timings** (log and `/api/models/status`) | Shows how much of each prompt was read and how much came from the cache | none |

### End to end (four coding tasks, 9B and 4B)

| Model | Settings | Total | Explore | Fix tests | Rename | gcc | All verified |
|---|---|---|---|---|---|---|---|
| 9B | old (thinking on, no speculation) | 106 s / 98 s | 8 / 13 s | 29 / 28 s | 51 / 45 s | 18 / 13 s | yes / yes |
| 9B | **new defaults** | **70 s / 66 s** | 8 / 6 s | 20 / 19 s | 32 / 31 s | 12 / 10 s | yes / yes |
| 4B | old | 86 s | 7 s | 23 s | 43 s (hit the 24-step limit) | 12 s | **no** (rename failed) |
| 4B | **new defaults** | **49 s** | 2 s | 17 s | 23 s | 7 s | yes |

About one third less time on the 9B, in two runs each. On the 4B the old settings looped on the rename task until the
step limit; with thinking off it finished. That is one run, so it shows what can happen, not how often.
(One 9B run was marked unverified by my checker because the compiled program was placed in the project root, not in the
`c_demo` folder; the program and its output were correct, and the checker now accepts either place.)

During agent runs 66 to 98% of prompt tokens (per task) came from the prompt cache, because each step's prompt starts
with the previous step's prompt. For example the 9B fixing the tests read 1,063 new prompt tokens and reused 24,707
(96%); the short explore task is lowest because its prompt is mostly new.

## What did not matter

Measured and left alone:

| Tried | Result |
|---|---|
| Dropping `-v` (verbose log) | no speed change; the app reads GPU-layer and memory lines that only `-v` prints, so it stays |
| Larger batch (`-b 2048 -ub 1024`) | no change in prefill or decode; 82 MB more VRAM |
| CPU thread count with part of the model on the CPU (6, 8, 10, 16, default) | 7.1 to 7.4 tok/s in every case: memory-bandwidth bound |
| KV cache quantization (`-ctk q8_0 -ctv q8_0`) | saves only 100 to 180 MB at 8k to 32k on these models (their caches are small by design) and **changes the output slightly**, so it is an option (`kv_cache_type`), not the default |
| `ngram-map-k`, `ngram-map-k4v` speculation | works but about half as fast as `ngram-mod` / `ngram-simple` |

Decode speed (about 80 tok/s on the 4B, 50 tok/s on the 9B) is set by memory bandwidth; the gains above come from doing
less work (fewer thinking tokens, guessed tokens verified in bulk, prompts reused) rather than from faster generation.

## Not measured, and limits

- **A truly small card.** The test machine has 8 GB, and everything tested fits. Windows (WDDM) moves idle GPU
  allocations out to system memory, so I could not reproduce a hard out-of-memory failure by loading extra models. What
  was measured is that `--fit` adapts (the 9B on a ~1.9 GB budget runs at about 7 tok/s); I did not measure what
  the old flags would have done on a real 4 to 6 GB card.
- **Draft-model speculation** (a small model proposing tokens for a large one) was not tried: it needs the same
  tokenizer and extra VRAM, and no pair of the installed models fits an 8 GB card.
- Single runs of a sampled model vary by roughly 10%; the speculation and thinking effects are far larger than that.
- If a custom llama-server runtime does not know the speculation flag, the engine starts again without it.

## Settings

- **Settings, Inference, Thinking:** Auto (default; the model's own behaviour in chat, off while the agent works), Off,
  Always on. `reasoning_budget` (default 768, -1 = unlimited), `speculation` (default on), `kv_cache_type` (f16 or
  q8_0) and `n_gpu_layers` (99 = as many as fit) are accepted by `POST /api/models/load`.
