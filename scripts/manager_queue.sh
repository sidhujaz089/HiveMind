#!/bin/bash
# The manager takes the TOP of QUEUE.md, one task at a time (Jas, 2026-09-24).
# scripts/manager_next.py picks the task; a Claude-written prompt in custom/<task>-*.txt wins,
# else scripts/manager_prompt.py writes the standard one. ask_q16 --task keeps the session.
# Stop cleanly: touch workspace/run-logs/manager-queue/STOP
# THE REPOSITORY IS THIS SCRIPT'S OWN PARENT, not a literal path (L1 review, 2026-10-29: it hard-coded
# /home/user/ai-workspace twice, so a checkout anywhere else ran against the wrong tree).
R=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
Q=$R/workspace/run-logs/manager-queue
# ONE LOOP PER SCOPING ROLE (Jas, 2026-10-14: "I want the auditor and Manager working together to clear
# our backlog ... I just need to get them both scoping"). `manager_queue.sh auditor` is a second copy of
# this loop that scopes as the Auditor, on its own model and lanes (scoping_lanes.py target auditor). The
# claims are shared, so two loops never scope the same task; each counts only its own workers.
# THE SCOPING ROLE IS LOOKED UP, NOT ASSUMED (Jas, 2026-10-29: "manager_queue sounds like a hard coded role.
# Please point that to a source of truth"). `SCOPER` names WHICH scoping loop this is -- the store decides
# who that is: whoever lists the `scoping` task on the Models tab. Delete the Manager role and give Scoping to
# a new one and this loop follows, with no edit here.
# AND THE FAILURE IS NEVER SILENT (L1 review, 2026-10-29). The first version wrote `... 2>/dev/null || echo
# manager`, which made a BROKEN store indistinguishable from a store that answered: the error was discarded, the
# fallback made the variable non-empty, and the warning below never fired. A silent fallback to a literal is
# exactly how a renamed role becomes a dead queue nobody notices -- which is what this comment claims to refuse.
_store_scoper() {
  python3 - "$R" <<'PY2'
import sys
sys.path.insert(0, sys.argv[1] + "/scripts")
import task_routing
print(task_routing.owner(task_routing.load(), "scoping") or "")
PY2
}
SCOPER="$1"
if [ -z "$SCOPER" ]; then
  if SCOPER=$(_store_scoper 2>&1); then
    SCOPER=$(printf '%s' "$SCOPER" | tr -d '[:space:]')
  else
    echo "manager_queue: the role store could not be read -- falling back to 'manager'" >&2
  fi
  if [ -z "$SCOPER" ]; then
    echo "manager_queue: the role store names no scoping role -- falling back to 'manager'" >&2
    SCOPER=manager
  fi
fi
# --role only picks the asker's TOOLS; ask_q16's model defaults to the Manager's. Another role's loop
# names its own model, or its scopes run on Swift (2026-10-14: the Auditor's first scope, t1160, did).
MODEL_ARG=""
if [ "$SCOPER" != manager ]; then
  MODEL_ARG="--model $(python3 -c "import sys;sys.path.insert(0,'scripts');import roles;print(roles.tag('$SCOPER',''))")"
fi
mkdir -p "$Q/in" "$Q/out" "$Q/custom"
cd "$R" || exit 1
# PARALLEL SCOPING (Jas, 2026-10-08: "Why is scoping capped at 1?"). This loop is now a LAUNCHER:
# each task runs in its own background worker (run_task, below), up to scoping_lanes.py target --
# the lanes the scheduler's rotation granted Scoping, at least 1, never above Scoping's Max lanes.
# A worker CLAIMS its task first (claims/<task>), so two never scope the same one.
run_task() {
  local t=$1
  trap 'python3 scripts/scoping_lanes.py release "$t"' EXIT
  custom=$(ls "$Q/custom/$t"-*.txt 2>/dev/null | head -1)
  if [ -n "$custom" ]; then cp "$custom" "$Q/in/$t.txt"; else python3 scripts/manager_prompt.py "$t" > "$Q/in/$t.txt"; fi
  echo "$(date -u +%H:%M:%SZ) START $t (task $t) station $STATION role $HIVEMIND_ROLE" >> "$Q/progress.log"
  # THE STATION IS SHARED, ONE JOB AT A TIME: senior_slot reads this and starts nothing while it lives.
  if [ "$STATION" = 2 ]; then
    echo "{\"pid\": $$, \"task\": \"$t\", \"at\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" > "$Q/STATION2"
    echo "{\"at\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\", \"task\": \"$t\"}" >> "$Q/station2.jsonl"
  fi
  # THE BUDGET IS THE MODEL'S (2026-10-06): 16000 unless its server entry declares a job_budget
  # (SERVERS.json; Swift 32000 -- its first scope was cut off at 16000 mid-chunks-block).
  budget=$(python3 -c "import sys;sys.path.insert(0,'scripts');sys.path.insert(0,'projects/local-inference');import roles,infer;print(infer.job_budget(roles.tag('$SCOPER',''),16000))" 2>/dev/null || echo 16000)
  # 12 TOOL ROUNDS, NOT 6 (t9549, Claude 2026-10-29). Measured over the last 7 days: 88 of 283 scoping
  # answers (31%) say they ran out of tool budget before reading the file they were scoping -- and each
  # one went back to Claude as "BLOCKED", then back to the scoper unchanged: a rework loop by construction.
  # 12 is ask_q16's own default; settings.py's tool_rounds note records that 8 rounds left a session with
  # no answer at all. The wall-clock timeout below is unchanged and still bounds the run.
  timeout 5700 python3 scripts/ask_q16.py --prompt "$Q/in/$t.txt" --out "$Q/out/$t.md" \
    --budget "$budget" --max-escalations 0 --max-rounds 12 --timeout 5400 \
    --purpose "manager: $t (top of QUEUE.md)" --task "$t" --role "$HIVEMIND_ROLE" $MODEL_ARG > "$Q/out/$t.log" 2>&1
  rc=$?
  [ "$STATION" = 2 ] && rm -f "$Q/STATION2"
  echo "$(date -u +%H:%M:%SZ) END $t rc=$rc bytes=$(stat -c%s "$Q/out/$t.md" 2>/dev/null || echo 0)" >> "$Q/progress.log"
  # rc 3 = ask_q16 could not reach the model at all; rc 4 = the model's lease is held. The task
  # was never asked: release the claim (the trap) and let a later pass retry it.
  if [ "$rc" = 4 ]; then
    echo "$(date -u +%H:%M:%SZ) RETRY $t later: model lease held by another job" >> "$Q/progress.log"
    sleep 60; return
  fi
  if [ "$rc" = 3 ]; then
    echo "$(date -u +%H:%M:%SZ) RETRY $t later: model server unreachable during the run" >> "$Q/progress.log"
    sleep 120; return
  fi
  # Note the task and route it (b -> Jas's Review tab, else Claude) so the Hub shows it was worked.
  # THE LINT GATE IN THE SESSION (Jas, 2026-10-19, "aim for 90%"): findings go back to the same session
  # for up to 2 rewrites before the note and the bridge see the answer (scripts/scope_gate.py).
  [ -s "$Q/out/$t.md" ] && timeout 12000 python3 scripts/scope_gate.py "$Q/out/$t.md" --task "$t" \
    --model "$(python3 -c "import sys;sys.path.insert(0,'scripts');import roles;print(roles.tag('$SCOPER','manager'))")" \
    --role "$HIVEMIND_ROLE" >> "$Q/progress.log" 2>&1
  python3 scripts/manager_note.py "$t" "workspace/run-logs/manager-queue/out/$t.md" >> "$Q/progress.log" 2>&1
  # THE BRIDGE, AUTOMATIC (t323, p58): a valid chunks block becomes branch bridge/<task>; --lint
  # refuses a broken scope back to the manager, --freeze merges a passing one into main by the
  # gates (D-35 amended), --record scores every outcome. bridge_trigger's freeze runs under a lock,
  # so two workers finishing together merge one after the other.
  if [ -s "$Q/out/$t.md" ]; then
    m=$(python3 -c "import sys;sys.path.insert(0,'scripts');import roles;print(roles.tag('$SCOPER','manager'))")
    timeout 1800 python3 scripts/bridge_trigger.py "$Q/out/$t.md" --task "$t" --model "$m" \
      --lint --freeze --record >> "$Q/progress.log" 2>&1
    echo "$(date -u +%H:%M:%SZ) BRIDGE $t rc=$?" >> "$Q/progress.log"
  fi
}

# ONE MANAGER CALL FOR SEVERAL TASKS (t1177): the batched prompt, one answer, then the answer is cut
# into out/<task>.md per task and each goes through manager_note and the bridge exactly as run_task's.
run_batch() {
  local ids="$*" first=$1
  trap 'python3 scripts/scoping_lanes.py release '"$ids" EXIT
  python3 scripts/scope_batch.py prompt $ids > "$Q/in/batch-$first.txt"
  echo "$(date -u +%H:%M:%SZ) START BATCH $ids station $STATION role $HIVEMIND_ROLE" >> "$Q/progress.log"
  budget=$(python3 -c "import sys;sys.path.insert(0,'scripts');sys.path.insert(0,'projects/local-inference');import roles,infer;print(infer.job_budget(roles.tag('$SCOPER',''),16000))" 2>/dev/null || echo 16000)
  timeout 5700 python3 scripts/ask_q16.py --prompt "$Q/in/batch-$first.txt" --out "$Q/out/batch-$first.md" \
    --budget "$budget" --max-escalations 0 --max-rounds 12 --timeout 5400 \
    --purpose "manager: batch $ids (top of QUEUE.md)" --task "$first" --role "$HIVEMIND_ROLE" $MODEL_ARG > "$Q/out/batch-$first.log" 2>&1
  rc=$?
  echo "$(date -u +%H:%M:%SZ) END BATCH $ids rc=$rc bytes=$(stat -c%s "$Q/out/batch-$first.md" 2>/dev/null || echo 0)" >> "$Q/progress.log"
  if [ "$rc" = 4 ] || [ "$rc" = 3 ]; then
    echo "$(date -u +%H:%M:%SZ) RETRY BATCH $ids later (rc=$rc)" >> "$Q/progress.log"
    sleep 60; return
  fi
  [ -s "$Q/out/batch-$first.md" ] || return
  done_ids=$(python3 scripts/scope_batch.py split "$Q/out/batch-$first.md" "$Q/out" $ids 2>>"$Q/progress.log")
  echo "$(date -u +%H:%M:%SZ) SPLIT BATCH $ids -> sections for: $done_ids" >> "$Q/progress.log"
  m=$(python3 -c "import sys;sys.path.insert(0,'scripts');import roles;print(roles.tag('$SCOPER','manager'))")
  for t in $done_ids; do
    # THE LINT GATE IN THE SESSION (Jas, 2026-10-19, "aim for 90%"): findings go back to the same session
    # for up to 2 rewrites before the note and the bridge see the answer (scripts/scope_gate.py).
    [ -s "$Q/out/$t.md" ] && timeout 12000 python3 scripts/scope_gate.py "$Q/out/$t.md" --task "$t" \
      --model "$(python3 -c "import sys;sys.path.insert(0,'scripts');import roles;print(roles.tag('$SCOPER','manager'))")" \
      --role "$HIVEMIND_ROLE" >> "$Q/progress.log" 2>&1
    python3 scripts/manager_note.py "$t" "workspace/run-logs/manager-queue/out/$t.md" >> "$Q/progress.log" 2>&1
    timeout 1800 python3 scripts/bridge_trigger.py "$Q/out/$t.md" --task "$t" --model "$m" \
      --lint --freeze --record >> "$Q/progress.log" 2>&1
    echo "$(date -u +%H:%M:%SZ) BRIDGE $t rc=$? (batch $first)" >> "$Q/progress.log"
  done
}

while [ ! -f "$Q/STOP" ]; do
  # THE ROLES TAB DECIDES WHO SCOPES (2026-10-20). manager-queue.service ran as "manager" whatever ROLES.json
  # said, so the Manager scoped with no `scoping` on its list. A loop whose role does not list it idles.
  if ! python3 -c "import json,sys;d=json.load(open('projects/local-inference/models/ROLES.json'));sys.exit(0 if any(t.get('task')=='scoping' for t in d['roles'].get('$SCOPER',{}).get('tasks',[])) else 1)" 2>/dev/null; then
    echo "$(date -u +%H:%M:%SZ) IDLE the $SCOPER role does not list scoping on the Roles tab" >> "$Q/progress.log"
    touch "$Q/progress.log"; sleep 120; continue
  fi
  live=$(python3 scripts/scoping_lanes.py live "$SCOPER" 2>/dev/null || echo 0)
  if [ "$SCOPER" = manager ]; then
    want=$(python3 scripts/scoping_lanes.py target 2>/dev/null || echo 1)
  else
    want=$(python3 scripts/scoping_lanes.py target "$SCOPER" 2>/dev/null || echo 0)
  fi
  if [ "$live" -ge "$want" ]; then
    # HEARTBEAT (2026-10-14): the scheduler grants scoping lanes only to a queue whose progress.log
    # moved in the last 15 min (dispatch_loop.scoping_waiting). Waiting on a grant of 0 wrote nothing,
    # so after a restart the log went stale, the grant stayed 0, and the queue waited for ever.
    touch "$Q/progress.log"
    sleep 30    # every granted lane is busy; re-check for a finished worker or a bigger grant
    continue
  fi
  t=$(python3 scripts/manager_next.py --scoper "$SCOPER")
  if [ -z "$t" ]; then
    echo "$(date -u +%H:%M:%SZ) IDLE nothing in QUEUE.md needs the manager" >> "$Q/progress.log"
    sleep 120    # idle: re-check QUEUE.md every 2 min, so a new task starts promptly (Jas, 2026-09-28)
    continue
  fi
  # THE MANAGER RUNS ON THE MAIN SERVER (Jas, 2026-09-28, later that night: station 2 became the
  # SENIOR role's server, dedicated to preflight -- "The Manager Q4 can go back to session review").
  # It yields to the main server's priority work below, as it did before station 2 existed.
  unset HIVEMIND_OLLAMA HIVEMIND_LEASE
  SERVER=http://localhost:11434; STATION=1; export HIVEMIND_ROLE=manager
  if [ "$SCOPER" != manager ]; then
    # ANOTHER ROLE'S LOOP: no station, no YIELD (both are the main server's); infer sends its calls to
    # wherever its model is served.
    export HIVEMIND_ROLE=$SCOPER; STATION=3
  fi
  # ONE PAUSE QUESTION, ASKED BY EVERY LOOP (Jas, 2026-10-30: "when I press pause it stops every model from working on
  # tasks"). This used to be inside `if [ "$SCOPER" != manager ]`, so the MANAGER loop never asked it and went on
  # scoping through a pause on both devices -- its own progress.log shows START t9580 while the auditor loop printed
  # PAUSED. must_hold answers for the production screen, for ALL devices, and for this role's device.
  if python3 -c "import sys;sys.path.insert(0,'scripts');import scheduler_commands as s;ok,why=s.must_hold(role='$SCOPER');print(why) if ok else None;sys.exit(0 if ok else 1)"; then
    echo "$(date -u +%H:%M:%SZ) PAUSED not starting $t" >> "$Q/progress.log"
    sleep 60
    continue
  fi
  # UNLESS THE SENIOR'S LIST SAYS SCOPING IS ITS WORK (Jas, 2026-10-01: he moved backlog, scoping
  # and scope check to the top of the Senior's list; the queue never followed). Then scoping runs
  # on the Senior's station 2 under its lease, and yields there only when the dispatcher writes
  # YIELD2 -- the Series limit handing the station to waiting preflights. task_routing decides.
  # Or SCOPE2 (2026-10-02): the dispatcher ranked Scoping as the Senior's next task this tick.
  [ "$SCOPER" = manager ] && st=$(python3 -c "import sys;sys.path.insert(0,'scripts');import task_routing as t,dispatch_loop as d;s=d.senior_station() if (t.scoping_station()=='senior' or d._scope2_fresh()) else None;print(f\"{s['endpoint']} {s['lease']}\" if s else '')" 2>/dev/null)
  # NO STATION, BUT THE SENIOR OWNS THE WORK (2026-10-06). With every role on Swift (vLLM) the
  # Senior has no Ollama station, so `st` is empty -- and the Manager, holding neither Scoping nor
  # Backlog, started nothing: no role scoped at all. The work belongs to whichever role's list has
  # it; infer.chat sends that role's calls to wherever its model is served (docs/ENGINES.md).
  [ "$SCOPER" = manager ] || st=""
  if [ "$SCOPER" = manager ] && [ -z "$st" ]; then
    own=$(python3 -c "import sys;sys.path.insert(0,'scripts');import task_routing as t;d=t.load();has=lambda r:any(x.get('task') in ('scoping','backlog') for x in t.tasks_of(d,r));print('senior' if has('senior') and not has('manager') else '')" 2>/dev/null)
    [ "$own" = senior ] && export HIVEMIND_ROLE=senior
  fi
  if [ -n "$st" ]; then
    export HIVEMIND_OLLAMA=${st%% *} HIVEMIND_LEASE=${st#* }
    SERVER=$HIVEMIND_OLLAMA; STATION=2; export HIVEMIND_ROLE=senior
    if [ -f "$Q/YIELD2" ] && [ $(( $(date +%s) - $(stat -c %Y "$Q/YIELD2") )) -lt 1800 ]; then
      echo "$(date -u +%H:%M:%SZ) YIELD2 the Senior's station goes to waiting preflights (Series); not starting $t" >> "$Q/progress.log"
      flock -n /tmp/dispatch_loop.lock timeout 900 python3 scripts/dispatch_loop.py \
        >> workspace/run-logs/dispatch-loop-cron.log 2>&1
      sleep 60
      continue
    fi
  fi
  # THE ROLE'S TASK LIST IS THE SWITCH (Jas, 2026-10-02: he emptied the manager's tasks in
  # ROLES.json and the queue kept running t458 -- this loop never read the list). This queue's work
  # is Scoping the Backlog, so a role with neither on its Models-tab list starts nothing (it was
  # "any task at all" until later that day). Re-checked every pass: no restart needed either way.
  n=$(python3 -c "import sys;sys.path.insert(0,'scripts');import task_routing as t;print(sum(x.get('task') in ('scoping','backlog') for x in t.tasks_of(t.load(),'$HIVEMIND_ROLE')))" 2>/dev/null)
  if [ "$n" = 0 ]; then
    echo "$(date -u +%H:%M:%SZ) OFF the $HIVEMIND_ROLE has neither Scoping nor Backlog in ROLES.json; not starting $t" >> "$Q/progress.log"
    sleep 120
    continue
  fi
  # THE PRODUCTION SCREEN'S PAUSE IS THE SWITCH TOO (Jas, 2026-10-02): the Pause/Resume button
  # (scheduler_commands, the flag the dispatcher reads) stops the manager from STARTING a job.
  # A job already running finishes; Stop on the same screen is what ends one.
  if python3 -c "import sys;sys.path.insert(0,'scripts');import scheduler_commands as s;sys.exit(0 if s.is_paused() else 1)" 2>/dev/null; then
    echo "$(date -u +%H:%M:%SZ) PAUSED the scheduler is paused (production screen); not starting $t" >> "$Q/progress.log"
    sleep 60
    continue
  fi
  # OUTAGE GUARD (2026-09-25): a reboot took Ollama down mid-queue and the loop ran 22 tasks
  # to "no answer" in a minute. Never START a task while the model server is unreachable.
  # ANY ENGINE (2026-10-06): ask infer whether the role's model has an answering server -- this
  # was `curl $SERVER/api/version`, which is Ollama's and says nothing about a model on vLLM.
  if ! python3 -c "import sys;sys.path.insert(0,'scripts');sys.path.insert(0,'projects/local-inference');import roles,infer;a,_=infer.resolve(roles.tag('$HIVEMIND_ROLE','')) ;sys.exit(0 if a.health() else 1)" 2>/dev/null; then
    echo "$(date -u +%H:%M:%SZ) WAIT model server unreachable; not starting $t" >> "$Q/progress.log"
    sleep 60
    continue
  fi
  # YIELD TO WAITING PRIORITY WORK (2026-09-26): dispatch_loop writes YIELD when preflights
  # queue behind the manager's model. Pause until its next tick takes the model; a flag older
  # than 30 min is ignored so a dead dispatcher cannot stop the manager for good.
  if [ "$STATION" = 1 ] && [ -f "$Q/YIELD" ] && [ $(( $(date +%s) - $(stat -c %Y "$Q/YIELD") )) -lt 1800 ]; then
    echo "$(date -u +%H:%M:%SZ) YIELD priority work is waiting on the model; not starting $t" >> "$Q/progress.log"
    # DO NOT JUST WAIT FOR CRON (2026-09-26): the dispatcher ticks every 10 min and a review
    # takes ~4, so the model sat idle ~6 min a cycle with the manager paused. Run a tick now;
    # the flock is cron's own, so this never overlaps a scheduled tick.
    flock -n /tmp/dispatch_loop.lock timeout 900 python3 scripts/dispatch_loop.py \
      >> workspace/run-logs/dispatch-loop-cron.log 2>&1
    sleep 60
    continue
  fi
  # CLAIM, THEN START THE WORKER IN THE BACKGROUND. A claim lost to a race just loops round.
  python3 scripts/scoping_lanes.py claim "$t" $$ || { sleep 2; continue; }
  # BATCH SCOPING (t1177): tasks that change the same files as $t join it in ONE Manager call, up to
  # scope_batch.BATCH_MAX. The others are claimed all-or-none; losing that race just scopes $t alone.
  # Station 2 stays one task at a time. Off switch: $Q/BATCH_OFF.
  batch="$t"
  if [ "$STATION" != 2 ]; then
    ids=$(python3 scripts/scope_batch.py pick "$t" 2>/dev/null || echo "$t")
    extra=$(echo "$ids" | tr ' ' '\n' | grep -vx "$t" | tr '\n' ' ')
    if [ -n "${extra// /}" ] && python3 scripts/scoping_lanes.py claim-batch $$ $extra; then
      batch="$t $extra"
    fi
  fi
  # Station 2 (an Ollama station shared one job at a time) stays serial: wait for its worker.
  if [ "$STATION" = 2 ]; then ( run_task "$t" ); else
    if [ "$batch" = "$t" ]; then run_task "$t" & else run_batch $batch & fi
    # the claims name the WORKER's pid, so a killed worker's claims are seen as stale
    for x in $batch; do echo "$! $HIVEMIND_ROLE" > "$Q/claims/$x"; done
    sleep 5
  fi
done
wait    # let running workers finish their scope before the loop exits
echo "$(date -u +%H:%M:%SZ) STOPPED (STOP file)" >> "$Q/progress.log"
