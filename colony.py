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
  colony start <id> <brief> [--model M] [--effort E]  open a pane, start a worker, send the brief
  colony wait [--timeout S]                  block until new outcomes, then print them
  colony answer <id> <text>                  reply to a worker that asked a QUESTION
  colony close <id> [--force]                record the worker's session ID, close its pane
  colony status [--all]                      one line per task
  colony report                              tokens per worker and for the Master
  colony checkpoint                          write the Master's state file; prints its path
  colony restart --confirm                   clear the Master and resume it from the checkpoint
Worker:
  colony done <id> <status> <result> <summary>   PASS, FAIL, BLOCKED or QUESTION

Start the Master with CLAUDE_CODE_AUTO_COMPACT_WINDOW=600000 claude: Masters
compact at 600k, other sessions at 800k. colony start warns when it is missing.

A project can hold several colonies: set COLONY_NAME (default "main"). Workers
inherit it. State: $COLONY_HOME (default ~/.local/state/claude-system/colony)/
<project path>/<colony name>/. Exit codes: 0 ok, 1 Herdr failed, 2 usage error.
"""

import argparse
import fcntl
import glob
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

OUTCOMES = ("PASS", "FAIL", "BLOCKED", "QUESTION")
# Statuses that wake the Master: worker outcomes plus the ones colony itself detects.
REPORTED = OUTCOMES + ("LOST", "IDLE", "START-FAILED")
SUMMARY_MAX = 300
SCRATCH = ("/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/")
# A brief must have these sections, so every worker gets the same protocol.
# Every brief names a pinned model and an effort; both are validated against
# colony-models.json (models_config). The config, not this file, holds the
# allowed models, the banned ones and the effort levels, so a fork sets its own
# policy in one place without editing the code.
BRIEF_SECTIONS = ("goal", "inputs", "acceptance criteria", "you may decide",
                  "model", "effort", "result")
# Seconds between worker health checks in wait, and seconds to collect outcomes
# that arrive together. The tests shorten both.
CHECK_EVERY = float(os.environ.get("COLONY_CHECK_EVERY", 60))
SETTLE = float(os.environ.get("COLONY_SETTLE", 3))
CHECKPOINT_FRESH = 15 * 60  # restart needs a checkpoint written this recently
MASTER_WINDOW = "600000"  # Masters compact earlier than other sessions (record 0005)

PROMPT = (
    "Colony task {id}. Read the brief at {brief} and do the task. Decide alone "
    "only what its \"You may decide\" allows. Ask the user with the question "
    "tool about the user's choices; ask the Master about anything else with "
    "colony done {id} QUESTION - \"<question>\" and wait. When done: 1. Run "
    "the tests that apply. 2. Have one read-only reviewer sub-agent, with model "
    "{reviewer_model} and a new context, check the result against each criterion, in at "
    "most 12 tool calls. "
    "It is not a staff-review. Fix what it finds. 3. Write the result file, "
    "noting checks skipped or partial. 4. Save to memory project facts the next "
    "worker needs. 5. Run exactly one of: colony done {id} PASS <result file> "
    "\"criteria 1-N pass; confirm: <path:line or URL> ...\" | colony done {id} "
    "FAIL <result file> \"<criterion that failed and why>\" | colony done {id} "
    "BLOCKED - \"<what stops you>\". PASS only if every criterion passes. "
    "Summaries are at most 300 characters. Then stop."
)

BRIEF_TEMPLATE = """# Task {id}

## Goal

<One or two sentences: what the result must make possible.>

## Inputs

- <path:line-range, URL or command, one per line>

## Acceptance criteria

1. <A check someone who did not write the result can run.>
2. <...>

## You may decide

- <Choices the worker makes alone, for example wording, file layout.>
- Anything not listed: ask the Master (QUESTION), or the user if the choice is the user's.

## Model

<One of: {models}. Pick the cheapest that can pass every acceptance criterion. Not allowed: {banned}.>

## Effort

<One of: {efforts}. Match the reasoning the task needs; a higher level costs more.>

## Result

Write to: {result}
"""

CHECKPOINT_TEMPLATE = """# Colony Master checkpoint: {name}

Written {when}. A Master that restarts reads this first, then runs
`colony status`.

## Plan

<The goal of this colony and the remaining steps.>

## Tasks

{status}

## Decisions, each with its reason

- <decision>: <reason>

## Open questions to the user

- <question, or none>

## Next step

<What the Master does first after it restarts.>
"""

RESUME_PROMPT = (
    "You are the Master of colony {name}. Your context was cleared on purpose. "
    "Read {checkpoint}, run colony status, start a background colony wait if a "
    "worker is still open, then continue from its Next step."
)


class ColonyError(Exception):
    def __init__(self, message, code=2):
        super().__init__(message)
        self.code = code


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(ts):
    return datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def project_root():
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return os.getcwd()


def colony_name():
    name = os.environ.get("COLONY_NAME") or "main"
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,19}", name):
        raise ColonyError(f"bad COLONY_NAME {name!r}: lowercase letters, digits and -, 20 at most")
    return name


def state_dir():
    home = os.environ.get("COLONY_HOME") or os.path.expanduser(
        "~/.local/state/claude-system/colony")
    path = os.path.join(home, re.sub(r"[^A-Za-z0-9]", "-", project_root()), colony_name())
    for sub in ("tasks", "briefs", "results"):
        os.makedirs(os.path.join(path, sub), exist_ok=True)
    return path


def check_id(task_id):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,29}", task_id):
        raise ColonyError(f"bad task id {task_id!r}: use letters, digits, - and _, 30 at most")
    return task_id


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


def clean(text):
    return re.sub(r"[\t\r\n]+", " ", text).strip()


def append_event(sd, task_id, status, result="-", summary=""):
    line = "\t".join([now(), task_id, status, clean(result) or "-", clean(summary)]) + "\n"
    with open(os.path.join(sd, "events.log"), "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(line)
        f.flush()
        fcntl.flock(f, fcntl.LOCK_UN)


def read_events(sd, offset=0):
    path = os.path.join(sd, "events.log")
    if not os.path.exists(path):
        return [], 0
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read()
    # Keep a partial last line for the next read.
    end = data.rfind(b"\n") + 1
    lines = data[:end].decode("utf-8").splitlines()
    return [ln.split("\t") for ln in lines if ln.count("\t") >= 4], offset + end


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def models_config():
    """The colony model policy from colony-models.json, or COLONY_MODELS_CONFIG.
    Read relative to the real script path, so it works through the ~/.local/bin
    symlink."""
    path = os.environ.get("COLONY_MODELS_CONFIG") or os.path.join(
        os.path.dirname(os.path.realpath(__file__)), "colony-models.json")
    data = load_json(path)
    if data is None:
        raise ColonyError(f"cannot read the colony model config at {path}")
    for key in ("banned", "worker_models", "master_model", "reviewer_model", "efforts"):
        if key not in data:
            raise ColonyError(f"the colony model config at {path} lacks {key!r}")
    return data


def check_model(cfg, value):
    """Raise unless the value is an allowed worker model."""
    if value in cfg["banned"]:
        raise ColonyError(f"the model {value!r} is not allowed; choose one of: "
                          + ", ".join(cfg["worker_models"]))
    if value not in cfg["worker_models"]:
        raise ColonyError("the model must be one of " + ", ".join(cfg["worker_models"])
                          + f", not {value!r}")


def check_effort(cfg, value):
    """Raise unless the value is an allowed effort level."""
    if value not in cfg["efforts"]:
        raise ColonyError("the effort must be one of " + ", ".join(cfg["efforts"])
                          + f", not {value!r}")


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


def load_task(sd, task_id):
    task = load_json(os.path.join(sd, "tasks", task_id + ".json"))
    if task is None:
        raise ColonyError(f"no task {task_id} in colony {colony_name()}; start it with colony start")
    return task


def save_task(sd, task):
    save_json(os.path.join(sd, "tasks", task["id"] + ".json"), task)


def all_tasks(sd):
    tasks = []
    for path in sorted(glob.glob(os.path.join(sd, "tasks", "*.json"))):
        task = load_json(path)
        if task:
            tasks.append(task)
    return tasks


def latest_events(sd):
    """Latest event per task id, from the whole log."""
    events, _ = read_events(sd)
    latest = {}
    for ev in events:
        latest[ev[1]] = ev
    return latest


def check_brief(path):
    with open(path, encoding="utf-8") as f:
        text = f.read().lower()
    headings = re.findall(r"^#+\s*(.+?)\s*$", text, re.M)
    missing = [s for s in BRIEF_SECTIONS if not any(h.startswith(s) for h in headings)]
    if missing:
        raise ColonyError("brief lacks sections: " + ", ".join(missing)
                          + "; start from colony brief <id>, or pass --no-check")
    if "<" in text and re.search(r"<(one or two sentences|path:line-range|a check someone|choices the worker|one of)", text):
        raise ColonyError("brief still holds template placeholders; fill them in")


def _section_value(text, name):
    """(present, first word) of a brief's '## <name>' section. present is False
    when the section is absent; the word is '' when the section is empty."""
    m = re.search(rf"^#+\s*{name}\s*$(.*?)(?=^#+\s|\Z)", text, re.M | re.S | re.I)
    if not m:
        return False, ""
    lines = [ln.strip() for ln in m.group(1).splitlines() if ln.strip()]
    if not lines:
        return True, ""
    return True, lines[0].split()[0].strip(".:,")


def brief_model_effort(path, cfg, flag_model=None, flag_effort=None):
    """(model, effort) for a worker, from the brief's Model and Effort sections,
    each overridden by its flag. Both are required and validated against cfg."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    model = flag_model
    if model is None:
        present, model = _section_value(text, "model")
        if not present:
            raise ColonyError("the brief has no Model section; name one of: "
                              + ", ".join(cfg["worker_models"]))
        if not model:
            raise ColonyError("the brief's Model section is empty; name one of: "
                              + ", ".join(cfg["worker_models"]))
    effort = flag_effort
    if effort is None:
        present, effort = _section_value(text, "effort")
        if not present:
            raise ColonyError("the brief has no Effort section; name one of: "
                              + ", ".join(cfg["efforts"]))
        if not effort:
            raise ColonyError("the brief's Effort section is empty; name one of: "
                              + ", ".join(cfg["efforts"]))
    check_model(cfg, model)
    check_effort(cfg, effort)
    return model, effort


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


def cmd_brief(a):
    task_id = check_id(a.id)
    sd = state_dir()
    path = os.path.join(sd, "briefs", task_id + ".md")
    if os.path.exists(path):
        raise ColonyError(f"{path} exists already; edit it or use a new id")
    result = os.path.abspath(a.result) if a.result else os.path.join(sd, "results", task_id + ".md")
    cfg = models_config()
    with open(path, "w", encoding="utf-8") as f:
        f.write(BRIEF_TEMPLATE.format(id=task_id, result=result,
                                      models=", ".join(cfg["worker_models"]),
                                      efforts=", ".join(cfg["efforts"]),
                                      banned=", ".join(cfg["banned"])))
    print(path)


def master_pane_info():
    pane = os.environ.get("HERDR_PANE_ID")
    if not pane:
        return None, None
    try:
        info = herdr("pane", "get", pane)["result"]["pane"]
        return pane, (info.get("agent_session") or {}).get("value")
    except (ColonyError, KeyError, TypeError):
        return pane, None


def master_last_model(meta):
    """The model of the Master session's most recent response, or None. Reads the
    last, not the most frequent: a session can switch model partway and stay."""
    session = meta.get("master_session")
    if not session:
        return None
    files = glob.glob(os.path.join(projects_dir(), "*", session + ".jsonl"))
    if not files:
        return None
    last = None
    try:
        with open(files[0], encoding="utf-8") as f:
            for line in f:
                if '"model"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") not in (None, "assistant"):
                    continue
                model = (rec.get("message") or {}).get("model")
                if model and not model.startswith("<"):
                    last = model
    except OSError:
        return None
    return last


def cmd_start(a):
    task_id = check_id(a.id)
    brief = os.path.abspath(a.brief)
    if not os.path.isfile(brief):
        raise ColonyError(f"no brief file at {brief}")
    if not a.no_check:
        check_brief(brief)
    cfg = models_config()
    model, effort = brief_model_effort(brief, cfg, a.model, a.effort)
    if os.environ.get("HERDR_ENV") != "1":
        raise ColonyError("colony start must run in a Herdr pane (HERDR_ENV is not 1)")
    sd = state_dir()
    if os.path.exists(os.path.join(sd, "tasks", task_id + ".json")):
        raise ColonyError(f"task {task_id} exists already; use a new id")
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
    split = herdr("pane", "split", *where, "--cwd", cwd, "--no-focus",
                  "--env", f"COLONY_NAME={colony_name()}")
    pane = split["result"]["pane"]["pane_id"]
    title = f"{os.path.basename(project_root())} {task_id}"
    try:
        start = ["agent", "start", name, "--kind", "claude", "--pane", pane,
                 "--timeout", "60000", "--", "--name", title,
                 "--exclude-dynamic-system-prompt-sections",
                 "--model", model, "--effort", effort]
        herdr(*start)
        herdr("agent", "prompt", name,
              PROMPT.format(id=task_id, brief=brief, reviewer_model=models_config()["reviewer_model"]))
    except ColonyError as e:
        herdr_quiet("pane", "close", pane)
        append_event(sd, task_id, "START-FAILED", "-", str(e))
        raise
    save_task(sd, {"id": task_id, "agent": name, "pane": pane, "brief": brief, "model": model,
                   "effort": effort, "cwd": cwd, "started": now(), "closed": None, "session": None})
    append_event(sd, task_id, "STARTED", brief,
                 f"pane {pane}, agent {name}, model {model}, effort {effort}")
    print(f"started {task_id}: pane {pane}, agent {name}")
    if os.environ.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW") != MASTER_WINDOW:
        print(f"warning: this Master compacts at the default window. Next time, start it with "
              f"CLAUDE_CODE_AUTO_COMPACT_WINDOW={MASTER_WINDOW} claude", file=sys.stderr)
    bad = master_last_model(meta)
    if bad in cfg["banned"]:
        print(f"warning: this Master runs {bad}, which is not allowed for a Master. Next time, "
              f"start it with CLAUDE_CODE_AUTO_COMPACT_WINDOW={MASTER_WINDOW} "
              f"claude --model {cfg['master_model']}", file=sys.stderr)


def cmd_done(a):
    task_id = check_id(a.id)
    status = a.status.upper()
    if status not in OUTCOMES:
        raise ColonyError(f"status must be one of {', '.join(OUTCOMES)}")
    summary = clean(a.summary)
    if len(summary) > SUMMARY_MAX:
        raise ColonyError(f"summary is {len(summary)} characters; shorten it to {SUMMARY_MAX}")
    sd = state_dir()
    load_task(sd, task_id)
    result = a.result
    if status in ("PASS", "FAIL"):
        result = os.path.abspath(result)
        if not os.path.exists(result):
            raise ColonyError(f"no result file at {result}")
        real = os.path.realpath(result) + ("/" if os.path.isdir(result) else "")
        if any(real.startswith(p) or result.startswith(p) for p in SCRATCH):
            raise ColonyError(f"{result} is in a scratch folder; write the result to a durable path")
    append_event(sd, task_id, status, result, summary)
    print(f"recorded {task_id} {status}")


def cmd_answer(a):
    task_id = check_id(a.id)
    sd = state_dir()
    task = load_task(sd, task_id)
    if task.get("closed"):
        raise ColonyError(f"task {task_id} is closed")
    text = clean(a.text)
    herdr("agent", "prompt", task["agent"], f"Answer from the Master on {task_id}: {text}")
    append_event(sd, task_id, "ANSWERED", "-", text[:SUMMARY_MAX])
    print(f"answered {task_id}")


def check_workers(sd, idle_seen):
    """Record LOST for a worker whose agent is gone, IDLE for one idle twice without an outcome."""
    for task_id, ev in latest_events(sd).items():
        if ev[2] not in ("STARTED", "ANSWERED"):
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
        status = info.get("result", {}).get("agent", {}).get("agent_status")
        if status in ("idle", "done"):
            if idle_seen.get(task_id):
                append_event(sd, task_id, "IDLE", "-", "worker stopped without colony done")
                idle_seen.pop(task_id, None)
            else:
                idle_seen[task_id] = True
        else:
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
    sd = state_dir()
    note_master_session(sd)
    task = load_task(sd, task_id)
    if task.get("closed"):
        print(f"{task_id} was closed at {task['closed']}")
        return
    session = None
    try:
        pane = herdr("pane", "get", task["pane"])["result"]["pane"]
        session = (pane.get("agent_session") or {}).get("value")
        if pane.get("agent_status") == "blocked" and not a.force:
            raise ColonyError(f"worker {task_id} waits for input (blocked); "
                              "answer it in its pane, or use --force")
        herdr("pane", "close", task["pane"])
    except ColonyError as e:
        if "not found" not in str(e):
            raise
    task["closed"] = now()
    task["session"] = session
    save_task(sd, task)
    append_event(sd, task_id, "CLOSED", session or "-", "")
    print(f"closed {task_id}; session {session or 'unknown'}")


def status_lines(sd, show_all):
    lines = []
    for task_id, ev in sorted(latest_events(sd).items()):
        if ev[2] == "CLOSED" and not show_all:
            continue
        age = int((datetime.now(timezone.utc) - parse_ts(ev[0])).total_seconds() // 60)
        lines.append(f"{task_id}\t{ev[2]}\t{age} min ago\t{ev[3]}\t{ev[4][:120]}")
    return lines


def cmd_status(a):
    lines = status_lines(state_dir(), a.all)
    print("\n".join(lines) if lines else ("no tasks" if a.all else "no open tasks"))


def projects_dir():
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    return os.path.join(base, "projects")


def transcript_usage(path, since=None):
    """Requests, tokens and first-request context of one transcript.

    A response can appear several times while it streams, with a growing output
    count, so each response counts once, with its largest output.
    """
    seen = {}
    models = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if '"usage"' not in line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = rec.get("message") or {}
            usage = msg.get("usage")
            if not usage:
                continue
            if since and rec.get("timestamp", "") < since:
                continue
            key = (msg.get("id"), rec.get("requestId"))
            ctx = (usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
                   + usage.get("cache_creation_input_tokens", 0))
            out = usage.get("output_tokens", 0)
            if key in seen:
                seen[key][1] = max(seen[key][1], out)
            else:
                seen[key] = [ctx, out]
                name = (msg.get("model") or "").replace("claude-", "")
                if name:
                    models[name] = models.get(name, 0) + 1
    values = list(seen.values())
    tokens = sum(c + o for c, o in values)
    model = max(models, key=models.get) if models else "-"
    return len(values), tokens, (values[0][0] if values else 0), model


def session_usage(session, since=None):
    """Usage of a session and its sub-agents: (requests, tokens, first context, sub-agent tokens, model)."""
    if not session:
        return None
    main = glob.glob(os.path.join(projects_dir(), "*", session + ".jsonl"))
    if not main:
        return None
    req, tok, first, model = transcript_usage(main[0], since)
    sub_tok = 0
    for path in glob.glob(os.path.join(main[0][:-len(".jsonl")], "subagents", "*.jsonl")):
        r, t, _, _ = transcript_usage(path, since)
        req += r
        sub_tok += t
    return req, tok + sub_tok, first, sub_tok, model


def fmt_k(n):
    return f"{n / 1000:,.0f}k"


def cmd_report(a):
    sd = state_dir()
    note_master_session(sd)
    meta = load_json(os.path.join(sd, "colony.json"), {})
    tasks = all_tasks(sd)
    if not tasks:
        print("no tasks")
        return
    print("task\tmodel\trequests\ttokens\tfirst context\tsub-agents\tminutes")
    total = 0
    for t in tasks:
        session = t.get("session")
        if not session and not t.get("closed"):
            try:
                pane = herdr("pane", "get", t["pane"])["result"]["pane"]
                session = (pane.get("agent_session") or {}).get("value")
            except (ColonyError, KeyError, TypeError):
                session = None
        u = session_usage(session)
        end = parse_ts(t["closed"]) if t.get("closed") else datetime.now(timezone.utc)
        minutes = int((end - parse_ts(t["started"])).total_seconds() // 60)
        if u is None:
            print(f"{t['id']}\t{t.get('model') or '-'}\t-\t-\t-\t-\t{minutes}")
            continue
        req, tok, first, sub, model = u
        total += tok
        print(f"{t['id']}\t{model}\t{req}\t{fmt_k(tok)}\t{fmt_k(first)}\t{fmt_k(sub)}\t{minutes}")
    since = min(t["started"] for t in tasks)
    sessions = meta.get("master_sessions") or [meta.get("master_session")]
    for i, session in enumerate(s for s in sessions if s):
        m = session_usage(session, since)
        if m:
            req, tok, _, sub, model = m
            label = "Master" if i == 0 else f"Master after restart {i}"
            print(f"{label} (since {since})\t{model}\t{req}\t{fmt_k(tok)}\t-\t{fmt_k(sub)}\t-")
            total += tok
    print(f"total\t\t\t{fmt_k(total)}")


def cmd_checkpoint(a):
    sd = state_dir()
    note_master_session(sd)
    path = os.path.join(sd, "master.md")
    if not os.path.exists(path):
        status = "\n".join("- " + ln.replace("\t", " · ") for ln in status_lines(sd, True)) or "- none yet"
        with open(path, "w", encoding="utf-8") as f:
            f.write(CHECKPOINT_TEMPLATE.format(name=colony_name(), when=now(), status=status))
    print(path)


def cmd_restart(a):
    if not a.confirm:
        raise ColonyError("restart clears this session's context; run it with --confirm")
    sd = state_dir()
    path = os.path.join(sd, "master.md")
    if not os.path.exists(path):
        raise ColonyError("no checkpoint; run colony checkpoint and fill it in first")
    if time.time() - os.path.getmtime(path) > CHECKPOINT_FRESH:
        raise ColonyError(f"checkpoint {path} is older than 15 minutes; update it first")
    with open(path, encoding="utf-8") as f:
        if "<The goal of this colony" in f.read():
            raise ColonyError("checkpoint still holds template placeholders; fill it in")
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
    c.set_defaults(func=cmd_close)
    st = sub.add_parser("status", help="one line per task")
    st.add_argument("--all", action="store_true", help="include closed tasks")
    st.set_defaults(func=cmd_status)
    r = sub.add_parser("report", help="tokens per worker and for the Master")
    r.set_defaults(func=cmd_report)
    ck = sub.add_parser("checkpoint", help="write the Master's state file; prints its path")
    ck.set_defaults(func=cmd_checkpoint)
    rs = sub.add_parser("restart", help="clear the Master and resume it from the checkpoint")
    rs.add_argument("--confirm", action="store_true", help="required: the Master's context is cleared")
    rs.set_defaults(func=cmd_restart)
    a = p.parse_args(argv)
    try:
        a.func(a)
    except ColonyError as e:
        print(f"colony: {e}", file=sys.stderr)
        return e.code
    return 0


if __name__ == "__main__":
    sys.exit(main())
