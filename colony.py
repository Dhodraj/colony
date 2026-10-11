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
# What the Master made of a result, recorded by colony close. A worker's PASS is
# its own claim; the verdict is the Master's, and stays unset until it gives one.
VERDICTS = ("accept", "reject", "partial")
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
# Checks in a row that must find a worker idle before IDLE is reported. The
# checks are CHECK_EVERY apart, so three of them is about three minutes.
IDLE_SAMPLES = max(1, int(os.environ.get("COLONY_IDLE_SAMPLES", 3)))
# Seconds to keep waiting for a worker agent that herdr says is not ready yet.
START_READY_WAIT = float(os.environ.get("COLONY_START_READY_WAIT", 120))
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


def git_state(root):
    """(revision, dirty, reason) for a project root.

    revision is the committed HEAD. dirty says whether the tree differs from
    it, counting untracked files, because a worker reads those too. reason
    says why either is unknown.

    A check that fails gives None, never a revision colony did not read and
    never a clean tree it could not confirm. The revision can be known while
    dirty is not, so the two are reported apart.
    """
    def git(*args):
        try:
            out = subprocess.run(["git", "-C", root, *args],
                                 capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as e:
            return None, f"git {args[0]} failed: {e}"
        if out.returncode != 0:
            first = (out.stderr.strip().splitlines() or [f"git exited {out.returncode}"])[0]
            return None, f"git {args[0]}: {first}"
        return out.stdout, None

    head, why = git("rev-parse", "HEAD")
    if head is None:
        return None, None, why
    revision = head.strip()
    if not revision:
        return None, None, "git rev-parse HEAD printed nothing"
    status, why = git("status", "--porcelain")
    if status is None:
        return revision, None, why
    return revision, bool(status.strip()), None


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
    check_pricing(data, path)
    return data


def check_pricing(cfg, path):
    """Validate the optional pricing block. A config without one stays valid."""
    block = cfg.get("pricing")
    if block is None:
        return
    if not isinstance(block, dict) or not isinstance(block.get("models", {}), dict):
        raise ColonyError(f"the pricing block in {path} must be an object with a models object")
    for model, rate in (block.get("models") or {}).items():
        if not isinstance(rate, dict):
            raise ColonyError(f"the pricing rates for {model!r} in {path} must be an object")
        unknown = sorted(set(rate) - set(RATE_KEYS))
        if unknown:
            raise ColonyError(f"the pricing rates for {model!r} in {path} name "
                              + ", ".join(unknown) + "; use " + ", ".join(RATE_KEYS))
        for key, value in rate.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                raise ColonyError(f"the pricing rate {model!r}.{key} in {path} must be a "
                                  "number of currency units per million tokens, not negative")


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
    if os.environ.get("HERDR_ENV") != "1":
        raise ColonyError("colony start must run in a Herdr pane (HERDR_ENV is not 1)")
    sd = state_dir()
    if os.path.exists(os.path.join(sd, "tasks", task_id + ".json")):
        raise ColonyError(f"task {task_id} exists already; use a new id")
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
    revision, dirty, why_no_revision = git_state(project_root())
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
              PROMPT.format(id=task_id, brief=brief, reviewer_model=models_config()["reviewer_model"]))
    except ColonyError as e:
        herdr_quiet("pane", "close", pane)
        append_event(sd, task_id, "START-FAILED", "-", str(e))
        raise
    task = {"id": task_id, "agent": name, "pane": pane, "brief": brief, "model": model,
            "effort": effort, "cwd": cwd, "started": now(), "closed": None, "session": None,
            "revision": revision, "dirty": dirty, "verdict": None, "retry_of": retry_of}
    if why_no_revision:
        task["revision_unavailable"] = why_no_revision
    save_task(sd, task)
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
    """Record LOST for a worker whose agent is gone, and IDLE for one that every
    check in a row has found idle without an outcome.

    A worker is idle between turns, while the Master answers a QUESTION, and
    while its own question tool waits for the user. Two checks a minute apart
    do not tell those apart from a worker that stopped: all 10 IDLE events in
    the archive to 2026-10-11 were followed by a real outcome. So IDLE needs
    IDLE_SAMPLES checks in a row and a pane that is not waiting for input.
    """
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
        if status not in ("idle", "done") or waits_for_input(task):
            idle_seen.pop(task_id, None)
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


def close_verdict(a):
    """The verdict its flags name, or None. Two flags are an error."""
    named = [v for v in VERDICTS if getattr(a, v, False)]
    if len(named) > 1:
        raise ColonyError("name one verdict, not " + ", ".join("--" + v for v in named))
    return named[0] if named else None


def cmd_close(a):
    task_id = check_id(a.id)
    verdict = close_verdict(a)
    sd = state_dir()
    note_master_session(sd)
    task = load_task(sd, task_id)
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
        herdr("pane", "close", task["pane"])
    except ColonyError as e:
        if "not found" not in str(e):
            raise
    task["closed"] = now()
    task["session"] = session
    task["verdict"] = verdict
    save_task(sd, task)
    append_event(sd, task_id, "CLOSED", session or "-", "")
    print(f"closed {task_id}; session {session or 'unknown'}"
          + (f"; verdict {verdict}" if verdict else ""))


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
    """Per-category usage of one transcript, split by the model that served it.

    A response can appear several times while it streams. Within one
    (message id, request id) the input and cache counts never change, and the
    output and thinking counts only grow: measured over 11,276 duplicate
    groups in 235 archived transcripts on 2026-10-11. So each response counts
    once, with its largest output and thinking.

    thinking_unknown counts responses whose copies all lacked
    output_tokens_details.thinking_tokens. There the field is missing, not
    zero, so the caller must not read a 0 sum as "no thinking".
    """
    seen = {}
    order = []
    with open(path, encoding="utf-8", errors="replace") as f:
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
            details = usage.get("output_tokens_details")
            think = None
            if isinstance(details, dict) and "thinking_tokens" in details:
                think = details["thinking_tokens"]
            out = usage.get("output_tokens", 0)
            entry = seen.get(key)
            if entry is None:
                creation = usage.get("cache_creation") or {}
                seen[key] = {
                    "model": msg.get("model") or "-",
                    "input": usage.get("input_tokens", 0),
                    "cache_read": usage.get("cache_read_input_tokens", 0),
                    "cache_write": usage.get("cache_creation_input_tokens", 0),
                    # The 1h/5m split is on cache writes only.
                    # cache_read_input_tokens is one scalar with no TTL split,
                    # so none is reported for reads.
                    "cache_write_1h": creation.get("ephemeral_1h_input_tokens", 0),
                    "cache_write_5m": creation.get("ephemeral_5m_input_tokens", 0),
                    "output": out,
                    "thinking": think,
                    "effort": rec.get("effort"),
                    "per_turn_effort": rec.get("perTurnEffort"),
                    "requested_model": rec.get("requestedModel"),
                }
                order.append(key)
            else:
                entry["output"] = max(entry["output"], out)
                if think is not None:
                    entry["thinking"] = think if entry["thinking"] is None \
                        else max(entry["thinking"], think)
    return summarize([seen[k] for k in order])


def blank_usage():
    return {"requests": 0, "tokens": 0, "first_context": 0, "input": 0, "output": 0,
            "thinking": 0, "thinking_unknown": 0, "cache_read": 0, "cache_write": 0,
            "cache_write_1h": 0, "cache_write_5m": 0, "by_model": {}, "efforts": {},
            "per_turn_efforts": {}, "requested_models": {}}


def summarize(responses):
    """Totals, per-model totals and per-request settings of deduplicated responses."""
    u = blank_usage()
    for i, r in enumerate(responses):
        # Context is what the request carried: fresh input plus both cache parts.
        ctx = r["input"] + r["cache_read"] + r["cache_write"]
        if i == 0:
            u["first_context"] = ctx
        u["requests"] += 1
        u["tokens"] += ctx + r["output"]
        m = u["by_model"].setdefault(r["model"], {
            "requests": 0, "tokens": 0, "input": 0, "output": 0, "thinking": 0,
            "thinking_unknown": 0, "cache_read": 0, "cache_write": 0,
            "cache_write_1h": 0, "cache_write_5m": 0})
        m["requests"] += 1
        m["tokens"] += ctx + r["output"]
        for key in ("input", "output", "cache_read", "cache_write",
                    "cache_write_1h", "cache_write_5m"):
            u[key] += r[key]
            m[key] += r[key]
        if r["thinking"] is None:
            u["thinking_unknown"] += 1
            m["thinking_unknown"] += 1
        else:
            u["thinking"] += r["thinking"]
            m["thinking"] += r["thinking"]
        for key, value in (("efforts", r["effort"]),
                           ("per_turn_efforts", r["per_turn_effort"]),
                           ("requested_models", r["requested_model"])):
            if value is None:
                continue
            name = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
            u[key][name] = u[key].get(name, 0) + 1
    return u


def add_usage(into, other):
    """Add one usage summary into another. first_context keeps the earlier one."""
    for key in ("requests", "tokens", "input", "output", "thinking", "thinking_unknown",
                "cache_read", "cache_write", "cache_write_1h", "cache_write_5m"):
        into[key] += other[key]
    for model, totals in other["by_model"].items():
        target = into["by_model"].setdefault(model, {k: 0 for k in totals})
        for key, value in totals.items():
            target[key] = target.get(key, 0) + value
    for field in ("efforts", "per_turn_efforts", "requested_models"):
        for name, count in other[field].items():
            into[field][name] = into[field].get(name, 0) + count
    return into


def top_model(usage):
    """The model that served the most requests, or None when none did."""
    models = usage["by_model"]
    return max(models, key=lambda m: models[m]["requests"]) if models else None


def session_usage(session, since=None):
    """A session's own usage and one entry per sub-agent transcript, or None.

    A sub-agent entry carries its agentType and description from its
    agent-<id>.meta.json. Claude Code never names an agent type "reviewer", so
    a worker's bounded reviewer cannot be told apart from its other sub-agents
    here; the type and description are reported as they stand.
    """
    if not session:
        return None
    main = glob.glob(os.path.join(projects_dir(), "*", session + ".jsonl"))
    if not main:
        return None
    own = transcript_usage(main[0], since)
    subs = []
    for path in sorted(glob.glob(os.path.join(main[0][:-len(".jsonl")], "subagents", "*.jsonl"))):
        sub = transcript_usage(path, since)
        meta = load_json(path[:-len(".jsonl")] + ".meta.json", {}) or {}
        sub["file"] = os.path.basename(path)
        sub["agent_type"] = meta.get("agentType")
        sub["description"] = meta.get("description")
        subs.append(sub)
    total = add_usage(blank_usage(), own)
    total["first_context"] = own["first_context"]
    for sub in subs:
        add_usage(total, sub)
    return {"session": session, "transcript": main[0], "own": own, "subs": subs,
            "total": total}


# Rates are per million tokens, by the category the tokens fall in. A model is
# priced only when every category it actually used has a rate, so a half-filled
# rate set reports no cost instead of a low one.
RATE_KEYS = ("input", "output", "cache_read", "cache_write_1h", "cache_write_5m")


def pricing_rates(cfg):
    """The configured model rates, or {} when the config has no pricing block."""
    block = cfg.get("pricing") or {}
    return block.get("models") or {}


def cost_estimate(cfg, usage):
    """(cost in the configured currency, [reasons it is unavailable]).

    cost is None when no model could be priced. A model is skipped, with a
    reason, when it has no rate set, when a category it used has no rate, or
    when it is <synthetic>: a synthetic response was not served by a model and
    is not billed.
    """
    rates = pricing_rates(cfg)
    cost = None
    reasons = []
    for model, totals in sorted(usage["by_model"].items()):
        if model.startswith("<"):
            reasons.append(f"{model}: not a served model, not priced")
            continue
        rate = rates.get(model)
        if not rate:
            reasons.append(f"{model}: no rate in the pricing config")
            continue
        used = [k for k in RATE_KEYS if totals.get(k)]
        missing = [k for k in used if rate.get(k) is None]
        if missing:
            reasons.append(f"{model}: no rate for " + ", ".join(missing))
            continue
        amount = sum(totals[k] * rate[k] / 1_000_000 for k in used)
        cost = amount if cost is None else cost + amount
    return cost, reasons


def fmt_k(n):
    return f"{n / 1000:,.0f}k"


def fmt_thinking(usage):
    """Thinking tokens, or - when no response in the set reported the field."""
    if usage["requests"] and usage["thinking_unknown"] == usage["requests"]:
        return "-"
    return fmt_k(usage["thinking"])


def fmt_cost(cost):
    return "-" if cost is None else f"{cost:,.2f}"


def short_model(model):
    return (model or "-").replace("claude-", "")


def task_session(task):
    """A task's worker session: the recorded one, or the open pane's."""
    session = task.get("session")
    if session or task.get("closed"):
        return session
    try:
        pane = herdr("pane", "get", task["pane"])["result"]["pane"]
        return (pane.get("agent_session") or {}).get("value")
    except (ColonyError, KeyError, TypeError):
        return None


def report_rows(sd, meta, tasks):
    """One row per worker and per Master session, newest Master last.

    A row is {"label", "kind", "task", "pinned_model", "pinned_effort",
    "minutes", "since", "usage" or None}. usage is None when the session's
    transcript was not found, which the report shows as - rather than 0.
    """
    rows = []
    for task in tasks:
        usage = session_usage(task_session(task))
        end = parse_ts(task["closed"]) if task.get("closed") else datetime.now(timezone.utc)
        rows.append({"label": task["id"], "kind": "worker", "task": task["id"],
                     "pinned_model": task.get("model"), "pinned_effort": task.get("effort"),
                     "minutes": int((end - parse_ts(task["started"])).total_seconds() // 60),
                     "since": None, "usage": usage})
    since = min(t["started"] for t in tasks)
    sessions = meta.get("master_sessions") or [meta.get("master_session")]
    for i, session in enumerate(s for s in sessions if s):
        usage = session_usage(session, since)
        if not usage:
            continue
        label = "Master" if i == 0 else f"Master after restart {i}"
        rows.append({"label": label, "kind": "master", "task": None,
                     "pinned_model": None, "pinned_effort": None, "minutes": None,
                     "since": since, "usage": usage})
    return rows, since


def name_list(labels, most=8):
    """At most `most` labels, then how many more."""
    shown = ", ".join(labels[:most])
    return shown if len(labels) <= most else f"{shown} +{len(labels) - most} more"


def report_notes(cfg, rows):
    """Everything the numbers do not say: drift, missing data, unpriced models.

    Notes are grouped, not repeated per row: a Master reads this report, and
    every repeated line costs it context.
    """
    notes = []
    missing, drift, synthetic, partial, unpriced, asked = [], {}, [], [], {}, {}
    thin_rows = thin_responses = thin_total = 0
    for row in rows:
        usage = row["usage"]
        if usage is None:
            missing.append(row["label"])
            continue
        total = usage["total"]
        served = top_model(total)
        pinned = row["pinned_model"]
        if pinned and served and pinned != served:
            drift.setdefault((pinned, served), []).append(row["label"])
        for model, counts in sorted(total["requested_models"].items()):
            asked.setdefault(model, []).append(f"{row['label']} x{counts}")
        if total["thinking_unknown"]:
            thin_rows += 1
            thin_responses += total["thinking_unknown"]
            thin_total += total["requests"]
            partial.append(row["label"])
        for model, totals in sorted(total["by_model"].items()):
            if model.startswith("<"):
                synthetic.append(f"{row['label']} x{totals['requests']}")
        for reason in cost_estimate(cfg, total)[1]:
            unpriced.setdefault(reason, []).append(row["label"])
    if missing:
        notes.append(f"no transcript found for {len(missing)} row(s), so their usage is "
                     f"unavailable, not zero: {name_list(missing)}")
    for (pinned, served), labels in sorted(drift.items()):
        notes.append(f"brief pinned {pinned} but {served} served most requests on "
                     f"{len(labels)} row(s): {name_list(labels)}")
    if asked:
        for model, where in sorted(asked.items()):
            notes.append(f"requestedModel {model} on {name_list(where)}")
    else:
        notes.append("no requestedModel field on any request, so the requested model is "
                     "unavailable; the served model comes from message.model")
    if partial:
        notes.append(f"thinking is a partial sum: {thin_responses} of {thin_total} responses "
                     f"on {thin_rows} row(s) carried no thinking_tokens field "
                     f"({name_list(partial)})")
    if synthetic:
        notes.append(f"<synthetic> responses, which no model served and none is billed: "
                     f"{name_list(synthetic)}")
    for reason, labels in sorted(unpriced.items()):
        notes.append(f"{reason}, on {len(labels)} row(s)")
    if not pricing_rates(cfg):
        notes.append("no pricing rates configured; add them under pricing.models in the "
                     "colony model config to get a cost")
    return notes


def report_json(sd, cfg, meta, rows, since):
    """The report as data. A value is null where the source does not have it."""
    block = cfg.get("pricing") or {}
    out = {"colony": colony_name(), "root": meta.get("root") or project_root(),
           "generated": now(), "since": since,
           "pricing": {"units": block.get("units"), "models_with_rates": sorted(pricing_rates(cfg))},
           "rows": [], "totals": None, "notes": report_notes(cfg, rows)}
    grand = blank_usage()
    for row in rows:
        usage = row["usage"]
        item = {k: row[k] for k in ("label", "kind", "task", "pinned_model",
                                    "pinned_effort", "minutes", "since")}
        if usage is None:
            item.update({"session": None, "measured": False, "usage": None,
                         "sub_agents": [], "cost": None})
            out["rows"].append(item)
            continue
        cost, reasons = cost_estimate(cfg, usage["total"])
        add_usage(grand, usage["total"])
        item.update({"session": usage["session"], "transcript": usage["transcript"],
                     "measured": True, "served_model": top_model(usage["total"]),
                     "own": usage["own"], "total": usage["total"],
                     "sub_agents": usage["subs"], "cost": cost,
                     "cost_unavailable_because": reasons})
        out["rows"].append(item)
    cost, reasons = cost_estimate(cfg, grand)
    out["totals"] = {"usage": grand, "cost": cost, "cost_unavailable_because": reasons,
                     "measured_rows": sum(1 for r in rows if r["usage"]),
                     "rows": len(rows)}
    return out


def cmd_report(a):
    sd = state_dir()
    note_master_session(sd)
    cfg = models_config()
    meta = load_json(os.path.join(sd, "colony.json"), {})
    tasks = all_tasks(sd)
    if not tasks:
        # The same shape as a full report, so a reader needs no special case.
        if a.json:
            empty = report_json(sd, cfg, meta, [], None)
            empty["notes"] = ["no tasks"]
            print(json.dumps(empty, indent=1, default=str))
        else:
            print("no tasks")
        return
    rows, since = report_rows(sd, meta, tasks)
    if a.json:
        print(json.dumps(report_json(sd, cfg, meta, rows, since), indent=1, default=str))
        return
    print("task\tmodel\trequests\ttokens\tfirst context\tsub-agents\tminutes")
    total = 0
    for row in rows:
        usage = row["usage"]
        if row["kind"] == "worker":
            if usage is None:
                print(f"{row['task']}\t{short_model(row['pinned_model'])}\t-\t-\t-\t-\t{row['minutes']}")
                continue
            print(f"{row['task']}\t{short_model(top_model(usage['total']))}\t"
                  f"{usage['total']['requests']}\t{fmt_k(usage['total']['tokens'])}\t"
                  f"{fmt_k(usage['own']['first_context'])}\t"
                  f"{fmt_k(usage['total']['tokens'] - usage['own']['tokens'])}\t{row['minutes']}")
        else:
            print(f"{row['label']} (since {row['since']})\t{short_model(top_model(usage['total']))}\t"
                  f"{usage['total']['requests']}\t{fmt_k(usage['total']['tokens'])}\t-\t"
                  f"{fmt_k(usage['total']['tokens'] - usage['own']['tokens'])}\t-")
        total += usage["total"]["tokens"]
    print(f"total\t\t\t{fmt_k(total)}")
    print()
    print("where the tokens went")
    print("row\tinput\toutput\tthinking\tcache read\tcache write 5m\tcache write 1h\tcost")
    grand = blank_usage()
    for row in rows:
        usage = row["usage"]
        if usage is None:
            print(f"{row['label']}\t-\t-\t-\t-\t-\t-\t-")
            continue
        u = usage["total"]
        add_usage(grand, u)
        cost, _ = cost_estimate(cfg, u)
        print(f"{row['label']}\t{fmt_k(u['input'])}\t{fmt_k(u['output'])}\t{fmt_thinking(u)}\t"
              f"{fmt_k(u['cache_read'])}\t{fmt_k(u['cache_write_5m'])}\t"
              f"{fmt_k(u['cache_write_1h'])}\t{fmt_cost(cost)}")
    cost, _ = cost_estimate(cfg, grand)
    print(f"total\t{fmt_k(grand['input'])}\t{fmt_k(grand['output'])}\t{fmt_thinking(grand)}\t"
          f"{fmt_k(grand['cache_read'])}\t{fmt_k(grand['cache_write_5m'])}\t"
          f"{fmt_k(grand['cache_write_1h'])}\t{fmt_cost(cost)}")
    subs = [(row, sub) for row in rows if row["usage"] for sub in row["usage"]["subs"]]
    if subs:
        print()
        print("sub-agents")
        print("row\tagent type\trequests\ttokens\tdescription")
        for row, sub in subs:
            print(f"{row['label']}\t{sub['agent_type'] or '-'}\t{sub['requests']}\t"
                  f"{fmt_k(sub['tokens'])}\t{(sub['description'] or '-')[:60]}")
    efforts = blank_usage()
    for row in rows:
        if row["usage"]:
            add_usage(efforts, row["usage"]["total"])
    if efforts["efforts"] or efforts["per_turn_efforts"]:
        print()
        print("effort\trequests\tper-turn effort\trequests")
        served = sorted(efforts["efforts"].items())
        turn = sorted(efforts["per_turn_efforts"].items())
        for i in range(max(len(served), len(turn))):
            left = f"{served[i][0]}\t{served[i][1]}" if i < len(served) else "-\t-"
            right = f"{turn[i][0]}\t{turn[i][1]}" if i < len(turn) else "-\t-"
            print(f"{left}\t{right}")
    notes = report_notes(cfg, rows)
    if notes:
        print()
        print("notes")
        for note in notes:
            print(f"- {note}")


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
