# Cloud progress log

One line per task: tNNN | done / already-green / needs-live-service / blocked | branch | reason

t296 | done | cloud/t296 | deleted retired census block from check_drifting_claims; oracle 8/8 green; known collateral: test_check_workspace.py census assertions = t899's scope
t478 | done | cloud/t478 | scope_lint oracle-gate evidence now appends repair sentence; oracle2 3/3 green; oracle1 already green on main; 17 consumer suites pre-red on main, unchanged
t645 | done | cloud/t645 | write_deliverable + deliverable_gate added to scripts/probe.py; oracle 15/15 green; round-loop wiring impossible here (brief's probe.py not in export, noted in task file)
t675 | already-green | cloud/t675 | fix landed 2026-10-05 (write_tick_row); wrote pinning oracle test_t675_slim_tick_rows.py, 16/16 green on arrival
