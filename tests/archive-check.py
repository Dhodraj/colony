#!/usr/bin/env python3
"""Reconcile colony report's token counts against every archived session.

tests/colony.sh proves the script's logic against crafted transcripts. This
proves the same parser against real ones: every worker and Master session
recorded under COLONY_HOME, and each session's sub-agent transcripts.

It counts each session a second way. colony.py keeps the largest output and
thinking of a streamed response; this keeps the last copy, and separately
checks that no copy of a response ever reports less output or thinking than
the copy before it. The last copy is therefore the largest one, and a
difference between the two counts means one of them is wrong. It also checks
three identities: tokens is input plus both cache parts plus output, the
1-hour and 5-minute cache writes add up to the cache-write total, and thinking
never exceeds output.

It reads only. Run it where the archive is:

    CLAUDE_CONFIG_DIR=~/.claude python3 tests/archive-check.py

Exit codes: 0 every session reconciles, 1 a count differs, 2 no session found.
"""

import collections
import glob
import importlib.util
import json
import os
import sys

FIELDS = ("requests", "tokens", "input", "output", "cache_read", "cache_write",
          "cache_write_1h", "cache_write_5m", "thinking", "thinking_unknown")


def load_colony():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "colony.py")
    spec = importlib.util.spec_from_file_location("colony", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def recount(path, since=None):
    """Totals of one transcript, keeping the last copy of each response.

    backwards counts responses whose later copy reported less output or
    thinking than an earlier one. While that stays 0, the last copy is also
    the largest, so this count can be compared with colony.py's.
    """
    last = {}
    order = []
    backwards = 0
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
            if key not in last:
                order.append(key)
            details = usage.get("output_tokens_details")
            think = None
            if isinstance(details, dict) and "thinking_tokens" in details:
                think = details["thinking_tokens"]
            elif key in last:
                # An early copy of a response can arrive without the field.
                think = last[key]["thinking"]
            before = last.get(key)
            if before:
                if usage.get("output_tokens", 0) < before["output"]:
                    backwards += 1
                if think is not None and before["thinking"] is not None \
                        and think < before["thinking"]:
                    backwards += 1
            creation = usage.get("cache_creation") or {}
            last[key] = {
                "input": usage.get("input_tokens", 0),
                "cache_read": usage.get("cache_read_input_tokens", 0),
                "cache_write": usage.get("cache_creation_input_tokens", 0),
                "cache_write_1h": creation.get("ephemeral_1h_input_tokens", 0),
                "cache_write_5m": creation.get("ephemeral_5m_input_tokens", 0),
                "output": usage.get("output_tokens", 0),
                "thinking": think,
            }
    totals = collections.Counter()
    for key in order:
        response = last[key]
        totals["requests"] += 1
        totals["tokens"] += (response["input"] + response["cache_read"]
                             + response["cache_write"] + response["output"])
        for field in ("input", "cache_read", "cache_write", "cache_write_1h",
                      "cache_write_5m", "output"):
            totals[field] += response[field]
        if response["thinking"] is None:
            totals["thinking_unknown"] += 1
        else:
            totals["thinking"] += response["thinking"]
    totals["backwards"] = backwards
    return totals


def sessions(home):
    """(role, label, session id) for every session the colonies recorded."""
    found = []
    for path in sorted(glob.glob(os.path.join(home, "*", "*", "tasks", "*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                task = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if task.get("session"):
            found.append(("worker", task.get("id", "?"), task["session"]))
    for path in sorted(glob.glob(os.path.join(home, "*", "*", "colony.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        name = meta.get("name") or "?"
        for i, session in enumerate(meta.get("master_sessions")
                                    or [meta.get("master_session")]):
            if session:
                found.append(("master", f"{name} Master {i}", session))
    return found


def main():
    colony = load_colony()
    home = os.environ.get("COLONY_HOME") or os.path.expanduser(
        "~/.local/state/claude-system/colony")
    targets = sessions(home)
    if not targets:
        print(f"no session recorded under {home}", file=sys.stderr)
        return 2
    reconciled = absent = differences = 0
    subs = collections.Counter()
    variants = collections.Counter()
    for role, label, session in targets:
        usage = colony.session_usage(session)
        if usage is None:
            absent += 1
            continue
        reconciled += 1
        mine = collections.Counter()
        paths = [usage["transcript"]] + sorted(glob.glob(os.path.join(
            usage["transcript"][:-len(".jsonl")], "subagents", "*.jsonl")))
        for path in paths:
            mine.update(recount(path))
        theirs = usage["total"]
        if mine["backwards"]:
            differences += 1
            print(f"differs: {label} has {mine['backwards']} response copy(ies) "
                  "reporting less output or thinking than an earlier copy, so "
                  "the last copy is not the largest and this check cannot "
                  "compare the two counts")
        for field in FIELDS:
            if mine[field] != theirs[field]:
                differences += 1
                print(f"differs: {role} {label} {session[:8]} {field}: "
                      f"recount {mine[field]}, report {theirs[field]}")
        parts = (theirs["input"] + theirs["cache_read"] + theirs["cache_write"]
                 + theirs["output"])
        if parts != theirs["tokens"]:
            differences += 1
            print(f"differs: {label} tokens {theirs['tokens']} is not the sum {parts}")
        if theirs["cache_write_1h"] + theirs["cache_write_5m"] != theirs["cache_write"]:
            differences += 1
            print(f"differs: {label} cache writes do not add up to the total")
        if theirs["thinking"] > theirs["output"]:
            differences += 1
            print(f"differs: {label} thinking {theirs['thinking']} is above "
                  f"output {theirs['output']}")
        subs[role] += len(usage["subs"])
        for sub in usage["subs"]:
            variants["sub-agent with meta" if sub["agent_type"]
                     else "sub-agent without meta"] += 1
        if theirs["thinking_unknown"]:
            variants["session with partial thinking"] += 1
        if any(model.startswith("<") for model in theirs["by_model"]):
            variants["session with a <synthetic> response"] += 1
        if len(theirs["by_model"]) > 1:
            variants["session served by more than one model"] += 1
    print(f"sessions reconciled {reconciled}, without a transcript {absent}, "
          f"counts that differ {differences}")
    print("sub-agent transcripts:", dict(subs) or "none")
    print("variants met:", dict(variants) or "none")
    if absent:
        print(f"note: {absent} session(s) have no transcript under "
              f"{colony.projects_dir()}; their usage is unavailable, not zero")
    return 1 if differences else 0


if __name__ == "__main__":
    sys.exit(main())
