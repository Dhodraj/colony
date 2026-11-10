---
title: colonybg stops sessions by the short id and restarts the Master as a new background session
author: Dhodraj
date: 2026-11-10
branch: main
id: l-1a383b20
scope: repo
status: active
replaces: l-73c1132b
src: agent
touches:
  - README.md
  - colony.py
  - docs/colony.md
---

## Decided

colonybg talks to `claude stop`, `claude logs` and `claude attach` with the short id that `claude --bg` prints. It keeps the full session id only for `claude --resume` and for finding transcripts in `report`. After every stop it reads `claude agents --json --all` and treats a row with no pid as stopped; it never trusts the stop command's own output.

A restart still needs a separate `colonybg watchdog` process, because a Master that stopped itself could not start its replacement. The watchdog stops the old Master by short id, then starts a new `claude --bg` Master in the same folder with a prompt naming the checkpoint, and records the new session id in colony.json. `colonybg restart` refuses unless the Master's own row in `claude agents` has kind `background`.

## Tried and ruled out

- Stopping by the full session id (`CLAUDE_CODE_SESSION_ID` or the `sessionId` field): `claude stop` prints `No job matching` for a full id even on a running session (claude 2.1.319). The earlier conclusion that "a session cannot stop itself" came from this: the self-stop test used the full id, so it does not show whether self-stop works.
- Clearing the old Master with `claude --resume <id> /clear --bg` followed by a second resume carrying the prompt: the first resume leaves the session running, so the second starts a copy with the old context. `/clear` also gives the session a new session id.
- Treating a row with no `status` as idle: a session that exited or was stopped keeps its row with no pid and no status, and `state` done, stopped or failed. colonybg reports that worker LOST.

## Watch out

- `claude --bg` refuses a folder that is not trusted and shows no prompt. A Git repository must be trusted itself; the trust of a parent folder does not reach it. A worktree takes the trust of its repository, so `--worktree` works in a trusted project.
- The new Master after a restart runs on the default model: `claude agents` does not show the old Master's model.
- The test fake in tests/colony_bg.sh copies these CLI rules. If a new claude version changes them, update the fake from a live check first.

In the code: colony_bg.py `stop_session`, `is_running`, `cmd_answer`, `cmd_close`, `check_workers_bg`, `cmd_restart`, `run_restart_request`; tests/colony_bg.sh fake `claude`.
