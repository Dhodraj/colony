---
title: "colonybg restart needs a separate watchdog process, not self-injection"
author: Dhodraj
date: 2026-11-10
branch: main
id: l-73c1132b
scope: repo
status: active
src: agent
touches:
  - README.md
  - colony.py
  - docs/colony.md
---

## Decided

colonybg (the no-Herdr colony backend, built on `claude --bg`) cannot clear
and resume the Master's own context from inside the Master's own `restart`
call. `colonybg restart --confirm` writes a request file instead; a
separate, user-started `colonybg watchdog` process reads it and does the
stop + `--resume .../clear` + resume-prompt sequence from outside. This only
works at all when the Master itself was started with `claude --bg`, not as a
plain interactive session.

In the code: colony_bg.py `cmd_restart`, `run_restart_request`,
`cmd_watchdog`. docs/colony.md "colonybg: the no-Herdr backend" section.

## Tried and ruled out

- Live pane-style injection (what colony.py does via `herdr agent prompt`
  into the Master's own Herdr pane): no equivalent exists for a plain
  `claude` session, live or backgrounded.
- Self stop-then-resume (`claude stop "$CLAUDE_CODE_SESSION_ID"` run from
  inside that same session): fails with `No job matching '<id>'`. Verified
  live with a disposable background worker told to stop itself; it printed
  the error and kept running. A session cannot stop itself, so this path is
  closed regardless of whether the Master runs foreground or `--bg`.
- Claude Code hooks as an external-injection channel: hooks (PreToolUse,
  SessionStart, PreCompact, etc.) fire from the agent's own lifecycle, not
  from an outside trigger. No hook gives an external process a way to push
  text into a running session.

## Watch out

- `claude --resume <id> "<text>" --bg` on a session that is still running
  (even idle, process still alive) forks a copy instead of continuing it in
  place — confirmed with both the short id and the full session id. Every
  colonybg call that resumes a session (`answer`, `close`, the watchdog)
  stops it first for this reason.
- The watchdog is not started automatically anywhere; running one is a
  manual step the user has to take for `colonybg restart` to do anything.
