# Colony

Colony is a small Python tool. It runs several Claude Code sessions as parallel
workers, each in its own terminal pane, driven by one Master session.

A worker is a separate Claude Code session, not a sub-agent. It does one task,
reports a single line to a shared event log, and closes. Because each worker is
its own session, it can ask the user, and it keeps going while the Master
compacts or restarts. The Master plans, writes a brief for each task, waits on
the log in the background, answers questions, and checks results.

For the full explanation of what colony is and why it works this way, read
[docs/colony.md](docs/colony.md).

## Requirements

- Python 3, standard library only. No third-party packages.
- Herdr (required). Herdr is the terminal multiplexer that provides the panes
  and the agent control. Colony drives it through the `herdr` CLI: it splits
  panes, starts agents and reads pane state. Without Herdr, colony does not run.
- The Claude Code CLI (`claude`), which Herdr starts in each worker pane.
- git, used to find the project root so each project gets its own state.

## Install

Put `colony.py` on your `PATH` and keep `colony-models.json` next to it. For
example:

```sh
ln -s "$PWD/colony.py" ~/.local/bin/colony
```

The config is read relative to the script's real path, so the symlink resolves
to the real file's directory and finds `colony-models.json` there. Set
`COLONY_MODELS_CONFIG` to read the config from another path.

## Commands

Master:

- `colony brief <id>` writes a brief template and prints its path.
- `colony start <id> <brief>` opens a pane, starts a worker, and sends it the
  brief. `--retry-of <id>` names the task this one retries.
- `colony wait` blocks until new outcomes, then prints them.
- `colony answer <id> <text>` replies to a worker that asked a QUESTION.
- `colony close <id>` records the session ID and closes the pane.
  `--accept`, `--reject` or `--partial` records the Master's verdict on the
  result. Without one the verdict stays unset, and a worker's PASS is never
  read as acceptance.
- `colony status` prints one line per task; `--all` includes closed tasks.
- Each worker runs with a PreToolUse hook. It refuses an unbounded read of a
  file over `retrieval.read_limit_bytes`, 32,000 by default, and names a ranged
  read, `rg` and `graft skeleton` instead. Searches and graft calls are left
  alone. `colony close` reports what the hook saw.
- `colony start --worktree` gives the worker its own Git worktree and branch
  under the colony state, indexed for graft, so two workers cannot overwrite
  each other. `colony close` removes it when clean and keeps it when it holds
  work. The pane carries `COLONY_ROOT`, so `colony done` works inside it.
- `colony report` shows tokens per worker and for the Master, where those
  tokens went by category, and a cost when the config holds rates. It prints
  weighted units beside the raw token count and names the weighting, because a
  raw count is not a plan-limit count. `--weights NAME` picks one of the sets
  in `limit_weights`: `raw`, `api-price` or `cache-free`. The report also costs
  each accepted change, charging the tasks it retried to the same change, and
  separates a worker's reviewer from its other sub-agents.
  `--json` prints the same report as data.
- `colony checkpoint` writes the Master's state file and prints its path.
- `colony restart --confirm` clears the Master and resumes it from the
  checkpoint.

Worker:

- `colony done <id> <status> <result> <summary>` records the outcome: PASS,
  FAIL, BLOCKED or QUESTION.

## The brief workflow

1. `colony brief <id>` writes a template file and prints its path.
2. Fill in its sections: Goal, Inputs, Acceptance criteria, Owns, You may
   decide, Model, Effort, Result. Owns names the paths the worker may change,
   or `-` for none. `colony start` refuses a brief that still holds template
   placeholders, lacks a section, or names a path an open task already owns.
3. `colony start <id> <brief>` opens a pane beside the Master (later workers
   stack below). It starts a Claude Code worker on the brief's model and
   effort, then sends it the worker prompt.
4. The worker does the task, runs the tests, has a read-only reviewer sub-agent
   check the result, writes the result file, and reports with
   `colony done <id> PASS|FAIL|BLOCKED <result> "<summary>"`. For a decision
   outside the brief it reports `colony done <id> QUESTION - "<question>"` and
   waits.
5. The Master runs `colony wait` in the background. It returns a batch of new
   outcomes in one wake and uses no tokens while it waits.
6. The Master answers a QUESTION with `colony answer <id> <text>`. It confirms
   a PASS by checking the cited lines itself, then runs `colony close <id>` to
   record the worker's session ID and close the pane.

## Model and effort policy

Every brief names a pinned model id and an effort. Both are validated against
`colony-models.json`, so a worker runs the cheapest model that can pass its
task, and never a banned or floating one. A bare alias such as `opus` is
refused, because an alias floats to the newest model. `--model` and `--effort`
on `colony start` override the brief's sections and are validated the same way.

The policy lives in `colony-models.json`. Its keys:

- `worker_models`: the pinned ids a brief may name.
- `banned`: ids a brief may not name.
- `master_model`: the model the Master should run.
- `subagent_models`: the aliases a worker passes to the Agent tool, one of
  `sonnet`, `opus`, `haiku` or `fable`. `reviewer` and `explore` go into the
  worker prompt; `default` goes on the pane as `CLAUDE_CODE_SUBAGENT_MODEL` and
  covers a sub-agent the worker does not name. The Agent tool takes no pinned
  id, so a sub-agent cannot be held to one.
- `efforts`: the allowed effort levels.
- `pricing` (optional): `units`, and a `models` map of rates per million
  tokens. A rate set may name `input`, `output`, `cache_read`,
  `cache_write_1h` and `cache_write_5m`. A model is priced only when every
  category it used has a rate, so `colony report` shows no cost rather than a
  low one. Colony ships the block empty: read the rates from the vendor's
  current price list. A config without the block stays valid.

Edit the config, not the code, to set your own policy.

## Environment variables

| Variable | Default | What it does |
|---|---|---|
| `COLONY_HOME` | `~/.local/state/claude-system/colony` | State directory. Colony keeps per-project, per-colony state under it |
| `COLONY_NAME` | `main` | Colony name, so one project can hold several colonies. Workers inherit it |
| `COLONY_HERDR` | `herdr` | The Herdr executable colony calls |
| `COLONY_MODELS_CONFIG` | next to the script | Path to the model-policy config, overriding the default location |
| `CLAUDE_CONFIG_DIR` | `~/.claude` | Where Claude Code keeps session transcripts. Colony reads them for `colony report` and the Master-model warning |
| `COLONY_CHECK_EVERY` | `60` | Seconds between worker health checks during `colony wait` |
| `COLONY_START_READY_WAIT` | `120` | Seconds `colony start` waits for a worker agent that Herdr reports as not ready. It gives up only if the agent never reports a session |
| `COLONY_IDLE_SAMPLES` | `3` | Health checks in a row that must find a worker idle, with a pane that is not waiting for input, before `colony wait` reports IDLE |
| `COLONY_SETTLE` | `3` | Seconds `colony wait` waits to collect outcomes that arrive together |

## Tests

```sh
bash tests/colony.sh
```

The tests run colony against a fake `herdr`, so they need no real Herdr. There
are 211 cases.

```sh
python3 tests/archive-check.py
```

`tests/archive-check.py` reconciles `colony report`'s token counts against the
real transcripts of every session the colonies recorded. It counts each session
a second way and compares. It reads only, needs no Herdr, and exits non-zero
when a count differs.

## Documentation

[docs/colony.md](docs/colony.md) is the deep explanation. It covers the problem
colony solves, how work moves, the decisions and their reasons, the design
principles, the evidence, and the known limits.

## License

MIT. See [LICENSE](LICENSE).
