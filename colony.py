#!/usr/bin/env python3
"""colony: a Master session and one-task worker sessions, each in its own Herdr pane.

A worker is a separate Claude Code session, not a sub-agent: it returns one
line to an event log instead of its whole answer, it can ask the user, and it
keeps going while the Master compacts or restarts. docs/colony.md explains why.

The Master writes a brief, starts a worker, waits in the background and closes
the worker when its outcome is in. Workers check their own result before they
report one line to an append-only event log, so the Master wakes once per batch
of outcomes and reads detail only when something failed.

Master:
  colony brief <id> [--result PATH]          write a brief template; prints its path
  colony start <id> <brief> [--model M] [--effort E] [--retry-of ID]
                                             open a pane, start a worker, send the brief
  colony wait [--timeout S]                  block until new outcomes, then print them
  colony answer <id> <text>                  reply to a worker that asked a QUESTION
  colony close <id> [--force] [--accept|--reject|--partial]
                                             record the session ID, close the pane, set the verdict
  colony status [--all]                      one line per task
  colony report [--json]                     tokens per worker and for the Master
  colony checkpoint                          write the Master's state file; prints its path
  colony restart --confirm                   clear the Master and resume it from the checkpoint
Worker:
  colony done <id> <status> <result> <summary>   PASS, FAIL, BLOCKED or QUESTION

This is the Herdr backend: every worker runs in its own Herdr pane, which is
how colony starts it, drives it and tells whether it is idle or gone. colonybg
is the sibling with no Herdr dependency at all, backed by `claude --bg`
instead of panes; see docs/colony.md for which to run.

Start the Master with CLAUDE_CODE_AUTO_COMPACT_WINDOW=400000 claude: every
session compacts at 400k. colony start warns when it is missing.

colony start needs graft, as it needs herdr: it refuses to start a worker
against a missing or stale graft index. COLONY_GRAFT names the executable.

A project can hold several colonies: set COLONY_NAME (default "main"). Workers
inherit it. State: $COLONY_HOME (default ~/.local/state/claude-system/colony)/
<project path>/<colony name>/. Exit codes: 0 ok, 1 Herdr failed, 2 usage error.
"""

import argparse
import json
import os
import subprocess
import sys
import time

from colony_core import (
    CHECK_EVERY, SETTLE, IDLE_SAMPLES, MASTER_WINDOW, REPORTED, WATCHED, VERDICTS,
    check_failing,
    SUMMARY_MAX, ColonyError, now, clean, colony_name, state_dir, check_id,
    load_json, save_json, load_task, save_task, all_tasks, latest_events,
    last_event_kind, append_event, read_events, read_tally, remove_worktree,
    close_verdict, project_root, in_git_repo, git_state, tree_state,
    graft_check, make_worktree, brief_owns, owner_conflicts, brief_model_effort,
    check_brief, models_config, master_last_model, outside_cwd,
    write_worker_settings, retrieval_policy, write_checkpoint, checkpoint_path,
    checkpoint_ready, cmd_report_from, cmd_brief, cmd_done, cmd_status,
    cmd_retrieval_hook, PROMPT, RESUME_PROMPT,
)

# Seconds to keep waiting for a worker agent that herdr says is not ready yet.
START_READY_WAIT = float(os.environ.get("COLONY_START_READY_WAIT", 120))


def agent_name(task_id):
    # Herdr agent names are global, so the colony name keeps two colonies apart.
    name = colony_name()
    prefix = "w-" if name == "main" else f"w-{name}-"
    return (prefix + task_id.lower())[:32]


def herdr(*args):
    """Run a herdr command and return its parsed JSON; raise ColonyError(code 1) on failure."""
    exe = os.environ.get("COLONY_HERDR", "herdr")
    try:
        out = subprocess.run([exe, *args], capture_output=True, text=True, timeout=330)
    except (OSError, subprocess.SubprocessError) as e:
        raise ColonyError(f"herdr {' '.join(args[:2])}: {e}", 1)
    try:
        data = json.loads(out.stdout) if out.stdout.strip() else {}
    except json.JSONDecodeError:
        data = {}
    if out.returncode != 0 or "error" in data:
        err = data.get("error", {})
        msg = err.get("message") or out.stderr.strip() or f"exit {out.returncode}"
        raise ColonyError(f"herdr {' '.join(args[:2])}: {msg}", 1)
    return data


def herdr_quiet(*args):
    """Run herdr and ignore a failure; return the error message, or None."""
    try:
        herdr(*args)
        return None
    except ColonyError as e:
        return str(e)


def master_pane_info():
    pane = os.environ.get("HERDR_PANE_ID")
    if not pane:
        return None, None
    try:
        info = herdr("pane", "get", pane)["result"]["pane"]
        return pane, (info.get("agent_session") or {}).get("value")
    except (ColonyError, KeyError, TypeError):
        return pane, None


def live_session(task):
    """A herdr fallback to an open task's pane's own session, read live. Passed
    to colony_core's report pipeline, which otherwise trusts a task's recorded
    session; a herdr pane's session is only known once its worker is running."""
    try:
        pane = herdr("pane", "get", task["pane"])["result"]["pane"]
        return (pane.get("agent_session") or {}).get("value")
    except (ColonyError, KeyError, TypeError):
        return None


def note_master_session(sd):
    """Add the Master pane's current session to colony.json when it changed (after a restart)."""
    meta_path = os.path.join(sd, "colony.json")
    meta = load_json(meta_path, {})
    pane = os.environ.get("HERDR_PANE_ID")
    if not meta.get("master_pane") or pane != meta.get("master_pane"):
        return
    _, session = master_pane_info()
    sessions = meta.setdefault("master_sessions", [meta.get("master_session")])
    if session and session not in sessions:
        sessions.append(session)
        save_json(meta_path, meta)


def agent_session_of(name):
    """The worker agent's session id, or None when it has none yet."""
    try:
        info = herdr("agent", "get", name)
    except ColonyError:
        return None
    agent = (info.get("result") or {}).get("agent") or {}
    return (agent.get("agent_session") or {}).get("value")


def wait_ready(name, seconds):
    """Wait up to `seconds` for a worker agent to report a session id.

    `herdr agent start` can fail for an agent that is only still starting.
    Measured on 2026-10-12: a start with a 150-second timeout returned
    agent_not_ready while the pane already held a running Claude session, and
    the agent reported idle with a session id seconds later. Closing the pane
    on that error throws away a worker that would have worked, which is what
    the archive's 6 START-FAILED events were.

    The error is recognised by its message text, because `herdr` reports the
    code only inside the payload this script discards. A wording change
    upstream turns the recovery off, not into a wrong outcome.
    """
    deadline = time.monotonic() + seconds
    while True:
        session = agent_session_of(name)
        if session:
            return session
        left = deadline - time.monotonic()
        if left <= 0:
            return None
        time.sleep(max(0.05, min(2, left)))


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
        # Briefs written before 2026-10-20 have no Owns section. --no-check lets
        # one start, claiming nothing, so nothing is reserved for it and a
        # sibling can claim the same path.
        try:
            owns = brief_owns(brief)
        except ColonyError:
            owns = []
            print(f"warning: {brief} has no Owns section, so {task_id} claims no "
                  "path and cannot be protected from a sibling. Add an Owns "
                  "section to reserve its paths", file=sys.stderr)
    else:
        owns = brief_owns(brief)
    if os.environ.get("HERDR_ENV") != "1":
        raise ColonyError("colony start must run in a Herdr pane (HERDR_ENV is not 1)")
    root = project_root()
    if not in_git_repo(root):
        raise ColonyError(f"{root} is not a Git repository. A colony keys its state on "
                          "the project root, so outside a repository it would key on "
                          "the working directory and two colonies could share state")
    graft_check(root)
    sd = state_dir()
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
                              "colony status --all lists the tasks")
    # The code a worker starts from cannot be recovered once the tree moves on.
    revision, dirty, why_no_revision = git_state(root)
    dirty_paths, dirty_digest, why_no_tree = tree_state(root)
    meta_path = os.path.join(sd, "colony.json")
    meta = load_json(meta_path, {})
    if not meta.get("master_session"):
        pane, session = master_pane_info()
        meta.update({"name": colony_name(), "root": project_root(), "master_pane": pane,
                     "master_session": session, "master_sessions": [session],
                     "created": meta.get("created") or now()})
        save_json(meta_path, meta)
    # The first worker opens a column beside the Master; later ones stack in it.
    open_panes = [t["pane"] for t in all_tasks(sd) if not t.get("closed")]
    if open_panes:
        where = [open_panes[-1], "--direction", "down"]
    else:
        where = ["--current", "--direction", "right"]
    name = agent_name(task_id)
    cwd = os.getcwd()
    worktree = branch = None
    if a.worktree:
        worktree, branch = make_worktree(root, sd, task_id)
        cwd = worktree
    # A worker in a worktree has its own work-tree root, so its pane carries the
    # colony's root. Without it, colony done there would find no task.
    title = f"{os.path.basename(project_root())} {task_id}"
    env = ["--env", f"COLONY_NAME={colony_name()}",
           "--env", f"COLONY_ROOT={root}",
           # An explicit model in an Agent call beats this default:
           # verified against the real engine on 2026-10-20.
           "--env", "CLAUDE_CODE_SUBAGENT_MODEL="
                    + cfg["subagent_models"]["default"]]
    # A pane splits the Master's column, so each worker after the first is
    # shorter than the last and four of them are unreadable. A tab gives each
    # worker the whole window and the colony addresses it by its root pane.
    tab = None
    if a.tab:
        made = herdr("tab", "create", "--cwd", cwd, "--label", title, "--no-focus", *env)
        pane = made["result"]["root_pane"]["pane_id"]
        tab = made["result"]["tab"]["tab_id"]
    else:
        split = herdr("pane", "split", *where, "--cwd", cwd, "--no-focus", *env)
        pane = split["result"]["pane"]["pane_id"]
    try:
        start = ["agent", "start", name, "--kind", "claude", "--pane", pane,
                 "--timeout", "60000", "--", "--name", title,
                 "--exclude-dynamic-system-prompt-sections",
                 "--model", model, "--effort", effort]
        # A colony keeps its state outside the project, so a worker's brief and
        # result file sit outside its working directory. Name those directories
        # for the worker, or its first read waits for someone to allow it.
        for path in outside_cwd(cwd, sd, os.path.dirname(brief)):
            start += ["--add-dir", path]
        # A prompt sentence did not change how workers read, so the rule is
        # enforced by a hook on the worker's own tool calls.
        start += ["--settings", write_worker_settings(sd, task_id,
                                                      retrieval_policy(cfg))]
        try:
            herdr(*start)
        except ColonyError as e:
            if "not ready" not in str(e):
                raise
            if not wait_ready(name, START_READY_WAIT):
                raise
            print(f"warning: herdr said {name} was not ready; it became ready and was "
                  f"used anyway", file=sys.stderr)
        herdr("agent", "prompt", name,
              PROMPT.format(id=task_id, brief=brief,
                            reviewer_alias=cfg["subagent_models"]["reviewer"],
                            explore_alias=cfg["subagent_models"]["explore"],
                            explore_budget=cfg["explore_budget"]))
    except ColonyError as e:
        herdr_quiet("tab", "close", tab) if tab else herdr_quiet("pane", "close", pane)
        if worktree:
            remove_worktree(root, worktree, force=True)
        append_event(sd, task_id, "START-FAILED", "-", str(e))
        raise
    task = {"id": task_id, "agent": name, "pane": pane, "tab": tab, "brief": brief, "model": model,
            "effort": effort, "cwd": cwd, "started": now(), "closed": None, "session": None,
            "revision": revision, "dirty": dirty, "verdict": None, "retry_of": retry_of,
            "dirty_paths": dirty_paths, "dirty_digest": dirty_digest,
            "config_dir": os.environ.get("CLAUDE_CONFIG_DIR"), "owns": owns,
            "worktree": worktree, "branch": branch, "root": root}
    if why_no_revision:
        task["revision_unavailable"] = why_no_revision
    if why_no_tree:
        task["tree_unavailable"] = why_no_tree
    save_task(sd, task)
    append_event(sd, task_id, "STARTED", brief,
                 f"pane {pane}, agent {name}, model {model}, effort {effort}, "
                 f"owns {' '.join(owns) if owns else '-'}"
                 + (f", worktree {worktree} on {branch}" if worktree else ""))
    print(f"started {task_id}: pane {pane}, agent {name}")
    if os.environ.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW") != MASTER_WINDOW:
        print(f"warning: this Master compacts at the default window. Next time, start it with "
              f"CLAUDE_CODE_AUTO_COMPACT_WINDOW={MASTER_WINDOW} claude", file=sys.stderr)
    bad = master_last_model(meta)
    if bad in cfg["banned"]:
        print(f"warning: this Master runs {bad}, which is not allowed for a Master. Next time, "
              f"start it with CLAUDE_CODE_AUTO_COMPACT_WINDOW={MASTER_WINDOW} "
              f"claude --model {cfg['master_model']}", file=sys.stderr)


def cmd_answer(a):
    task_id = check_id(a.id)
    sd = state_dir()
    task = load_task(sd, task_id)
    if not task.get("pane"):
        raise ColonyError(f"task {task_id} was started by colonybg, not colony; "
                          "run this command with colonybg")
    if task.get("closed"):
        raise ColonyError(f"task {task_id} is closed")
    text = clean(a.text)
    herdr("agent", "prompt", task["agent"], f"Answer from the Master on {task_id}: {text}")
    append_event(sd, task_id, "ANSWERED", "-", text[:SUMMARY_MAX])
    print(f"answered {task_id}")


def waits_for_input(task):
    """True when the worker's pane is blocked on input, as colony close reads it.

    A worker that asks the user with the question tool waits in its own pane.
    Waiting is not stopping, so such a worker must not be reported as IDLE. A
    pane that cannot be read counts as not waiting, so a Herdr hiccup cannot
    hide a worker that really has stopped.
    """
    try:
        pane = herdr("pane", "get", task["pane"])["result"]["pane"]
    except (ColonyError, KeyError, TypeError):
        return False
    return pane.get("agent_status") == "blocked"


def check_workers(sd, idle_seen):
    """Record LOST for a worker whose agent is gone, FAILING for one whose tool
    calls keep failing (colony_core.FAIL_STREAK), and IDLE for one that every
    check in a row has found idle without an outcome.

    A worker is idle between turns, while the Master answers a QUESTION, and
    while its own question tool waits for the user. Two checks a minute apart
    do not tell those apart from a worker that stopped: all 10 IDLE events in
    the archive to 2026-10-11 were followed by a real outcome. So IDLE needs
    IDLE_SAMPLES checks in a row and a pane that is not waiting for input.
    """
    for task_id, ev in latest_events(sd).items():
        if ev[2] not in WATCHED:
            continue
        try:
            task = load_task(sd, task_id)
        except ColonyError:
            continue
        try:
            info = herdr("agent", "get", task["agent"])
        except ColonyError as e:
            if "not found" in str(e):
                append_event(sd, task_id, "LOST", "-", "worker agent not found in Herdr")
            continue
        agent = info.get("result", {}).get("agent", {})
        session = task.get("session") or (agent.get("agent_session") or {}).get("value")
        check_failing(sd, task_id, session, task.get("config_dir"))
        status = agent.get("agent_status")
        if status not in ("idle", "done") or waits_for_input(task):
            idle_seen.pop(task_id, None)
            continue
        if last_event_kind(sd, task_id) == "IDLE":
            # Already reported. A second IDLE for the same idle stretch is true
            # and useless, and it wakes the Master for nothing.
            continue
        seen = idle_seen.get(task_id, 0) + 1
        idle_seen[task_id] = seen
        if seen >= IDLE_SAMPLES:
            append_event(sd, task_id, "IDLE", "-",
                         f"worker idle for {seen} checks without colony done")
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
            print(f"no new outcomes in {a.timeout}s; run colony wait again")
            return
        if time.monotonic() >= next_check:
            check_workers(sd, idle_seen)
            next_check = time.monotonic() + CHECK_EVERY
        time.sleep(max(0.05, min(2, CHECK_EVERY, deadline - time.monotonic())))


def cmd_close(a):
    task_id = check_id(a.id)
    verdict = close_verdict(a)
    sd = state_dir()
    note_master_session(sd)
    task = load_task(sd, task_id)
    if not task.get("pane"):
        raise ColonyError(f"task {task_id} was started by colonybg, not colony; "
                          "run this command with colonybg")
    if task.get("closed"):
        # A Master often confirms a result after closing the pane, so a verdict
        # can still be recorded. Nothing else changes, and no event is written.
        if verdict:
            task["verdict"] = verdict
            save_task(sd, task)
            print(f"{task_id} was closed at {task['closed']}; verdict {verdict}")
        else:
            print(f"{task_id} was closed at {task['closed']}")
        return
    session = None
    try:
        pane = herdr("pane", "get", task["pane"])["result"]["pane"]
        session = (pane.get("agent_session") or {}).get("value")
        if pane.get("agent_status") == "blocked" and not a.force:
            raise ColonyError(f"worker {task_id} waits for input (blocked); "
                              "answer it in its pane, or use --force")
        # A worker given its own tab leaves an empty tab behind if only its
        # pane is closed, so the tab is what closes.
        if task.get("tab"):
            herdr("tab", "close", task["tab"])
        else:
            herdr("pane", "close", task["pane"])
    except ColonyError as e:
        if "not found" not in str(e):
            raise
    task["closed"] = now()
    task["session"] = session
    task["verdict"] = verdict
    note = ""
    tally = read_tally(sd, task_id)
    if tally:
        note = f"retrieval hook saw {tally['seen']} calls, refused {tally['denied']}"
    else:
        note = "the retrieval hook never ran"
    if task.get("worktree"):
        # A worker's work lives in its worktree, so an unclean one is kept.
        removed, why = remove_worktree(task.get("root") or project_root(),
                                       task["worktree"])
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
    cmd_report_from(a, sd, live_session=live_session)


def cmd_checkpoint(a):
    sd = state_dir()
    note_master_session(sd)
    print(write_checkpoint(sd))


def cmd_restart(a):
    if not a.confirm:
        raise ColonyError("restart clears this session's context; run it with --confirm")
    sd = state_dir()
    path = checkpoint_path(sd)
    checkpoint_ready(path, prog="colony")
    pane = os.environ.get("HERDR_PANE_ID")
    if not pane:
        raise ColonyError("colony restart must run in the Master's Herdr pane")
    # Claude Code queues input typed during a turn and runs it when the turn ends.
    herdr("agent", "prompt", pane, "/clear")
    herdr("agent", "prompt", pane, RESUME_PROMPT.format(name=colony_name(), checkpoint=path))
    append_event(sd, "MASTER", "RESTART", path, "context cleared; resume from checkpoint")
    print(f"restart queued: /clear, then resume from {path}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="colony", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("brief", help="write a brief template; prints its path")
    b.add_argument("id")
    b.add_argument("--result", help="result file; default <colony>/results/<id>.md")
    b.set_defaults(func=cmd_brief)
    s = sub.add_parser("start", help="open a pane, start a worker, send it the brief")
    s.add_argument("id")
    s.add_argument("brief")
    s.add_argument("--model", help="the worker's model; overrides the brief's Model section")
    s.add_argument("--effort", help="the worker's effort; overrides the brief's Effort section")
    s.add_argument("--no-check", action="store_true", help="skip the brief section check")
    s.add_argument("--worktree", action="store_true",
                   help="give the worker its own Git worktree and branch, indexed "
                        "for graft, so it cannot overwrite a sibling's edits")
    s.add_argument("--tab", action="store_true",
                   help="give the worker its own Herdr tab instead of a pane beside "
                        "the Master, so every worker gets the whole window")
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
    c = sub.add_parser("close", help="record the session ID and close the pane")
    c.add_argument("id")
    c.add_argument("--force", action="store_true", help="close even if the worker waits for input")
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
    rs = sub.add_parser("restart", help="clear the Master and resume it from the checkpoint")
    rs.add_argument("--confirm", action="store_true", help="required: the Master's context is cleared")
    rs.set_defaults(func=cmd_restart)

    rh = sub.add_parser("retrieval-hook",
                        help="a worker's PreToolUse hook; reads the event on stdin")
    rh.add_argument("--read-limit", type=int, required=True)
    rh.add_argument("--tally", required=True)
    rh.set_defaults(func=cmd_retrieval_hook)
    a = p.parse_args(argv)
    try:
        a.func(a)
    except ColonyError as e:
        print(f"colony: {e}", file=sys.stderr)
        return e.code
    return 0


if __name__ == "__main__":
    sys.exit(main())
