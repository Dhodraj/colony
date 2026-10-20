# Plan: cut colony token burn and raise wall speed

Written 2026-10-19. Every number here comes from the measurement in
`~/.local/state/claude-system/reviews/colony-speed-2026-10-19/REPORT.md`, which
read all 243 archived colony sessions. Figures from outside that folder are
marked as such.

## The frame

One request costs about 205,000 tokens, because each request re-sends the whole
context. A worker takes a median of 119 requests. So the bill is
`requests x context`, and nothing inside a request costs near the request.

Worker cache reads, 5.79B, come from:

| Source | Share |
|---|---:|
| Result of a Bash call | 42.0% |
| Startup context, re-sent every request | 22.5% |
| Model output that is not thinking | 18.4% |
| Thinking | 16.7% |
| Result of a Read call | 12.2% |

The model sums to 115% of the measured total, because compaction resets a
context. Read the shares, not the totals.

## Done on 2026-10-19

| Change | Where | Why |
|---|---|---|
| A worker finds code with graft, puts independent reads in one response, spends at most 20 of its own tool calls on reading, and hands the rest to one read-only Sonnet sub-agent | `PROMPT` in `colony.py` | 49% of worker responses only read. A sub-agent request costs 87,713 tokens against a worker's 208,114, and its reads never enter the worker's context |
| graft is a dependency, as herdr is: `colony start` runs `graft check <root>` and refuses to start against a missing or stale index. `COLONY_GRAFT` names the executable | `graft_check` in `colony.py` | No worker in the archive called graft, Grep or Glob once, against 24,187 Bash calls. A stale index would send a worker to the wrong lines |
| `explore_model` and `explore_budget` join the policy file | `colony-models.json` | One config file holds the policy, as the reviewer model already does |
| A Master compacts at 400,000, the window every other session uses | `MASTER_WINDOW` in `colony.py` | The user chose it on 2026-10-19. Masters are 36% of all colony spend at 348,563 tokens a request |

218 tests pass: `bash tests/colony.sh`.

## Proposed, in the order the measurement supports

### P1. Output discipline in workers: caveman, and effort that matches the task

Model output is 18.4% of a worker's burn and thinking is 16.7%, so 35% of the
bill is text the worker wrote and then re-read. No colony repo carries the
caveman activation rule, so no worker ran it.

Two separate knobs, because they act on different tokens:

- caveman cuts prose. Its own boundary rule keeps result files, briefs, memory
  and commits in normal English, which colony needs.
- Effort cuts thinking. 153 workers ran at high and 68 at xhigh. An xhigh
  worker wrote 1,129 output tokens a request, high 885, medium 708.

Do it in `colony start`, not per repo, so it cannot drift between the nine
colony roots.

Risk: compressed prose could cost a worker accuracy. The brief's acceptance
criteria and the reviewer sub-agent are the guard.

### P2. Compress a command's output before the model sees it

Bash results are the largest single source at 42%. 23,711 calls, median 473
tokens, 90th percentile 2,221.

Two outside tools do this. Headroom is a library, a proxy and an MCP server.
RTK sits between the command and the agent. Headroom claims about 20% fewer
tokens for coding agents and 60% to 95% for JSON. RTK claims 60% to 90% less
command output. Both sets of figures are the projects' own.

Honest limits: `bashOutputMaxChars: 4000` already caps a long result, and the
mass sits in the count of small results, not in a few large ones. The analysis
of 2026-10-11 put a size rule at 10% to 15% of what enters a context. So expect
far less than 42%.

Try it in a throwaway container first. A tool between the command and the agent
can change meaning, and an exact error string has to survive.

### P3. A tab for each worker, and a worktree where the work allows

Splitting panes stops being readable past three workers. Measured: six workers
ran at once, and 69.4% of the colony's 122 wall hours had exactly one worker
alive. Mean concurrency is 1.96.

The path exists in herdr today, checked against its API schema and a live
`herdr pane list`:

1. `herdr tab create --cwd <root> --label "<project> <task>" --env COLONY_NAME=... --no-focus` returns a `TabInfo` with `tab_id`.
2. `herdr pane list` returns each pane's `pane_id` and `tab_id`. Take the pane in the new tab.
3. `herdr agent start <name> --pane <pane_id> ...`, as now.

A worktree for each worker uses `herdr worktree create --branch --base --path
--label`. It also answers a limit recorded in `colony.md`. The dirty flag does not say
how the tree differed. So a revision does not identify the code a worker
read.

Two questions come first. Does graft index each worktree, or only the main
tree? What removes a worktree when `colony close` runs? Answer both before this
ships. A worktree left behind is worse than a stacked pane.

This buys wall speed, not tokens.

### P4. Bind memsearch and docs-search, as graft is now bound

- `docs-search "<query>"` searches markdown by meaning from a local index at
  `<root>/.memsearch/docs.db`, with local embeddings and no network. Read
  results are 12.2% of a worker's burn, median 2,400 tokens and 13,824 at the
  90th percentile. How much of that is markdown is not measured.
- `memsearch` recall answers the blind spot the 2026-10-11 analysis named.
  That analysis proved refetching files is immaterial, about 85,000 tokens over
  88 tasks. It could prove nothing about a worker re-deriving a conclusion,
  because re-deriving leaves no tool call behind.

Same shape as graft: a check in `colony start`, and one clause in the worker
prompt.

### P5. Stop writing numbers into documents

`docs/colony.md` is 475 lines and carries measured counts in its evidence and
decision tables. Several are already wrong: it says 3.49B tokens, a re-read
factor of 65.4 and 88 tasks, against 9.95B, 75.9 and 227 workers today. A
Master reads that file, so each stale number is both wrong and paid for.

Rule to adopt: a measured number lives in the review folder and the document
names the command that prints it. Keep a number only when it sets behaviour. Examples: the
400,000 window, the 20-call explore budget, the 12-call reviewer.

The saving is not measured. The correctness gain is the reason.

### P6. Machine-readable output

`colony report --json` exists. Add `--json` to `colony status` and `colony
wait`. A Master then reads a short object, not a table it must re-read. This is
small: the whole archive holds 192 `colony` calls.

### P7. Rust: not recommended

| Measured | Value |
|---|---|
| `python3 colony.py --help` | 0.03 s to 0.04 s |
| Python interpreter floor on this machine | 0.01 s |
| `colony` calls in the whole archive | 192 |
| Python time across the whole archive | about 6 seconds |
| Wall hours the colony has run | 122 |

Wall time is model latency, a median of 7.3 seconds a request, and worker
idling, a mean of 39 minutes in every 64. A rewrite would risk 211 passing
tests to win 6 seconds. If wall speed is the goal, cut requests and raise
concurrency.

## Reverted on 2026-10-19: the request budget

A request budget with a handover was built and reverted the same day. The code
is kept in
`~/.local/state/claude-system/reviews/colony-speed-2026-10-19/reverted-budget-work/`.

Why it was reverted: it moves cache reads and nothing else. The accepted
finding of 2026-10-06 says raw tokens are not a safe stand-in for plan-limit
use. Our own fits put the cache-read weight at 0 to 0.05. It is in
`~/.local/state/claude-system/reviews/weekly-limit-weights-2026-10-06/report.md`. Every ranking earlier in this plan was in
raw tokens, which are 98.3% cache reads. That was the mistake.

The same 9.95B tokens, under three weightings:

| Source | Raw tokens | API price weights | Cache reads at zero |
|---|---:|---:|---:|
| Cache reads | 98.3% | 68.4% | 0% |
| Cache writes | 1.3% | 16.4% | 52.0% |
| Output | 0.4% | 15.2% | 48.0% |
| Thinking, inside output | 0.2% | 6.5% | 20.7% |

Weights relative to base input, from the API price list: cache read 0.1, 5-minute
write 1.25, 1-hour write 2, output 5. No official page says the plan limits use
them. The 68.4% here reproduces the 66.7% measured on 2026-10-06, so the two
agree.

What this does to the ranking:

| Lever | Raw tokens | Cache reads at zero |
|---|---|---|
| A request budget and a handover | -26% of 5.89B | about nothing |
| Startup context on every request | 22.5% | about nothing |
| Output and thinking discipline | 0.4% | 48%, thinking alone 20.7% |
| Keeping material out of a context | 1.3% | 52% |
| graft and a sub-agent for the reading | large | partial: it also keeps material out |

The two levers ranked last above are the only two that survive every weighting.
The budget was the first to die, and it carried the only measured quality risk.
94% of workers would hand over, the median worker twice. The FAIL rate in the
archive also falls as sessions get longer: 33% under 40 tool calls, 7% between
160 and 199. That correlation is confounded, because a FAIL ends a session
early.

So the direction is output and thinking first, material entering a context
second, and session length last.

## Verified knobs in Claude Code 2.1.295

Read from the installed binary and `claude --help`, not from a blog:

| Knob | What it reaches |
|---|---|
| `MAX_THINKING_TOKENS` | thinking, the 20.7% line |
| `CLAUDE_CODE_SUBAGENT_MODEL` | every sub-agent's model |
| `DISABLE_INTERLEAVED_THINKING` | thinking between tool calls |
| `-w, --worktree [name]`, `--tmux` | a worktree and a terminal for each session, built in |
| `/context` | what fills the window: system prompt, CLAUDE.md, MCP servers, skills |
| `/usage` | the session's own token count |

`/context` measures the 44,592-token startup context that this plan called
unmeasured. It needs no probe.

## What the tools for many sessions actually solve

Three were read on 2026-10-19: `munderdiffl.in/blog/manage-multiple-claude-code-sessions`,
`conductor.build` and `github.com/twall/planner`.

None of them manages tokens. All three manage coordination and visibility.

They agree on four things colony already does. One session for each
independent unit of work. A named role for each. Tight scope. Durable facts
written to a file that the next session reads.

What they add that colony lacks:

| Theirs | Colony today |
|---|---|
| Trouble starts at about the third session; manual management stops working at three or four | The user hit the same wall past three panes. Measured: six workers ran at once, mean concurrency 1.96 |
| One writer for each file, and a single committer with retry and stale-lock recovery; agents never call git | Workers share one tree and call git themselves. Nothing stops two editing one file |
| Attach to a session, or preview its output full screen, from one dashboard | `colony status` prints one line for each task. There is no preview |
| Semantic recall over the notes, so a session reads only what it needs | The worker prompt saves to memory. Nothing tells it to recall |

Their position on tokens, where they state one: parallel sessions share one
quota. Three at once cost about three times the tokens and buy wall time, not
capacity. Keep concurrency to what you can review.

## What the outside literature says

Secondary sources, not checked against their own papers:

- A fixed-budget comparison found single agents matching or beating multi-agent
  systems on multi-hop reasoning.
- One report puts multi-agent overhead at about 15 times a chat interaction.
- A sub-agent should return a condensed summary, reportedly 1,000 to 2,000
  tokens, not a transcript. Colony's reviewer already has a 12-call cap; the
  new explore sub-agent needs the same discipline.
- Keeping only the most recent tool outputs and replacing older ones with
  placeholders is reported to work in a SWE-agent study. That is the same idea
  as P2, applied to age instead of size.
- Restricting each sub-agent's tools by role keeps its context lean. The new
  explore sub-agent is read-only for that reason.
