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

There are two backends. `colony` (`colony.py`) runs each worker in its own
Herdr pane and needs Herdr. `colonybg` (`colony_bg.py`) runs each worker as a
`claude --bg` background session instead, with no Herdr call anywhere in it.

The two share every command that is not backend-specific: `brief`, `done`,
`status`, `report`. They use the same state directory layout too. Either
tool's `status` and `report` read a colony the other started. But `answer`
and `close` need the tool that started the worker.
[docs/colony.md](docs/colony.md#colonybg-the-no-herdr-backend) covers the
real differences, including the one thing `colonybg` cannot yet do as well:
restart the Master in place.

## Requirements

- Python 3, standard library only. No third-party packages.
- The Claude Code CLI (`claude`).
- git, used to find the project root so each project gets its own state.
- `colony` also needs Herdr (required): the terminal multiplexer that
  provides the panes and the agent control, driven through the `herdr` CLI.
  Without Herdr, `colony` does not run; use `colonybg` instead.
- `colonybg` needs nothing beyond `claude` and git. Open the project with
  `claude` once first. `claude --bg` refuses a repository that is not
  trusted, and shows no prompt to accept.
- The skill `colony:split-the-work` calls skills from three packages: mstack,
  pstack and Dhodraj/skills. Without one of them, the skill does that step
  as its own text describes it and says which skill was missing.

## Install as a Claude Code plugin

Run `/plugin marketplace add Dhodraj/colony`, then `/plugin install colony@colony`.

The plugin puts `colony` and `colonybg` on the PATH of Claude's Bash tool. It also adds the skill `colony:split-the-work`.

Herdr is still a separate install. `colony` needs it. `colonybg` does not.

To use the commands in your own terminal, keep the `ln -s` lines from the Install section below.

## Install

Put `colony.py`, `colony_bg.py` and `colony_core.py` on your `PATH` together,
and keep `colony-models.json` next to them. `colony_core.py` holds code the
other two import; run it directly only for the worker-side retrieval hook.
For example:

```sh
ln -s "$PWD/colony.py" ~/.local/bin/colony
ln -s "$PWD/colony_bg.py" ~/.local/bin/colonybg
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

### colonybg, the no-Herdr backend

`colonybg` takes the same `brief`, `done`, `status` and `report` commands,
unchanged. Where it differs from `colony`:

- `colonybg start <id> <brief>` starts the worker as `claude --bg`, not a
  pane. There is no `--tab`: a background session has no layout to choose.
  Its output names the session id, not a pane id.
- `colonybg answer <id> <text>` and `colonybg close <id>` stop the worker's
  session first, keeping its conversation, then act. `claude --resume`
  cannot deliver a second prompt to a session that is still running.
  Checked against the real CLI: it forks a copy instead. Both refuse when
  the worker is still running after the stop.
- `colonybg logs <id>` prints a worker's recent terminal output.
  `colonybg attach <id>` opens its session in the current terminal. Neither
  has a `colony` equivalent, since a Herdr pane is already on screen.
- `colonybg restart --confirm` cannot clear the Master's own context the way
  `colony restart` injects into its own Herdr pane. It writes a request file
  instead, for a separate `colonybg watchdog` process to act on. The
  watchdog stops the Master and starts a new background Master from the
  checkpoint. It works only when the Master runs as `claude --bg`. See
  [docs/colony.md](docs/colony.md#colonybg-the-no-herdr-backend).

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
| `CLAUDE_CONFIG_DIR` | `~/.claude` | Where Claude Code keeps session transcripts. Colony reads them for `colony report`, the Master-model warning and the FAILING check |
| `COLONY_CHECK_EVERY` | `60` | Seconds between worker health checks during `colony wait` |
| `COLONY_START_READY_WAIT` | `120` | Seconds `colony start` waits for a worker agent that Herdr reports as not ready. It gives up only if the agent never reports a session |
| `COLONY_IDLE_SAMPLES` | `3` | Health checks in a row that must find a worker idle, with a pane that is not waiting for input, before `colony wait` reports IDLE |
| `COLONY_FAIL_STREAK` | `3` | Failed tool calls in a row, at the end of a worker's transcript, after which `colony wait` reports FAILING |
| `COLONY_FAIL_SILENCE` | `60` | Seconds a worker's transcript must stay unchanged after one failed tool call before `colony wait` reports FAILING |
| `COLONY_SETTLE` | `3` | Seconds `colony wait` waits to collect outcomes that arrive together |
| `COLONYBG_CLAUDE` | `claude` | The `claude` executable `colonybg` calls |

`COLONY_HOME`, `COLONY_NAME`, `COLONY_MODELS_CONFIG`, `CLAUDE_CONFIG_DIR`,
`COLONY_CHECK_EVERY`, `COLONY_IDLE_SAMPLES`, `COLONY_FAIL_STREAK`,
`COLONY_FAIL_SILENCE` and `COLONY_SETTLE` work the same
way for `colonybg`. `COLONY_HERDR` and `COLONY_START_READY_WAIT` are
`colony`-only: `colonybg` has no pane-readiness race to wait out, since
`claude --bg` returns only once the session exists (checked against the real
CLI, not assumed).

## Tests

```sh
bash tests/colony.sh
bash tests/colony_bg.sh
```

Each runs its tool against a fake binary (`herdr`, `claude`), so neither needs
the real one installed. The fake proves the script's own logic, not that the
real CLI behaves the same way; `docs/colony.md` names what was checked
against the real `claude` CLI directly.

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
