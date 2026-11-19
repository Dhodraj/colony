# Colony workers

A **colony worker** is a full Claude Code session that runs one ticket and reports one outcome to the colony's event log. Use colony only when the user names colony or Herdr.

## Pick the tool

- Inside Herdr (`test "${HERDR_ENV:-}" = 1`) → use `colony`. Each worker gets its own pane.
- Outside Herdr → use `colonybg`. Each worker is a `claude --bg` session. The project must have been opened with `claude` once, because `claude --bg` refuses an untrusted repository and shows no prompt.

The steps below name `colony`. With `colonybg`, run the same subcommands. `answer` and `close` need the tool that started the worker.

## Run a ticket

1. `colony brief <id>` writes a brief template and prints its path. Fill every section from the ticket. Goal and Acceptance criteria quote the requirement. Owns is the ticket's May change list. You may decide lists only what the ticket leaves open.
2. `colony start <id> <brief>` starts the worker. It refuses a brief with a missing section, a placeholder, a banned or floating model, or a path another open task owns. Add `--worktree` to give the worker its own checkout and branch. Add `--retry-of <old-id>` when the task retries another.
3. Run `colony wait` in the background and end your turn. It returns every new outcome at once.
4. Act on each outcome by the table below.
5. Record each ticket's outcome and verdict in the batch file.

## Outcomes

| `colony wait` reports | Do |
|---|---|
| PASS | Read the lines it cites, run the Check phase. With `--worktree`, merge the worker's branch before you close it, or say where it is. Then `colony close <id> --accept`, `--partial` or `--reject`. A close with no flag leaves the verdict unset. |
| FAIL | Read the failed criterion. Fix the brief or the ticket, then `colony close <id> --reject` and start a new task with `--retry-of <id>`. An open task still owns its files. |
| BLOCKED | Read why. Remove the block, or bring it to the user. |
| QUESTION | Ask the user with the question tool, then send their words with `colony answer <id> "<answer>"`. Never answer it yourself. |
| FAILING | Its last tool calls failed. Look at the worker (`colonybg logs <id>`, or its pane) before you act. It can still be working. |
| IDLE | It stopped with no outcome. A `colonybg` worker waiting on its own question also shows IDLE, so look before you close it. |
| LOST | Its pane or session is gone. Close it and retry the ticket. |
| START-FAILED | The start did not run. Read the error and start again. |

## Keep the Master small

- Context getting full → `colony checkpoint` prints the path of the Master's state file. Fill it in, then offer `colony restart --confirm` right after, because it refuses a checkpoint older than 15 minutes. Restart clears your context and resumes from the checkpoint. `colonybg restart` works only when the Master runs as `claude --bg` with `colonybg watchdog` running.
- `colony status` shows one line per task. `colony report` shows the tokens each worker used.
- Batch done → close every worker, and stop any `colonybg watchdog` you started.
