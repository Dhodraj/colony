#!/usr/bin/env bash
# Tests for colony_bg.py against a fake claude. Run: bash tests/colony_bg.sh
# The fake proves the script's own logic only; it does not prove claude --bg
# behaves the same. See docs/colony.md for what was verified against the real
# claude CLI before this was written (claude --bg prints a backgrounded id,
# claude agents --json --all reports idle/busy, a running --bg session must be
# stopped before --resume continues it in place rather than forking a copy).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
COLONY="$HERE/../colony_bg.py"
# Not under /tmp or /var/folders: colonybg done refuses result files in scratch folders.
mkdir -p "$HOME/.cache"
T="$(mktemp -d "$HOME/.cache/colonybg-tests.XXXXXX")"
trap '[[ -n "$T" && -d "$T" ]] && rm -rf "$T"' EXIT

# The fake keeps its sessions in $FAKE_STATE and copies the real CLI's rules,
# each verified live against claude 2.1.319:
# - stop, logs and attach take only the short id; a full session id gets
#   "No job matching";
# - a running session has a pid and a status; a stopped or exited one keeps its
#   row with no pid or status, and a state of stopped or done;
# - --resume of a session that is still running starts a copy and says so;
# - --bg runs in the folder it is started from, and refuses an untrusted one.
cat > "$T/claude" <<'FAKE'
#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(" ".join(args) + "\n")
state_path = os.environ["FAKE_STATE"]
try:
    rows = json.load(open(state_path))
except (OSError, ValueError):
    rows = []
def save():
    json.dump(rows, open(state_path, "w"))
def fail(msg):
    print(msg, file=sys.stderr); sys.exit(1)
def by_id(i):
    return next((r for r in rows if r["id"] == i), None)
def new_row(i, cwd, name):
    n = len(rows) + 1
    row = {"id": i, "pid": 1000 + n, "sessionId": f"{i}-c937-45b3-af13-{n:012d}",
           "status": "idle", "state": "working", "kind": "background",
           "cwd": cwd, "name": name}
    rows.append(row)
    return row
def no_job(x):
    fail(f"No job matching '{x}'. Run 'claude agents' to list running sessions.")
cmd = args[0] if args else ""
if cmd == "--bg":
    if os.environ.get("FAKE_BG_FAIL") == "1":
        fail("claude --bg exploded")
    if os.environ.get("FAKE_UNTRUSTED") == "1":
        fail(f"Workspace not trusted. Run `claude` in {os.getcwd()} once and accept the trust prompt, then retry.")
    with open(os.environ["FAKE_LOG"] + ".cwd", "a") as f:
        f.write(os.getcwd() + "\n")
    i = os.environ.get("FAKE_BG_ID") or f"bg{len(rows) + 6:06x}"
    name = args[args.index("-n") + 1] if "-n" in args else ""
    new_row(i, os.getcwd(), name)
    save()
    print("Starting background service…")
    print(f"backgrounded · {i}")
elif cmd == "agents":
    print(os.environ.get("FAKE_AGENTS_JSON") or json.dumps(rows))
elif cmd == "--resume":
    if os.environ.get("FAKE_RESUME_FAIL") == "1":
        fail("resume failed")
    sid, prompt = args[1], args[2]
    row = next((r for r in rows if r["sessionId"] == sid), None)
    if row and row.get("pid"):
        copy = new_row(f"cp{len(rows):06x}", os.getcwd(), row["name"])
        save()
        print(f"note: session {row['id']} is already running in the background, "
              f"so this started a copy as {copy['id']}.")
        print(f"backgrounded · {copy['id']}")
        sys.exit(0)
    if row is None:
        row = new_row(sid[:8], os.getcwd(), "")
    row.update(pid=2000 + len(rows), status="idle", state="working")
    if prompt == "/clear":
        row["sessionId"] = row["id"] + "-c1ea-0000-0000-000000000000"
    save()
    print(f"backgrounded · {row['id']}")
elif cmd == "stop":
    if os.environ.get("FAKE_STOP_FAIL") == "1":
        fail("stop exploded")
    row = by_id(args[1])
    if row is None or os.environ.get("FAKE_STOP_GONE") == "1":
        no_job(args[1])
    if os.environ.get("FAKE_STOP_IGNORED") != "1":
        row.pop("pid", None); row.pop("status", None); row["state"] = "stopped"
        save()
    print(f"stopped {args[1]}")
elif cmd in ("logs", "attach"):
    if by_id(args[1]) is None:
        no_job(args[1])
    print(f"fake log output for {args[1]}" if cmd == "logs" else f"attached to {args[1]}")
elif cmd == "rm":
    print(f"removed {args[1]}")
else:
    fail(f"unknown claude invocation: {' '.join(args)}")
FAKE
chmod +x "$T/claude"

cat > "$T/graft" <<'FAKE'
#!/usr/bin/env bash
echo "graft $*" >> "$FAKE_LOG"
if [[ "${FAKE_GRAFT_STALE:-0}" = 1 && "$1" = "check" ]]; then
  echo "graft/ is stale: 3 files changed" >&2; exit 1
fi
echo '{"ok":true}'
FAKE
chmod +x "$T/graft"

export COLONYBG_CLAUDE="$T/claude" COLONY_GRAFT="$T/graft" COLONY_HOME="$T/state" FAKE_LOG="$T/calls.log" FAKE_STATE="$T/sessions.json"
export COLONY_CHECK_EVERY=0.2 COLONY_SETTLE=0.1 COLONYBG_STOP_WAIT=0.5
export CLAUDE_CONFIG_DIR="$T/claude-config" CLAUDE_CODE_SESSION_ID="master-sess-1"
# COLONY_ROOT pins project_root() ahead of any cd, so a shell that still has
# it exported from a real run (a worker's own environment carries it) would
# make every start below resolve to the wrong project silently.
unset COLONY_NAME COLONY_ROOT
mkdir -p "$T/proj" "$T/results" "$T/claude-config/projects/x/sess-123/subagents" "$T/norepo"
git init -q "$T/proj" 2>/dev/null || git -C "$T/proj" init -q
printf 'fixture\n' > "$T/proj/seed.txt"
git -C "$T/proj" -c user.name=colony-tests -c user.email=tests@example.invalid \
    -c commit.gpgsign=false add seed.txt >/dev/null 2>&1
git -C "$T/proj" -c user.name=colony-tests -c user.email=tests@example.invalid \
    -c commit.gpgsign=false commit -qm "fixture" >/dev/null 2>&1
cd "$T/proj"
SLUG="$(python3 -c 'import re,os;print(re.sub(r"[^A-Za-z0-9]","-",os.getcwd()))')"
SD="$COLONY_HOME/$SLUG/main"
echo "result" > "$T/results/r.md"
brief() { # id -> a filled-in brief at ./<id>.md
  printf '# Task %s\n\n## Goal\n\nDo it.\n\n## Inputs\n\n- a.md\n\n## Acceptance criteria\n\n1. It is done.\n\n## Owns\n\n- %s.txt\n\n## You may decide\n\n- wording\n\n## Model\n\nclaude-opus-4-8\n\n## Effort\n\nhigh\n\n## Result\n\nWrite to: %s\n' "$1" "$1" "$T/results/r.md" > "$1.md"
}

pass=0 fail=0
check() { # name, expected exit, actual exit, [grep pattern, text]
  local ok=1
  [[ "$2" = "$3" ]] || ok=0
  if [[ $# -ge 5 ]] && ! grep -q -- "$4" <<<"$5"; then ok=0; fi
  if [[ $ok = 1 ]]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL: $1 (exit $3, want $2)"; echo "$5" | head -5; fi
}
absent() { # name, pattern, text
  if grep -q -- "$2" <<<"$3"; then fail=$((fail+1)); echo "FAIL: $1 (found $2)"; else pass=$((pass+1)); fi
}
run() { rc=0; out="$(python3 "$COLONY" "$@" 2>&1)" || rc=$?; }
agents_row() { # id, status -> one live row, as claude agents --json --all prints it
  printf '[{"id":"%s","pid":4242,"sessionId":"%s-c937-45b3-af13-000000000000","status":"%s","state":"working","kind":"background","name":"w"}]' "$1" "$1" "$2"
}
ended_row() { # id, state -> one row for a session whose process has exited
  printf '[{"id":"%s","sessionId":"%s-c937-45b3-af13-000000000000","state":"%s","kind":"background","name":"w"}]' "$1" "$1" "$2"
}
task_field() { # id, field [, colony] -> one field of a task file
  python3 -c "import json,sys;print(json.load(open(sys.argv[1]))[sys.argv[2]])" "$COLONY_HOME/$SLUG/${3:-main}/tasks/$1.json" "$2"
}

# Start. FAKE_BG_ID pins the id the fake prints, so assertions below name it
# directly instead of predicting a counter-based one.
brief T1
FAKE_BG_ID=bg-t1 run start T1 T1.md
check "start" 0 $rc "started T1: session bg-t1" "$out"
check "the task keeps the short id" 0 0 "bg-t1" "$(task_field T1 agent)"
check "the task keeps the full session id" 0 0 "bg-t1-c937" "$(task_field T1 session)"
check "master session saved" 0 0 '"master_session": "master-sess-1"' "$(cat "$SD/colony.json")"
check "bg flags" 0 0 "--model claude-opus-4-8 --effort high -n proj T1" "$(grep -- '^--bg' "$FAKE_LOG")"
check "state dir named for the worker" 0 0 "--add-dir $SD" "$(grep -- '^--bg' "$FAKE_LOG")"
run start T1 T1.md;                             check "duplicate id" 2 $rc "exists already" "$out"
brief T2
FAKE_BG_FAIL=1 run start T2 T2.md;              check "start fails" 1 $rc "claude --bg exploded" "$out"
absent "a failed start does not print the prompt" "Read the brief" "$out"
check "failed start logged" 0 0 "START-FAILED" "$(cat "$SD/events.log")"
absent "a failed start leaves no task" "T2" "$(ls "$SD/tasks")"
brief U1
FAKE_UNTRUSTED=1 run start U1 U1.md;            check "an untrusted folder stops the start" 1 $rc "Workspace not trusted" "$out"
absent "the untrusted error does not print the prompt" "Read the brief" "$out"
brief U2
FAKE_UNTRUSTED=1 run start U2 U2.md --worktree; check "an untrusted worktree names the repository to trust" 1 $rc "run claude once in $T/proj" "$out"
absent "a failed worktree start leaves no worktree" "U2" "$(git worktree list)"

# A worker started with --worktree runs inside its worktree, not the Master's folder.
brief W1
FAKE_BG_ID=bg-w1 run start W1 W1.md --worktree; check "start in a worktree" 0 $rc "started W1" "$out"
check "the worker runs in its worktree" 0 0 "$(task_field W1 cwd)" "$(tail -1 "$FAKE_LOG.cwd")"
check "the worktree is not the Master's folder" 0 0 "worktrees" "$(tail -1 "$FAKE_LOG.cwd")"

# A missing or stale graft index stops a start, same as colony.
brief GR1
FAKE_GRAFT_STALE=1 run start GR1 GR1.md;        check "a stale index stops the start" 2 $rc "missing or stale" "$out"
COLONY_GRAFT="$T/no-such-graft" run start GR1 GR1.md
check "a missing graft stops the start" 2 $rc "cannot run it" "$out"
absent "the graft error does not name herdr" "herdr" "$out"

# Done, wait, answer
run done T1 PASS "$T/results/r.md" "criteria 1-1 pass; confirm: a.md:3"
check "done" 0 $rc "recorded T1 PASS" "$out"
run wait --timeout 5;                           check "wait reports outcome" 0 $rc "T1	PASS	$T/results/r.md	criteria 1-1 pass" "$out"

brief T3
FAKE_BG_ID=bg-t3 run start T3 T3.md
run answer T3 "keep it";                        check "answer" 0 $rc "answered T3" "$out"
check "answer stops the worker by its short id" 0 0 "^stop bg-t3$" "$(cat "$FAKE_LOG")"
check "answer resumes the stopped session" 0 0 "resume bg-t3-c937-45b3-af13-[0-9]* Answer from the Master on T3: keep it --bg" "$(tail -1 "$FAKE_LOG")"
absent "answer starts no copy of the worker" '"cp0' "$(cat "$FAKE_STATE")"
check "answer logged" 0 0 "T3	ANSWERED" "$(cat "$SD/events.log")"
FAKE_STOP_IGNORED=1 run answer T3 "again";     check "answer refuses a worker it cannot stop" 1 $rc "could not stop" "$out"
absent "a refused answer starts no copy" '"cp0' "$(cat "$FAKE_STATE")"

# Wait: a worker claude no longer lists is LOST, and so is one whose session
# has exited; a worker it lists idle is IDLE. A colony of its own, so a sweep's
# listing does not also catch T3, still open with an ANSWERED event, in main.
WSD="$COLONY_HOME/$SLUG/bgwait"
COLONY_NAME=bgwait brief T4
FAKE_BG_ID=bg-t4 COLONY_NAME=bgwait run start T4 T4.md
FAKE_AGENTS_JSON='[]' COLONY_NAME=bgwait run wait --timeout 2
check "wait finds lost worker" 0 $rc "T4	LOST" "$out"
COLONY_NAME=bgwait brief T5
FAKE_BG_ID=bg-t5 COLONY_NAME=bgwait run start T5 T5.md
FAKE_AGENTS_JSON="$(agents_row bg-t5 idle)" COLONY_NAME=bgwait run wait --timeout 3
check "wait finds idle worker" 0 $rc "T5	IDLE" "$out"
check "the idle status is named" 0 0 "worker status 'idle'" "$(cat "$WSD/events.log")"
COLONY_NAME=bgwait brief T6
FAKE_BG_ID=bg-t6 COLONY_NAME=bgwait run start T6 T6.md
FAKE_AGENTS_JSON="$(agents_row bg-t6 busy)" COLONY_NAME=bgwait run wait --timeout 2
check "a busy worker is not idle" 0 $rc "no new outcomes" "$out"
absent "no IDLE while the worker is busy" "T6	IDLE" "$(cat "$WSD/events.log")"
COLONY_NAME=bgwait brief T8
FAKE_BG_ID=bg-t8 COLONY_NAME=bgwait run start T8 T8.md
FAKE_AGENTS_JSON="$(ended_row bg-t8 done)" COLONY_NAME=bgwait run wait --timeout 3
check "wait finds a worker whose session exited" 0 $rc "T8	LOST" "$out"
check "the exit is named" 0 0 "session ended (state done)" "$out"
COLONY_NAME=bgwait brief T9
FAKE_BG_ID=bg-t9 COLONY_NAME=bgwait run start T9 T9.md
mkdir -p "$CLAUDE_CONFIG_DIR/projects/x"
ERR='{"type":"user","message":{"content":[{"type":"tool_result","is_error":true}]}}'
printf '%s\n' "$ERR" "$ERR" "$ERR" > "$CLAUDE_CONFIG_DIR/projects/x/$(task_field T9 session bgwait).jsonl"
FAKE_AGENTS_JSON="$(agents_row bg-t9 busy)" COLONY_NAME=bgwait run wait --timeout 2
check "a busy worker whose tool calls keep failing is FAILING" 0 $rc "T9	FAILING" "$out"

# Close and status
run close T1;                                   check "close" 0 $rc "closed T1; session" "$out"
check "close stops the worker by its short id" 0 0 "^stop bg-t1$" "$(cat "$FAKE_LOG")"
run close T1;                                   check "close twice" 0 $rc "was closed" "$out"
run answer T1 "late";                           check "answer closed task" 2 $rc "is closed" "$out"
run status;                                     check "status hides closed" 0 $rc "T3	ANSWERED" "$out"
absent "status hides T1" "^T1	" "$out"
run status --all;                               check "status --all" 0 $rc "T1	CLOSED" "$out"
# A worker that finished and already exited on its own: claude stop says "No
# job matching" and claude agents shows no live process. Not a failure.
brief T7
FAKE_BG_ID=bg-t7 run start T7 T7.md
FAKE_STOP_GONE=1 FAKE_AGENTS_JSON="$(ended_row bg-t7 done)" run close T7
check "a worker already gone closes without --force" 0 $rc "closed T7" "$out"
# A worker claude stop did not stop keeps running, so close must not say closed.
brief T9
FAKE_BG_ID=bg-t9 run start T9 T9.md
FAKE_STOP_IGNORED=1 run close T9;               check "close refuses a worker still running" 2 $rc "could not stop" "$out"
FAKE_STOP_FAIL=1 COLONY_NAME=bgwait run close T4;         check "close refuses an unstoppable worker" 2 $rc "could not stop" "$out"
FAKE_STOP_FAIL=1 COLONY_NAME=bgwait run close T4 --force; check "close --force closes anyway" 0 $rc "closed T4" "$out"
run close W1;                                   check "close a worktree task" 0 $rc "worktree removed" "$out"

# A task belongs to the tool that started it: close, answer, logs and attach
# need that tool's own backend, so the other tool refuses with a clear message.
python3 -c "
import json
json.dump({'id': 'X1', 'pane': 'w9:p1', 'brief': 'X1.md', 'closed': None, 'session': None},
          open('$SD/tasks/X1.json', 'w'))"
run close X1;                                   check "colonybg refuses a colony task" 2 $rc "started by colony" "$out"
run answer X1 "hi";                             check "colonybg answer refuses a colony task" 2 $rc "started by colony" "$out"
rc=0; out="$(HERDR_ENV=1 COLONY_HERDR=false python3 "$HERE/../colony.py" close T3 2>&1)" || rc=$?
check "colony refuses a colonybg task" 2 $rc "started by colonybg" "$out"
rm "$SD/tasks/X1.json"

# Report: a session's own transcript and a sub-agent's, same shape as colony's.
# The Master's own session needs a transcript too, or its row has no usage.
u='"model":"claude-opus-5-5","usage":{"input_tokens":10,"cache_read_input_tokens":30000,"cache_creation_input_tokens":5000,"output_tokens":200}'
echo "{\"timestamp\":\"2099-01-01T00:00:00Z\",\"requestId\":\"r0\",\"message\":{\"id\":\"m0\",$u}}" > "$T/claude-config/projects/x/master-sess-1.jsonl"
echo "{\"timestamp\":\"2099-01-01T00:00:00Z\",\"requestId\":\"r1\",\"message\":{\"id\":\"m1\",$u}}" > "$T/claude-config/projects/x/sess-123.jsonl"
mv "$SD/tasks/T1.json" "$T/t1.json.bak"
python3 -c "
import json
t = json.load(open('$T/t1.json.bak'))
t['session'] = 'sess-123'
json.dump(t, open('$SD/tasks/T1.json', 'w'))"
run report;                                     check "report" 0 $rc "T1	opus-5-5	1" "$out"
check "report master" 0 0 "Master (since" "$out"

# Checkpoint and restart: a session cannot stop itself, so restart hands off
# to a request file a colonybg watchdog acts on. The watchdog stops the Master
# and starts a new background Master from the checkpoint, which is how a
# colonybg Master's context is cleared.
run restart;                                    check "restart needs --confirm" 2 $rc "--confirm" "$out"
run restart --confirm;                          check "restart needs checkpoint" 2 $rc "no checkpoint" "$out"
run checkpoint;                                 check "checkpoint" 0 $rc "$SD/master.md" "$out"
run restart --confirm;                          check "restart refuses template" 2 $rc "placeholders" "$out"
sed -i '' 's/<The goal of this colony and the remaining steps.>/Finish T3./' "$SD/master.md"
touch "$SD/master.md"
FAKE_AGENTS_JSON='[{"id":"master01","pid":5,"sessionId":"master-sess-1","status":"busy","kind":"interactive","name":"Master"}]' run restart --confirm
check "restart refuses an interactive Master" 2 $rc "claude --bg" "$out"
[[ ! -f "$SD/restart-request.json" ]] && pass=$((pass+1)) || { fail=$((fail+1)); echo "FAIL: a refused restart leaves no request"; }
python3 -c "
import json
rows = json.load(open('$FAKE_STATE'))
rows.append({'id': 'master01', 'pid': 4243, 'sessionId': 'master-sess-1', 'status': 'busy',
             'state': 'working', 'kind': 'background', 'cwd': '$T/proj', 'name': 'proj Master'})
json.dump(rows, open('$FAKE_STATE', 'w'))"
run restart --confirm;                          check "restart requests a watchdog" 0 $rc "restart requested" "$out"
check "the request is logged" 0 0 "RESTART-REQUESTED" "$(cat "$SD/events.log")"
[[ -f "$SD/restart-request.json" ]] && pass=$((pass+1)) || { fail=$((fail+1)); echo "FAIL: the restart request file exists"; }
run watchdog --once;                            check "watchdog --once acts on it" 0 $rc "restart handled" "$out"
check "the watchdog stops the Master by its short id" 0 0 "^stop master01$" "$(cat "$FAKE_LOG")"
check "the watchdog starts a new Master" 0 0 "^--bg You are the Master of colony main" "$(cat "$FAKE_LOG")"
check "the new Master is told to use colonybg" 0 0 "run colonybg status, start a background colonybg wait" "$(cat "$FAKE_LOG")"
check "the new Master starts in the old one's folder" 0 0 "$T/proj" "$(tail -1 "$FAKE_LOG.cwd")"
absent "the watchdog starts no copy of the old Master" '"cp0' "$(cat "$FAKE_STATE")"
NEW_MASTER="$(python3 -c "import json;print(json.load(open('$SD/colony.json'))['master_session'])")"
absent "the new Master's session is recorded" "^master-sess-1$" "$NEW_MASTER"
[[ ! -f "$SD/restart-request.json" ]] && pass=$((pass+1)) || { fail=$((fail+1)); echo "FAIL: the request file was removed"; }
run watchdog --once;                            check "a second --once finds nothing" 0 $rc "no restart requested" "$out"
# A Master that claude stop did not stop must not be reported as restarted.
CLAUDE_CODE_SESSION_ID="$NEW_MASTER" run restart --confirm
check "the new Master can restart too" 0 $rc "restart requested" "$out"
FAKE_STOP_IGNORED=1 run watchdog --once;        check "the watchdog refuses a Master it cannot stop" 1 $rc "could not stop the Master" "$out"
[[ -f "$SD/restart-request.json" ]] && pass=$((pass+1)) || { fail=$((fail+1)); echo "FAIL: a failed restart keeps its request"; }

# Logs and attach: thin wrappers, no colony.py analogue. Each takes the
# colony task id and resolves it to the short id claude itself takes.
COLONY_NAME=bgwait run logs T5;                 check "logs" 0 $rc "fake log output for bg-t5" "$out"
COLONY_NAME=bgwait run attach T5;               check "attach" 0 $rc "attached to bg-t5" "$out"

# A colony outside a Git repository would key its state on the working
# directory, so colonybg start refuses, same as colony.
brief NR1
cp NR1.md "$T/norepo/NR1.md"
rc=0
( cd "$T/norepo" && python3 "$COLONY" start NR1 NR1.md >"$T/norepo.out" 2>&1 ) || rc=$?
check "start outside a repository" 2 $rc "not a Git repository" "$(cat "$T/norepo.out")"

echo "colonybg tests: $pass passed, $fail failed"
[[ $fail = 0 ]]
