#!/usr/bin/env python3
"""colony_core: everything colony.py and colony_bg.py share.

Every function here is backend-free: no herdr call, no claude --bg call.
colony.py (the herdr backend) and colony_bg.py (the background-session
backend, no herdr) both import this module; it imports neither, so a change
to one backend can never reach the other through here.

Also a standalone CLI for one command, `retrieval-hook`: a worker's settings
file names this file's own path to run its PreToolUse hook, so the hook must
be runnable as `python3 colony_core.py retrieval-hook ...` on its own.
"""

import argparse
import fcntl
import glob
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone

OUTCOMES = ("PASS", "FAIL", "BLOCKED", "QUESTION")
# What the Master made of a result, recorded by close. A worker's PASS is its
# own claim; the verdict is the Master's, and stays unset until it gives one.
VERDICTS = ("accept", "reject", "partial")
# Statuses that wake the Master: worker outcomes plus the ones colony itself detects.
REPORTED = OUTCOMES + ("LOST", "IDLE", "START-FAILED")
SUMMARY_MAX = 300
# The Agent tool's model parameter takes one of these, not a pinned id.
SUBAGENT_ALIASES = ("sonnet", "opus", "haiku", "fable")
SCRATCH = ("/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/")
# A brief must have these sections, so every worker gets the same protocol.
# Every brief names a pinned model and an effort; both are validated against
# colony-models.json (models_config). The config, not this file, holds the
# allowed models, the banned ones and the effort levels, so a fork sets its own
# policy in one place without editing the code.
BRIEF_SECTIONS = ("goal", "inputs", "acceptance criteria", "owns", "you may decide",
                  "model", "effort", "result")
# Seconds between worker health checks in wait, and seconds to collect outcomes
# that arrive together. The tests shorten both.
CHECK_EVERY = float(os.environ.get("COLONY_CHECK_EVERY", 60))
SETTLE = float(os.environ.get("COLONY_SETTLE", 3))
# Checks in a row that must find a worker idle before IDLE is reported. The
# checks are CHECK_EVERY apart, so three of them is about three minutes.
IDLE_SAMPLES = max(1, int(os.environ.get("COLONY_IDLE_SAMPLES", 3)))
CHECKPOINT_FRESH = 15 * 60  # restart needs a checkpoint written this recently
MASTER_WINDOW = "400000"  # The window every session uses (record 0005, 2026-10-19)

PROMPT = (
    "Colony task {id}. Read the brief at {brief} and do the task. Decide alone "
    "only what its \"You may decide\" allows. Ask the user with the question "
    "tool about the user's choices; ask the Master about anything else with "
    "colony done {id} QUESTION - \"<question>\" and wait. Find code with graft, "
    "not rg or cat: graft ask, graft grep, graft skeleton. Put independent reads "
    "in one response. Spend at most {explore_budget} of your own tool calls on "
    "locating and reading; past that, send one read-only sub-agent with model "
    "{explore_alias} and a new context to read and report, and work from what it "
    "returns. Read a range, not a whole file: a hook refuses an unbounded read "
    "of a large file and names the cheaper call. "
    "Change only the paths the brief's Owns section names; another "
    "worker may hold the rest. When done: 1. Run "
    "the tests that apply. 2. Have one read-only reviewer sub-agent, with model "
    "{reviewer_alias} and a new context, check the result against each criterion, in at "
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

## Owns

- <Paths this worker may change, one per line, relative to the project root. No
  other open task may own the same path. Write `-` when the task changes no file.>

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
    """The project the colony belongs to.

    COLONY_ROOT pins it, because a worker in a Git worktree has a different
    work-tree root and would otherwise resolve a different colony state. Its
    pane carries the variable, so `colony done` in a worktree reaches the task
    that started it.
    """
    pinned = os.environ.get("COLONY_ROOT")
    if pinned:
        return pinned
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return os.getcwd()


def in_git_repo(root):
    """Whether the path sits inside a Git work tree."""
    try:
        out = subprocess.run(["git", "-C", root, "rev-parse", "--is-inside-work-tree"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0 and out.stdout.strip() == "true"


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


def tree_state(root):
    """(paths, digest, reason) for the uncommitted work in a tree.

    paths names every file that differs from HEAD, including untracked ones.
    digest is a hash of the porcelain status and the tracked diff, so two
    workers that read the same working tree carry the same digest. The digest
    covers the names of untracked files, not their contents, because hashing
    them would read the whole tree. reason says why either is unknown.
    """
    def git(*args):
        try:
            out = subprocess.run(["git", "-C", root, *args],
                                 capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as e:
            return None, f"git {args[0]} failed: {e}"
        if out.returncode != 0:
            first = (out.stderr.strip().splitlines() or [f"git exited {out.returncode}"])[0]
            return None, f"git {args[0]}: {first}"
        return out.stdout, None

    status, why = git("status", "--porcelain")
    if status is None:
        return None, None, why
    paths = sorted(line[3:].strip() for line in status.splitlines() if len(line) > 3)
    diff, why = git("diff", "HEAD")
    if diff is None:
        # A repository with no commit has no HEAD to diff against, so fall back
        # to the working tree against the index.
        diff, why2 = git("diff")
        if diff is None:
            return paths, None, why2 or why
    digest = hashlib.sha1((status + diff).encode("utf-8", "replace")).hexdigest()
    return paths, digest, None


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


def worktree_path(sd, task_id):
    return os.path.join(sd, "worktrees", task_id)


def worktree_branch(task_id):
    return f"colony/{colony_name()}/{task_id.lower()}"


def git_quiet(root, *args):
    """Run a git command and return (ok, first line of any error)."""
    try:
        out = subprocess.run(["git", "-C", root, *args],
                             capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"git {args[0]} failed: {e}"
    if out.returncode != 0:
        first = (out.stderr.strip().splitlines() or [f"git exited {out.returncode}"])[0]
        return False, f"git {' '.join(args[:2])}: {first}"
    return True, None


def make_worktree(root, sd, task_id):
    """Create a worktree for a task and index it. Returns (path, branch).

    A worker in its own checkout cannot overwrite a sibling's edits. graft
    detects the worktree and seeds its index from the parent graph, which took
    0.25 seconds on a 138-file repository, so the gate still has an index to
    check.
    """
    path = worktree_path(sd, task_id)
    branch = worktree_branch(task_id)
    if os.path.exists(path):
        raise ColonyError(f"{path} exists already; remove it or use a new task id")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ok, why = git_quiet(root, "worktree", "add", "-q", "-b", branch, path, "HEAD")
    if not ok:
        raise ColonyError(f"cannot make a worktree for {task_id}: {why}")
    exe = os.environ.get("COLONY_GRAFT", "graft")
    try:
        built = subprocess.run([exe, "build", path], capture_output=True, text=True,
                               timeout=600)
    except (OSError, subprocess.SubprocessError) as e:
        remove_worktree(root, path, force=True)
        raise ColonyError(f"cannot index the worktree for {task_id}: {e}")
    if built.returncode != 0:
        first = (built.stderr.strip() or built.stdout.strip()
                 or f"exit {built.returncode}").splitlines()[0]
        remove_worktree(root, path, force=True)
        raise ColonyError(f"cannot index the worktree for {task_id}: {first}")
    return path, branch


def remove_worktree(root, path, force=False):
    """Remove a worktree. Returns (removed, why not).

    A worker's work lives in its worktree, so an unclean one is kept, not
    discarded. force is for cleaning up a start that failed before any worker
    ran.
    """
    if not os.path.exists(path):
        return True, None
    if not force:
        try:
            out = subprocess.run(["git", "-C", path, "status", "--porcelain"],
                                 capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as e:
            return False, f"cannot read the worktree: {e}"
        if out.returncode != 0 or out.stdout.strip():
            return False, "it holds uncommitted work"
    args = ["worktree", "remove", "--force", path] if force else \
        ["worktree", "remove", path]
    ok, why = git_quiet(root, *args)
    if not ok:
        return False, why
    git_quiet(root, "worktree", "prune")
    return True, None


def retrieval_policy(cfg):
    """(read_limit_bytes,) the retrieval policy from the model config."""
    block = cfg.get("retrieval") or {}
    limit = block.get("read_limit_bytes")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ColonyError("retrieval.read_limit_bytes in the colony model config "
                          f"must be a whole number of bytes, at least 1, not {limit!r}")
    return limit


def tally_path(sd, task_id):
    return os.path.join(sd, "retrieval", task_id + ".json")


def bump_tally(path, denied):
    """Count one hook decision and return (seen, denied)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.seek(0)
            try:
                data = json.loads(f.read() or "{}")
            except json.JSONDecodeError:
                data = {}
            seen = (data.get("seen") or 0) + 1
            blocked = (data.get("denied") or 0) + (1 if denied else 0)
            f.seek(0)
            f.truncate()
            f.write(json.dumps({"seen": seen, "denied": blocked}))
            f.flush()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    return seen, blocked


def read_tally(sd, task_id):
    """{"seen", "denied"} for a task, or None when the hook never ran."""
    try:
        with open(tally_path(sd, task_id), encoding="utf-8") as f:
            data = json.loads(f.read() or "{}")
    except (OSError, json.JSONDecodeError):
        return None
    if not data.get("seen"):
        return None
    return data


BARE_CAT = re.compile(r"""^cat\s+(-n\s+)?(?P<path>"[^"]+"|'[^']+'|[^\s|<>;&]+)\s*$""")


def oversized_target(event, limit):
    """The path a tool call would read whole, when that file is over the limit.

    Only an unbounded read counts. A read that names a range, a search such as
    rg or grep, and a graft query all stay untouched: those are the cheap paths
    this gate exists to leave open.
    """
    tool = event.get("tool_name") or ""
    args = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    path = None
    if tool == "Read":
        if args.get("limit") is not None or args.get("offset") is not None:
            return None
        path = args.get("file_path")
    elif tool == "Bash":
        command = " ".join((args.get("command") or "").split())
        m = BARE_CAT.match(command)
        if not m:
            return None
        path = m.group("path").strip("\"'")
    if not isinstance(path, str) or not path:
        return None
    try:
        size = os.path.getsize(os.path.expanduser(path))
    except OSError:
        return None
    return (path, size) if size > limit else None


def cmd_retrieval_hook(a):
    """A worker's PreToolUse hook: refuse to read a large file whole.

    Measured over 227 archived workers: 765 of 1,383 Read calls named no range,
    and the 243 of those over 8,000 tokens carried 9.09M tokens, 91% of all
    unbounded read volume. A sentence in the prompt did not change this, so the
    rule is enforced instead of asked for. The reply names the cheaper call, so
    a worker is never stuck.
    """
    raw = sys.stdin.read()
    try:
        event = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return
    if not isinstance(event, dict):
        return
    if "/subagents/" in (event.get("transcript_path") or ""):
        return
    over = oversized_target(event, a.read_limit)
    bump_tally(a.tally, bool(over))
    if not over:
        return
    path, size = over
    emit_hook({"hookEventName": "PreToolUse", "permissionDecision": "deny",
               "permissionDecisionReason":
               f"{path} is {size:,} bytes, over the {a.read_limit:,}-byte limit for "
               "reading a file whole. Read a range instead, with Read and its "
               "limit and offset; or search it with rg; or ask graft for the "
               f"shape: graft skeleton {path}."})


def emit_hook(payload):
    print(json.dumps({"hookSpecificOutput": payload}))


def write_worker_settings(sd, task_id, read_limit):
    """A settings file holding the worker's retrieval hook. Everything the hook
    needs is on its command line, so it reads no config before each tool call."""
    command = " ".join([
        shlex.quote(sys.executable), "-I",
        shlex.quote(os.path.realpath(__file__)), "retrieval-hook",
        "--read-limit", str(read_limit),
        "--tally", shlex.quote(tally_path(sd, task_id))])
    path = os.path.join(sd, "settings", task_id + ".json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    save_json(path, {"hooks": {"PreToolUse": [
        {"matcher": "Read|Bash",
         "hooks": [{"type": "command", "command": command, "timeout": 10}]}]}})
    return path


def graft_check(root):
    """Raise unless the project has a graft index that matches the code. A worker
    locates code through graft, so a missing index leaves it reading whole files
    and a stale one sends it to the wrong lines."""
    exe = os.environ.get("COLONY_GRAFT", "graft")
    try:
        out = subprocess.run([exe, "check", root], capture_output=True, text=True,
                             timeout=300)
    except (OSError, subprocess.SubprocessError) as e:
        raise ColonyError(f"colony needs graft and cannot run it: {e}. "
                          f"Install graft, then run: graft build {root}")
    if out.returncode != 0:
        first = (out.stderr.strip() or out.stdout.strip()
                 or f"exit {out.returncode}").splitlines()[0]
        raise ColonyError(f"the graft index for {root} is missing or stale: {first}. "
                          f"Run: graft build {root}")


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
    for key in ("banned", "worker_models", "master_model", "subagent_models",
                "explore_budget", "efforts"):
        if key not in data:
            raise ColonyError(f"the colony model config at {path} lacks {key!r}")
    budget = data["explore_budget"]
    if not isinstance(budget, int) or isinstance(budget, bool) or budget < 1:
        raise ColonyError(f"explore_budget in {path} must be a whole number of tool "
                          f"calls, at least 1, not {budget!r}")
    check_subagent_models(data, path)
    check_pricing(data, path)
    check_limit_weights(data, path)
    retrieval_policy(data)
    return data


def limit_weights(cfg, name=None):
    """(name, weights) for a named plan-limit weighting.

    Raw tokens are not a safe stand-in for plan-limit use: cache reads are most
    of the count, and their weight is unpublished. So a weighting is always
    named, never assumed.
    """
    block = cfg.get("limit_weights") or {}
    sets = block.get("sets") or {}
    chosen = name or block.get("default")
    if not sets:
        raise ColonyError("the colony model config has no limit_weights.sets; "
                          "add one, because a raw token count is not a limit count")
    if chosen not in sets:
        raise ColonyError(f"no weighting called {chosen!r}; use one of: "
                          + ", ".join(sorted(sets)))
    return chosen, sets[chosen]


def check_limit_weights(cfg, path):
    """Validate the limit_weights block. Every set names every token category,
    because a missing category would silently weigh zero."""
    block = cfg.get("limit_weights")
    if block is None:
        raise ColonyError(f"the colony model config at {path} lacks 'limit_weights'")
    if not isinstance(block, dict) or not isinstance(block.get("sets"), dict) \
            or not block["sets"]:
        raise ColonyError(f"limit_weights in {path} must hold a non-empty sets object")
    for name, weights in block["sets"].items():
        if not isinstance(weights, dict):
            raise ColonyError(f"the weighting {name!r} in {path} must be an object")
        missing = [k for k in RATE_KEYS if k not in weights]
        if missing:
            raise ColonyError(f"the weighting {name!r} in {path} lacks "
                              + ", ".join(missing))
        for key, value in weights.items():
            if key not in RATE_KEYS:
                raise ColonyError(f"the weighting {name!r} in {path} names {key!r}; "
                                  "use " + ", ".join(RATE_KEYS))
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                raise ColonyError(f"the weight {name!r}.{key} in {path} must be a "
                                  "number, not negative")
    default = block.get("default")
    if default is not None and default not in block["sets"]:
        raise ColonyError(f"limit_weights.default in {path} is {default!r}, "
                          "which names no set")


def weighted_units(usage, weights):
    """The usage in weighted units under one weighting.

    A cache write that carries no ephemeral breakdown is charged at the
    5-minute weight, the cheaper of the two, so an unlabelled write is never
    silently free. Without this the weighted total would fall below the raw
    token count even at weight 1.
    """
    total = sum((usage.get(k) or 0) * weights[k] for k in RATE_KEYS)
    unlabelled = (usage.get("cache_write") or 0) \
        - (usage.get("cache_write_1h") or 0) - (usage.get("cache_write_5m") or 0)
    if unlabelled > 0:
        total += unlabelled * weights["cache_write_5m"]
    return total


def check_subagent_models(cfg, path):
    """Validate the subagent_models block.

    Every value is an alias, not a pinned id, because the Agent tool's model
    parameter takes only `sonnet`, `opus`, `haiku` or `fable`. Measured on
    2026-10-20: a prompt naming `claude-opus-4-8` reached 17 of 221 archived
    reviewers, and the rest rode the newest Opus.
    """
    block = cfg.get("subagent_models")
    if not isinstance(block, dict):
        raise ColonyError(f"subagent_models in {path} must be an object")
    roles = ("default", "reviewer", "explore")
    for key in roles:
        if key not in block:
            raise ColonyError(f"subagent_models in {path} lacks {key!r}")
        if block[key] not in SUBAGENT_ALIASES:
            raise ColonyError(f"subagent_models.{key} in {path} is {block[key]!r}; "
                              "the Agent tool takes an alias, one of "
                              + ", ".join(SUBAGENT_ALIASES))
    extra = sorted(set(block) - set(roles) - {"note"})
    if extra:
        raise ColonyError(f"subagent_models in {path} names " + ", ".join(extra)
                          + "; use " + ", ".join(roles) + " and an optional note")
    return block


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


def _section_lines(text, name):
    """(present, [lines]) of a brief's '## <name>' section, bullets stripped."""
    m = re.search(rf"^#+\s*{name}\s*$(.*?)(?=^#+\s|\Z)", text, re.M | re.S | re.I)
    if not m:
        return False, []
    out = []
    for line in m.group(1).splitlines():
        line = line.strip()
        if line.startswith("- "):
            line = line[2:].strip()
        elif line == "-":
            line = "-"
        if line:
            out.append(line)
    return True, out


def brief_owns(path):
    """The paths a brief's Owns section declares, in order and without
    duplicates. [] means the task changes no file, written as '-'."""
    with open(path, encoding="utf-8") as f:
        present, lines = _section_lines(f.read(), "owns")
    if not present:
        raise ColonyError("the brief has no Owns section. Name the paths the "
                          "worker may change, or - for none")
    if not lines:
        raise ColonyError("the brief's Owns section is empty. Name the paths the "
                          "worker may change, or - for none")
    if lines == ["-"]:
        return []
    paths = [line for line in lines if line != "-"]
    bad = [p for p in paths if p.startswith("/") or ".." in p.split("/")]
    if bad:
        raise ColonyError("an owned path must be relative to the project root "
                          "and must not leave it: " + ", ".join(bad))
    seen, out = set(), []
    for item in paths:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def owner_conflicts(sd, paths):
    """[(path, task id)] where an open task already owns one of these paths.

    Two workers in one tree that change the same file overwrite each other, and
    discipline alone does not prevent it. So an overlap is refused at the start,
    not discovered at the merge.
    """
    clashes = []
    for task in all_tasks(sd):
        if task.get("closed"):
            continue
        owned = set(task.get("owns") or [])
        for path in paths:
            if path in owned or any(shares_tree(path, other) for other in owned):
                clashes.append((path, task["id"]))
    return clashes


def shares_tree(one, other):
    """Whether two declared paths overlap, counting a directory and what is
    under it as the same claim."""
    a = one.rstrip("/") + "/"
    b = other.rstrip("/") + "/"
    return a.startswith(b) or b.startswith(a)


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


def master_last_model(meta):
    """The model of the Master session's most recent response, or None. Reads the
    last, not the most frequent: a session can switch model partway and stay."""
    session = meta.get("master_session")
    if not session:
        return None
    # The Master reads its own transcript, so the current config directory is right.
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


def outside_cwd(cwd, *paths):
    """The given directories that lie outside cwd, each once, in order."""
    root = os.path.realpath(cwd)
    out = []
    for path in paths:
        real = os.path.realpath(path)
        if real == root or real.startswith(root + os.sep) or real in out:
            continue
        out.append(real)
    return out


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


def last_event_kind(sd, task_id):
    """The kind of the last event logged for a task, or None."""
    path = os.path.join(sd, "events.log")
    kind = None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 3 and parts[1] == task_id:
                    kind = parts[2]
    except OSError:
        return None
    return kind


def close_verdict(a):
    """The verdict its flags name, or None. Two flags are an error."""
    named = [v for v in VERDICTS if getattr(a, v, False)]
    if len(named) > 1:
        raise ColonyError("name one verdict, not " + ", ".join("--" + v for v in named))
    return named[0] if named else None


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


def checkpoint_path(sd):
    return os.path.join(sd, "master.md")


def write_checkpoint(sd):
    """Write the Master's checkpoint file if it does not exist yet. Returns its
    path. Shared so colony.py and colony_bg.py checkpoint identically; each
    calls its own note_master_session first."""
    path = checkpoint_path(sd)
    if not os.path.exists(path):
        status = "\n".join("- " + ln.replace("\t", " · ") for ln in status_lines(sd, True)) or "- none yet"
        with open(path, "w", encoding="utf-8") as f:
            f.write(CHECKPOINT_TEMPLATE.format(name=colony_name(), when=now(), status=status))
    return path


def checkpoint_ready(path, prog="colony"):
    """Raise unless the checkpoint at path exists, is fresh and is filled in."""
    if not os.path.exists(path):
        raise ColonyError(f"no checkpoint; run {prog} checkpoint and fill it in first")
    if time.time() - os.path.getmtime(path) > CHECKPOINT_FRESH:
        raise ColonyError(f"checkpoint {path} is older than 15 minutes; update it first")
    with open(path, encoding="utf-8") as f:
        if "<The goal of this colony" in f.read():
            raise ColonyError("checkpoint still holds template placeholders; fill it in")


def projects_dir(config_dir=None):
    """Where transcripts live. A task records the config directory its worker
    inherited, because a Master that reads one cannot measure a worker that
    wrote to another."""
    base = config_dir or os.environ.get("CLAUDE_CONFIG_DIR") \
        or os.path.expanduser("~/.claude")
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


def task_session(task, live=None):
    """A task's worker session: the recorded one, a closed task's recorded one,
    or `live(task)` for an open task that has not recorded one yet.

    A backend that learns a worker's session id at start time (colony_bg) saves
    it immediately, so `live` is never needed. A backend that learns it only
    once a worker is running (colony's herdr panes) passes its own live lookup.
    """
    session = task.get("session")
    if session or task.get("closed"):
        return session
    return live(task) if live else None


def sub_role(description, agent_type):
    """"reviewer" when a sub-agent's description names the review, else
    "other". The role is read from the description because no agent type says
    it; `agent_type` carries values such as general-purpose and Explore."""
    if "review" in (description or "").lower():
        return "reviewer"
    return "other"


def session_usage(session, since=None, config_dir=None):
    """A session's own usage and one entry per sub-agent transcript, or None.

    A sub-agent entry carries its agentType and description from its
    agent-<id>.meta.json, and a role read from that description. Claude Code
    names no agent type "reviewer", but the worker prompt names the review, so
    the description identifies it: it did so in 222 of 253 archived sub-agent
    transcripts on 2026-10-19.

    config_dir is the directory the worker wrote its transcript to, recorded on
    the task. Without it a Master that reads one directory cannot measure a
    worker that wrote to another.
    """
    if not session:
        return None
    main = glob.glob(os.path.join(projects_dir(config_dir), "*", session + ".jsonl"))
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


def report_rows(sd, meta, tasks, live_session=None):
    """One row per worker and per Master session, newest Master last.

    A row is {"label", "kind", "task", "pinned_model", "pinned_effort",
    "minutes", "since", "usage" or None}. usage is None when the session's
    transcript was not found, which the report shows as - rather than 0.

    live_session resolves an open task's session when the backend learns it
    only once the worker is running; omit it when the backend already saved
    the session at start time.
    """
    rows = []
    for task in tasks:
        usage = session_usage(task_session(task, live_session), config_dir=task.get("config_dir"))
        end = parse_ts(task["closed"]) if task.get("closed") else datetime.now(timezone.utc)
        rows.append({"label": task["id"], "kind": "worker", "task": task["id"],
                     "pinned_model": task.get("model"), "pinned_effort": task.get("effort"),
                     "minutes": int((end - parse_ts(task["started"])).total_seconds() // 60),
                     "since": None, "usage": usage, "verdict": task.get("verdict"),
                     "retry_of": task.get("retry_of")})
    since = min(t["started"] for t in tasks)
    sessions = meta.get("master_sessions") or [meta.get("master_session")]
    for i, session in enumerate(s for s in sessions if s):
        usage = session_usage(session, since)
        if not usage:
            continue
        label = "Master" if i == 0 else f"Master after restart {i}"
        rows.append({"label": label, "kind": "master", "task": None,
                     "pinned_model": None, "pinned_effort": None, "minutes": None,
                     "since": since, "usage": usage, "verdict": None, "retry_of": None})
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


def accepted_changes(rows):
    """[(task id, weighted usage dict, [task ids charged])] for each accepted
    task, with the cost of the tasks it retried charged to it.

    A worker's PASS is not the Master's verdict, so only an accepted task
    counts. A retry's predecessor is charged to the same change, because the
    rework is part of what the change cost.
    """
    by_task = {r["task"]: r for r in rows if r["kind"] == "worker"}
    out = []
    for row in rows:
        if row["kind"] != "worker" or row.get("verdict") != "accept":
            continue
        charged, seen = [], set()
        node = row
        while node is not None and node["task"] not in seen:
            seen.add(node["task"])
            charged.append(node["task"])
            node = by_task.get(node.get("retry_of"))
        total = blank_usage()
        for task_id in charged:
            usage = by_task[task_id].get("usage")
            if usage:
                add_usage(total, usage["total"])
        out.append((row["task"], total, charged))
    return out


def cmd_report_from(a, sd, live_session=None):
    """The body of `report`: gather rows and print them. The caller has already
    done any backend-specific setup (such as recording the Master's current
    session) before calling this."""
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
    rows, since = report_rows(sd, meta, tasks, live_session)
    if a.json:
        print(json.dumps(report_json(sd, cfg, meta, rows, since), indent=1, default=str))
        return
    weighting, weights = limit_weights(cfg, getattr(a, "weights", None))
    print(f"weighting: {weighting} "
          f"({', '.join(f'{k} {weights[k]:g}' for k in RATE_KEYS)}). "
          "An assumption, not a published figure.")
    print("task\tmodel\trequests\ttokens\tunits\tverdict\tfirst context\tsub-agents\tminutes")
    total = units = 0
    for row in rows:
        usage = row["usage"]
        if row["kind"] == "worker":
            if usage is None:
                print(f"{row['task']}\t{short_model(row['pinned_model'])}\t-\t-\t-\t"
                      f"{row.get('verdict') or '-'}\t-\t-\t{row['minutes']}")
                continue
            print(f"{row['task']}\t{short_model(top_model(usage['total']))}\t"
                  f"{usage['total']['requests']}\t{fmt_k(usage['total']['tokens'])}\t"
                  f"{fmt_k(weighted_units(usage['total'], weights))}\t"
                  f"{row.get('verdict') or '-'}\t"
                  f"{fmt_k(usage['own']['first_context'])}\t"
                  f"{fmt_k(usage['total']['tokens'] - usage['own']['tokens'])}\t{row['minutes']}")
        else:
            print(f"{row['label']} (since {row['since']})\t{short_model(top_model(usage['total']))}\t"
                  f"{usage['total']['requests']}\t{fmt_k(usage['total']['tokens'])}\t"
                  f"{fmt_k(weighted_units(usage['total'], weights))}\t-\t-\t"
                  f"{fmt_k(usage['total']['tokens'] - usage['own']['tokens'])}\t-")
        total += usage["total"]["tokens"]
        units += weighted_units(usage["total"], weights)
    print(f"total\t\t\t{fmt_k(total)}\t{fmt_k(units)}")
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
    accepted = accepted_changes(rows)
    if accepted:
        print()
        print(f"cost for each accepted change, in {weighting} units")
        print("change\ttasks charged\trequests\ttokens\tunits")
        ch_units = 0
        for task_id, usage, charged in accepted:
            print(f"{task_id}\t{' '.join(charged)}\t{usage['requests']}\t"
                  f"{fmt_k(usage['tokens'])}\t{fmt_k(weighted_units(usage, weights))}")
            ch_units += weighted_units(usage, weights)
        print(f"a change costs on average\t{len(accepted)} accepted\t\t\t"
              f"{fmt_k(ch_units / len(accepted))}")
    else:
        print()
        print("no accepted change yet: colony close --accept records one")
    subs = [(row, sub) for row in rows if row["usage"] for sub in row["usage"]["subs"]]
    if subs:
        print()
        print("sub-agents")
        print("row\trole\tagent type\trequests\ttokens\tdescription")
        role_total = {}
        for row, sub in subs:
            role = sub_role(sub.get("description"), sub.get("agent_type"))
            role_total[role] = role_total.get(role, 0) + sub["tokens"]
            print(f"{row['label']}\t{role}\t{sub['agent_type'] or '-'}\t{sub['requests']}\t"
                  f"{fmt_k(sub['tokens'])}\t{(sub['description'] or '-')[:60]}")
        print("by role\t" + "\t".join(f"{r} {fmt_k(v)}" for r, v in sorted(role_total.items())))
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


def main(argv=None):
    """Only `retrieval-hook`: a worker's settings file names this file's own
    path (see write_worker_settings), so it must run standalone."""
    p = argparse.ArgumentParser(prog="colony_core")
    sub = p.add_subparsers(dest="cmd", required=True)
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
