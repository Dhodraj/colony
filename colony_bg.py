#!/usr/bin/env python3
"""colonybg: a Master session and one-task worker sessions, each its own
background `claude` session. No Herdr: colonybg never calls it and works
whether or not it is installed.

Master:
  colonybg brief <id> [--result PATH]        write a brief template; prints its path
  colonybg start <id> <brief> [--model M] [--effort E] [--retry-of ID]
                                             start a worker in the background, send the brief
  colonybg wait [--timeout S]                block until new outcomes, then print them
  colonybg answer <id> <text>                reply to a worker that asked a QUESTION
  colonybg close <id> [--force] [--accept|--reject|--partial]
                                             record the session ID, stop the worker, set the verdict
  colonybg status [--all]                    one line per task
  colonybg report [--json]                   tokens per worker and for the Master
  colonybg checkpoint                        write the Master's state file; prints its path
  colonybg restart --confirm                 ask the watchdog to replace the Master with a fresh one
  colonybg watchdog [--once]                 replace the Master on request (see below)
  colonybg logs <id>                         a worker's recent terminal output
  colonybg attach <id>                       open a worker's session in this terminal
Worker:
  colonybg done <id> <status> <result> <summary>   PASS, FAIL, BLOCKED or QUESTION

This is the no-Herdr backend: every worker is a `claude --bg` session, which
is how colonybg starts it, drives it and tells whether it is idle or gone.
colony is the sibling backed by Herdr panes instead; see docs/colony.md for
which to run.

A session cannot stop itself, so colonybg restart cannot clear the Master's
own context from inside the Master's own tool call, the way colony's Herdr
backend injects into its own pane. It writes a request instead, for a
colonybg watchdog running as a separate process to act on; run one with
colonybg watchdog (it loops) or trigger colonybg watchdog --once yourself on
a schedule. The watchdog stops the Master and starts a new background Master
from the checkpoint, which gives the colony a Master with a clear context.
colonybg restart refuses unless the Master itself runs as claude --bg.

colonybg start needs graft, as colony does: it refuses to start a worker
against a missing or stale graft index. COLONY_GRAFT names the executable.

claude --bg refuses a folder that is not trusted, with no prompt to accept.
A Git repository must be trusted itself; a worktree takes the trust of its
repository. So run colonybg in a project you have opened with claude once.

A project can hold several colonies: set COLONY_NAME (default "main"). Workers
inherit it. State: $COLONY_HOME (default ~/.local/state/claude-system/colony)/
<project path>/<colony name>/. Exit codes: 0 ok, 1 claude failed, 2 usage error.

Known gap: a worker's own question tool, waiting for the user with no TTY
attached, cannot yet be told apart from a worker that stopped. colony's Herdr
backend reads the pane's blocked state to tell those apart; colonybg's IDLE
detection cannot, so a worker that asks the user may be reported IDLE while
it waits. docs/colony.md says more.
"""

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time

from colony_core import (
    CHECK_EVERY, SETTLE, IDLE_SAMPLES, REPORTED, WATCHED, VERDICTS, SUMMARY_MAX,
    check_failing,
    ColonyError, now, clean, colony_name, state_dir, check_id, load_json,
    save_json, load_task, save_task, latest_events, last_event_kind,
    append_event, read_events, read_tally, remove_worktree, close_verdict,
    project_root, in_git_repo, git_state, tree_state, graft_check,
    make_worktree, brief_owns, owner_conflicts, brief_model_effort, check_brief,
    models_config, master_last_model, outside_cwd, write_worker_settings,
    retrieval_policy, write_checkpoint, checkpoint_path, checkpoint_ready,
    cmd_report_from, cmd_brief, cmd_done, cmd_status, cmd_retrieval_hook,
    PROMPT,
)

BG_ID = re.compile(r"backgrounded\s*\xb7\s*(\S+)")
COPY_NOTE = re.compile(r"started a copy as (\S+?)\.?(\s|$)")
STOP_WAIT = float(os.environ.get("COLONYBG_STOP_WAIT", 15))
RESUME_PROMPT = (
    "You are the Master of colony {name}. A colonybg restart replaced the "
    "previous Master with you to clear its context. Read {checkpoint}, run "
    "colonybg status, start a background colonybg wait if a worker is still "
    "open, then continue from its Next step."
)


def claude_label(args):
    """The command for an error message: `claude --bg` without its prompt,
    otherwise the subcommand and its id."""
    return "claude --bg" if args and args[0] == "--bg" else "claude " + " ".join(args[:2])


def claude(*args, timeout=330, env_extra=None, cwd=None):
    """Run the claude CLI and return its stdout; raise ColonyError(code 1) on
    failure. env_extra is merged over this process's own environment, which a
    `--bg` session inherits at the moment it is spawned (verified live: a
    variable set here was still readable from inside the background session
    seconds later). cwd is the folder the command runs in; a `--bg` session
    runs in the folder it was started from (verified live)."""
    exe = os.environ.get("COLONYBG_CLAUDE", "claude")
    env = {**os.environ, **env_extra} if env_extra else None
    try:
        out = subprocess.run([exe, *args], capture_output=True, text=True,
                             timeout=timeout, env=env, cwd=cwd)
    except (OSError, subprocess.SubprocessError) as e:
        raise ColonyError(f"{claude_label(args)}: {e}", 1)
    if out.returncode != 0:
        first = (out.stderr.strip() or out.stdout.strip()
                 or f"exit {out.returncode}").splitlines()[0]
        raise ColonyError(f"{claude_label(args)}: {first}", 1)
    return out.stdout


def claude_quiet(*args, **kwargs):
    """Run claude and ignore a failure; return the error message, or None."""
    try:
        claude(*args, **kwargs)
        return None
    except ColonyError as e:
        return str(e)


def agents_json(all_sessions=True):
    """Every session claude agents knows of, interactive and background, as a
    list of dicts with at least id/sessionId/status/name/kind."""
    args = ["agents", "--json"] + (["--all"] if all_sessions else [])
    try:
        return json.loads(claude(*args))
    except json.JSONDecodeError as e:
        raise ColonyError(f"claude agents --json printed no valid JSON: {e}", 1)


def is_running(row):
    """True when a claude agents row is a session with a live process.

    Verified live against claude 2.1.319: a running session has a pid and a
    status of idle or busy. Once it exits or is stopped, its row stays in
    `claude agents --json --all` with no pid or status, and a state of done,
    stopped or failed."""
    return row is not None and row.get("pid") is not None


def stop_session(agent):
    """Stop a background session by its short id, then wait until claude
    agents shows no live process for it. Return None once it is not running,
    or the reason it still is.

    claude stop, logs and attach take only the short id: given the full
    session id they print "No job matching" (verified live). A stop error is
    not trusted on its own either way; the listing decides."""
    err = claude_quiet("stop", agent)
    deadline = time.monotonic() + STOP_WAIT
    while True:
        row = {r.get("id"): r for r in agents_json()}.get(agent)
        if not is_running(row):
            return None
        if time.monotonic() >= deadline:
            return err or f"session {agent} still running {STOP_WAIT:g}s after claude stop"
        time.sleep(min(0.5, STOP_WAIT))


def copy_started(text):
    """The id of a copy, when claude --resume started one because the session
    was still running (it prints a note naming the copy), else None."""
    m = COPY_NOTE.search(text)
    return m.group(1) if m else None


def bg_task(sd, task_id):
    """Load a task colonybg started. A task colony started has a Herdr pane
    and no background session, so colonybg cannot stop or resume it."""
    task = load_task(sd, task_id)
    if task.get("pane"):
        raise ColonyError(f"task {task_id} was started by colony, not colonybg; "
                          "run this command with colony")
    return task


def parse_bg_id(text):
    """The short id `claude --bg` printed, or raise if the line is not there."""
    m = BG_ID.search(text)
    if not m:
        raise ColonyError(f"claude --bg printed no backgrounded id: {text.strip()[:200]!r}", 1)
    return m.group(1)


def note_master_session(sd):
    """Record the Master's own session id in colony.json, once.

    Read directly from this process's own environment: every claude session
    carries CLAUDE_CODE_SESSION_ID (verified live), so no subprocess call is
    needed. A restart starts a new Master session; the watchdog records it
    in colony.json itself (see run_restart_request), so this only fills in
    the first one.
    """
    session = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not session:
        return
    meta_path = os.path.join(sd, "colony.json")
    meta = load_json(meta_path, {})
    if meta.get("master_session"):
        return
    meta.update({"name": colony_name(), "root": project_root(),
                 "master_session": session, "master_sessions": [session],
                 "created": meta.get("created") or now()})
    save_json(meta_path, meta)


def cmd_start(a):
    task_id = check_id(a.id)
    brief = os.path.abspath(a.brief)
    if not os.path.isfile(brief):
        raise ColonyError(f"no brief file at {brief}")
    if not a.no_check:
        check_brief(brief)
    cfg = models_config()
    model, effort = brief_model_effort(brief, cfg, a.model, a.effort)
    if a.no_check:
        try:
            owns = brief_owns(brief)
        except ColonyError:
            owns = []
            print(f"warning: {brief} has no Owns section, so {task_id} claims no "
                  "path and cannot be protected from a sibling. Add an Owns "
                  "section to reserve its paths", file=sys.stderr)
    else:
        owns = brief_owns(brief)
    root = project_root()
    if not in_git_repo(root):
        raise ColonyError(f"{root} is not a Git repository. A colony keys its state on "
                          "the project root, so outside a repository it would key on "
                          "the working directory and two colonies could share state")
    graft_check(root)
    sd = state_dir()
    note_master_session(sd)
    if os.path.exists(os.path.join(sd, "tasks", task_id + ".json")):
        raise ColonyError(f"task {task_id} exists already; use a new id")
    clashes = owner_conflicts(sd, owns)
    if clashes:
        raise ColonyError("another open task already owns "
                          + ", ".join(f"{path} ({owner})" for path, owner in clashes)
                          + ". Two workers in one tree that change the same path "
                            "overwrite each other, so name different paths, or "
                            "close the other task first")
    retry_of = None
    if a.retry_of:
        retry_of = check_id(a.retry_of)
        if retry_of == task_id:
            raise ColonyError(f"--retry-of {retry_of} is this task's own id; "
                              "name the task it retries")
        try:
            load_task(sd, retry_of)
        except ColonyError:
            raise ColonyError(f"no task {retry_of} in colony {colony_name()} to retry; "
                              "colonybg status --all lists the tasks")
    revision, dirty, why_no_revision = git_state(root)
    dirty_paths, dirty_digest, why_no_tree = tree_state(root)
    cwd = os.getcwd()
    worktree = branch = None
    if a.worktree:
        worktree, branch = make_worktree(root, sd, task_id)
        cwd = worktree
    title = f"{os.path.basename(project_root())} {task_id}"
    env_extra = {"COLONY_NAME": colony_name(), "COLONY_ROOT": root,
                 # An explicit model in an Agent call beats this default:
                 # verified against the real engine on 2026-10-20 (colony.py).
                 "CLAUDE_CODE_SUBAGENT_MODEL": cfg["subagent_models"]["default"]}
    prompt = PROMPT.format(id=task_id, brief=brief,
                           reviewer_alias=cfg["subagent_models"]["reviewer"],
                           explore_alias=cfg["subagent_models"]["explore"],
                           explore_budget=cfg["explore_budget"])
    args = ["--bg", prompt, "--model", model, "--effort", effort,
            "-n", title, "--exclude-dynamic-system-prompt-sections"]
    for path in outside_cwd(cwd, sd, os.path.dirname(brief)):
        args += ["--add-dir", path]
    args += ["--settings", write_worker_settings(sd, task_id, retrieval_policy(cfg))]
    try:
        out = claude(*args, env_extra=env_extra, cwd=cwd)
        agent = parse_bg_id(out)
        session = agent  # the short id doubles as the session reference colonybg stores
        listing = {row.get("id"): row for row in agents_json(all_sessions=True)}
        if agent in listing:
            session = listing[agent].get("sessionId") or agent
    except ColonyError as e:
        if worktree:
            remove_worktree(root, worktree, force=True)
        append_event(sd, task_id, "START-FAILED", "-", str(e))
        if worktree and "Workspace not trusted" in str(e):
            raise ColonyError(f"{e} A worktree takes its trust from its repository, so "
                              f"run claude once in {root} and accept the prompt", 1)
        raise
    task = {"id": task_id, "agent": agent, "brief": brief, "model": model,
            "effort": effort, "cwd": cwd, "started": now(), "closed": None,
            "session": session, "revision": revision, "dirty": dirty, "verdict": None,
            "retry_of": retry_of, "dirty_paths": dirty_paths, "dirty_digest": dirty_digest,
            "config_dir": os.environ.get("CLAUDE_CONFIG_DIR"), "owns": owns,
            "worktree": worktree, "branch": branch, "root": root}
    if why_no_revision:
        task["revision_unavailable"] = why_no_revision
    if why_no_tree:
        task["tree_unavailable"] = why_no_tree
    save_task(sd, task)
    append_event(sd, task_id, "STARTED", brief,
                 f"session {agent}, model {model}, effort {effort}, "
                 f"owns {' '.join(owns) if owns else '-'}"
                 + (f", worktree {worktree} on {branch}" if worktree else ""))
    print(f"started {task_id}: session {agent}")
    bad = master_last_model(load_json(os.path.join(sd, "colony.json"), {}))
    if bad in cfg["banned"]:
        print(f"warning: this Master runs {bad}, which is not allowed for a Master.",
              file=sys.stderr)


def cmd_answer(a):
    task_id = check_id(a.id)
    sd = state_dir()
    task = bg_task(sd, task_id)
    if task.get("closed"):
        raise ColonyError(f"task {task_id} is closed")
    text = clean(a.text)
    # A running background session cannot take a second prompt in place
    # (verified live: --resume on a still-running session starts a copy
    # instead); stop it first, which keeps its conversation, then resume it
    # with the answer.
    err = stop_session(task["agent"])
    if err:
        raise ColonyError(f"could not stop worker {task_id} before answering: {err}. "
                          "Resuming a running session starts a copy, so the answer "
                          "was not sent", 1)
    out = claude("--resume", task["session"], f"Answer from the Master on {task_id}: {text}",
                 "--bg", cwd=task.get("cwd"))
    copy = copy_started(out)
    if copy:
        raise ColonyError(f"claude --resume started a copy of worker {task_id} as {copy} "
                          f"instead of resuming it; stop the copy with claude stop {copy}", 1)
    append_event(sd, task_id, "ANSWERED", "-", text[:SUMMARY_MAX])
    print(f"answered {task_id}")


def check_workers_bg(sd, idle_seen):
    """Record LOST for a worker whose session claude no longer knows of,
    FAILING for one whose tool calls keep failing (colony_core.FAIL_STREAK),
    and IDLE for one that every check in a row has found idle without an
    outcome.

    One claude agents --json --all call covers every open task, where colony's
    herdr backend needs one herdr agent get call per task. See check_workers in
    colony.py for the IDLE debounce reasoning, which applies unchanged here.

    A row with no live process means the session exited or was stopped
    without colonybg done, which is LOST, the way colony reports a pane that
    is gone.

    Known gap: there is no confirmed third status for a worker parked on its
    own question tool with no TTY attached, the way colony's herdr backend
    reads a pane's blocked state. Such a worker may be reported IDLE while it
    waits; see the module docstring.
    """
    by_id = {row.get("id"): row for row in agents_json(all_sessions=True)}
    for task_id, ev in latest_events(sd).items():
        if ev[2] not in WATCHED:
            continue
        try:
            task = load_task(sd, task_id)
        except ColonyError:
            continue
        row = by_id.get(task["agent"])
        if row is None:
            append_event(sd, task_id, "LOST", "-", "worker session not found in claude agents")
            idle_seen.pop(task_id, None)
            continue
        if not is_running(row):
            append_event(sd, task_id, "LOST", "-",
                         f"worker session ended (state {row.get('state') or 'unknown'}) "
                         "without colonybg done")
            idle_seen.pop(task_id, None)
            continue
        check_failing(sd, task_id, task.get("session"), task.get("config_dir"))
        if row.get("status") == "busy":
            idle_seen.pop(task_id, None)
            continue
        if last_event_kind(sd, task_id) == "IDLE":
            continue
        seen = idle_seen.get(task_id, 0) + 1
        idle_seen[task_id] = seen
        if seen >= IDLE_SAMPLES:
            append_event(sd, task_id, "IDLE", "-",
                         f"worker status {row.get('status')!r} for {seen} checks "
                         "without colonybg done")
            idle_seen.pop(task_id, None)


def cmd_wait(a):
    sd = state_dir()
    note_master_session(sd)
    offset_path = os.path.join(sd, "wait.offset")
    try:
        with open(offset_path, encoding="utf-8") as f:
            offset = int(f.read().strip() or 0)
    except (OSError, ValueError):
        offset = 0
    deadline = time.monotonic() + a.timeout
    next_check = time.monotonic() + CHECK_EVERY
    idle_seen = {}
    while True:
        events, new_offset = read_events(sd, offset)
        if any(ev[2] in REPORTED for ev in events):
            time.sleep(SETTLE)
            events, new_offset = read_events(sd, offset)
            for ev in events:
                if ev[2] in REPORTED:
                    print("\t".join(ev[1:]))
            with open(offset_path, "w", encoding="utf-8") as f:
                f.write(str(new_offset))
            return
        if events:
            offset = new_offset
            with open(offset_path, "w", encoding="utf-8") as f:
                f.write(str(offset))
        if time.monotonic() >= deadline:
            print(f"no new outcomes in {a.timeout}s; run colonybg wait again")
            return
        if time.monotonic() >= next_check:
            check_workers_bg(sd, idle_seen)
            next_check = time.monotonic() + CHECK_EVERY
        time.sleep(max(0.05, min(2, CHECK_EVERY, deadline - time.monotonic())))


def cmd_close(a):
    task_id = check_id(a.id)
    verdict = close_verdict(a)
    sd = state_dir()
    note_master_session(sd)
    task = bg_task(sd, task_id)
    if task.get("closed"):
        if verdict:
            task["verdict"] = verdict
            save_task(sd, task)
            print(f"{task_id} was closed at {task['closed']}; verdict {verdict}")
        else:
            print(f"{task_id} was closed at {task['closed']}")
        return
    # Stopping keeps the conversation (claude attach/--resume still works), the
    # same thing colony's herdr backend gets from closing a pane: the worker
    # stops running but its transcript and session id stay valid for report.
    # A worker that finished can have exited on its own by the time close
    # runs; stop_session then finds no live process, the ordinary case, the
    # way colony.py treats a pane already gone.
    err = stop_session(task["agent"])
    if err and not a.force:
        raise ColonyError(f"could not stop worker {task_id}: {err}; use --force to close anyway")
    session = task["session"]
    task["closed"] = now()
    task["verdict"] = verdict
    tally = read_tally(sd, task_id)
    note = f"retrieval hook saw {tally['seen']} calls, refused {tally['denied']}" if tally \
        else "the retrieval hook never ran"
    if task.get("worktree"):
        removed, why = remove_worktree(task.get("root") or project_root(), task["worktree"])
        note += "; worktree removed" if removed else f"; worktree kept: {why}"
    save_task(sd, task)
    append_event(sd, task_id, "CLOSED", session or "-", note or "-")
    print(f"closed {task_id}; session {session or 'unknown'}"
          + (f"; verdict {verdict}" if verdict else "")
          + (f"; {note}" if note else ""))
    if not tally:
        print(f"warning: no tool call by {task_id} reached the retrieval hook, so "
              f"its read limit was never enforced. Check that the worker loaded "
              f"{os.path.join(sd, 'settings', task_id + '.json')}", file=sys.stderr)


def cmd_report(a):
    sd = state_dir()
    note_master_session(sd)
    cmd_report_from(a, sd)


def cmd_checkpoint(a):
    sd = state_dir()
    note_master_session(sd)
    print(write_checkpoint(sd))


def restart_request_path(sd):
    return os.path.join(sd, "restart-request.json")


def cmd_restart(a):
    if not a.confirm:
        raise ColonyError("restart clears the Master's context; run it with --confirm")
    sd = state_dir()
    path = checkpoint_path(sd)
    checkpoint_ready(path, prog="colonybg")
    session = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not session:
        raise ColonyError("no CLAUDE_CODE_SESSION_ID; run colonybg restart from the "
                          "Master's own session")
    row = next((r for r in agents_json() if r.get("sessionId") == session), None)
    if row is None or row.get("kind") != "background":
        kind = f"session kind is {row.get('kind')}" if row else "session is not listed by claude agents"
        raise ColonyError(f"colonybg restart only works when the Master runs as claude --bg; "
                          f"this Master's {kind}. Clear it by hand with /clear, then "
                          f"read {path}")
    save_json(restart_request_path(sd), {"requested": now(), "master_session": session,
                                         "master_agent": row["id"], "name": row.get("name"),
                                         "cwd": os.getcwd(), "checkpoint": path})
    append_event(sd, "MASTER", "RESTART-REQUESTED", path,
                 "waiting for colonybg watchdog to replace the Master")
    print(f"restart requested: run `colonybg watchdog --once` (or keep one running) "
          f"to stop this Master and start a new one from {path}. A session cannot stop "
          "itself, so nothing happens until a watchdog process does it from outside.")


def run_restart_request(sd):
    """Act on a pending restart request, if one exists. Returns True if it did."""
    path = restart_request_path(sd)
    request = load_json(path)
    if request is None:
        return False
    # Resuming the old session with /clear does not work: the resume leaves
    # the session running, so the next resume starts a copy with the old
    # context, and /clear gives the session a new id (both verified live). A
    # new background session started from the checkpoint is a Master with a
    # clear context, and needs one claude call.
    err = stop_session(request["master_agent"])
    if err:
        raise ColonyError(f"could not stop the Master ({request['master_agent']}): {err}. "
                          f"The restart request is kept at {path}", 1)
    prompt = RESUME_PROMPT.format(name=colony_name(), checkpoint=request["checkpoint"])
    args = ["--bg", prompt] + (["-n", request["name"]] if request.get("name") else [])
    out = claude(*args, cwd=request.get("cwd"),
                 env_extra={"COLONY_NAME": colony_name(), "COLONY_ROOT": project_root()})
    agent = parse_bg_id(out)
    row = {r.get("id"): r for r in agents_json()}.get(agent) or {}
    session = row.get("sessionId") or agent
    meta_path = os.path.join(sd, "colony.json")
    meta = load_json(meta_path, {})
    meta["master_session"] = session
    meta["master_sessions"] = meta.get("master_sessions", []) + [session]
    save_json(meta_path, meta)
    append_event(sd, "MASTER", "RESTART", request["checkpoint"],
                 f"Master {request['master_agent']} stopped; new Master {agent} "
                 "started from the checkpoint")
    os.remove(path)
    return True


def cmd_watchdog(a):
    """Clear and resume the Master on request, from a process the Master
    itself is not. --once checks and exits; without it, loops until stopped
    (Ctrl-C, or SIGTERM), removing its own pid file either way."""
    sd = state_dir()
    pid_path = os.path.join(sd, "watchdog.pid")
    if a.once:
        acted = run_restart_request(sd)
        print("restart handled" if acted else "no restart requested")
        return
    with open(pid_path, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    stop = {"go": True}

    def handle_stop(signum, frame):
        stop["go"] = False

    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)
    try:
        print(f"colonybg watchdog running (pid {os.getpid()}); Ctrl-C to stop")
        while stop["go"]:
            try:
                if run_restart_request(sd):
                    print(f"restart handled at {now()}")
            except ColonyError as e:
                print(f"colonybg watchdog: {e}", file=sys.stderr)
            for _ in range(int(max(1, CHECK_EVERY))):
                if not stop["go"]:
                    break
                time.sleep(1)
    finally:
        try:
            os.remove(pid_path)
        except OSError:
            pass


def cmd_logs(a):
    task = bg_task(state_dir(), check_id(a.id))
    print(claude("logs", task["agent"]), end="")


def cmd_attach(a):
    task = bg_task(state_dir(), check_id(a.id))
    exe = os.environ.get("COLONYBG_CLAUDE", "claude")
    os.execvp(exe, [exe, "attach", task["agent"]])


def main(argv=None):
    p = argparse.ArgumentParser(prog="colonybg", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("brief", help="write a brief template; prints its path")
    b.add_argument("id")
    b.add_argument("--result", help="result file; default <colony>/results/<id>.md")
    b.set_defaults(func=cmd_brief)
    s = sub.add_parser("start", help="start a worker in the background, send it the brief")
    s.add_argument("id")
    s.add_argument("brief")
    s.add_argument("--model", help="the worker's model; overrides the brief's Model section")
    s.add_argument("--effort", help="the worker's effort; overrides the brief's Effort section")
    s.add_argument("--no-check", action="store_true", help="skip the brief section check")
    s.add_argument("--worktree", action="store_true",
                   help="give the worker its own Git worktree and branch, indexed "
                        "for graft, so it cannot overwrite a sibling's edits")
    s.add_argument("--retry-of", dest="retry_of", metavar="ID",
                   help="the task this one retries; it must exist in this colony")
    s.set_defaults(func=cmd_start)
    d = sub.add_parser("done", help="record a worker's outcome")
    d.add_argument("id")
    d.add_argument("status", help="PASS, FAIL, BLOCKED or QUESTION")
    d.add_argument("result", help="result file; - for BLOCKED or QUESTION")
    d.add_argument("summary", help=f"at most {SUMMARY_MAX} characters")
    d.set_defaults(func=cmd_done)
    w = sub.add_parser("wait", help="block until new outcomes, then print them")
    w.add_argument("--timeout", type=int, default=1700,
                   help="seconds; keep it below the Bash timeout of the background "
                        "command (30 minutes unless you set one)")
    w.set_defaults(func=cmd_wait)
    an = sub.add_parser("answer", help="reply to a worker that asked a QUESTION")
    an.add_argument("id")
    an.add_argument("text")
    an.set_defaults(func=cmd_answer)
    c = sub.add_parser("close", help="record the session ID and stop the worker")
    c.add_argument("id")
    c.add_argument("--force", action="store_true", help="close even if the worker could not be stopped")
    for name in VERDICTS:
        c.add_argument(f"--{name}", action="store_true",
                       help=f"record the Master's verdict on the result: {name}")
    c.set_defaults(func=cmd_close)
    st = sub.add_parser("status", help="one line per task")
    st.add_argument("--all", action="store_true", help="include closed tasks")
    st.set_defaults(func=cmd_status)
    r = sub.add_parser("report", help="tokens per worker and for the Master")
    r.add_argument("--json", action="store_true", help="the whole report as JSON")
    r.add_argument("--weights", metavar="NAME",
                   help="the plan-limit weighting to report in; default "
                        "limit_weights.default from the model config")
    r.set_defaults(func=cmd_report)
    ck = sub.add_parser("checkpoint", help="write the Master's state file; prints its path")
    ck.set_defaults(func=cmd_checkpoint)
    rs = sub.add_parser("restart", help="ask the watchdog to clear and resume the Master")
    rs.add_argument("--confirm", action="store_true", help="required: the Master's context is cleared")
    rs.set_defaults(func=cmd_restart)
    wd = sub.add_parser("watchdog", help="clear and resume the Master on request")
    wd.add_argument("--once", action="store_true",
                    help="check once and exit, instead of looping")
    wd.set_defaults(func=cmd_watchdog)
    lg = sub.add_parser("logs", help="a worker's recent terminal output")
    lg.add_argument("id")
    lg.set_defaults(func=cmd_logs)
    at = sub.add_parser("attach", help="open a worker's session in this terminal")
    at.add_argument("id")
    at.set_defaults(func=cmd_attach)

    rh = sub.add_parser("retrieval-hook",
                        help="a worker's PreToolUse hook; reads the event on stdin")
    rh.add_argument("--read-limit", type=int, required=True)
    rh.add_argument("--tally", required=True)
    rh.set_defaults(func=cmd_retrieval_hook)
    a = p.parse_args(argv)
    try:
        a.func(a)
    except ColonyError as e:
        print(f"colonybg: {e}", file=sys.stderr)
        return e.code
    return 0


if __name__ == "__main__":
    sys.exit(main())
