# Plan: which orchestration concepts help colony, and in what order

Written 2026-10-19. Each verdict cites a measurement. The sources are
`~/.local/state/claude-system/reviews/colony-speed-2026-10-19/REPORT.md` (all
243 archived colony sessions), `colony-burn-2026-10-11/out/REPORT.md`,
`weekly-limit-weights-2026-10-06/report.md` and
`~/Claude-System/docs/token-forensics/retrieval-efficiency-2026-10-18.md`.

## The one fact that decides the order

Raw tokens are not a safe stand-in for plan-limit use. Cache reads are 98.3% of
colony's 9.95B raw tokens, and our own fits put their weight at 0 to 0.05.

| Source | Raw tokens | API price weights | Cache reads at zero |
|---|---:|---:|---:|
| Cache reads | 98.3% | 68.4% | 0% |
| Cache writes | 1.3% | 16.4% | 52.0% |
| Output | 0.4% | 15.2% | 48.0% |
| Thinking, inside output | 0.2% | 6.5% | 20.7% |

Any concept that only moves cache reads is worth between almost nothing and a
great deal, and we cannot yet say which. Any concept that moves output or what
enters a context is worth a lot under every weighting. That single test sorts
the whole catalogue.

## Already in colony, with evidence

| Concept | Where it already is |
|---|---|
| Master-worker orchestration | The colony. One Master, bounded briefs, one task each |
| Proof-carrying artifacts | PASS must cite `path:line` or a URL, a reviewer sub-agent checks each criterion, and `colony close` records a verdict. 198 of 227 workers ran exactly one sub-agent, the reviewer |
| Information-theoretic optimisation | The reason the worker prompt now sends reading to graft and to one read-only sub-agent |
| Sub-agent optimisation | Shipped 2026-10-19: an `explore_budget` of 20 own tool calls, then a read-only Sonnet sub-agent. A sub-agent request costs 87,713 tokens against a worker's 208,114, and its reads never enter the worker's context. No nesting exists to remove |
| Context engineering, in part | The brief is a hand-written smallest-sufficient context: goal, inputs, criteria, what the worker may decide |

## Measured out: the numbers are already in

| Concept | Measured | Verdict |
|---|---|---|
| Prompt caching, prefix stability | Fresh input is **0.0010%** of all worker input. The prefix cache already hits on everything | No headroom. Nothing to stabilise |
| Common-subexpression elimination | Identical call with an identical result inside one worker: **414 of 28,692 calls, 1.44%**. Across tasks, file refetch is ~85,000 tokens over 88 tasks, 0.16% of material | Immaterial |
| Incremental computation, fingerprinted artifacts | A findings file between tasks costs **2.1x to 8.3x** the rediscovery it would save, before any invalidation work | Negative for file contents |
| Semantic caching of results | Same measurement as above. `docs-search` and `memsearch` are the cheap version and already installed | Use the cheap version, build nothing |
| Partial-order reduction | No evidence colony ever explores alternative orders | Not applicable |
| KV-cache sharing | Needs control of the model-serving infrastructure, which a hosted Claude Code session does not expose | Unavailable |
| Causal experimentation, repository benchmarking | The 2026-10-11 analysis costed a controlled proof of an 8% saving at about **49 paired runs, near 1 billion tokens** | Too dear for small effects. Reserve it for a change we expect to be large |
| Learned inference scheduling, contextual bandits | Verdicts exist only from 2026-10-11, and the labelled set is 160 PASS against 51 FAIL. Attribution is still in raw tokens | Premature. It would learn the wrong objective |

## Phase 1: token attribution

This is first because it is the instrument. Without it, every later decision repeats the
error of 2026-10-19. A request budget was built and reverted the same day. It
optimised a quantity that may weigh zero.

What changes in `colony report`:

- Weighted units beside raw, with the weighting named and switchable, and raw
  never shown alone.
- Per task: tool calls, retries, the verdict, and duplicate work.
- Tokens for each **verified, integrated change**, not for each session. The
  fields exist: `verdict` and `retry_of` since 2026-10-11.
- The Master's own cost split from the workers'. Masters are 36% of all spend
  at 348,563 tokens a request, the dearest request in the colony.

What it is worth: nothing by itself. It decides everything after it.

## Phase 2: output and thinking, which survive every weighting

Output is 15.2% of weighted burn at API prices and 48% with cache reads at
zero. Thinking alone is 6.5% and 20.7%.

| Measured output a response | Model and effort |
|---:|---|
| 1,129 | Opus 5.5 at xhigh (now banned) |
| 996 | Opus 4.8 |
| 875 | Opus 5 at high |
| 698 | Sonnet 5.5 at medium |

Knobs read from the installed Claude Code 2.1.295 binary, not from a blog:
`MAX_THINKING_TOKENS`, `CLAUDE_CODE_SUBAGENT_MODEL`,
`DISABLE_INTERLEAVED_THINKING`.

Concepts this covers: model routing, adaptive test-time compute,
budget-aware stopping, optimal stopping, cost-constrained reliability.

One warning from 2026-10-19. The budget that was reverted capped *requests*,
which is a cache-read lever, and it cut into real work. 94% of workers would
hand over. A budget on *exploration depth* is a different thing. It caps
output and thinking, and the archive supports it: 49% of worker responses only
read.

## Phase 3: parallel execution, ownership and conflict-aware scheduling

This buys wall speed and correctness. It buys no tokens, and three parallel
sessions cost about three times the tokens of one.

| Measured | Value |
|---|---|
| Mean concurrency | 1.96 |
| Wall hours with exactly one worker alive | 84.3 of 122, 69.4% |
| Most workers alive at once | 6 |
| Worker span | median 30 min, mean 64 min |
| Mean time in gaps over 2 minutes | 39 of those 64 minutes |
| Request latency | 3.9 s under a 50k context, 8.1 s at 200k |

One fault is still unmet. Workers share one working tree and call git
themselves. Nothing assigns a file to one worker, and nothing serialises the
commits. `munderdiffl.in` reports that discipline alone fails here, with
`index.lock` and half-applied changes. Mean concurrency of 1.96 is why colony
does not meet it yet.

Checked against the herdr API schema and a live `herdr pane list`.
`herdr tab create` returns a `TabInfo` with `tab_id`. `herdr pane list` gives
each pane's `tab_id`. `herdr worktree create` takes `--branch`, `--base` and
`--path`. Claude Code itself has `-w, --worktree` and `--tmux`.

Concepts this covers: parallel execution, conflict-aware scheduling, adaptive
concurrency, ownership partitioning.

Two questions to answer before it ships: whether graft indexes each worktree or
only the main tree, and what removes a worktree when `colony close` runs.

## Phase 4: move Master work out of the model

Masters are 36% of all colony spend. The event log was measured against the
Master transcripts. The Master spends a median 21 responses and 6.6M tokens
between one STARTED event and the next. It spends a median 6 responses and 1.9M
from a PASS to the CLOSED after it.

Concepts this covers: compiler-style orchestration with a typed task DAG,
deterministic workflow extraction, context compilation, artifact-based
handoffs.

The deterministic part has a measurement behind it. Workers made 24,187 Bash
calls, 78.2% of them starting with `cd`, at a median length of 274
characters. Repeated shell work that a script could do is work no model needs
to reason about, and each avoided call removes a whole request.

Colony already holds most of a DAG: briefs, task state, an event log, verdicts
and `retry_of`. What it lacks is a runtime that schedules and retries without a
Master turn.

## Left open, deliberately

- **Reasoning dependency graphs.** This targets the one cost never measured.
  The 2026-10-11 analysis proved file rediscovery is immaterial. It proved
  nothing about a worker re-deriving a conclusion, because re-deriving leaves
  no tool call. It is the only honest open item in that family, and also the
  dearest to build. It needs a cheap proxy measurement first.
- **Session reuse and forking.** Sharing a prefix saves cache reads, whose
  weight is unknown, and it cuts against one task per worker, which exists for
  a measured reason. Decide after Phase 1.
- **Speculative execution.** It spends tokens to buy latency. Under one shared
  quota that is a straight loss unless wall speed dominates.
- **The meter refit.** The 2026-10-06 report names the next step. Log when
  each meter value was last seen. Then refit on five-hour steps, with no chats
  or web tools. It would replace the 0-to-0.05 guess with a measurement. It changes
  the status line, so it needs agreement first.

## Evaluation metric for all of it

Tokens for each verified, integrated change, under a named weighting, beside
wall-clock time, retries and rework. Not tokens for each session.
