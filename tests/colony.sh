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
    if [[ "${FAKE_START_BROKEN:-0}" = 1 ]]; then
      echo '{"error":{"code":"internal","message":"herdr exploded"}}'; exit 1
    fi
    if [[ "${FAKE_START_FAIL:-0}" = 1 ]]; then
      echo '{"error":{"code":"agent_not_ready","message":"agent not ready"}}'; exit 1
    fi
    echo '{"result":{}}' ;;
  "agent prompt") echo '{"result":{}}' ;;
  "agent get")
    if [[ "${FAKE_AGENT_GONE:-0}" = 1 ]]; then
      echo "{\"error\":{\"code\":\"agent_not_found\",\"message\":\"agent target $3 not found\"}}"; exit 1
    fi
    echo "{\"result\":{\"agent\":{\"agent_status\":\"${FAKE_STATUS:-working}\",\"agent_session\":{\"value\":\"${FAKE_AGENT_SESSION:-}\"}}}}" ;;
  "pane get") echo "{\"result\":{\"pane\":{\"agent_status\":\"${FAKE_PANE_STATUS:-${FAKE_STATUS:-idle}}\",\"agent_session\":{\"value\":\"${FAKE_SESSION:-sess-123}\"}}}}" ;;
  "pane close") echo '{"result":{}}' ;;
  *) echo '{"error":{"message":"unknown"}}'; exit 1 ;;
esac
FAKE
chmod +x "$T/herdr"

export COLONY_HERDR="$T/herdr" COLONY_HOME="$T/state" FAKE_LOG="$T/calls.log"
export COLONY_CHECK_EVERY=0.2 COLONY_SETTLE=0.1 HERDR_ENV=1 HERDR_PANE_ID=w9:p1
export COLONY_START_READY_WAIT=0.5
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

# Token ledger: a colony of its own, with crafted transcripts.
# L1 holds a streamed duplicate, a response without the thinking field and a
# <synthetic> response. L2 serves a model its brief did not pin and has a
# sub-agent. L3's session has no transcript at all.
model_effort_brief L1 claude-opus-4-8 high
COLONY_NAME=led run start L1 L1.md;           check "ledger start L1" 0 $rc "started L1" "$out"
FAKE_SESSION=sess-led COLONY_NAME=led run close L1;  check "ledger close L1" 0 $rc "session sess-led" "$out"
model_effort_brief L2 claude-sonnet-5-5 low
COLONY_NAME=led run start L2 L2.md;           check "ledger start L2" 0 $rc "started L2" "$out"
FAKE_SESSION=sess-l2 COLONY_NAME=led run close L2;   check "ledger close L2" 0 $rc "session sess-l2" "$out"
model_effort_brief L3 claude-opus-4-8 low
COLONY_NAME=led run start L3 L3.md;           check "ledger start L3" 0 $rc "started L3" "$out"
FAKE_SESSION=sess-gone COLONY_NAME=led run close L3; check "ledger close L3" 0 $rc "session sess-gone" "$out"

mkdir -p "$T/claude/projects/x/sess-l2/subagents"
c1='"cache_creation":{"ephemeral_1h_input_tokens":4000,"ephemeral_5m_input_tokens":1000}'
{
  echo "{\"timestamp\":\"2099-01-01T00:00:00Z\",\"requestId\":\"q1\",\"effort\":\"high\",\"perTurnEffort\":\"high\",\"message\":{\"id\":\"n1\",\"model\":\"claude-opus-4-8\",\"usage\":{\"input_tokens\":100,\"cache_read_input_tokens\":20000,\"cache_creation_input_tokens\":5000,$c1,\"output_tokens\":500,\"output_tokens_details\":{\"thinking_tokens\":300}}}}"
  echo "{\"timestamp\":\"2099-01-01T00:00:01Z\",\"requestId\":\"q1\",\"effort\":\"high\",\"perTurnEffort\":\"high\",\"message\":{\"id\":\"n1\",\"model\":\"claude-opus-4-8\",\"usage\":{\"input_tokens\":100,\"cache_read_input_tokens\":20000,\"cache_creation_input_tokens\":5000,$c1,\"output_tokens\":1500,\"output_tokens_details\":{\"thinking_tokens\":900}}}}"
  echo "{\"timestamp\":\"2099-01-01T00:01:00Z\",\"requestId\":\"q2\",\"effort\":\"high\",\"message\":{\"id\":\"n2\",\"model\":\"claude-opus-4-8\",\"usage\":{\"input_tokens\":0,\"cache_read_input_tokens\":30000,\"cache_creation_input_tokens\":0,\"output_tokens\":200}}}"
  echo "{\"timestamp\":\"2099-01-01T00:02:00Z\",\"requestId\":\"q3\",\"message\":{\"id\":\"n3\",\"model\":\"<synthetic>\",\"usage\":{\"input_tokens\":0,\"cache_read_input_tokens\":0,\"cache_creation_input_tokens\":0,\"output_tokens\":10}}}"
} > "$T/claude/projects/x/sess-led.jsonl"
echo "{\"timestamp\":\"2099-01-01T00:03:00Z\",\"requestId\":\"q4\",\"message\":{\"id\":\"n4\",\"model\":\"claude-opus-5-5\",\"usage\":{\"input_tokens\":0,\"cache_read_input_tokens\":10000,\"cache_creation_input_tokens\":0,\"output_tokens\":100}}}" > "$T/claude/projects/x/sess-l2.jsonl"
echo "{\"timestamp\":\"2099-01-01T00:04:00Z\",\"requestId\":\"q5\",\"message\":{\"id\":\"n5\",\"model\":\"claude-opus-4-8\",\"usage\":{\"input_tokens\":0,\"cache_read_input_tokens\":2000,\"cache_creation_input_tokens\":0,\"output_tokens\":50,\"output_tokens_details\":{\"thinking_tokens\":20}}}}" > "$T/claude/projects/x/sess-l2/subagents/agent-a.jsonl"
echo '{"agentType":"general-purpose","description":"Review L2 against criteria"}' > "$T/claude/projects/x/sess-l2/subagents/agent-a.meta.json"

COLONY_NAME=led run report
check "ledger keeps the old table" 0 $rc "L1	opus-4-8	3	57k	25k	0k" "$out"
check "ledger counts a sub-agent" 0 0 "L2	opus-5-5	2	12k	10k	2k" "$out"
check "no transcript stays blank" 0 0 "L3	opus-4-8	-	-	-	-" "$out"
check "ledger breakdown header" 0 0 "row	input	output	thinking	cache read	cache write 5m	cache write 1h	cost" "$out"
check "ledger breakdown row" 0 0 "L1	0k	2k	1k	50k	1k	4k	-" "$out"
check "sub-agent type and description" 0 0 "L2	general-purpose	1	2k	Review L2 against criteria" "$out"
check "effort per request" 0 0 "high	2" "$out"
check "drift noted" 0 0 "brief pinned claude-sonnet-5-5 but claude-opus-5-5 served most requests on 1 row(s): L2" "$out"
check "missing transcript noted" 0 0 "no transcript found for 1 row(s), so their usage is unavailable, not zero: L3" "$out"
check "partial thinking noted" 0 0 "carried no thinking_tokens field (L1, L2" "$out"
check "synthetic noted" 0 0 "<synthetic> responses, which no model served and none is billed: L1 x1" "$out"
check "no rates noted" 0 0 "no pricing rates configured" "$out"
absent "no invented cache-read split" "cache read 1h" "$out"

COLONY_NAME=led run report --json
check "json parses" 0 $rc '"colony": "led"' "$out"
jrc=0; python3 -c 'import json,sys; json.load(sys.stdin)' <<<"$out" >/dev/null 2>&1 || jrc=$?
check "json is valid JSON" 0 $jrc "" ""
check "json input" 0 0 '"input": 100' "$out"
check "json output" 0 0 '"output": 1710' "$out"
check "json thinking is not added to output" 0 0 '"thinking": 900' "$out"
check "json thinking unknown" 0 0 '"thinking_unknown": 2' "$out"
check "json cache read" 0 0 '"cache_read": 50000' "$out"
check "json cache write" 0 0 '"cache_write": 5000' "$out"
check "json cache write 1h" 0 0 '"cache_write_1h": 4000' "$out"
check "json cache write 5m" 0 0 '"cache_write_5m": 1000' "$out"
check "json tokens" 0 0 '"tokens": 56810' "$out"
check "json first context" 0 0 '"first_context": 25100' "$out"
check "json sub-agent type" 0 0 '"agent_type": "general-purpose"' "$out"
check "json unmeasured row" 0 0 '"measured": false' "$out"
check "json cost is unavailable" 0 0 '"cost": null' "$out"
check "json pricing units" 0 0 '"units": "USD per million tokens"' "$out"
absent "json invents no cache-read 1h" "cache_read_1h" "$out"
absent "json invents no cache-read 5m" "cache_read_5m" "$out"
COLONY_NAME=fresh run report --json;          check "json with no tasks" 0 $rc '"no tasks"' "$out"

# Pricing: rates in the config, partial rates, a config without the block, bad blocks.
price() { # rates for claude-opus-4-8 -> $T/price.json
  python3 - "$1" <<'PY'
import json, os, sys
cfg = json.load(open(os.environ["REPO_CONFIG"]))
cfg["pricing"] = {"units": "USD per million tokens", "models": {"claude-opus-4-8": json.loads(sys.argv[1])}}
json.dump(cfg, open(os.path.join(os.environ["T"], "price.json"), "w"))
PY
}
export REPO_CONFIG="$HERE/../colony-models.json" T="$T"
price '{"input":15,"output":75,"cache_read":1.5,"cache_write_1h":30,"cache_write_5m":18.75}'
COLONY_MODELS_CONFIG="$T/price.json" COLONY_NAME=led run report
check "cost from rates" 0 $rc "L1	0k	2k	1k	50k	1k	4k	0.34" "$out"
check "cost of a priced sub-agent" 0 0 "L2	0k	0k	0k	12k	0k	0k	0.01" "$out"
check "unpriced model named" 0 0 "claude-opus-5-5: no rate in the pricing config" "$out"
check "synthetic not priced" 0 0 "<synthetic>: not a served model, not priced" "$out"
price '{"input":15}'
COLONY_MODELS_CONFIG="$T/price.json" COLONY_NAME=led run report
check "partial rates give no cost" 0 $rc "L1	0k	2k	1k	50k	1k	4k	-" "$out"
check "partial rates say which" 0 0 "no rate for output, cache_read, cache_write_1h, cache_write_5m" "$out"
python3 -c "
import json, os
cfg = json.load(open(os.environ['REPO_CONFIG'])); cfg.pop('pricing', None)
json.dump(cfg, open(os.path.join(os.environ['T'], 'nopricing.json'), 'w'))"
COLONY_MODELS_CONFIG="$T/nopricing.json" COLONY_NAME=led run report
check "config without pricing still works" 0 $rc "no pricing rates configured" "$out"
echo '{"banned":[],"worker_models":["m"],"master_model":"m","reviewer_model":"m","efforts":["low"],"pricing":{"models":{"m":{"bogus":1}}}}' > "$T/badprice.json"
COLONY_MODELS_CONFIG="$T/badprice.json" COLONY_NAME=led run report
check "unknown rate name refused" 2 $rc "name bogus; use input, output, cache_read" "$out"
echo '{"banned":[],"worker_models":["m"],"master_model":"m","reviewer_model":"m","efforts":["low"],"pricing":{"models":{"m":{"input":-1}}}}' > "$T/badprice.json"
COLONY_MODELS_CONFIG="$T/badprice.json" COLONY_NAME=led run report
check "negative rate refused" 2 $rc "not negative" "$out"
echo '{"banned":[],"worker_models":["m"],"master_model":"m","reviewer_model":"m","efforts":["low"],"pricing":[]}' > "$T/badprice.json"
COLONY_MODELS_CONFIG="$T/badprice.json" COLONY_NAME=led run report
check "bad pricing block refused" 2 $rc "must be an object with a models object" "$out"

# Task metadata: revision, dirty, verdict and retry lineage.
# $T/proj is not a Git repository, so start records no revision and says why.
brief MD1
run start MD1 MD1.md;                           check "metadata start" 0 $rc "started MD1" "$out"
MD1J="$(cat "$SD/tasks/MD1.json")"
check "no revision outside a repo" 0 0 '"revision": null' "$MD1J"
check "no dirty flag outside a repo" 0 0 '"dirty": null' "$MD1J"
check "says why there is no revision" 0 0 '"revision_unavailable": "git rev-parse' "$MD1J"
check "verdict starts unset" 0 0 '"verdict": null' "$MD1J"
check "retry_of starts unset" 0 0 '"retry_of": null' "$MD1J"
FAKE_SESSION=sess-m1 run close MD1;            check "close without a verdict" 0 $rc "closed MD1; session sess-m1" "$out"
absent "close says nothing of a verdict" "verdict" "$out"
check "verdict stays unset" 0 0 '"verdict": null' "$(cat "$SD/tasks/MD1.json")"
run close MD1 --accept;                        check "verdict after closing" 0 $rc "was closed at" "$out"
check "verdict after closing is named" 0 0 "verdict accept" "$out"
check "verdict recorded" 0 0 '"verdict": "accept"' "$(cat "$SD/tasks/MD1.json")"
check "verdict keeps the revision fields" 0 0 '"revision": null' "$(cat "$SD/tasks/MD1.json")"
run close MD1 --accept --reject;               check "two verdicts refused" 2 $rc "name one verdict, not --accept, --reject" "$out"
brief MD2
run start MD2 MD2.md;                           check "metadata start MD2" 0 $rc "started MD2" "$out"
FAKE_SESSION=sess-m2 run close MD2 --partial;  check "partial verdict" 0 $rc "verdict partial" "$out"
check "partial recorded" 0 0 '"verdict": "partial"' "$(cat "$SD/tasks/MD2.json")"
brief MD3
run start MD3 MD3.md
FAKE_SESSION=sess-m3 run close MD3 --reject;   check "reject verdict" 0 $rc "verdict reject" "$out"
check "reject recorded" 0 0 '"verdict": "reject"' "$(cat "$SD/tasks/MD3.json")"

# Retry lineage
brief MD4
run start MD4 MD4.md --retry-of MD1;             check "retry start" 0 $rc "started MD4" "$out"
check "retry recorded" 0 0 '"retry_of": "MD1"' "$(cat "$SD/tasks/MD4.json")"
brief MD5
run start MD5 MD5.md --retry-of NOPE;           check "retry of an unknown task" 2 $rc "no task NOPE in colony main to retry" "$out"
absent "a refused retry starts nothing" "started MD5" "$out"
run start MD5 MD5.md --retry-of MD5;             check "retry of itself" 2 $rc "own id" "$out"
run start MD5 MD5.md --retry-of "bad id!";      check "retry of a bad id" 2 $rc "bad task id" "$out"

# A task file written before these fields loads and takes a verdict, and colony
# invents no revision for it.
printf '{"id":"OLD","agent":"w-old","pane":"w9:p99","brief":"%s","model":"claude-opus-4-8","effort":"high","cwd":"%s","started":"2026-01-01T00:00:00Z","closed":"2026-01-01T00:30:00Z","session":null}\n' "$T/proj/T1.md" "$T/proj" > "$SD/tasks/OLD.json"
run report;                                   check "an old task file loads" 0 $rc "OLD	opus-4-8	-	-	-	-" "$out"
run close OLD --accept;                       check "an old task takes a verdict" 0 $rc "verdict accept" "$out"
OLDJ="$(cat "$SD/tasks/OLD.json")"
check "the old task keeps its verdict" 0 0 '"verdict": "accept"' "$OLDJ"
absent "no revision invented for an old task" '"revision"' "$OLDJ"

# A real repository: the revision and dirty state describe the project root,
# not the directory colony start ran in.
G="$T/gitproj"
mkdir -p "$G/sub"
git -C "$G" init -q -b main
echo tracked > "$G/a.txt"
git -C "$G" add a.txt
git -C "$G" -c user.name=t -c user.email=t@example.com -c commit.gpgsign=false commit -qm init
HEAD_SHA="$(git -C "$G" rev-parse HEAD)"
GSD="$COLONY_HOME/$(python3 -c 'import re,sys;print(re.sub(r"[^A-Za-z0-9]","-",sys.argv[1]))' "$G")/main"
brief G1
cd "$G/sub"
run start G1 "$T/proj/G1.md";                 check "start inside a repo" 0 $rc "started G1" "$out"
G1J="$(cat "$GSD/tasks/G1.json")"
check "revision is the committed HEAD" 0 0 "\"revision\": \"$HEAD_SHA\"" "$G1J"
check "a clean tree is not dirty" 0 0 '"dirty": false' "$G1J"
check "cwd is where start ran" 0 0 "\"cwd\": \"$G/sub\"" "$G1J"
absent "a clean read needs no reason" "revision_unavailable" "$G1J"
echo untracked > "$G/b.txt"
cd "$T/proj"; brief G2; cd "$G/sub"
run start G2 "$T/proj/G2.md";                 check "start with a dirty tree" 0 $rc "started G2" "$out"
check "an untracked file is dirty" 0 0 '"dirty": true' "$(cat "$GSD/tasks/G2.json")"
FAKE_SESSION=sess-g1 run close G1 --accept;   check "verdict in a repo" 0 $rc "verdict accept" "$out"
G1J="$(cat "$GSD/tasks/G1.json")"
check "the revision survives a close" 0 0 "\"revision\": \"$HEAD_SHA\"" "$G1J"
check "the dirty flag survives a close" 0 0 '"dirty": false' "$G1J"
cd "$T/proj"

# IDLE: a worker that waits is not a worker that stopped. Its own colony, so
# the waits below start from an empty event log and offset.
IDSD="$COLONY_HOME/$SLUG/idlecheck"
brief ID1
COLONY_NAME=idlecheck run start ID1 ID1.md;   check "idle colony start" 0 $rc "started ID1" "$out"
FAKE_STATUS=idle FAKE_PANE_STATUS=blocked COLONY_NAME=idlecheck run wait --timeout 2
check "a blocked pane is not idle" 0 $rc "no new outcomes" "$out"
absent "no IDLE while the pane waits for input" "IDLE" "$(cat "$IDSD/events.log")"
FAKE_STATUS=blocked COLONY_NAME=idlecheck run wait --timeout 2
check "a blocked agent is not idle" 0 $rc "no new outcomes" "$out"
absent "no IDLE while the agent is blocked" "IDLE" "$(cat "$IDSD/events.log")"
FAKE_STATUS=working COLONY_NAME=idlecheck run wait --timeout 2
check "a working agent is not idle" 0 $rc "no new outcomes" "$out"
absent "no IDLE while the agent works" "IDLE" "$(cat "$IDSD/events.log")"
COLONY_IDLE_SAMPLES=50 FAKE_STATUS=idle COLONY_NAME=idlecheck run wait --timeout 2
check "fewer checks than the threshold" 0 $rc "no new outcomes" "$out"
absent "no IDLE below the sample count" "IDLE" "$(cat "$IDSD/events.log")"
FAKE_STATUS=idle COLONY_NAME=idlecheck run wait --timeout 3
check "a genuinely idle worker is still found" 0 $rc "ID1	IDLE" "$out"
check "IDLE names how many checks" 0 0 "worker idle for 3 checks without colony done" "$(cat "$IDSD/events.log")"
check "one IDLE for one idle spell" 0 0 "^1$" "$(grep -c IDLE "$IDSD/events.log")"
brief ID2
COLONY_NAME=idlecheck run start ID2 ID2.md
COLONY_IDLE_SAMPLES=1 FAKE_STATUS=idle COLONY_NAME=idlecheck run wait --timeout 3
check "the sample count is configurable" 0 $rc "ID2	IDLE" "$out"
brief ID3
COLONY_NAME=idlecheck run start ID3 ID3.md
FAKE_AGENT_GONE=1 COLONY_NAME=idlecheck run wait --timeout 2
check "a lost worker is still found at once" 0 $rc "ID3	LOST" "$out"

# An agent herdr calls not ready, which becomes ready, is used rather than lost.
brief NR1
FAKE_START_FAIL=1 FAKE_AGENT_SESSION=sess-ready run start NR1 NR1.md
check "a late-ready agent is used" 0 $rc "started NR1" "$out"
check "the recovery is reported" 0 0 "became ready and was used anyway" "$out"
check "the late-ready agent is prompted" 0 0 "agent prompt w-nr1" "$(grep 'agent prompt w-nr1' "$FAKE_LOG")"
check "the late-ready task is recorded" 0 0 "NR1	STARTED" "$(cat "$SD/events.log")"
check "the late-ready task has state" 0 0 '"id": "NR1"' "$(cat "$SD/tasks/NR1.json")"
# An agent that never reports a session is still a failed start.
brief NR2
FAKE_START_FAIL=1 run start NR2 NR2.md;       check "a never-ready agent fails" 1 $rc "agent not ready" "$out"
check "the failed start is recorded" 0 0 "NR2	START-FAILED" "$(cat "$SD/events.log")"
absent "the failed start leaves no task" "NR2" "$(ls "$SD/tasks")"
# Any other start error fails at once, with no waiting.
brief NR3
FAKE_START_BROKEN=1 run start NR3 NR3.md;     check "another start error still fails" 1 $rc "herdr exploded" "$out"
absent "no readiness wait for another error" "became ready" "$out"

# A worker is told about the directories holding its brief and its result.
check "state dir named for the worker" 0 0 "--add-dir $SD" "$(grep 'agent start w-t1' "$FAKE_LOG")"
absent "the working directory is not named" "--add-dir $T/proj" "$(grep 'agent start w-t1' "$FAKE_LOG")"
mkdir -p "$T/elsewhere"
brief AD1; mv AD1.md "$T/elsewhere/AD1.md"
run start AD1 "$T/elsewhere/AD1.md";          check "a brief outside both starts" 0 $rc "started AD1" "$out"
check "the brief's own directory is named" 0 0 "--add-dir $T/elsewhere" "$(grep 'agent start w-ad1' "$FAKE_LOG")"
check "the state dir is named as well" 0 0 "--add-dir $SD" "$(grep 'agent start w-ad1' "$FAKE_LOG")"

echo "colony tests: $pass passed, $fail failed"
[[ $fail = 0 ]]
