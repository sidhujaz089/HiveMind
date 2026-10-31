# Cloud progress log

One line per task: tNNN | done / already-green / needs-live-service / blocked | branch | reason

t296 | done | cloud/t296 | deleted retired census block from check_drifting_claims; oracle 8/8 green; known collateral: test_check_workspace.py census assertions = t899's scope
t478 | done | cloud/t478 | scope_lint oracle-gate evidence now appends repair sentence; oracle2 3/3 green; oracle1 already green on main; 17 consumer suites pre-red on main, unchanged
t645 | done | cloud/t645 | write_deliverable + deliverable_gate added to scripts/probe.py; oracle 15/15 green; round-loop wiring impossible here (brief's probe.py not in export, noted in task file)
t675 | already-green | cloud/t675 | fix landed 2026-10-05 (write_tick_row); wrote pinning oracle test_t675_slim_tick_rows.py, 16/16 green on arrival
t693 | blocked | cloud/t693 | oracle imports projects/app-repo/feedback.py which is not in the export (ModuleNotFoundError); red for the wrong reason, nothing built
t697 | done | cloud/t697 | load_all/find skip-with-report vanished files; new oracle red->green 8/8; 39 taskstore suites no difference vs main
mf1 | done | cloud/mf1 | merge_reviewed.require_oracle_for_guarded + scripts/text_assert_lint.py; new oracle red (2 failed) -> green (0 failed, 13/13); 92 live offenders reported
mf3 | done | cloud/mf3 | role_gate check-lane subcommand + hook delegates (guarded: role_gate.py, hooks/commit-msg) + regex_dup_lint.py; oracle 8 failed -> 0 failed; 21 live dup regexes reported
mf2 | done | cloud/mf2 | hook_shell_lint.py (silent fallbacks + phantom subcommands) wired into pre-commit (guarded: hooks/pre-commit); oracle 1 failed -> 0 failed (12/12); 9 live offenders reported
mf5 | done | cloud/mf5 | runtime-files registry + runtime_files.py + pre-commit refuse-stage (guarded: hooks/pre-commit) + check_workspace check + .gitignore; oracle red on main (no module) -> 15/15 green; 54-suite sweep: no other delta
mf4 | done | cloud/mf4 | state_path_gate.py + pre-commit wiring (guarded: hooks/pre-commit) + 4 run-logs writers report-not-create (guarded: dispatch_loop.py); oracle 3 failed on main -> 0 failed; 77-suite sweep: no other delta; JOBLOGS/JOB_TMP boot-dir question left for reviewer
mf6 | done | cloud/mf6 | merge_changelog/replay_flow/refresh_generated in merge_reviewed; FLOW conflicts merge as data, registries regenerate post-merge; oracle 3 failed -> 0 failed (16/16); test_merge_reviewed 0->0
mf7 | done | cloud/mf7 | lanes.py poster()/resolve_lane(): poster stamp on every row, HIVEMIND_LANE identity gates lane args; oracle 7 failed -> 0 failed (10/10); lanes/role_gate suites unchanged
mf8 | done | cloud/mf8 | lanes.py quotes_evidence/require_evidence/show_answered_for; oracle 6 failed -> 0 failed (10/10); known consequence: test_lanes_board bare-'yes' answer now refused (pre-mf8 contract, reviewer updates fixture)
