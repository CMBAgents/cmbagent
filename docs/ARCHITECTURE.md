# cmbagent_lg — architecture

`cmbagent_lg` is a LangGraph re-implementation of [cmbagent](https://github.com/CMBAgents/cmbagent)'s
deep-research workflow. A research task is turned into an ordered **plan** by a propose/critique
loop, and each plan step is executed by a sub-graph: an **engineer** step goes through a
self-debugging write → run → judge loop until the code runs *and* meets the step's goal; a
**researcher** step writes a markdown report graded first for goal coverage, then for claim
support. The orchestrator threads a bounded summary of prior steps into each later step, may
re-plan the not-yet-run tail, and checkpoints to disk so a crashed run is resumable. All
run-scoped configuration lives in one dataclass, `PlanContext`, passed as LangGraph's
`context_schema` at `invoke` time rather than as graph state, so one compiled graph generalizes
across tasks.

## Module map

| Module | Role |
| --- | --- |
| `context.py` | `PlanContext` — every run-scoped knob; also `DEFAULT_AVAILABLE_AGENTS` and `effective_code_execution_timeout`. |
| `llms.py` | `chat_model(model, role)` — provider inferred from the model name, temperature/thinking set by role; `lru_cache`d on `(model, role)`. |
| `persistence.py` | work_dir lifecycle (`prepare_work_dir`, `default_work_dir`) and the on-disk plan/summary/trace-id files. |
| `prompt_utils.py` | YAML prompt template helpers: `SafeDict` (missing placeholders render empty), content flattening, schema-field briefs. |
| `tracing.py` | Langfuse callback-handler factory; `langfuse_callbacks()` never raises. |
| `timing.py` | `@timed_node` — prints per-node wall clock and merges `node_elapsed_s` into the node's state delta. |
| `cli.py` | `cmbagent-lg-cost` — per-model and per-agent-tag cost/tokens/latency, read back from Langfuse. |
| `ocr.py` | `ocr_pdf_to_dir` — Mistral OCR of a PDF to `document.md` + figures (optional dependency). |
| `planning/` | `planner ↔ plan_reviewer` graph producing `Plan` / `Step` / `Review`. |
| `self_debug/` | Engineer step execution: engineer → executor → two evaluator gates, plus preflight, API checks, escape hatches, remote-execution backend. Three deterministic no-model modules sit on that path: `output_postcondition.py` (a clean run that did not write an output the step declared is bounced before any evaluator), `data_infeasibility.py` (a code-gate failure that is a fact about the data — non-convergence, a singular design — is named and bounded instead of retried to the cap), and `infeasibility_check.py` (the step evaluator's "this is impossible here" claim is resolved against the installed environment and the run's own artifacts rather than believed). |
| `researcher/` | Researcher step execution: report writer → `step_evaluator` → `claim_auditor`. |
| `deep_research/` | Orchestrator: run each step, optionally re-plan the tail, checkpoint. |
| `vlm/` | Multimodal grounding — image discovery/encoding (`images.py`) and the `image_reviewer` node (`reviewer.py`). No graph of its own. |

Public surface (`cmbagent_lg/__init__.py`): the planning `graph`, `PlanContext`, `Plan`/`Step`/`Review`,
`self_debug_graph` with `EngineerResponse`/`ExecutionVerdict`/`StepVerdict`/`ImageReview`,
`researcher_graph`, `deep_research_graph`, `ocr_pdf_to_dir`, and the persistence helpers.

## The graphs

### `planning` — propose/critique

Nodes: `planner`, `format_plan`, `plan_reviewer`, `format_review`.

```
START → planner → format_plan ──(route_after_format_plan)──► plan_reviewer → format_review ─┐
                        │                                                                   │
                        └──► END                                       planner ◄────────────┘
```

`route_after_format_plan` returns `plan_reviewer` while `state["round"] <= ctx.num_rounds`, else
`END`. `num_rounds` counts **review cycles** and the loop always ends on a planner pass, so total
planner passes are `num_rounds + 1`. `format_plan` builds a per-invocation Pydantic model whose
`Step.sub_task_agent` is `Literal[*ctx.available_agents]`, so a different agent team needs no
recompile; the result is round-tripped back into the static `Plan`. Planning `history` accumulates
`(plan, review)` pairs, so each planner pass sees the whole prior transcript.

### `self_debug` — one engineer step

Nodes: `preflight`, `engineer`, `format_engineer`, `static_preflight`, `api_preflight`,
`executor`, `output_postcondition`, `execution_evaluator`, `image_reviewer`,
`step_evaluator`, `escalation`, `data_fixer`, `missing_input`.

```
                    ┌────────────────────── engineer ◄───────────────────────┐
START ─(route_start)┤        ▲                  │                            │
   preflight ───────┘        │            format_engineer                    │
                             │                  │                            │
                             │           static_preflight                    │
                             │      (route_after_static_preflight)           │
                             │          │             │          │           │
                             │    api_preflight ───► executor    │ refused:  │
                             │  (bounce back to engineer)  │     │ rc 1, no  │
                             │                             ▼     │ run       │
                             │                   output_postcondition  │     │
                             │                             ▼     ▼           │
                                                        execution_evaluator  │
                       ┌───────────────────────────────────────┤            │
              escalation → executor      data_fixer ────────────┤            │
              missing_input → END        image_reviewer ────────┤            │
                                              │                 │            │
                                              ▼                 │            │
                                        step_evaluator ◄────────┘            │
                                              │ goal MET → END               │
                                              └ goal MISS + budget ──────────┘
```

- `route_start`: `enable_preflight` runs the file-inspection node first, else the plain
  `engineer`.
- `static_preflight` runs on every attempt (no flag, no model call): a script that does not
  `ast.parse` — or, with `reject_demo_datasets` (default on), one that loads a bundled example
  dataset (`lifelines.datasets.load_rossi`, `sklearn.datasets.load_*`/`fetch_*`,
  `seaborn.load_dataset`, `plotly.data`, `bokeh.sampledata`, `keras`/`torchvision.datasets`, …)
  instead of the project's own files — or, with `require_stdout` (default on), one with no
  `print`/`sys.stdout` write anywhere — is refused. `route_after_static_preflight` then returns `execution_evaluator` with
  the executor's fields filled as for a crashed run (rc 1, empty stdout, the diagnosis on stderr,
  `step_N_failure_M.*` on disk), so a script that cannot pass is an ordinary failed attempt
  without a subprocess. Otherwise it defers to `route_after_format_engineer`, which returns
  `executor` unless the API pre-flight is enabled; `route_after_api_preflight` returns `engineer`
  when a report was produced (the code is not run), else `executor`.
- `output_postcondition` sits on the executor → code-gate edge (no flag beyond
  `check_declared_outputs`, no model call): when the sub-task DECLARES an output artifact by
  path (a save-shaped bullet naming `data/<file>`) and the script exited 0 without writing it,
  the executor's result is rewritten into a failure with the missing path named on stderr, so
  the bounce costs neither evaluator call and is bounded by `max_n_attempts` like any other
  failed attempt. Conservative: save-shaped declarations only (an absent *input* is
  `safe_loading`'s terminal verdict, never a retry hint here), clear `data/...` paths only,
  clean runs only, and never under `CMBAGENT_LG_REMOTE_EXECUTOR` (the data is guest-side).
- `data_infeasibility` (no node of its own, no model call): `execution_evaluator` also
  matches each code-gate failure against a small table of numerical-failure phrases
  (`ConvergenceError` / `delta contains nan`, a singular or rank-deficient design, perfect
  separation, too few events) and tallies them per marker in `numeric_fault_seen`. One
  observation claims nothing — the next attempt often fixes it. Once the SAME marker
  recurs `max_data_infeasibility_repeats` times (3) the engine states the conclusion
  (`execution_infeasible_signal`, `logs/step_N_infeasible_execution.json`) and
  `route_after_execution_evaluator` ends the step, which `deep_research` then treats as
  replannable. Three DIFFERENT failures in a row are progress, not infeasibility, and
  deliberately produce no feasibility-named record.
- `route_after_execution_evaluator` (code gate) on **success**: `missing_input` if the intent gate's
  fabrication predicate fires, `engineer` for a single bypass-guard nudge while attempts remain,
  otherwise `image_reviewer` when `vlm_enabled` else `step_evaluator`. On **failure**: `escalation`
  (once per step, when enabled and `failure_kind` is `missing_module`/`renamed_api`),
  `missing_input` (intent gate or `safe_loading`), `data_fixer` (when enabled and the failure looks
  like a data load), `END` on a concluded data infeasibility or at the attempt cap, else `engineer`.
- `route_after_image_reviewer`: `engineer` only when a revision is needed and both
  `max_vlm_review_attempts` and `max_n_attempts` allow it, else `step_evaluator`.
- `route_after_step_evaluator` (goal gate): `END` when fulfilled or exhausted, else `engineer`.
  "Fulfilled" is `schemas.step_goal_met`, which also ends the step when EVERY unmet requirement
  was declared impossible in this environment (`StepVerdict.infeasible_requirements` +
  `infeasible_justification`) — the escape hatch for a requirement no installed library or
  available column can satisfy, adjudicated deterministically and all-or-nothing, so one
  still-fixable unmet requirement keeps the step failed. The claim itself is then
  **checked rather than believed** (`self_debug/infeasibility_check.py`, computed once in
  `step_evaluator` and left on state as `infeasibility_check`): symbols the justification
  names are resolved against the installed environment with `api_preflight`'s resolver, a
  bullet whose declared artifact the attempt actually wrote refutes the claim, and a
  declaration covering every bullet of a multi-bullet step whose run produced results is
  refused outright. `verified_absent` may pass; `refuted` fails the step and hands the
  engineer the real import path or the file it wrote; `unverified` (prose the engine
  cannot resolve) passes but is marked in the outcome, the step summary and
  `analysis_status.json`'s `unverified_infeasible_steps`, and counts against
  `max_unverified_infeasible_steps` so one plan cannot pass most of its steps on
  unchecked assertions. A declaration with no audit beside it fails closed. A
  completed-by-substitution step records what was NOT delivered in its step summary, its
  run outcome, and `analysis_status.json`'s `infeasible_steps`.
- `escalation` routes to `executor`, not `engineer`, so it does not consume an attempt.

Two LLM gates share one retry budget: `execution_evaluator` asks only whether the code *ran*
cleanly, `step_evaluator` whether the run *achieved* the sub-task. Two parallel trails feed the
retries — `error_history` (code failures) and `step_feedback_history` (goal misses).

Execution has two backends behind the same `executor` node: a local subprocess (own process group,
`cwd = work_dir`, so relative `data/<f>` writes land in `{work_dir}/data/`), and a remote guest
agent when `CMBAGENT_LG_REMOTE_EXECUTOR=host:port` is set (`remote_exec.py`). The evaluators are
unchanged either way because they read stdout from state. Both backends append a
`[killed by signal N (NAME) …]` line to stderr when the script died by signal with no traceback
(a cgroup OOM kill is SIGKILL, rc -9, and leaves both streams empty), quoting the run's
`hardware_constraints`; the timeout kill keeps its own `[killed: TimeoutExpired …]` marker and
rc 124. Remotely the data lives in the guest
session's `data/`, not in `{work_dir}/data/`, so `preflight` answers "is there input?" from one
`list_files` call on the guest — the engine's own `data/` is empty on every remote step, and
reading it would make each step that consumes the previous step's output look input-less.

The same split applies to a provided input named at a path: `resolve_named_input` decides
"is this a real input?" by whether the path already exists, and a read-only mount such as
a remote executor's `/datasets/<id>/<file>` exists only on the guest. So when that scan finds
nothing locally, the candidate paths go over in one `introspect` call — the probe runs in
the sandbox `execute` uses, with the same mounts. A confirmed file disarms the gate; a probe
that cannot run changes nothing, since the emptiness is already settled and a transient
error must not switch an anti-fabrication guard off.

### `researcher` — one prose step

Nodes: `researcher`, `react_researcher`, `step_evaluator`, `claim_auditor`.

```
START ─(writer_node_name)─► react_researcher | researcher
                                     │
                                     ▼
                              step_evaluator ── goal MISS + budget ─► writer
                                     │       └─ goal MISS + spent ──► END
                                     │ goal MET
                                     ▼
                               claim_auditor ── HARD claim + budget ─► writer
                                     └───────── clean / exhausted ───► END
```

`writer_node_name(ctx)` returns `react_researcher` for `researcher_mode == "react"` (the default)
and `researcher` for anything else, so an unrecognised value degrades to the plain writer. Both
writers loop back to the same `step_evaluator`, so the retry budget and downstream contract are
identical; only the START edge differs. With `claim_audit_enabled` False the goal gate goes
straight to `END`. The auditor sits strictly downstream of a fulfilled verdict, never sets
`fulfilled` and never feeds the goal router.

There is no execution evaluator and no escalation. The ReAct writer's tool budget scales with plan
position: `researcher_max_tool_calls + min(step_number - 1, 24)`, because once older steps are
compacted to headers a final synthesis may need one `read_file` per compacted step.

**Evidence windows.** `read_file(path, offset=0)` returns a small file whole and a large one as a
page: the first call is head + tail, a later call the page at `offset`, always with a footer naming
the character ranges shown and omitted and how to reach the rest. Every cut anywhere (`_head_tail`
in the researcher and in the step summaries, the evidence stubs) renders
`[… N chars omitted (a–b of M); full text: <path>]`, so no grader window is ever an unmarked prefix.
The tools-log record of a read carries `sha256` and `ranges` — provenance, not a copy — and the
graders re-read the file from disk, falling back to the recorded excerpt with a
`[source changed since the researcher read it]` note on a sha mismatch.

**Deterministic grounding (`researcher/grounding.py`, on by default).** An LLM cannot prove a number
absent from an excerpt, and on the fleet it did: R² values at byte ~7,600 of a 12 KB log were ruled
fabricated by both graders eight times. So presence and absence are now whole-token lookups over the
run's full sources, and the LLM keeps authority only over meaning. Once per step a `SourceIndex` is
built (shared by evaluator, auditor and override; keyed by a path/size/mtime digest) over
`codebase/step_*.log` stdout and text files under `data/` (*primary*), the researcher's own
`view_image` readings (*figure*), failure/bounced logs (*superseded*, never grounds), and
`input_files/` + `planning/` (*input*: indexed for provenance, never grounds). Scripts, `reports/`
and verdict JSON are never indexed. Numbers are whole tokens (`3054` never comes out of `4.783054`),
lines are deduplicated per file, and each token carries a locality label (CSV column + row key,
pandas table column by position + row, JSON key path, a log line ±1) and a meta tag for seeds,
tracebacks, timestamps, elapsed times, versions and shape tuples. A hit is *strong* when it is
labelled by one of the claim's own identifier words, or specific enough that fewer than
`grounding_coincidence_max` chance matches are expected at that precision in this run's own number
density, or lives in the file the claim cites; otherwise *weak*. Both graders receive the same
evidence: a 1,200-char head stub per artifact the researcher opened, headed by the ranges it saw,
and the **quantity ledger** — one line per report number, `GROUNDED` / `WEAK` with the source line
quoted, `INPUT-ONLY (input_files/…:line — not computed by this run)`, or `NOT FOUND in any artifact
(K files, M MB scanned)`. Persisted as `logs/step_N_quantity_ledger_{attempt}.json` and
`logs/step_N_claim_grounding_{seq}.json`.

**Input-sourced descriptions — the one place a model may weaken a deterministic finding.**
`apply_overrides` keeps a claim hard when its numbers were located ONLY in a file the run
was GIVEN. Two real fleet runs quote the same `input_files/data_description.md` and need
opposite verdicts: metabric's *"646 Died of Disease vs. 496 Died of Other Causes"* is a
correct description of the provided cohort (charging it as fabrication failed five
fulfilled steps and a finished report), while project99's *"We established that atom
counts alone explain nearly all variance"* is a result the run never computed. Provenance
cannot separate them, so a demotion to advisory requires ALL of: the numbers are
input-located; the sentence carries **no** result-assertion marker from the closed
`RESULT_ASSERTION_MARKERS` list (checked first, so project99's claim is refused before any
model is consulted); and a forced-choice `InputSourcedClaim` classification quotes a span
**verified verbatim** against the claim and calls it a description. Any one missing leaves
it hard, a failed call leaves it hard, and `input_sourced_description_demotion=False`
disables it. Every demotion is recorded with the span and the justification in
`logs/step_N_input_sourced_demotions.json`, the step outcome, and
`analysis_status.json`'s `input_sourced_demotion_steps`. Everywhere else in the engine
the direction is fixed — deterministic grounding demotes the LLM, the API pre-flight
overrules the engineer — so this exception is deliberately narrow and documented at
length in `researcher/grounding.py`.

**Verdict policy.** The step evaluator's schema gains `suspected_fabrications`: a grounding doubt
goes there, never into `fulfilled`. A goal-miss whose every unmet requirement refers to a suspected
span, and whose spans the index all finds, is flipped to fulfilled, the spans become `claim_hints`
for the auditor, and nothing is appended to `step_feedback_history` — the "stay consistent" rule is
how a false call locked in. In the claim auditor, `apply_overrides` runs after the LLM: a hard claim
whose numbers exist in this run's outputs becomes `unsupported_weak` with `grounding_override=True`,
unless the auditor's evidence carries a *receipt* (a strongly grounded, labelled conflicting value
of the same quantity) or the claim cites the wrong file. Hard stands for deterministic absence and
for input-only numbers, whose evidence is rewritten to say so. The **livelock breaker**
(`claim_accusations`, fingerprints of sorted canonical numbers + cited paths) never re-charges a
claim: a grounded repeat becomes `supported`, a still-absent one is annotated without another writer
round-trip, so audits per step are at most two whatever `max_claim_audit_attempts` says.
`claim_audit_failed` is set only for a surviving hard claim that is deterministically absent
(`ungrounded` / `partial` / `no_quantities`) and only while `claim_audit_halts` is True; a
grounded-but-flagged claim leaves the step fulfilled, rides on the outcome as `advisory_claims`,
and lists the step under `flagged_steps` in `analysis_status.json` (additive; `status` unchanged).
With `grounding_enabled=False` the graders get the legacy excerpt block and policy text; only the
markers and `read_file` paging are always on.

### `deep_research` — the orchestrator

Nodes: `run_step`, `adaptive_review`, `replan_tail`, `format_tail`, `merge_plan`.

```
START → run_step → adaptive_review ─(route_after_review)─► replan_tail → format_tail → merge_plan
                          ├──────────► run_step                                            │
                          └──────────► END          run_step ◄──(route_after_merge)────────┤
                                                         END ◄─────────────────────────────┘
```

- `run_step` dispatches on `step.sub_task_agent`: `"engineer"` → `self_debug_graph`,
  `"researcher"` → `researcher_graph`, anything else → an unfulfilled outcome plus a placeholder
  summary (`step_summaries` and `step_outcomes` must stay length-aligned). It checkpoints
  `logs/deep_research_run.json` after every step, best-effort.
- `route_after_review`: `END` when the step just finished was not fulfilled, or the plan is
  exhausted; `run_step` once `len(adaptive_history) >= ctx.max_adaptations`; `replan_tail` only when
  the review both asks for adaptation *and* carries non-empty recommendations; else `run_step`.
  **One failure is not an unconditional halt:** a step that failed by spending its whole attempt
  budget — or that the code gate stopped on a concluded data infeasibility, which stops
  EARLY and so has attempts to spare (`_step_exhausted` stamps `exhausted` on the outcome;
  `_replannable_exhaustion` then requires adaptive planning on and replan budget left) —
  gets one pass at the replanner, with the verdict feedback, the numerical marker, and the
  names the API pre-flight proved absent handed to the reviewer and the replanner. `adaptive_review` likewise skips its early-return for that case only, including when
  no tail remains — a rewritten tail may be what finishes the task. Halt stays the fallback:
  adaptive planning off, a spent `max_adaptations`, a step the terminal `missing_input` node marked
  `unfulfillable` (or a `data_fixer` "missing" verdict), a researcher claim-audit failure, a
  transient failure with budget left, and a reviewer with nothing to change all end the run as
  before. The failed outcome is never rewritten, so `analysis_status.json` still reports `halted`
  with `halted_at_step` at the first unfulfilled step.
- `route_after_merge`: `END` when the plan is exhausted, else `run_step`.
- `merge_plan` applies its safety rules in fixed order: an empty replan tail is a no-op; a tail
  naming an unknown agent rejects the whole replan; tail steps content-identical to a completed
  prefix step are de-duplicated (falling back to the no-op if that empties the tail); finally the
  merged plan is capped by `maximum_number_of_steps_in_plan`, truncating the **tail only**.
  Completed steps are never rewritten.
- `after_step` is exported by `deep_research/nodes.py` but is **not** a node in the compiled graph;
  it is the linear router that `route_after_review` reduces to when adaptive planning is off.
- `build_deep_research_graph()` resolves node functions on the `nodes` module at build time, so a
  caller can monkeypatch them before building. The module-level `graph` is the compiled default.

Cross-step context is the dominant cost model here. Each step summary is re-injected into every
later step's prompt, into `adaptive_review` and into `replan_tail`, so summaries are head+tail
capped (stdout 6000/2000, code 12000/4000 characters) and everything older than
`carryover_recent_full` steps collapses to a one-line header. The always-present workspace file
manifest lists `codebase/`, `data/`, `logs/` and `reports/`, which is how a later step re-opens the
detail that was compacted away.

The adaptive reviewer and tail planner are pinned to non-lite models
(`gemini-3.1-pro-preview` as critic, `gemini-3.6-flash` as generator) and read overrides only from
`ADAPTIVE_REVIEWER_MODEL` / `ADAPTIVE_PLANNER_MODEL`; `PlanContext.planner_model` and
`plan_reviewer_model` configure the *initial* planning loop and are deliberately not consulted.
The tail formatter does use `ctx.formatter_model`.

## `PlanContext` reference

All defaults below are read from `cmbagent_lg/context.py`.

### Task and plan

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `main_task` | `str` | *(required)* | The task the plan is built for; rendered into templates as `{main_task}`. |
| `hardware_constraints` | `str` | `"Standard laptop. Single CPU. No GPU."` | Free text about the execution machine, injected into prompts. |
| `code_execution_timeout` | `int \| None` | `120` | Run-wide seconds cap; a step's own value wins, and a falsy value falls back to `DEFAULT_CODE_EXECUTION_TIMEOUT` (300). Execution is never unbounded. |
| `maximum_number_of_steps_in_plan` | `int` | `5` | Upper bound on plan length, including after an adaptive merge. |
| `num_rounds` | `int` | `2` | Plan review cycles; planner passes = `num_rounds + 1`. |
| `available_agents` | `List[Tuple[str, str]]` | copy of `DEFAULT_AVAILABLE_AGENTS` (`engineer`, `researcher`) | `(name, description)` pairs; also constrains `Step.sub_task_agent`. |
| `planner_append_instructions` | `str` | `""` | Extra planner prompt text. |
| `plan_reviewer_append_instructions` | `str` | `""` | Extra plan-reviewer prompt text. |

### Adaptive planning and carryover

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `adaptive_planning` | `bool` | **`True`** | After each executed step a reviewer may rewrite the not-yet-run tail. False makes `adaptive_review` early-return with zero extra model calls. |
| `max_adaptations` | `int` | `3` | Tail re-plans allowed in one run, including the one a step that exhausted its attempt budget may trigger instead of halting. |
| `carryover_recent_full` | `int` | `3` | How many recent step summaries are carried verbatim; older ones collapse to a header. |

### Per-role models

`planner_model`, `plan_reviewer_model`, `engineer_model`, `researcher_model`, `evaluator_model`,
`formatter_model` — all `str | None`, all default `None` (→ `llms._DEFAULT_MODEL`,
`"gemini-3.1-flash-lite"`). The provider is inferred from the name (`gpt-`/`o1`–`o4`/`chatgpt` →
OpenAI, `claude*` → Anthropic, everything else including `gemini-*` → Google), so a caller's config
can pass names straight through.

| Field | Consumed by | `chat_model` role |
| --- | --- | --- |
| `planner_model` | `planning.planner` | `generator` |
| `plan_reviewer_model` | `planning.plan_reviewer` | `critic` |
| `engineer_model` | `self_debug.engineer` and the data fixer's fallback | `coder` |
| `researcher_model` | `researcher` and `react_researcher` | `generator` |
| `evaluator_model` | the self_debug and researcher evaluators, and the claim auditor's fallback | `critic` |
| `formatter_model` | all formatter nodes, and the intent gate's fallback | `formatter` |

Role settings: `generator` temp 1.0 / thinking low, `coder` temp 0.2 / thinking high, `critic`
temp 0.4 / thinking low, `formatter` temp 0.0. An unknown role falls back to `formatter`.
`coder` exists so the engineer can be low-temperature and high-thinking while the planner and
researcher stay creative.

### Multimodal grounding

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `vlm_enabled` | `bool` | **`True`** | Attach plots under `data/` to the report writers as image blocks, and route self_debug through `image_reviewer`. |
| `vlm_max_images` | `int` | `8` | Cap on attached images. |
| `vlm_model` | `str \| None` | `None` | Overrides both the `image_reviewer` model (default `gemini-3.1-pro-preview`) and the researcher's `view_image` model (default `gemini-3.6-flash`). Must be vision-capable. |
| `max_vlm_review_attempts` | `int` | `2` | Image review/revision cycles per step. |

### self_debug and the engineer

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `max_n_attempts` | `int` | `3` | Engineer/writer attempts per step; the budget every bounded loop also draws on. |
| `engineer_append_instructions` | `str` | `""` | Extra engineer prompt text. |
| `evaluator_append_instructions` | `str` | `""` | Judging guidance; the evaluators only. |
| `shared_append_instructions` | `str` | `""` | Operator text for the engineer **and** both evaluators: facts about the environment, and about what counts as a finished step. The engineer's own block also reaches the evaluators under an explicit "instructions the engineer was given" header, so a fact stated once cannot reach only half the loop. |
| `stop_on_repeated_data_infeasibility` | `bool` | **`True`** | End the step once the same numerical fault (non-convergence, singular design, perfect separation) has recurred, named in `logs/step_N_infeasible_execution.json`, instead of retrying to the cap. |
| `max_data_infeasibility_repeats` | `int` | `3` | Observations of one marker before that conclusion is drawn. |
| `max_unverified_infeasible_steps` | `int` | `2` | Steps one plan may pass on an infeasibility claim the engine could not check. A verified absence never counts. |
| `require_stdout` | `bool` | **`True`** | `static_preflight` refuses a script with no `print`/`sys.stdout` write anywhere; the step evaluator sees only stdout. |
| `reject_demo_datasets` | `bool` | **`True`** | `static_preflight` refuses a script that loads a bundled example dataset instead of the project's own files. `sklearn.datasets.make_*`, the generic directory readers, and a sub-task that names the dataset are exempt. |
| `check_declared_outputs` | `bool` | **`True`** | `output_postcondition` bounces a clean run that did not write an output path the sub-task declared, before either evaluator is called. |

### researcher

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `researcher_mode` | `str` | **`"react"`** | Bounded ReAct writer with read-only `list_workspace` / `read_file` / `view_image`; `"plain"` is the single-invoke writer. Errors fall back to plain. |
| `researcher_max_tool_calls` | `int` | `6` | Base tool round-trips, before the per-prior-step scaling. |
| `researcher_append_instructions` | `str` | `""` | Extra researcher prompt text. |
| `claim_audit_enabled` | `bool` | **`True`** | Grade each result-claim against the run's evidence after the goal gate passes. |
| `claim_audit_model` | `str \| None` | `None` | Falls back to `evaluator_model` (critic role). |
| `max_claim_audit_attempts` | `int` | `2` | The auditor's own ceiling, on top of `max_n_attempts`. |
| `input_sourced_description_demotion` | `bool` | **`True`** | Allow the bounded input-sourced envelope above: an input-only claim with no result-assertion marker, classified as a description of provided data with a verified span, is demoted to advisory instead of failing the step. The only model judgement in the engine that can weaken a deterministic finding. |
| `claim_audit_source_chars` | `int` | `8000` | Per-source excerpt for the auditor when grounding is off; the stub cap fallback otherwise. |
| `grounding_enabled` | `bool` | **`True`** | Deterministic presence/absence of every report number via the per-step `SourceIndex` and quantity ledger. Env fallback `CMBAGENT_LG_GROUNDING_ENABLED` (every grounding knob has one, `CMBAGENT_LG_<UPPER>`). |
| `grounding_budget_s` | `float` | `1.5` | Wall-clock budget for one index build; over budget the index is marked incomplete and absence is never charged. |
| `grounding_file_head_bytes` / `grounding_file_tail_bytes` | `int` | `16 MB` / `4 MB` | Head/tail of each file that is indexed. |
| `grounding_coincidence_max` | `float` | `0.05` | An unlabelled hit is strong only when fewer chance matches than this are expected at the claim's precision. |
| `evidence_ledger_chars` | `int` | `24000` | Cap on the quantity ledger block. |
| `researcher_read_page_chars` | `int` | `8000` | `read_file` page size (tail = a quarter). Always on. |
| `claim_audit_halts` | `bool` | **`True`** | Fail the step on a deterministically absent hard claim; `False` delivers the annotated report with the step flagged. |

### Data integrity and escape hatches

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `safe_loading` | `bool` | **`True`** | Mandatory anti-fabrication layer, zero extra model calls: data-integrity rules in the step evaluator, plus a terminal not-fulfilled when a required input file the script does not itself write is genuinely absent. "Absent" means also not found by a bounded search of `data/` and `input_roots` for the same basename, or a close name in the path's existing parent (`self_debug/input_search.py`). A strong hit (every directory between its root and the file appears in the path the script used) is an ordinary retry with the locations in the engineer's prompt, on every attempt; a weak hit (under a directory the path did not name) or a close name is such a retry once per step and file name, with a do-not-substitute note; a close name differing in its numbers is only named in the terminal note. |
| `reassign_researcher_code_steps` | `bool` | **`True`** | Researcher steps that need code go to the engineer (`planning/agent_check.py`): after `format_plan` / `format_tail`, a researcher step other than the last whose text asks, in an imperative clause, to compute, load data, read binary data files, run code or persist results to a data file; at run time, once, when an unfulfilled researcher report says it cannot execute code, or, on a step whose text asks for code work, when the verdict says the step needs code or the report says it cannot compute or write files; never on the last step of a plan of several steps. Recorded as `reassigned` in the step outcome. Needs an `engineer` agent. |
| `enable_preflight` | `bool` | **`True`** | Deterministic, no-LLM `preflight` node inspects every file in `work_dir/data/` before the first attempt and injects a factual FILE REPORT. |
| `enable_intent_gate` | `bool` | **`True`** | Fires only when `preflight` finds `data/` empty: one cheap classifier call decides whether the step needs provided input or whether creating the data *is* the step. Fails open. |
| `intent_gate_model` | `str \| None` | `None` | Falls back to `formatter_model` (formatter role). |
| `enable_data_fixer` | `bool` | `False` | On a data-load failure, inspect the real file (magic bytes / `csv.Sniffer` / HDF5 tree) and synthesise a loader in one model call; also adds a loader block to the engineer prompt. Non-fatal. |
| `data_fixer_model` | `str \| None` | `None` | Falls back to `engineer_model`, then `"gemini-3.6-flash"`; built with the `coder` role. |
| `enable_escalation` | `bool` | `False` | On a structurally unfixable failure, escalate once to a Claude Agent SDK agent that may web-search or install. The one Anthropic dependency; needs `ANTHROPIC_API_KEY` and `claude-agent-sdk`. |
| `escalation_max_budget_usd` | `float` | `0.50` | Escalation spend cap. |
| `escalation_max_turns` | `int` | `12` | Escalation turn cap. |
| `escalation_model` | `str \| None` | `None` | `None` = the SDK's default. |
| `escalation_append_instructions` | `str` | `""` | Extra escalation prompt text. |

**On by default:** `adaptive_planning`, `vlm_enabled`, `researcher_mode="react"`,
`claim_audit_enabled`, `safe_loading`, `enable_preflight`, `enable_intent_gate`,
`enable_api_preflight`, `api_introspect_retry`.
**Opt-in:** `enable_data_fixer`, `enable_escalation`, `enable_api_digest`,
`enable_bypass_after_fault_guard`.

## Robustness and anti-fabrication

| Safeguard | What it prevents |
| --- | --- |
| Two gates, one budget | An exit code of 0 being read as success: `step_evaluator` judges the goal separately from `execution_evaluator`'s "did it run". |
| `safe_loading` | Retrying a genuinely absent required input until the engineer invents stand-in data; it terminates the step instead. Conservative — a possible create-then-read, or a missing *package* rather than a missing *file*, falls through to the normal retry path. |
| `enable_preflight` | A loader written from guesswork: the real file's magic bytes, delimiter, HDF5 tree and dtypes are in the prompt before the first line is written. Zero model calls. |
| `enable_intent_gate` | A clean run on a step that had nothing to load — which *is* the fabrication. It arms a do-not-fabricate directive and a terminal router guard. Fails open on any error. |
| `image_reviewer` | A blank, mis-scaled or scientifically wrong figure passing unseen; an unresolved defect at budget exhaustion is surfaced to `step_evaluator` rather than silently accepted. |
| ReAct researcher tools | A Results section inventing numbers that live in a saved artifact rather than in stdout. |
| `claim_auditor` | A report that passes the goal gate while carrying claims the run's evidence does not support; exhaustion annotates the deliverable and fails the step for a claim the sources provably lack. |
| Deterministic grounding | The opposite failure: both graders ruling a correct, file-sourced number "fabricated" because it sat outside their excerpt, and the retry loop locking on that call. Presence is a whole-token lookup over the full sources; the LLM judges meaning only. |
| Carryover compaction | Unbounded O(steps²) prompt growth, while keeping the compacted detail reachable on disk through the workspace manifest. |
| `merge_plan` freezing | An adaptive re-plan rewriting, deleting or re-doing work that already completed. |
| Artifact demotion | A failed or nudged attempt overwriting the canonical `codebase/step_N.py` / `reports/step_N.md`; those are renamed with the attempt number instead. |

Two structural rules hold across the system. Every bounded loop is bounded **twice** — image review,
API pre-flight bounces, the bypass nudge and the claim audit each have their own ceiling *and*
consume the shared `max_n_attempts`; the adaptive loop is bounded by `max_adaptations` and by
`maximum_number_of_steps_in_plan`. And every optional feature is **non-fatal**: the ReAct
engineer/researcher fall back to their plain nodes on any error, the data fixer's exceptions are
no-ops, the intent gate fails open, and the adaptive reviewer/replanner/formatter degrade to "keep
the existing tail".

## API-bug mitigations (PR #4)

These address one bug class: generated code calling a library name the model recalls but the
installed release no longer has. All are deterministic — `importlib`, `dir()`,
`inspect.signature`, `difflib` — and cost zero model calls. Each has a `PlanContext` field and an
env-var twin (`1` on, `0` off). Pre-flight and retry introspection default **ON**; digest and the
bypass guard stay off. Under `CMBAGENT_LG_REMOTE_EXECUTOR`, pre-flight / retry / escalation ask the
guest sandbox (`env_report`); they stay silent if the guest cannot answer.

| Feature | Field / env var | Default | What it does |
| --- | --- | --- | --- |
| Digest | `enable_api_digest` / `CMBAGENT_LG_API_DIGEST` | `False` | Injects a deterministic package inventory — exact installed versions, submodule lists, public names of modules the task names — into the engineer's **first-attempt** prompt, so the stale call is never written. Candidates come from the task text, the workspace and the environment; no library is named in the static prompt text. |
| Pre-flight | `enable_api_preflight` / `CMBAGENT_LG_API_PREFLIGHT` | **`True`** | An `api_preflight` node on the one edge where code exists but has not run: `ast`-extracts third-party symbols and verifies them against the installed packages. A provably absent name bounces back to the engineer with the real API facts, so the stale call is never run. |
| Retry introspection | `api_introspect_retry` / `CMBAGENT_LG_API_INTROSPECT_RETRY` | **`True`** | On an API-shaped error, classifies the fault and injects a deterministic report into the plain engineer's retry context, so the stale call runs, fails, and is *explained*. Reads stdout as well as stderr, since a script that catches and prints its own exception exits 0. |
| Bypass-after-fault guard | `enable_bypass_after_fault_guard` / `CMBAGENT_LG_BYPASS_AFTER_FAULT_GUARD` | `False` | Covers what the other three cannot see: a real removed-API fault on an earlier attempt, and a later attempt that "succeeds" by dropping the module entirely and hand-rolling the computation. Nudges the engineer once with the module's real API. |

Supporting knobs: `max_api_preflight_bounces` (`2`) caps how often one step may be bounced before
the code is allowed to run regardless; `api_digest_max_chars` (`6000`) caps the injected digest,
which is assembled in whole tiers so a name list is never cut in half, and the version manifest is
never dropped.

Two properties are worth stating explicitly. The governing rule is **prove or say nothing** — every
path returns nothing rather than a hedged answer, because a wrong report would launder a guess into
an authoritative-looking answer, which is the failure mode the feature exists to remove.
`api_preflight` is additionally conservative by construction: a symbol is reported absent only when
its module imports cleanly and the attribute genuinely is not on it, so star imports, rebound names,
accesses inside `try:`, dynamic `getattr` and unimportable modules all pass silently. The
bypass guard is a **nudge, not a block** — the code already ran and is never discarded; it sets
`bypass_after_fault_detected_live` on state and never mutates the step's status.

Ranking and merge rationale for these flags: [`docs/API_BUG_MITIGATIONS.md`](API_BUG_MITIGATIONS.md).

## Tracing

`tracing.py` builds a Langfuse `CallbackHandler` from the `LANGFUSE_*` environment variables.
LangChain callbacks propagate into child runs, so passing it once at `graph.invoke(config=...)`
traces every LLM call inside the graph, including `with_structured_output` formatter sub-calls —
there is no per-node plumbing. `langfuse_handler()` raises when keys are missing (fail loud in dev);
`langfuse_callbacks()` never raises, returns `[]`, and logs which variable is missing, so it can be
spliced into every invoke unconditionally. Cost and latency are read back per model and per agent
tag with `cmbagent-lg-cost <trace_id | work_dir>`; the executor makes no LLM call, so it never
appears. Setup, the base-URL/host distinction and the self-hosted project layout are in
[README.md § Langfuse](../README.md#langfuse-self-hosted-optional) — not repeated here.

## Running examples

`examples/` holds one runnable script per entry point: `run_planner_review.py` (planning only),
`run_self_debug*.py` (single engineer steps — plain, forced-retry, plotting, CSV, provided input,
escalation), `run_deep_research*.py` (full plan → multi-step execution, engineer-only and mixed),
the OCR demos (`ocr_*.py`, `mistral_captions.py`), two self-contained LangGraph teaching demos
(`react_demo.py`, `eval_demo.py`), and `smoke_test_keys.py` for validating provider keys.
`_common.py` is shared scaffolding, not an example. Run them as `python examples/<name>.py
[work_dir]` — they import `_common` as a sibling, so `-m` does not work. The work_dir comes from
`argv[1]`, then `$WORK_DIR`, then a timestamped default, and is cleared unless `KEEP_WORK_DIR=1`.

The stress harnesses (anti-fabrication, VLM review, context compaction, claim auditing,
robustness and held-out case sets, and the planner and reviewer case sets) are not part of this
public branch.
