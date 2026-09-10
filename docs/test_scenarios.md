# KTV Optimizer — Test Scenarios and Desired Outcomes

This document defines black-box scenarios for the operational pipeline
(`OperationalSnapshotProcessor` + `SnapshotReducer`). Each scenario states the
business question, the exact inputs to feed, the behavior the pipeline should
show, and the **desired outcome** expressed as concrete assertions on the run
output files.

Reference for how the pipeline currently behaves:
[`docs/PIPELINE_DATA_FLOW_REVIEW.md`](PIPELINE_DATA_FLOW_REVIEW.md).

## How to run a scenario

The scenarios follow the same harness as
[`utils/test_real_metrics_scenarios.py`](../utils/test_real_metrics_scenarios.py):

```bash
PYTHONPATH=src /home/nguyenquocphu/.venv/bin/python \
  -m ktv_optimizer.application.process_snapshot \
  --snapshot-id TEST-S0N \
  --snapshot-time <TIME> \
  --maintenance <events.csv> \
  --roster <roster.csv> \
  --boundary data/boundary_2026-07-31.geojson \
  --runtime-dir data/runtime_scenarios \
  --output-root artifacts/scenario_runs \
  --mode task_location
```

Each snapshot's outputs are written to
`artifacts/scenario_runs/<RUN_ID>/`:

| File | What to check in these scenarios |
|---|---|
| `jobs.csv` | Canonical active jobs after the event batch |
| `completed_jobs.csv` | Only jobs with an explicit terminal status in this run |
| `changes.csv` | `ADDED` / `UPDATED` / `COMPLETED` vs previous checkpoint |
| `conflicts.csv` | Audit-logged inconsistencies and their resolution |
| `assignments.csv` | `SYSTEM_FIXED` + locked + `OPTIMIZER_V0` plan |
| `unassigned.csv` | Jobs not assigned and the exact reason |
| `routes.csv` | Stop order and Haversine ETA per KTV |
| `technician_job_queue.csv` | `IN_PROGRESS` / `NEXT` / `QUEUED` / `PAUSED` / `QUEUED_REVIEW` |
| `summary.json` | Counts + `evaluation_metrics` (planned/completed SLA, distance, wait, planning time) |

## Conventions used in the inputs

- **Roster is a test proxy.** There is no real shift roster yet; `EMP_ACCOUNT`,
  GPS and shift times are simulated from historical data. See
  `utils/real_data_e2e.py` for how the proxy is built.
- **Statuses and timestamps are simulated; checklist IDs and addresses are
  real.** The scenario tables use real `CHECKLIST_ID`s extracted from
  `data/QOS_MAINTENANCE_utf8.csv` (all `CASE_TYPE = MAINTENANCE`, so
  `priority = 2`, `service_minutes = 60`, `sla_minutes = 600` →
  `due_at = created_at + 10h`). The `CHECKLIST_STATUS`, `CREATE_DATE` and
  `EMP_ACCOUNT` values below are crafted for the scenario, following the
  established pattern in `utils/test_real_metrics_scenarios.py`.
- Where a real checklist of a given status does not exist in the source (e.g.
  "Đã phân công" in branch HNI_05), the scenario reuses a real ID from the same
  branch and only overrides the status field. This is marked `*simulated*`.
- Events must be sent in time order. A checklist absent from a later batch
  stays active (absence ≠ completion).

Real IDs used below (branch / source status):

| ID | Branch | Source status | Source `EMP_ACCOUNT` |
|---|---|---|---|
| `1482626336` | HNI_05 | Chưa phân công | — |
| `1481808376` | HNI_05 | Đang xử lý | `TIN0502.QUANNC` |
| `1482741226` | HNI_05 | Đang xử lý | `TIN0503.HOATTV` |
| `1484629596` | HNI_05 | Đã xử lý và đang theo dõi | `TIN0501.DAITV2` |
| `1484453346` | HNI_05 | Tạm dừng chờ xử lý | `TIN0501.HUUNV` |
| `1484520256` | HTH | Đã phân công | `HTHTI.TIENTN4` |

---

## Scenario matrix

| ID | Scenario | Primary statuses exercised | Key conflict/metric expected |
|---|---|---|---|
| S01 | Mixed statuses in one batch | Chưa phân công, Đã phân công, Đang xử lý | None |
| S02 | Batch with no finished checklist | all active | `completed_jobs.csv` empty |
| S03 | Already-late job (due < planning time) | Chưa phân công | routed first, planned SLA miss |
| S04 | SLA-first routing beats distance | Chưa phân công | route order by `due_at`, not proximity |
| S05 | Same-due jobs tie-break | Chưa phân công | order by priority, then checklist ID |
| S06 | System job on off-shift/unavailable KTV | Đã phân công | `SYSTEM_ASSIGNED_TO_UNAVAILABLE_TECHNICIAN` |
| S07 | Multiple busy jobs on one KTV | Đang xử lý | `TECHNICIAN_MULTIPLE_BUSY_JOBS` |
| S08 | System overrides previous assignment | Đã phân công | `SYSTEM_ASSIGNMENT_OVERRIDE` |
| S09 | Empty event batch | (none) | no `COMPLETED`, active state retained |
| S10 | Missing location across modes | Chưa phân công | `NO_COMPATIBLE_TECHNICIAN` vs penalty assign |
| S11 | Shift capacity overflow | Chưa phân công | `SHIFT_CAPACITY_EXCEEDED` |

---

## S01 — Two checklists with different statuses in one batch

**Business question.** When one batch mixes "Chưa phân công" (optimizer must
assign) and "Đã phân công" (system already fixed) — plus one "Đang xử lý" (KTV
busy) — does each get the right treatment without interfering with the others?

**Inputs.** One roster KTV `TIN0501.DAITV2` (branch HNI_05, shift
06:00–22:00). Single snapshot `TEST-S01` at `2026-06-30 08:00`:

| CHECKLIST_ID | Status | EMP_ACCOUNT |
|---|---|---|
| `1482626336` | Chưa phân công | — |
| `1484520256` *simulated* | Đã phân công | `TIN0501.DAITV2` |
| `1481808376` *simulated* | Đang xử lý | `TIN0501.DAITV2` |

**Expected pipeline behavior.**

1. `1482626336` enters greedy assignment (it is `Chưa phân công`).
2. `1484520256` becomes a `SYSTEM_FIXED` decision that keeps its `EMP_ACCOUNT`;
   it bypasses compatibility/capacity but still gets routed.
3. `1481808376` claims the KTV as `BUSY`; it consumes capacity
   (`initial_workloads += 60`) but is **not** part of assignments/routing.
4. The busy job and the fixed job both constrain the KTV's remaining capacity
   before the optimizer considers the unassigned job.

**Desired outcome (assert).**

- `assignments.csv` contains exactly 2 rows: `1484520256` with
  `source = SYSTEM_FIXED` and `1482626336` with `source = OPTIMIZER_V0`, both
  on `TIN0501.DAITV2`.
- `routes.csv` contains a route for `TIN0501.DAITV2` with sequence
  `1 → 2` covering both assigned jobs; `1481808376` is **not** in the route.
- `jobs.csv` contains all 3 jobs (active); `1481808376` keeps its
  `EMP_ACCOUNT`.
- `conflicts.csv` is empty.
- `technician_job_queue.csv`: `1481808376` is `IN_PROGRESS`; the two planned
  jobs are `NEXT`/`QUEUED`; `queued_job_count` excludes the `IN_PROGRESS` job.
- `summary.json.evaluation_metrics.planned_sla_on_time_rate_percent` is computed
  over all 3 jobs that have a `due_at`.

**Notes / gaps.** A system-fixed or busy job is counted in planned SLA even
though it is not routed; that is intended denominator behavior. If the KTV
cannot physically fit all jobs in shift, no feasibility warning is produced
today (see S11).

---

## S02 — Batch with no finished checklist

**Business question.** If a batch contains **only active statuses** and zero
terminal events, the run must not invent completions, and every job must survive
to the next snapshot.

**Inputs.** Branch HNI_05, KTV `TIN0501.DAITV2`. Snapshot `TEST-S02` at
`2026-06-30 08:00`:

| CHECKLIST_ID | Status | EMP_ACCOUNT |
|---|---|---|
| `1482626336` | Chưa phân công | — |
| `1482741226` | Đang xử lý | `TIN0501.DAITV2` |
| `1484629596` | Đã xử lý và đang theo dõi | `TIN0501.DAITV2` |
| `1484453346` | Tạm dừng chờ xử lý | `TIN0501.DAITV2` |

**Expected pipeline behavior.** All four are active. The optimizer assigns only
`1482626336`. Busy/follow-up/paused jobs are tracked in state but excluded from
assignment/routing. No job is terminal, so `completed_jobs.csv` is empty.

**Desired outcome (assert).**

- `completed_jobs.csv` is **empty** and `changes.csv` has **no `COMPLETED`**
  row; only `ADDED` (or nothing on replay).
- `jobs.csv` contains all 4 jobs; `summary.json.active_jobs = 4`.
- `assignments.csv` has 1 row (`1482626336 → OPTIMIZER_V0`); the other three
  are absent from assignments (they are `SYSTEM_ACTIVE`/paused, not assigned).
- `summary.json.evaluation_metrics.completed_sla_on_time_rate_percent` is
  `null` (no completion to evaluate), while
  `planned_sla_on_time_rate_percent` is still computed over all active jobs
  with a due date.
- `technician_job_queue.csv` shows `IN_PROGRESS` = follow-up job,
  `PAUSED` = `1484453346`, plus the queued planned job.
- On the next snapshot, all 4 jobs are still present in the checkpoint.

**Notes / gaps.** This scenario pins the "absence ≠ completion" rule: only
`Đóng checklist` / `Đã xử lý` produce `COMPLETED`. `Đã xử lý hoàn tất qua
phone` is currently **not** a terminal status (it is filtered out), which is a
business decision to confirm.

---

## S03 — Already-late job (due time already passed at planning time)

**Business question.** What happens when a job's SLA deadline is already in the
past when the plan is built? It should surface prominently (earliest due), be
counted as missed, and still be dealt with.

**Inputs.** Branch HNI_05, KTV `TIN0501.DAITV2`, snapshot `TEST-S03` at
`2026-06-30 08:00`. `CREATE_DATE` is simulated so that `due_at` is before the
snapshot:

| CHECKLIST_ID | Status | CREATE_DATE | due_at (create + 10h) |
|---|---|---|---|
| `1482626336` | Chưa phân công | `2026-06-29 21:00` | `2026-06-30 07:00` ← **past** |
| `1484629596` *simulated* | Chưa phân công | `2026-06-30 04:00` | `2026-06-30 14:00` (future) |

**Expected pipeline behavior.** V0 has **no SLA-feasibility hard rule**, so the
late job is not rejected: it is assigned and routed, and because routing is
SLA-first (`due_at` earliest wins) it becomes the first stop.

**Desired outcome (assert).**

- Both jobs are assigned (`OPTIMIZER_V0`) and both appear in `routes.csv`.
- `routes.csv` sequence: `1482626336` is stop 1 (earliest `due_at`), the other
  is stop 2.
- `planned_sla_on_time_rate_percent = 0%`: the late job's
  `ESTIMATED_FINISH > due_at`, and even the second job is evaluated against its
  own due.
- `changes.csv` shows `ADDED` for both; no conflict is raised.

**Notes / gaps.** Documented gap (audit P0 #6 in the review doc): there is no
pre-scheduling check that rejects/repairs already-late jobs, no predicted
lateness warning, and no shift-end feasibility check. This scenario is the
regression that guards the behavior until a feasibility gate is added.

---

## S04 — SLA-first routing beats distance

**Business question.** Routing rank is `due_at → priority → distance →
checklist_id`. Verify a job with an earlier deadline is visited first **even if
it is geographically farther**, i.e. deadline dominates distance.

**Inputs.** Branch HNI_05, two KTVs (`TIN0501.DAITV2`, `TIN0502.QUANNC`),
snapshot `TEST-S04` at `2026-06-30 08:00`. `CREATE_DATE` simulated so the
farther job has the earlier deadline:

| CHECKLIST_ID | Status | CREATE_DATE | due_at |
|---|---|---|---|
| `1482626336` | Chưa phân công | `2026-06-30 02:00` | `12:00` |
| `1482741226` | Chưa phân công | `2026-06-30 01:00` | `11:00` ← earlier due |

After checking both job GPS centroids, confirm that `1482741226` is the one
farther from `TIN0501.DAITV2`'s starting point (if not, swap the two
`CREATE_DATE`s).

**Expected pipeline behavior.** Greedy assigns both to one KTV (or the nearest),
and the router picks stops by `due_at`, not by nearest-neighbour distance.

**Desired outcome (assert).**

- The job with `due_at 11:00` appears at a lower `SEQUENCE` in `routes.csv`
  than the one with `due_at 12:00`, regardless of which is closer to the KTV.
- The second stop's `LEG_DISTANCE_KM` equals the true distance between the two
  stops, not the distance from the depot (documents that travel is chained).
- This holds for every KTV route in the run.

**Notes / gaps.** This is the documented behavior ("SLA-first greedy route"),
not nearest-neighbour despite the class name. It also documents that travel is
never added to capacity/workload (audit P1 #6).

---

## S05 — Same-due jobs: priority and checklist-ID tie-break

**Business question.** When two jobs share the same `due_at`, the router must
break the tie by `priority` (1 = highest), then `distance`, then
`checklist_id`. Real data has only `CASE_TYPE = MAINTENANCE`, so two real jobs
tie on priority and resolve by `checklist_id`.

**Inputs.** Branch HNI_05, KTV `TIN0501.DAITV2`, snapshot `TEST-S05` at
`2026-06-30 08:00`, both jobs with the **same** `CREATE_DATE` (hence same
`due_at`):

| CHECKLIST_ID | Status | CREATE_DATE | due_at |
|---|---|---|---|
| `1482626336` | Chưa phân công | `2026-06-30 03:00` | `13:00` |
| `1482741226` | Chưa phân công | `2026-06-30 03:00` | `13:00` |

**Expected pipeline behavior.** Same due, same priority (both MAINTENANCE=2), so
the router falls through to `checklist_id`:
`1482626336 < 1482741226` → the former is stop 1.

**Desired outcome (assert).**

- `routes.csv` sequence is deterministic and stable across runs:
  `1482626336` (sequence 1), `1482741226` (sequence 2).
- Re-running the same snapshot with `--reset-state` produces the identical
  sequence (reproducibility).

**Notes / gaps.** To exercise the `priority` tie-break, a job with a different
`CASE_TYPE` (e.g. `VẬT LÝ`, priority 1) must be injected — real CSV contains
only `MAINTENANCE`. That is a future scenario once the taxonomy lands.

---

## S06 — Time conflict: system-assigned job on an off-shift / unavailable KTV

**Business question.** If the source system has already assigned a checklist to
a KTV who is `OFF_SHIFT` at snapshot time (or `WORK_STATUS = UNAVAILABLE`),
should the plan keep the job, and how is the conflict surfaced?

**Inputs.** Branch HNI_05, KTV `TIN0501.DAITV2` with
`SHIFT_START = 2026-06-30 08:00`, `SHIFT_END = 2026-06-30 22:00`. Snapshot
`TEST-S06` at `2026-06-30 07:00` (before shift start):

| CHECKLIST_ID | Status | EMP_ACCOUNT |
|---|---|---|
| `1484520256` *simulated* | Đã phân công | `TIN0501.DAITV2` |

**Expected pipeline behavior.** `derive_pre_optimization_states` marks the KTV
`OFF_SHIFT` (snapshot before `shift_start`). The reserved claim on the
off-shift KTV raises `SYSTEM_ASSIGNED_TO_UNAVAILABLE_TECHNICIAN`. The system
assignment is kept (`KEEP_SYSTEM_JOB_AND_REQUIRE_REVIEW`); the KTV is excluded
from the assignable set.

**Desired outcome (assert).**

- `conflicts.csv` has one row with `code = SYSTEM_ASSIGNED_TO_UNAVAILABLE_TECHNICIAN`
  for `1484520256`.
- `assignments.csv` still contains the `SYSTEM_FIXED` row (system wins), but
  `routes.csv` has **no route** for it (unavailable KTV is not assignable), or
  it appears in `unassigned.csv` — the exact split is pinned by the run.
- `technicians.csv` shows `WORK_STATUS = OFF_SHIFT`.
- Same expectations when instead `WORK_STATUS = UNAVAILABLE` is set on the
  roster row at a time inside the shift.

**Notes / gaps.** System-fixed jobs bypass availability/capacity by design and
are only logged, never rejected (audit P1 #4). Confirm with business whether
such assignments should instead be blocked.

---

## S07 — Time conflict: two jobs simultaneously "Đang xử lý" on one KTV

**Business question.** One KTV cannot genuinely be mid-service on two jobs at
the same time. How does the pipeline detect and resolve it?

**Inputs.** Branch HNI_05, KTV `TIN0501.DAITV2`, snapshot `TEST-S07` at
`2026-06-30 08:00`:

| CHECKLIST_ID | Status | EMP_ACCOUNT |
|---|---|---|
| `1481808376` | Đang xử lý | `TIN0501.DAITV2` |
| `1482741226` *simulated* | Đang xử lý | `TIN0501.DAITV2` |

**Expected pipeline behavior.** Both jobs claim the KTV `BUSY`; `busy_claims`
length > 1 triggers `TECHNICIAN_MULTIPLE_BUSY_JOBS`. One job becomes the
`current_job_id` (`IN_PROGRESS`), the other is flagged
`QUEUED_REVIEW` in the queue. Both still consume capacity (60 min each).

**Desired outcome (assert).**

- `conflicts.csv` has `TECHNICIAN_MULTIPLE_BUSY_JOBS` with
  `incoming_value` = `1481808376|1482741226` (sorted).
- `technician_job_queue.csv`: exactly one `IN_PROGRESS` row and one
  `QUEUED_REVIEW` row; `technicians.csv` shows `WORK_STATUS = BUSY` and
  `queued_job_count` consistent with one current job.
- Neither job appears in `assignments.csv` or `routes.csv` (busy jobs are not
  routed).
- `jobs.csv` keeps both active (no completion).

**Notes / gaps.** The pipeline does not decide which of the two is "really"
current — that is left to review. A single busy claim (one job) must **not**
produce this conflict (guards against the old false-positive rule).

---

## S08 — System overrides a previous optimizer assignment

**Business question.** When the source system changes the KTV of a job that the
optimizer had already assigned in the previous snapshot, does the system win and
is the change audited?

**Inputs.** Branch HNI_05, KTVs `TIN0501.DAITV2` and `TIN0502.QUANNC`.

- Snapshot `TEST-S08a` at `08:00`: `1482626336` Chưa phân công →
  optimizer assigns it to `TIN0501.DAITV2` (reservation created).
- Snapshot `TEST-S08b` at `08:30`:
  `1482626336` → status `Đã phân công`, `EMP_ACCOUNT = TIN0502.QUANNC`.

**Expected pipeline behavior.** In `resolve_previous_assignments`, the old
planned technician (`TIN0501.DAITV2`) differs from the incoming system KTV
(`TIN0502.QUANNC`) → `SYSTEM_ASSIGNMENT_OVERRIDE`, resolution
`USE_SYSTEM_ASSIGNMENT`. The job moves to `TIN0502.QUANNC`; the old
reservation on `TIN0501.DAITV2` is released.

**Desired outcome (assert).**

- `conflicts.csv` of `TEST-S08b` has `SYSTEM_ASSIGNMENT_OVERRIDE` with
  `previous_value = TIN0501.DAITV2`, `incoming_value = TIN0502.QUANNC`.
- `assignments.csv` of `TEST-S08b` shows the job as `SYSTEM_FIXED` on
  `TIN0502.QUANNC`.
- `TIN0501.DAITV2` no longer has this job; if it holds no other jobs its
  `WORK_STATUS` returns to `IDLE`.
- `routes.csv` routes the job under `TIN0502.QUANNC`.

**Notes / gaps.** System wins by design. If the incoming `EMP_ACCOUNT` were
absent or missing from the roster, the conflict codes are instead
`SYSTEM_ASSIGNMENT_MISSING_EMP_ACCOUNT` / `SYSTEM_TECHNICIAN_NOT_IN_ROSTER`
(S06 covers the unavailable case).

---

## S09 — Empty event batch (absence ≠ completion)

**Business question.** If a run receives zero checklist events, it must keep the
previous active plan intact and must not treat missing jobs as finished.

**Inputs.** Two snapshots.

- Snapshot `TEST-S09a` at `08:00`: `1482626336` Chưa phân công,
  `1481808376` Đang xử lý (`TIN0501.DAITV2`).
- Snapshot `TEST-S09b` at `08:30`: **header-only maintenance CSV** (the 7
  pipeline columns, zero data rows), same roster.

**Expected pipeline behavior.** `apply_job_events` receives no events; previous
active jobs are kept. No diff, no completion.

**Desired outcome (assert).**

- `jobs.csv` of `TEST-S09b` still contains both jobs, with the same statuses
  and `EMP_ACCOUNT`.
- `changes.csv` is empty; `completed_jobs.csv` is empty.
- The optimizer assignment from `TEST-S09a` is retained (incumbent/reservation
  kept), and `routes.csv` still shows the plan.
- `summary.json.active_jobs = 2`, `completed_jobs = 0`.

**Notes / gaps.** Confirms the documented rule: only an explicit terminal event
closes a job. A zero-row maintenance CSV must be constructed header-only; the
pipeline's pandas `usecols` read requires the columns to exist.

---

## S10 — Job with missing location, across modes

**Business question.** A checklist with no `OBJ_LOCATION` cannot be geocoded. In
location-aware modes it should be impossible to assign; in `task` mode it should
still be assignable with a distance penalty. Does the mode switch behave that
way?

**Inputs.** Branch HNI_05, KTV `TIN0501.DAITV2`.

| CHECKLIST_ID | Status | OBJ_LOCATION |
|---|---|---|
| `1482626336` | Chưa phân công | (real address, geocodable) |
| `1482741226` *simulated* | Chưa phân công | (blank / missing) |

Run the identical event + roster twice, once with `--mode task_location` and
once with `--mode task`.

**Expected pipeline behavior.**

- `task_location`: the missing-location job has no compatible edge (location is
  a hard requirement) → `NO_COMPATIBLE_TECHNICIAN:...`.
- `task`: location is not required; the scorer applies the missing-distance
  penalty (cost 25) instead of the real distance.

**Desired outcome (assert).**

- `task_location` run: `unassigned.csv` contains `1482741226` with reason
  starting `NO_COMPATIBLE_TECHNICIAN:`; `assignments.csv` has only the
  geocodable job.
- `task` run: both jobs are assigned; in `assignments.csv` the missing-location
  job's `distance_cost = 25.0` and its `distance_km` is blank, while the
  geocodable job has real distance cost.
- `routes.csv` in `task` run: the stop without GPS has no valid
  `ESTIMATED_ARRIVAL`/`ESTIMATED_FINISH`, and any later stops on the same route
  also lose ETA (chained-time caveat).

**Notes / gaps.** Documents that missing GPS is a hard block in
`task_location`/`location` and only a soft penalty in `task`. The ETA
propagation gap for GPS-less stops is audit P1 #9 / routing note in the review.

---

## S11 — Shift capacity overflow

**Business question.** When jobs cannot fit in a KTV's shift, the extra job must
stay unassigned with an explicit capacity reason, and existing assignments must
not be silently re-worked.

**Inputs.** Branch HNI_05, one KTV `TIN0501.DAITV2`,
`SHIFT_START 06:00 → SHIFT_END 08:00` (capacity **120 min**), 3 jobs × 60 min.
Snapshot `TEST-S11` at `06:00`:

| CHECKLIST_ID | Status |
|---|---|
| `1482626336` | Chưa phân công |
| `1482741226` | Chưa phân công |
| `1481808376` *simulated* | Chưa phân công |

**Expected pipeline behavior.** Greedy assignment sorts jobs by due; each
accepted job adds 60 min. The third job cannot fit (projected workload > 120) →
`SHIFT_CAPACITY_EXCEEDED`.

**Desired outcome (assert).**

- `assignments.csv` has exactly 2 rows (2 × 60 min = 120 min capacity).
- `unassigned.csv` contains the remaining job with
  `reason = SHIFT_CAPACITY_EXCEEDED`.
- `routes.csv` covers only the two assigned jobs; the unassigned job is absent.
- `summary.json.evaluation_metrics.planned_sla_on_time_rate_percent` counts the
  unassigned job as **not** on time (it has a due date but no route finish),
  i.e. the denominator is not shrunk by dropping it.
- Re-running the same events against a fresh `--runtime-dir` is deterministic.

**Notes / gaps.** Capacity is the full `SHIFT_END − SHIFT_START` window, not the
remaining time from `planning_time`, and travel is never counted (audit P1 #6).
Only service minutes are added, so a route that physically cannot complete
within the shift is still reported as "feasible".

---

## Acceptance summary

For every scenario, a run is **PASS** only when:

1. `run_metadata.json.status == "SUCCESS"` and the checkpoint was committed
   (`latest.json` points to the new snapshot).
2. Every "Desired outcome (assert)" bullet for that scenario holds on the run
   output files listed in the scenario table.
3. `summary.json.evaluation_metrics.ai_planning_target_met` is `true`
   (`planning_seconds ≤ 5`) — matches the existing planning-time contract.
4. Re-running with `--reset-state` on a fresh runtime dir reproduces the same
   assignments, routes and conflicts (reproducibility).

Suggested regression coverage once implemented: bundle S01–S11 into one
executable script under `utils/` following the
`utils/test_real_metrics_scenarios.py` pattern, with independent recalculation
of the asserted metrics rather than trusting `summary.json` alone.
