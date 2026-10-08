#!/usr/bin/env bash
# Tests for colony.py against a fake herdr. Run: bash bin/tests/colony.sh
# The fake proves the script's own logic only; it does not prove Herdr behaves the same.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
COLONY="$HERE/../colony.py"
# Not under /tmp or /var/folders: colony done refuses result files in scratch folders.
mkdir -p "$HOME/.cache"
T="$(mktemp -d "$HOME/.cache/colony-tests.XXXXXX")"
trap '[[ -n "$T" && -d "$T" ]] && rm -rf "$T"' EXIT

cat > "$T/herdr" <<'FAKE'
#!/usr/bin/env bash
echo "$*" >> "$FAKE_LOG"
case "$1 $2" in
  "pane split") echo "{\"result\":{\"pane\":{\"pane_id\":\"w9:p$(( $(grep -c 'pane split' "$FAKE_LOG") + 6 ))\"}}}" ;;
  "agent start")
    if [[ "${FAKE_START_FAIL:-0}" = 1 ]]; then
      echo '{"error":{"code":"agent_not_ready","message":"agent not ready"}}'; exit 1
    fi
    echo '{"result":{}}' ;;
  "agent prompt") echo '{"result":{}}' ;;
  "agent get")
    if [[ "${FAKE_AGENT_GONE:-0}" = 1 ]]; then
      echo "{\"error\":{\"code\":\"agent_not_found\",\"message\":\"agent target $3 not found\"}}"; exit 1
    fi
    echo "{\"result\":{\"agent\":{\"agent_status\":\"${FAKE_STATUS:-working}\"}}}" ;;
  "pane get") echo "{\"result\":{\"pane\":{\"agent_status\":\"${FAKE_STATUS:-idle}\",\"agent_session\":{\"value\":\"${FAKE_SESSION:-sess-123}\"}}}}" ;;
  "pane close") echo '{"result":{}}' ;;
  *) echo '{"error":{"message":"unknown"}}'; exit 1 ;;
esac
FAKE
chmod +x "$T/herdr"

export COLONY_HERDR="$T/herdr" COLONY_HOME="$T/state" FAKE_LOG="$T/calls.log"
export COLONY_CHECK_EVERY=0.2 COLONY_SETTLE=0.1 HERDR_ENV=1 HERDR_PANE_ID=w9:p1
export CLAUDE_CONFIG_DIR="$T/claude"
unset COLONY_NAME CLAUDE_CODE_AUTO_COMPACT_WINDOW
mkdir -p "$T/proj" "$T/results" "$T/claude/projects/x/sess-123/subagents"
# Every command below runs in the fake project folder.
cd "$T/proj"
SLUG="$(python3 -c 'import re,os;print(re.sub(r"[^A-Za-z0-9]","-",os.getcwd()))')"
SD="$COLONY_HOME/$SLUG/main"
echo "result" > "$T/results/r.md"
brief() { # id -> a filled-in brief at ./<id>.md (model/effort default to a valid pair)
  printf '# Task %s\n\n## Goal\n\nDo it.\n\n## Inputs\n\n- a.md\n\n## Acceptance criteria\n\n1. It is done.\n\n## You may decide\n\n- wording\n\n## Model\n\nclaude-opus-4-8\n\n## Effort\n\nhigh\n\n## Result\n\nWrite to: %s\n' "$1" "$T/results/r.md" > "$1.md"
}
model_effort_brief() { # id, model, effort -> self-contained brief at ./<id>.md
  printf '# Task %s\n\n## Goal\n\nDo it.\n\n## Inputs\n\n- a.md\n\n## Acceptance criteria\n\n1. It is done.\n\n## You may decide\n\n- wording\n\n## Model\n\n%s\n\n## Effort\n\n%s\n\n## Result\n\nWrite to: %s\n' "$1" "$2" "$3" "$T/results/r.md" > "$1.md"
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

# Briefs
run brief T1;                                 check "brief template" 0 $rc "$SD/briefs/T1.md" "$out"
check "template has sections" 0 0 "## You may decide" "$(cat "$SD/briefs/T1.md")"
check "template result path" 0 0 "Write to: $SD/results/T1.md" "$(cat "$SD/briefs/T1.md")"
run brief T1;                                 check "brief exists" 2 $rc "exists already" "$out"
run start T1 "$SD/briefs/T1.md";              check "placeholders refused" 2 $rc "placeholders" "$out"
echo "Just do it." > plain.md
run start T1 plain.md;                        check "missing sections" 2 $rc "lacks sections: goal" "$out"
brief T1; brief T2; brief T3; brief T4; brief T5

# Start
HERDR_ENV=0 run start T1 T1.md;               check "start outside Herdr" 2 $rc "Herdr" "$out"
run start "bad id!" T1.md;                    check "bad id" 2 $rc "bad task id" "$out"
run start T1 missing.md;                      check "missing brief" 2 $rc "no brief" "$out"
run start T1 T1.md;                           check "start" 0 $rc "started T1: pane w9:p7, agent w-t1" "$out"
check "warns without the Master window" 0 0 "CLAUDE_CODE_AUTO_COMPACT_WINDOW=600000" "$out"
check "first worker beside Master" 0 0 "pane split --current --direction right" "$(grep 'pane split' "$FAKE_LOG" | head -1)"
check "colony name passed" 0 0 "--env COLONY_NAME=main" "$(grep 'pane split' "$FAKE_LOG" | head -1)"
check "worker flags" 0 0 "-- --name proj T1 --exclude-dynamic-system-prompt-sections" "$(grep 'agent start' "$FAKE_LOG")"
check "prompt names brief" 0 0 "Read the brief at $T/proj/T1.md" "$(grep 'agent prompt w-t1' "$FAKE_LOG")"
check "prompt asks for a check" 0 0 "check the result against each criterion" "$(grep 'agent prompt w-t1' "$FAKE_LOG")"
check "master session saved" 0 0 '"master_session": "sess-123"' "$(cat "$SD/colony.json")"
run start T1 T1.md;                           check "duplicate id" 2 $rc "exists already" "$out"
FAKE_START_FAIL=1 run start T2 T2.md;         check "start fails" 1 $rc "agent not ready" "$out"
check "failed start closes pane" 0 0 "pane close w9:p8" "$(tail -1 "$FAKE_LOG")"
check "failed start logged" 0 0 "START-FAILED" "$(cat "$SD/events.log")"
CLAUDE_CODE_AUTO_COMPACT_WINDOW=600000 run start T3 T3.md --model claude-sonnet-5; check "start with model" 0 $rc "started T3" "$out"
absent "no warning with the Master window" "AUTO_COMPACT" "$out"
check "model flag passed" 0 0 "--model claude-sonnet-5" "$(grep 'agent start w-t3' "$FAKE_LOG")"
check "brief model by default" 0 0 "--model claude-opus-4-8" "$(grep 'agent start w-t1' "$FAKE_LOG")"
check "brief effort by default" 0 0 "--effort high" "$(grep 'agent start w-t1' "$FAKE_LOG")"
check "model and effort logged" 0 0 "model claude-opus-4-8, effort high" "$(grep 'T1	STARTED' "$SD/events.log")"
check "template has a model" 0 0 "## Model" "$(cat "$SD/briefs/T1.md")"
check "template has an effort" 0 0 "## Effort" "$(cat "$SD/briefs/T1.md")"
check "prompt pins the reviewer" 0 0 "reviewer sub-agent, with model claude-opus-4-8" "$(grep 'agent prompt w-t1' "$FAKE_LOG")"
check "next worker stacks below" 0 0 "pane split w9:p7 --direction down" "$(grep 'pane split' "$FAKE_LOG" | sed -n 3p)"

# Model and Effort sections in a brief
model_brief() { # id, model -> ./<id>.md with a valid Effort
  model_effort_brief "$1" "$2" high
}
# A template brief with placeholders is refused.
run brief P1; mv "$SD/briefs/P1.md" ./P1.md
run start P1 P1.md;                            check "template placeholders refused" 2 $rc "placeholders" "$out"
# Bare aliases are refused: they drift to the newest model, which can be banned.
model_brief MA1 "opus"
run start MA1 MA1.md;                          check "bare opus refused" 2 $rc "must be one of" "$out"
model_brief MA2 "sonnet"
run start MA2 MA2.md;                          check "bare sonnet refused" 2 $rc "must be one of" "$out"
# Banned models are refused with a clear reason.
model_brief MB1 "claude-opus-5-5"
run start MB1 MB1.md;                          check "banned opus 5.5 refused" 2 $rc "is not allowed" "$out"
model_brief MB2 "claude-fable-5-1"
run start MB2 MB2.md;                          check "banned fable refused" 2 $rc "is not allowed" "$out"
# An unknown model is refused.
model_brief MU "gpt-9"
run start MU MU.md;                            check "unknown model refused" 2 $rc "must be one of" "$out"
# Pinned allowed models pass, with model and effort sent to the worker.
model_brief M7 "claude-opus-4-8"
run start M7 M7.md;                            check "pinned opus from brief" 0 $rc "started M7" "$out"
check "pinned model passed" 0 0 "--model claude-opus-4-8" "$(grep 'agent start w-m7' "$FAKE_LOG")"
model_effort_brief M9 claude-opus-5 low
run start M9 M9.md;                            check "opus 5 allowed" 0 $rc "started M9" "$out"
check "opus 5 model passed" 0 0 "--model claude-opus-5" "$(grep 'agent start w-m9' "$FAKE_LOG")"
check "opus 5 effort passed" 0 0 "--effort low" "$(grep 'agent start w-m9' "$FAKE_LOG")"
model_effort_brief MH claude-haiku-4-5-20251001 medium
run start MH MH.md;                            check "haiku allowed" 0 $rc "started MH" "$out"
check "haiku model passed" 0 0 "--model claude-haiku-4-5-20251001" "$(grep 'agent start w-mh' "$FAKE_LOG")"
# Effort is required and validated.
model_effort_brief ME1 claude-opus-4-8 turbo
run start ME1 ME1.md;                          check "unknown effort refused" 2 $rc "effort must be one of" "$out"
printf '# Task NE\n\n## Goal\n\ng\n\n## Inputs\n\n- a\n\n## Acceptance criteria\n\n1. x\n\n## You may decide\n\n- w\n\n## Model\n\nclaude-opus-4-8\n\n## Result\n\nWrite to: %s\n' "$T/results/r.md" > NE.md
run start NE NE.md --no-check;                 check "missing effort refused" 2 $rc "no Effort section" "$out"
# Flags override the brief, and are validated too.
model_effort_brief M5 claude-opus-4-8 high
run start M5 M5.md --model claude-sonnet-5 --effort low;  check "flags override brief" 0 $rc "started M5" "$out"
check "flag override model passed" 0 0 "--model claude-sonnet-5" "$(grep 'agent start w-m5' "$FAKE_LOG")"
check "flag override effort passed" 0 0 "--effort low" "$(grep 'agent start w-m5' "$FAKE_LOG")"
run start MX M5.md --model claude-opus-5-5;    check "flag model validated" 2 $rc "is not allowed" "$out"
run start MY M5.md --effort nope;              check "flag effort validated" 2 $rc "effort must be one of" "$out"

# Done
long="$(python3 -c 'print("x"*301)')"
run done T1 PASS "$T/results/r.md" "$long";   check "summary too long" 2 $rc "shorten" "$out"
run done T1 PASS "$T/nothing-here.md" "s";    check "missing result" 2 $rc "no result file" "$out"
mkdir -p "/tmp/colony-test-$$" && echo x > "/tmp/colony-test-$$/r.md"
run done T1 PASS "/tmp/colony-test-$$/r.md" "s"; check "scratch result" 2 $rc "scratch" "$out"
rm -rf "/tmp/colony-test-$$"
run done T1 MAYBE "$T/results/r.md" "s";      check "bad status" 2 $rc "status must be" "$out"
run done T9 PASS "$T/results/r.md" "s";       check "unknown task" 2 $rc "no task T9" "$out"
run done T1 PASS "$T/results/r.md" "criteria 1-1 pass; confirm: a.md:3";  check "done" 0 $rc "recorded T1 PASS" "$out"

# Wait
run wait --timeout 5;                         check "wait reports outcome" 0 $rc "T1	PASS	$T/results/r.md	criteria 1-1 pass" "$out"
absent "wait skips STARTED" "STARTED" "$out"
check "wait includes START-FAILED" 0 0 "T2	START-FAILED" "$out"
run wait --timeout 1;                         check "wait times out" 0 $rc "no new outcomes" "$out"
run done T3 QUESTION - "keep the old API or drop it?"
run done T1 BLOCKED - "tests need a database"
run wait --timeout 5;                         check "wait batches two" 0 $rc "T3	QUESTION" "$out"
check "batch has second" 0 0 "T1	BLOCKED" "$out"
run start T4 T4.md
FAKE_AGENT_GONE=1 run wait --timeout 3;       check "wait finds lost worker" 0 $rc "T4	LOST" "$out"
run start T5 T5.md
FAKE_STATUS=idle run wait --timeout 3;        check "wait finds idle worker" 0 $rc "T5	IDLE" "$out"

# Answer
run answer T3 "keep it";                      check "answer" 0 $rc "answered T3" "$out"
check "answer sent to worker" 0 0 "agent prompt w-t3 Answer from the Master on T3: keep it" "$(tail -1 "$FAKE_LOG")"
check "answer logged" 0 0 "T3	ANSWERED" "$(cat "$SD/events.log")"

# Close and status
FAKE_STATUS=blocked run close T1;             check "close refuses blocked" 2 $rc "waits for input" "$out"
FAKE_STATUS=blocked run close T1 --force;     check "close --force" 0 $rc "closed T1; session sess-123" "$out"
run close T1;                                 check "close twice" 0 $rc "was closed" "$out"
run answer T1 "late";                         check "answer closed task" 2 $rc "is closed" "$out"
run status;                                   check "status hides closed" 0 $rc "T3	ANSWERED" "$out"
absent "status hides T1" "^T1	" "$out"
run status --all;                             check "status --all" 0 $rc "T1	CLOSED" "$out"

# Report: two records with the same id and request count once, with the larger output.
u='"model":"claude-opus-5-5","usage":{"input_tokens":10,"cache_read_input_tokens":30000,"cache_creation_input_tokens":5000,"output_tokens":200}'
{
  echo "{\"timestamp\":\"2099-01-01T00:00:00Z\",\"requestId\":\"r1\",\"message\":{\"id\":\"m1\",$u}}"
  echo "{\"timestamp\":\"2099-01-01T00:00:00Z\",\"requestId\":\"r1\",\"message\":{\"id\":\"m1\",${u/200/20200}}}"
  echo "{\"timestamp\":\"2099-01-01T00:01:00Z\",\"requestId\":\"r2\",\"message\":{\"id\":\"m2\",$u}}"
} > "$T/claude/projects/x/sess-123.jsonl"
echo "{\"timestamp\":\"2099-01-01T00:02:00Z\",\"requestId\":\"r3\",\"message\":{\"id\":\"m3\",$u}}" > "$T/claude/projects/x/sess-123/subagents/agent-a.jsonl"
run report;                                   check "report" 0 $rc "T1	opus-5-5	3	126k	35k	35k" "$out"
check "report master" 0 0 "Master (since" "$out"

# Separate colonies in one project
COLONY_NAME=bench run brief B1;               check "second colony" 0 $rc "$COLONY_HOME/$SLUG/bench/briefs/B1.md" "$out"
brief B1
COLONY_NAME=bench run start B1 B1.md;         check "second colony start" 0 $rc "agent w-bench-b1" "$out"
check "master on banned model warns" 0 0 "Master runs claude-opus-5-5" "$out"
check "second colony env" 0 0 "--env COLONY_NAME=bench" "$(grep 'pane split' "$FAKE_LOG" | tail -1)"
COLONY_NAME=bench run status;                 check "colonies apart" 0 $rc "B1	STARTED" "$out"
absent "main tasks not in bench" "T3" "$out"
COLONY_NAME="Bad Name" run status;            check "bad colony name" 2 $rc "bad COLONY_NAME" "$out"

# Checkpoint and restart
run restart;                                  check "restart needs --confirm" 2 $rc "--confirm" "$out"
run restart --confirm;                        check "restart needs checkpoint" 2 $rc "no checkpoint" "$out"
run checkpoint;                               check "checkpoint" 0 $rc "$SD/master.md" "$out"
check "checkpoint lists tasks" 0 0 "T3 · ANSWERED" "$(cat "$SD/master.md")"
run restart --confirm;                        check "restart refuses template" 2 $rc "placeholders" "$out"
sed -i '' 's/<The goal of this colony and the remaining steps.>/Finish T3./' "$SD/master.md"
touch -t 202001010000 "$SD/master.md"
run restart --confirm;                        check "restart refuses stale" 2 $rc "older than 15 minutes" "$out"
touch "$SD/master.md"
run restart --confirm;                        check "restart" 0 $rc "restart queued" "$out"
check "restart clears" 0 0 "agent prompt w9:p1 /clear" "$(tail -2 "$FAKE_LOG" | head -1)"
check "restart resumes" 0 0 "agent prompt w9:p1 You are the Master of colony main" "$(tail -1 "$FAKE_LOG")"
echo "{\"timestamp\":\"2099-01-01T00:03:00Z\",\"requestId\":\"r4\",\"message\":{\"id\":\"m4\",$u}}" > "$T/claude/projects/x/sess-456.jsonl"
FAKE_SESSION=sess-456 run report;             check "report follows restart" 0 $rc "Master after restart 1" "$out"
check "new session saved" 0 0 "sess-456" "$(cat "$SD/colony.json")"

echo "colony tests: $pass passed, $fail failed"
[[ $fail = 0 ]]
