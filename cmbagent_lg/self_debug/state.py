"""Graph state for the self_debug loop (topology in `self_debug/graph.py`).

`raw_engineer` is the proposer's free-form prose; `current_code` is the formatter's
structured output. `error_history` accumulates one summary per code-execution failure,
`step_feedback_history` one per goal-miss, so each evaluator sees its own prior reasoning.
"""

import operator
from typing import TypedDict, List, Dict, Optional, Annotated

from cmbagent_lg.planning.schemas import Step
from cmbagent_lg.self_debug.schemas import (
    EngineerResponse,
    ExecutionVerdict,
    StepVerdict,
    ImageReview,
)
from cmbagent_lg.timing import NodeTiming


class DebugState(TypedDict, total=False):
    # Input
    step: Step
    # Plan-step index — drives the on-disk filename `codebase/step_{N}.py`. Defaults to 1
    # for standalone runs; deep_research passes the real index.
    step_number: int

    # Retry budget
    attempts: int  # bumped by the engineer node at the start of each attempt
    # One-shot per step: set True once the escalation node has run.
    escalated: bool
    escalation_reason: Optional[str]

    # data_loader_fixer — one-shot per step, mirroring `escalated`.
    # `data_fixed`         — True once the fixer node has run.
    # `data_fixer_format`  — its verdict on the input file: "recoverable" (a loader
    #                        snippet was produced), "missing" (absent required input →
    #                        terminal not-fulfilled), or None/"unknown" (normal retry).
    # `data_fixer_guidance`— loader guidance for the retry context; persists in state, so
    #                        later attempts keep seeing it.
    data_fixed: bool
    data_fixer_format: Optional[str]
    data_fixer_guidance: Optional[str]

    # preflight — deterministic (no-LLM) inspection of `work_dir/data/`, computed once
    # before the first engineer attempt and injected into its FIRST-attempt context when
    # `PlanContext.enable_preflight` is on. Empty/absent otherwise.
    preflight_report: Optional[str]

    # api_preflight — the deterministic pre-EXECUTION API check
    # (`PlanContext.enable_api_preflight`, see `self_debug/api_preflight.py`).
    # `api_preflight_report`   — facts about a symbol the installed packages do NOT have,
    #                            set only when absence was PROVEN. Consumed and cleared by
    #                            the engineer node, so it never leaks into a later retry;
    #                            its presence is also what routes the step back to the
    #                            engineer instead of the executor.
    # `api_preflight_bounces`  — how many times this step was sent back, capped by
    #                            `max_api_preflight_bounces` so a pathological case
    #                            degrades to normal execution instead of spinning.
    # `api_preflight_bounced`  — finding-id → how many times THIS step was bounced for
    #                            it; a bounce is issued only while some finding is new
    #                            to the step. Absent on old checkpoints (readers use
    #                            `state.get(..., {}) or {}`). Per-step state, so it
    #                            resets with the rest on each sub-graph invoke.
    api_preflight_report: Optional[str]
    api_preflight_bounces: int
    api_preflight_bounced: Optional[dict]

    # Set by the terminal `missing_input` node: this step cannot be fulfilled by ANY
    # script, because the input it needs does not exist. Distinct from an exhausted
    # attempt budget — `deep_research` sends an exhausted step to the adaptive
    # replanner, but never an unfulfillable one, since a rewritten plan could only
    # invent a stand-in. Absent on every other path and on old checkpoints.
    unfulfillable: Optional[bool]

    # output_postcondition — the deterministic check that the output artifacts the STEP
    # DECLARED (paths under `data/` in its text) exist once the subprocess has returned
    # (`self_debug/output_postcondition.py`). `"missing_output"` when the run was
    # otherwise clean but a declared file is not there; the node then rewrites the
    # executor's result into a failure-shaped one and the ordinary code-gate retry path
    # takes over. None on every pass through, so a bounce never leaks into a later
    # attempt.
    output_postcondition_rejected: Optional[str]

    # infeasibility_check — the engine's own audit of a `StepVerdict` infeasibility
    # claim (`self_debug/infeasibility_check.py`), computed in `step_evaluator` where the
    # step, the manifest and the installed environment are all in hand:
    #   {"status": "verified_absent" | "refuted" | "unverified", ...}
    # None when the verdict made no such claim. The goal gate, `deep_research.run_step`
    # and the step summary all read THIS rather than re-deciding, and a declaration with
    # no audit beside it fails closed (`schemas._evidence_refuses`) — an unaudited claim
    # is the evaluator's bare word, which is what the channel must not run on.
    infeasibility_check: Optional[dict]

    # data_infeasibility — the deterministic reading of a code-gate failure as a fact
    # about the DATA rather than a code fault (`self_debug/data_infeasibility.py`).
    # `numeric_fault_seen`          — marker → the attempt numbers on which THIS step
    #                                  saw it. An OBSERVATION, not a conclusion: one
    #                                  numerical fault is usually fixable, so the name
    #                                  claims nothing.
    # `execution_infeasible_signal`  — set ONLY once one marker has recurred to
    #                                  `max_data_infeasibility_repeats`: the engine's
    #                                  CONCLUSION that the fault is a property of the
    #                                  data. It carries "infeasible" in its name
    #                                  deliberately, beside
    #                                  `logs/step_N_infeasible_execution.json`, because
    #                                  a feasibility-named record must mean the
    #                                  conclusion was drawn — three DIFFERENT failures
    #                                  in a row are progress, not infeasibility.
    # Both absent on old checkpoints (readers use `... or {}`).
    numeric_fault_seen: Optional[dict]
    execution_infeasible_signal: Optional[str]

    # static_preflight — the deterministic parse / demo-dataset / prints-anything check that runs
    # on EVERY attempt right after `format_engineer` (`self_debug/static_preflight.py`).
    # `"syntax"`, `"demo_dataset"` or `"no_stdout"` when the script was refused; the node then
    # fills the executor's `execution_*` fields with a failure-shaped result and the router sends
    # the step to `execution_evaluator` as if it had run and crashed. None on every pass
    # through (the node always writes it, so a rejection never leaks into a later attempt).
    static_preflight_rejected: Optional[str]

    # bypass-after-fault guard (`PlanContext.enable_bypass_after_fault_guard`, see
    # `self_debug/nodes.py`'s `_bypass_guard_*` helpers): a real fault that DID fire on an
    # earlier attempt, followed by a success that dropped the faulted library entirely.
    # `bypass_guard_fault`            — `(module, symbol)` of the most recent
    #                                   module-shaped API fault this step hit. Cleared
    #                                   once resolved either way.
    # `bypass_after_fault_report`     — ONE-SHOT nudge text for the next engineer attempt
    #                                   (mirrors `api_preflight_report`).
    # `bypass_after_fault_detected_live` — NOT one-shot: true forever once the guard fired
    #                                   for this step. Deliberately separate from
    #                                   `status`/`science`, which the guard never mutates.
    # `bypass_guard_nudges`           — bounded counter; the guard fires at most once.
    bypass_guard_fault: Optional[tuple]
    bypass_after_fault_report: Optional[str]
    bypass_after_fault_detected_live: Optional[bool]
    bypass_guard_nudges: int

    # Data-safety fact (`PlanContext.safe_loading`): was `work_dir/data/` EMPTY before the
    # first engineer attempt? Set ONCE — by `preflight` when enabled, else by the executor
    # on attempt 1 — and never overwritten, or a later attempt would see the files an
    # EARLIER attempt wrote (attempt N fabricates `signal.csv`, attempt N+1 "loads" it and
    # looks clean). True means any data the script analysed was created by the script;
    # step_evaluator decides from the sub-task text whether that is fabrication or the
    # point of the step. None = not determined (e.g. remote) → the rule is not rendered.
    input_dir_empty: Optional[bool]

    # Intent gate (`PlanContext.enable_intent_gate`) — set by `preflight`, and ONLY when
    # it found `data/` empty. One classifier call on the sub-task text: does this step need
    # input that was supposed to be PROVIDED (True → the engineer must refuse to invent
    # it), or is creating the data the point of the step (False → proceed normally)?
    # `intent_gate_reason` is the classifier's one-line justification, kept for the run
    # log. Both None when the gate did not run or failed — FAIL-OPEN, behaves like False.
    needs_provided_input: Optional[bool]
    intent_gate_reason: Optional[str]

    # Engineer
    raw_engineer: str
    current_code: EngineerResponse

    # Executor
    execution_stdout: str
    execution_stderr: str
    execution_returncode: int
    execution_timed_out: bool
    execution_elapsed_s: float  # subprocess wall clock (separate from node total)
    # Snapshot of data/ (filename → mtime) taken before each attempt — lets the executor
    # attribute new/modified files to this step without touching the files or their names.
    data_baseline: Dict[str, float]
    # Files this step produced (path/bytes/modified) — what step_evaluator sees.
    data_manifest: List[dict]

    # execution_evaluator — did the code RUN cleanly?
    current_execution_verdict: ExecutionVerdict

    # step_evaluator — did the run ACHIEVE the sub-task's goal?
    current_step_verdict: StepVerdict

    # image_reviewer — visual review of this step's figures (see vlm/reviewer.py).
    # `vlm_review_attempts` counts REVISION REQUESTS only (a clean pass does not burn
    # budget); `vlm_reviews_logged` is a monotonic per-call index, used only to give each
    # review a distinct audit file.
    current_image_review: ImageReview
    vlm_review_attempts: int
    vlm_reviews_logged: int

    # Accumulated across the loop — one entry per failed attempt.
    # `error_history`         — code-execution failures (execution_evaluator)
    # `step_feedback_history` — goal-misses (step_evaluator), so step_evaluator
    #                           sees its own prior feedback on later attempts
    error_history: List[str]
    step_feedback_history: List[str]

    # Evaluator model failures (the call raised even after its structured retry), kept apart from the model's
    # verdicts so a benchmark can tell them from a step the model failed:
    # `execution_verdict_fallbacks` — execution_evaluator verdicts decided from the return code and stderr instead
    # `step_evaluator_outcome`      — "verdict" | "evaluator_error" (judge the same run again, no attempt used) |
    #                                 "failed_closed" (not accepted after consecutive errors)
    # `step_evaluator_errors`       — consecutive errors for this step; a real verdict resets it
    # `step_evaluator_errors_total` — every error for this step
    # `execution_verdict_modes` / `step_verdict_modes` — one entry per verdict, naming what produced it:
    #                                 "first_attempt", "retry_no_thinking", "retry_max_tokens" (the open-model
    #                                 structured retry, `llms.answer_mode`), "model" (a provider without it),
    #                                 "deterministic_fallback" (execution) or "failed_closed" (step)
    execution_verdict_fallbacks: int
    step_evaluator_outcome: Optional[str]
    step_evaluator_errors: int
    step_evaluator_errors_total: int
    execution_verdict_modes: List[str]
    step_verdict_modes: List[str]

    # Bookkeeping
    work_dir: Optional[str]
    # appended per node pass via @timed_node; concatenated by operator.add
    node_elapsed_s: Annotated[List[NodeTiming], operator.add]
    # Set by `deep_research` in a multi-step plan: prior steps' code+output plus a
    # workspace file manifest, injected into the engineer's system prompt so step N can
    # reference / import step N-1's outputs. Absent for standalone self_debug runs.
    previous_steps_execution_summary: Optional[str]
