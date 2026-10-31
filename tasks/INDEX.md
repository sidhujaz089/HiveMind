# Harness tasks for the cloud

**Also: tasks/MECHANICAL-FIXES.md: eight harness checks (mf1 to mf8), in order. Do these first.**

Refreshed 2026-10-31 from the origin repository's main. Tasks closed since the last export are gone; t296, t478, t645, t675 and t697 are DONE on their cloud/ branches and awaiting review, so do not redo them. t693 is back: projects/app-repo/feedback.py, the import it was blocked on, is now in the export.

| Task | Title | Oracle |
|---|---|---|
| t693 | Scheduler bar should say who paused the scheduler and why (audit s02; left for next sessio | test_scheduler_bar_pause_attribution.py |
| t702 | coder.py: a repeat request for a file shown as a SYMBOL SLICE is served lines 1..260,000 a | test_coder_repeat_names_symbols.py |
| t712 | Scope RCA: oracle-green | test_coder_serves_file_under_edit.py, test_scope_lint_evidence.py |
| t713 | Scope RCA: oracle-crash | test_scope_lint_prose_pointer.py |
| t723 | Make check_workspace projects census refuse PASS over an empty population | (write one) |
| t732 | Add the oracle-authoring node to FLOW.json and require a flow node in every frozen scope | test_scope_lint_frozen.py |
| t754 | Migrate direct Ollama call sites onto infer.chat with a ratchet gate | test_call_sites_census.py |
| t755 | Scope RCA: oracle-red-reason | test_scope_lint_stored_expectation.py |
| t787 | Fail check_workspace while nothing loads docs/PREFERENCES.md | test_preference_loaders_wired.py |
| t791 | Session review cover search: a task that quotes a finding outranks the task that covers it | test_session_review_cover_ranking.py |
| t798 | Add a 'control' node kind to FLOW.json + refuse unregistered hook edits | test_flow_control_nodes.py, test_settings_flow_link.py |
| t800 | Require a Flow node for every hook in tracked settings, with a red fixture | test_check_workspace_hooks.py, test_hook_flow_link.py |
| t804 | Record every harness monitor event at the monitor endpoint, with a red fixture | (write one) |
| t853 | Add a load-sensitivity declaration to the sandbox baseline contract | test_sandbox_baseline_load.py |
| t899 | HARNESS: test_check_workspace.py asserts deleted code exists; not in file scope | test_check_workspace.py, test_t899_census_assertions_gone.py |
| t980 | HARNESS: coder.py output budget consumes 96.7% of context window, starving the prompt+file | test_coder_slice_fits_reserve_comes_from_the_window.py, test_hook_flow_link.py |
| t989 | HARNESS: coder.py requests frozen files and demands full re-emission under context pressur | test_askmemory_proposal_lands_in_repo.py, test_coder_scope_apply.py, test_coder_slice_room.py |
| t991 | HARNESS: coder.py passes the whole target file, exhausting the context window before a mul | test_coder_records_the_starting_excerpt.py, test_finding_anchors.py |
| t1000 | HARNESS: coder.py fills context with 62-suite baseline list, starving the model's patch-ou | test_coder_baseline_report_is_bounded.py |
| t1003 | HARNESS: coder.py accepts truncated model output (reasoning=on starves generation budget)  | test_coder_truncated_reply.py |
| t1007 | HARNESS: coder.py slice-fit guard refuses to serve the file being edited (self-referential | (write one) |
| t1019 | Gate session-review RCA on a computed same-session-resolution check | test_session_review_resolution.py |
| t1021 | Write a bridge-proof ledger row at the MCP call-log append point | test_workspace_mcp_bridge_proof.py |
| t1023 | HARNESS: Target suite in baseline-failing set makes acceptance unsatisfiable | test_coder_target_in_baseline.py |
| t1024 | Scope RCA: oracle-dirty | (write one) |
| t1027 | Compute STATE.md status claims from the machine record; ban prose gate states | test_state_claims.py |
| t1029 | Generate STATE.md's gate-closure marker from the ledger and refuse hand-typed markers | test_gate_marker_generated.py |
| t1033 | Make bridge.py the only definition of 'answer has a chunks block' | test_chunks_predicate_selfcheck.py, test_chunks_predicate_single_source.py |
| t1062 | Gate every coder patch on the target file's computed indent profile | test_coder_indent_profile.py |
| t1087 | Emit a taskstore/ledger row the instant an item is deferred, not forced | test_t1087_deferral_row.py |
| t1088 | Gate a block-level finding to green only via recorded exception or fix | test_library_register_evasive_rewrite.py |
| t1107 | Make owner-declared priorities a required tracked brief + preflight gate | test_preflight_owner_priority.py |
| t1109 | HARNESS: Stop condition treats 'target suite fails' and 'unrelated suite fails' as identic | test_coder_stop_signature.py |
| t1121 | HARNESS: coder.py sandbox() crashes on transient files (lease slots) vanishing during copy | test_coder_sandbox_transient_copy.py |
| t1134 | HARNESS: coder.py dispatched while a concurrent copy held the job lock | (write one) |
| t1137 | HARNESS: coder.py sandbox() crashes on a file that vanishes mid-copytree | test_coder_sandbox_survives_vanishing_file.py |
| t1142 | HARNESS: Stale coder.py process (pid 1406609) holds lease, blocking re-dispatch of t1067 | (write one) |
| t1143 | HARNESS: coder.py refuses dispatch when a prior process (pid 1534309) holds the per-brief  | (write one) |
| t1144 | HARNESS: coder.py concurrency guard blocks re-dispatch when a prior-process lease is stale | (write one) |
| t1147 | HARNESS: coder.py lease block prevents dispatch; no auto-retry or queue-wait path | test_hub_moves2_moved.py |
| t1149 | HARNESS: coder.py refuses dispatch when a stale/active PID holds the same brief's job leas | (write one) |
| t1153 | session_end: fail fast, resume instead of rerun, checks through the day, and a run log --  | test_run_journal_progress.py |
| t1157 | HARNESS: JSON edit protocol rejects Python dict output; f-string quoting in brief causes m | (write one) |
| t1159 | HARNESS: preflight_brief.py dispatches work to a file with a pre-existing syntax error (co | test_preflight_syntax.py |
| t1162 | HARNESS: JSON edit protocol cannot express large verbatim code blocks containing f-strings | (write one) |
| t1165 | HARNESS: HTTP 400 Bad Request kills dispatch after six successful model responses | (write one) |
| t1170 | HARNESS: coder stops on non-identical test failures classified as identical | (write one) |
| t1172 | HARNESS: Sandbox runs non-target, non-protected suite as a gate | (write one) |
| t1174 | HARNESS: Stop condition treats all TESTS-FAILED as identical, halting a converging model | (write one) |
| t1182 | No role does rca | (write one) |
| t1209 | Scope RCA: oracle-live-state | test_scope_lint_live_state_sources.py |
| t1224 | Gate every model-role assignment on a qualification record for its serving tag | test_role_qualification_gate.py |
| t1225 | Fix scripts/tests/test_check_workspace_standing_processes_v2.py: 0/5 on main 2026-10-15 (t | (write one) |
| t1231 | HARNESS: coder.py dispatch refused: stale process holds the same job's lease | (write one) |
| t1233 | HARNESS: coder.py duplicate-dispatch detection produces stale positive match due to /proc  | test_coder_twin_race.py |
| t1240 | Temp-file garbage collector for both machines. GX10: ~/.cache/hivemind-tmp reached 47 GB a | (write one) |
| t1250 | HARNESS: coder.py: no path to accept line-range edits when file exceeds output budget | test_coder_line_range_path_after_size_refusal.py |
| t1264 | HARNESS: Dispatched dependent chunk (p551-t1115-feedback) before its prerequisite (hub_com | (write one) |
| t1268 | HARNESS: coder.py accepts patches to files outside the brief's declared scope | test_coder_patch_scope.py |
| t1278 | HARNESS: Dispatch wall-clock timeout kills long-reasoning model before done_reason=length  | test_coder_timeout_outcome.py |
| t1286 | Decide: If you want the harness fully settled first, the second bullet is the one I'd ch | test_gpt_oss_high_budget_floor.py |
| t1299 | HARNESS: HTTP 400 on attempt 3 after two successful model responses | (write one) |
| t1323 | HARNESS: Test failure detail truncated to empty string in dispatch result record | test_coder_failure_detail.py |
| t1330 | HARNESS: window-full: target suite excluded from prompt, model implements from prose contr | (write one) |
| t1337 | Record why a test suite failed, not that it failed -- coder.py's job log has no failure de | (write one) |
| t1344 | Make the harness skill registry a derived projection of docs/skills, with a per-session le | test_precommit_registry_gate.py, test_skill_registry_reconciliation.py |
| t1358 | HARNESS: coder.py accepts files outside the brief's declared scope | test_coder_scope.py |
| t1363 | Require a green oracle before a change is recorded as delivered | test_restart_stale_dip.py |
| t1365 | HARNESS: dispatch requests 4 files when brief specifies 1; line-range edit cuts multi-line | (write one) |
| t1367 | Give every RCA dispatch a terminal ledger row naming rc, elapsed, rounds and the empty-rep | (write one) |
| t1374 | HARNESS: Brief for t800 omits all source context from a 151KB target file | test_scope_lint_no_source.py |
| t1376 | HARNESS: Harness counts known-defect suite (test_failure_schema_status_currency.py) as a b | (write one) |
| t1378 | HARNESS: coder.py rejects large-file edits without offering a line-range path | (write one) |
| t1399 | Add ROSTER-EXHAUSTED decision + RELEASED ledger row (never brief free-text) | test_dispatch_loop_released_event.py, test_supervise_roster_exhausted.py |
| t1404 | Gate: handover-deferred intents must resolve to a task number | test_declared_intent_handover_deferral.py |
| t1411 | HARNESS: coder.py dispatch times out on large-file structural edits (105 min, no output) | (write one) |
| t1421 | Gate model-output parsing to jsonx: one approved call site, red fixture | test_jsonx_allowlist.py |
| t1423 | A session review whose transcript is gone moves itself to failed/ instead of being retried | test_session_review_missing_transcript.py |
| t1425 | HARNESS: coder.py syntax-error feedback omits surrounding indentation context, causing inf | test_coder_syntax_context.py |
| t1429 | Add session_end hook: scan handover for deferred actions, mint tasks | (write one) |
| t1432 | HARNESS: Model times out emitting edits to 1500+ line file; output budget consumed by reas | test_coder_output_budget_clock.py |
| t1445 | Require an 'agreements -> tNNN' section in the handover contract, enforced by session_end. | test_session_end_agreements.py |
| t1451 | HARNESS: num_predict set to 96.7% of context window, causing HTTP 400 on every dispatch | (write one) |
| t1457 | Probe freshness gate: emit STALE-ENV and refuse new-finding reports when env HEAD != sourc | (write one) |
| t1463 | HARNESS: Symbol-slice window-full path cannot provide enough context for multi-site edits  | test_coder_region_request.py |
| t1471 | Stop dispatching a script-review whose path+sha256 already has a verdict | (write one) |
| t1472 | HARNESS: Brief describes insertion point in prose inside a large function; model cannot lo | (write one) |
| t1475 | HARNESS: Sandbox refuses to provide target file to model; task becomes unexecutable | (write one) |
| t1479 | HARNESS: coder.py Truncated recovery inherits the full dispatch timeout, causing recovery  | (write one) |
| t1481 | HARNESS: Edit-protocol parser silently discards model output as 'no ops' (1811 and 1517 to | (write one) |
| t1488 | Gate every collected test against writing into the live repo tree | (write one) |
| t1492 | HARNESS: Output budget 57138 tokens on a single-file edit starves the answer via shared-bu | (write one) |
| t1513 | Run preflight inside the scoping session: brief-unsatisfiable objections go back to the sc | (write one) |
| t1516 | test_coder_branch_delivery collides with itself: it uses one fixed brief label (brief-6e09 | (write one) |
| t1544 | Detect a stale .git/index.lock: one left at 2026-10-21 02:00:58 (no git process alive) blo | test_hourly_git_lock.py |
| t1560 | A model's run_command switched the live checkout's branch and applied an unreviewed patch  | test_console_run_command_guard_git.py |
| t1576 | HARNESS: modellease.py crashed: TypeError at modellease.py:357 | test_modellease_slot_paths_model_arg.py |
| t2114 | Certify a script by its oracle, not by a model's opinion | (write one) |
| t2151 | One shared, content-hash-keyed sweep result store so coder.py's 373-suite sandbox baseline | test_sweep_result_store.py |
| t3947 | The role gate told me to grant a permission the role already had | (write one) |
| t4383 | Two agents merging to main collide on the live checkout's runtime ledgers and on each othe | (write one) |
| t9523 | HUB: a 'Serve Swift only' button calling scheduler_commands.swift_on() | (write one) |
| t9569 | Finish the scheduler redesign: one admission layer (measured lanes, measured build charge, | (write one) |
| t9574 | Retire this sprint's lanes 1 and 2 and the plan's A/B labels when the scheduler work ends | (write one) |
| t9578 | merge_reviewed.py --agent defaults to claude-code/opus-5.5, so other models' merges are at | (write one) |
| t9580 | The pre-commit gate cannot tell a RED suite from one that did not finish: rc 124 is report | (write one) |
| t9581 | session_end needs a seconds-long preflight that reports every cheap blocker before the 10- | (write one) |
| t9582 | Derive the pre-commit gate's timeout from close-out timings once the ledger has data (1800 | (write one) |

## New since the last export (not vetted the same way -- write the oracle first, and stop if the task needs a live service)

| Task | Title | Oracle |
|---|---|---|
| t9586 | Red on main: test_manager_outcomes.py | (write one) |
