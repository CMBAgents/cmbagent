"""Deep-research orchestrator nodes: run one Step, then decide what's next.

`run_step` builds the cross-step context (prior summaries + a freshly-scanned
workspace file manifest) and invokes the sub-graph for the step's agent
(`engineer` → self_debug, `researcher` → researcher; anything else halts the
plan). `after_step` advances or halts based on the step's outcome.

Per-step state resets automatically — each step is a fresh sub-graph invoke —
so this state carries only the cross-step things.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END
from langgraph.runtime import Runtime

from cmbagent_lg.context import DEFAULT_AVAILABLE_AGENTS, PlanContext
from cmbagent_lg.deep_research.prompts import (
    adaptive_replanner_instructions,
    adaptive_replanner_user,
    adaptive_reviewer_instructions,
    adaptive_reviewer_user,
)
from cmbagent_lg.deep_research.file_schema import schema_line
from cmbagent_lg.deep_research.schemas import AdaptiveReview
from cmbagent_lg.deep_research.state import DeepResearchState
from cmbagent_lg.llms import ModelCallFailed, chat_model
from cmbagent_lg.persistence import save_analysis_status, save_deep_research_summary
from cmbagent_lg.planning.nodes import _build_dynamic_plan_model, _formatter
from cmbagent_lg.planning.schemas import Plan, Step
from cmbagent_lg.researcher.graph import graph as researcher_graph
from cmbagent_lg.self_debug.graph import graph as self_debug_graph
from cmbagent_lg.self_debug.prompts import NO_STDOUT_MARKER, _head_tail
from cmbagent_lg.self_debug.schemas import infeasible_substitutions
from cmbagent_lg.timing import timed_node


# Caps on one step's summary: these blocks are re-injected into every later
# step's prompt and into each checkpoint, so uncapped output is billed O(steps²).
_SUMMARY_STDOUT_HEAD, _SUMMARY_STDOUT_TAIL = 6000, 2000
_SUMMARY_CODE_HEAD, _SUMMARY_CODE_TAIL = 12000, 4000


def _checkpoint(work_dir, plan, outcomes, summaries) -> None:
    """Persist `logs/deep_research_run.json` + `logs/analysis_status.json` after every
    step (best-effort).

    Incremental writes leave a resumable checkpoint after a mid-run crash
    (next = last fulfilled step_number + 1). `analysis_status.json` is the
    machine-readable verdict on the run — `complete` only when every step is
    fulfilled — so a consumer (Denario's results stage) never has to infer it from
    the run summary or from the shape of a downstream file; the contract is in
    `cmbagent_lg.persistence`. A failed write must never break the run, so swallow
    errors, but write the status even when the summary write failed.
    """
    if not work_dir:
        return
    try:
        save_deep_research_summary(work_dir, plan, outcomes, summaries)
    except Exception as e:  # noqa: BLE001 — checkpointing must not crash the run
        print(f"[deep_research] checkpoint write failed: {e}", file=sys.stderr, flush=True)
    try:
        save_analysis_status(work_dir, plan, outcomes)
    except Exception as e:  # noqa: BLE001
        print(f"[deep_research] analysis_status write failed: {e}", file=sys.stderr, flush=True)


# ── summary + manifest builders ─────────────────────────────────────────


def _build_step_summary(step: Step, sub_state: dict) -> str:
    """One per-step block appended to `step_summaries`: a
    `### Step N (agent): sub_task` header followed by the step's artifact
    (executed code + stdout for engineer, written report for researcher). The
    next step's agent sees this in its prompt.
    """
    n = sub_state.get("step_number", 1)
    lines = [f"### Step {n} ({step.sub_task_agent}): {step.sub_task}", ""]

    if step.sub_task_agent == "researcher":
        report = (sub_state.get("current_report") or "").strip()
        report_path = sub_state.get("report_path") or ""
        if report:
            # Full report, no truncation: downstream steps may need exact
            # wording (named methods, numbers, recommendations) to act on it.
            label = f"Written report ({report_path})" if report_path else "Written report"
            lines += [f"**{label}:**", "", report, ""]
        return "\n".join(lines)

    # engineer (and any future code-running agent)
    code_obj = sub_state.get("current_code")
    code = getattr(code_obj, "python_code", "") if code_obj else ""
    stdout = (sub_state.get("execution_stdout") or "").strip()
    if code:
        # Head+tail capped generously (16 kB): the next step's engineer needs
        # the exact I/O lines (e.g. `np.savez(..., train=...)` keys) to consume
        # what this step produced.
        lines += [
            "**Executed code:**",
            "```python",
            _head_tail(code, _SUMMARY_CODE_HEAD, _SUMMARY_CODE_TAIL),
            "```",
            "",
        ]
    # Rendered unconditionally: "this step printed nothing" is information —
    # otherwise a silent run is indistinguishable from an unrecorded one.
    lines += [
        "**Execution output:**",
        _head_tail(stdout, _SUMMARY_STDOUT_HEAD, _SUMMARY_STDOUT_TAIL) if stdout
        else NO_STDOUT_MARKER,
        "",
    ]
    # A requirement the step evaluator judged IMPOSSIBLE here completed the step
    # (`StepVerdict.infeasible_requirements`), so what was NOT delivered has to travel
    # with the step: a later step reading this block, and the reader of the final
    # report, would otherwise take a complete step to mean everything was delivered.
    verdict = sub_state.get("current_step_verdict")
    substituted = (
        infeasible_substitutions(verdict, step, sub_state)
        if step.sub_task_agent == "engineer" else []   # gated exactly as `run_step` is
    )
    if substituted:
        lines += ["**NOT DELIVERED — judged infeasible in this environment:**", ""]
        lines += [f"- {r}" for r in substituted]
        justification = getattr(verdict, "infeasible_justification", None)
        if justification:
            lines += ["", f"Why: {justification}"]
        # Greppable, and the thing a reader of the final report most needs: was the
        # impossibility CHECKED against the installed environment, or only asserted?
        audit = sub_state.get("infeasibility_check") or {}
        if audit.get("status") == "verified_absent":
            lines += ["", "Checked: " + (audit.get("detail") or "verified absent")]
        else:
            lines += ["", "UNVERIFIED infeasibility claim: the engine could not check "
                          "this against the installed environment or the data."]
        lines += [""]
    return "\n".join(lines)


# Entries in the listing's "other files" section (outputs outside the four engine
# sections: root files, work/, nested data/ ...); the rest are counted, not listed.
_WORKSPACE_OTHER_MAX = 60


def _build_workspace_manifest(work_dir: Optional[str]) -> str:
    """Flat listing of files under `codebase/`, `reports/`, `data/`, `logs/`, then every
    other file in the run directory (root files, `work/`, nested `data/` subdirectories),
    up to `_WORKSPACE_OTHER_MAX` of them.

    Everything generated so far, so an agent knows the exact relative paths to
    load earlier steps' work from; each data file also gets a one-line schema
    (columns, keys, arrays: `file_schema.py`), within a total budget. (Distinct from self_debug's
    `data_manifest`, which is provenance for *one* attempt's outputs.) The other-files
    section uses the same bounded walk as that manifest (`attempt_manifest`), so it
    skips the engine's directories, caches and symlinks, and says when it was cut.

    INVARIANT: must list every step's `codebase/step_N.log`. Once a step is
    compacted to a header (`_compact_summary`), that log is the only place its
    printed findings survive, and this listing is how the researcher finds it.
    The four sections are listed in full and come first; the budget applies only to
    the other-files section.
    """
    if not work_dir:
        return ""
    wd = Path(work_dir).expanduser()
    sections = [
        ("Python scripts (codebase/)",  wd / "codebase"),
        ("Research reports (reports/)", wd / "reports"),
        ("Data files (data/)",          wd / "data"),
        ("Run logs (logs/)",            wd / "logs"),
    ]
    rendered = []
    schema_budget = 8_000   # chars of schema lines in all; later files are listed by path only
    for label, sub in sections:
        if not sub.is_dir():
            continue
        files = sorted(p.relative_to(wd) for p in sub.iterdir() if p.is_file())
        if not files:
            continue
        rendered.append(f"**{label}:**")
        for p in files:
            rendered.append(f"- {p}")
            # data files carry their schema, so a later step loads the right columns and keys
            # instead of guessing them (file_schema.py)
            if sub.name == "data" and schema_budget > 0:
                line = schema_line(wd / p)
                if line:
                    rendered.append(f"    {line}")
                    schema_budget -= len(line)
        rendered.append("")
    try:
        from cmbagent_lg.self_debug.attempt_manifest import other_workspace_files

        others, truncated = other_workspace_files(wd)
    except Exception:  # noqa: BLE001 — the listing is a convenience, never a failure
        others, truncated = [], None
    if others or truncated:
        rendered.append("**Other files in the run directory:**")
        rendered.extend(f"- {rel}" for rel, _size in others[:_WORKSPACE_OTHER_MAX])
        if len(others) > _WORKSPACE_OTHER_MAX:
            rendered.append(f"- ... and {len(others) - _WORKSPACE_OTHER_MAX} more, not listed")
        if truncated:
            rendered.append(f"- (listing incomplete: {truncated})")
        rendered.append("")
    if not rendered:
        return ""
    return (
        "**WORKSPACE FILE MANIFEST** (use these exact paths to load earlier "
        "steps' artifacts):\n\n" + "\n".join(rendered)
    )


def _compact_summary(block: str) -> str:
    """Collapse one already-rendered step summary to its header + a pointer.

    For steps outside the recency window, keep the `### Step N (agent)` header
    and replace the body with a one-line pointer: the detail still lives on disk
    and can be re-opened via the WORKSPACE FILE MANIFEST paths. Turns the
    O(steps²) carryover bill into O(steps × recent_full)."""
    lines = block.splitlines()
    # Blocks always start with the `### Step …` header; fall back to the first
    # line if the shape ever changes.
    header = next((ln for ln in lines if ln.startswith("### Step")), lines[0] if lines else "")
    # Name the exact artifacts THIS agent wrote — an engineer step persists
    # `codebase/step_N.py` + `.log`, a researcher step `reports/step_N.md`. A
    # wrong path reads as authoritative and is worse than a vague pointer.
    m = re.match(r"### Step (\d+) \((\w+)\)", header)
    if m and m.group(2) == "researcher":
        n = m.group(1)
        pointer = (
            f"_(report body elided to bound context — re-open the full write-up "
            f"from `reports/step_{n}.md` if you need it.)_"
        )
    elif m:
        n = m.group(1)
        pointer = (
            f"_(code + full output elided to bound context — recover them if needed "
            f"from `codebase/step_{n}.py` (code) and `codebase/step_{n}.log` "
            f"(its complete stdout/stderr); any data files are under `data/`.)_"
        )
    else:
        pointer = (
            "_(code + output elided to bound context — re-open this step's "
            "artifacts from the WORKSPACE FILE MANIFEST below if you need them)_"
        )
    c = f"{header}\n{pointer}"
    # Never let the pointer make a block LARGER than the content it replaces.
    return c if len(c) < len(block) else block


def _bound_summaries(summaries: list, recent_full: Optional[int]) -> tuple[list, int]:
    """Compact all but the last `recent_full` summaries to one-line headers.
    Returns (rendered_summaries, n_condensed). `recent_full=None` (or >= len)
    keeps everything verbatim. Shared by `run_step` and `replan_tail` so both
    carryover paths bound identically."""
    if recent_full is None or recent_full >= len(summaries):
        return list(summaries), 0
    cut = len(summaries) - max(0, recent_full)
    older = [_compact_summary(s) for s in summaries[:cut]]
    n_condensed = sum(1 for s, o in zip(summaries[:cut], older) if o is not s)
    return older + list(summaries[cut:]), n_condensed


def _render_previous_steps(summaries: list, manifest: str,
                           recent_full: Optional[int] = None) -> str:
    """Combined block injected into the engineer/researcher prompt as
    `{previous_steps_execution_summary}`. Empty when both are empty so a
    standalone self_debug invocation isn't polluted with empty headers.

    `recent_full` bounds the carryover: only the last `recent_full` summaries are
    rendered VERBATIM, earlier ones collapse to a one-line header (see
    `_compact_summary`). `None` keeps every summary in full."""
    if not summaries and not manifest:
        return ""
    parts = []
    if summaries:
        parts.append("----- PREVIOUS STEPS -----")
        parts.append("")
        rendered, n_condensed = _bound_summaries(summaries, recent_full)
        if n_condensed:
            parts.append(
                f"_({n_condensed} earlier step(s) condensed to headers to bound "
                "context; their outputs remain on disk — see the manifest.)_")
            parts.append("")
        parts.extend(rendered)
    if manifest:
        if parts:
            parts.append("")
        parts.append(manifest)
    return "\n".join(parts).rstrip() + "\n"


# ── run_step node ───────────────────────────────────────────────────────


def evaluator_outcome_fields(sub_state: dict) -> dict:
    """The step outcome's record of how its verdicts were produced (self_debug/state.py), so a score can tell a
    model's verdict from an evaluator failure and a thinking-off retry from a first answer:

    - `step_evaluator_errors`: every step_evaluator model failure on the step;
    - `step_evaluator_failed_closed`: how many attempts failed closed (not only whether the last one did);
    - `execution_verdict_fallbacks`: execution verdicts decided from the return code and stderr;
    - `verdict_modes`: {"step": {mode: count}, "execution": {mode: count}}, present when a mode other than "model"
      (a provider without the structured retry) occurs, so Gemini outcomes are unchanged.

    Empty for an ordinary step on a provider without the retry."""
    out: dict = {}
    if sub_state.get("step_evaluator_errors_total"):
        out["step_evaluator_errors"] = sub_state["step_evaluator_errors_total"]
    step_modes = list(sub_state.get("step_verdict_modes") or [])
    exec_modes = list(sub_state.get("execution_verdict_modes") or [])
    n_closed = step_modes.count("failed_closed")
    if not step_modes and sub_state.get("step_evaluator_outcome") == "failed_closed":
        n_closed = 1                   # a state from before the modes were recorded
    if n_closed:
        out["step_evaluator_failed_closed"] = n_closed
    if sub_state.get("execution_verdict_fallbacks"):
        out["execution_verdict_fallbacks"] = sub_state["execution_verdict_fallbacks"]
    if any(m != "model" for m in step_modes + exec_modes):
        counts = {}
        for key, modes in (("step", step_modes), ("execution", exec_modes)):
            c: dict = {}
            for m in modes:
                c[m] = c.get(m, 0) + 1
            counts[key] = c
        out["verdict_modes"] = counts
    return out


@timed_node("run_step")
def run_step(
    state: DeepResearchState, runtime: Runtime[PlanContext]
) -> DeepResearchState:
    """Invoke self_debug for one Step; collect outcome + summary; advance index."""
    plan = state["plan"]
    n = state.get("step_index", 1)
    # A restart index past the end of the plan used to surface as a bare
    # `IndexError: list index out of range` with no traceback reaching the operator.
    # Say which numbers disagree; the caller re-plans rather than resuming blind.
    if not 1 <= n <= len(plan.sub_tasks):
        raise ValueError(
            f"cannot start at step {n}: this plan has {len(plan.sub_tasks)} step(s). "
            "The restart point and the plan disagree -- re-plan instead of resuming."
        )
    step = plan.sub_tasks[n - 1]

    # Before the first step runs, leave an `in_progress` status on disk: a run that
    # crashes or is killed inside step 1 must not look like "no run" or "complete".
    if not state.get("step_outcomes"):
        _checkpoint(state.get("work_dir"), plan, [], [])

    # Step banner — the interleaved [time]/[escalation] lines from the nested
    # sub-graph run otherwise blur step boundaries together.
    bar = "━" * 70
    print(
        f"\n{bar}\n▶ STEP {n}/{len(plan.sub_tasks)}: {step.sub_task}\n{bar}",
        file=sys.stderr,
        flush=True,
    )

    summary_block = _render_previous_steps(
        state.get("step_summaries", []),
        _build_workspace_manifest(state.get("work_dir")),
        recent_full=getattr(runtime.context, "carryover_recent_full", None),
    )

    # Both sub-graphs return a StepVerdict-shaped `current_step_verdict`, so the
    # downstream outcome-building logic stays uniform.
    invoke_input = {
        "step": step,
        "work_dir": state.get("work_dir"),
        "step_number": n,
        "previous_steps_execution_summary": summary_block,
    }
    reassigned = None
    try:
        sub_state, step, plan, reassigned = _run_sub_graph(state, runtime, plan, step, n, invoke_input)
    except ModelCallFailed as err:
        # A model call that ends the run (the researcher's, after its one retry) ends it as a failed step whose
        # reason says which call failed and why, so the status file and the caller read a reason, not a traceback.
        print(f"[deep_research] step {n}: {err}", file=sys.stderr, flush=True)
        outcome = {"step_number": n, "fulfilled": False, "reason": str(err), "model_failure": err.record()}
        new_summaries = state.get("step_summaries", []) + [
            f"### Step {n} ({step.sub_task_agent}): {step.sub_task}\n\n(not completed: {err})"]
        new_outcomes = state.get("step_outcomes", []) + [outcome]
        _checkpoint(state.get("work_dir"), plan, new_outcomes, new_summaries)
        return {"step_summaries": new_summaries, "step_outcomes": new_outcomes, "step_index": n + 1}
    if sub_state is None:
        outcome = {
            "step_number": n,
            "fulfilled": False,
            "reason": (
                f"agent '{step.sub_task_agent}' not supported in v1 "
                f"(only `engineer` and `researcher` steps are implemented)"
            ),
        }
        # Append a placeholder summary too: `step_summaries` and `step_outcomes`
        # must stay length-aligned, or the adaptive reviewer is fed a mismatched
        # (summary, outcome) pair.
        placeholder = (
            f"### Step {n} ({step.sub_task_agent}): {step.sub_task}\n\n"
            f"(not executed — agent '{step.sub_task_agent}' not supported)"
        )
        new_summaries = state.get("step_summaries", []) + [placeholder]
        new_outcomes = state.get("step_outcomes", []) + [outcome]
        _checkpoint(state.get("work_dir"), plan, new_outcomes, new_summaries)
        return {
            "step_summaries": new_summaries,
            "step_outcomes": new_outcomes,
            "step_index": n + 1,
        }
    return _step_result(state, runtime, plan, step, n, sub_state, reassigned)


def _run_sub_graph(state, runtime, plan, step, n, invoke_input):
    """Run the step's sub-graph: `(sub_state, step, plan, reassigned)`, with `sub_state` None for an agent that
    is not supported. A researcher step whose run says it needs code is run once more by the engineer."""
    reassigned = None
    if step.sub_task_agent == "engineer":
        sub_state = self_debug_graph.invoke(invoke_input, context=runtime.context)
    elif step.sub_task_agent == "researcher":
        from cmbagent_lg.planning.agent_check import reassignment_enabled

        # Never the last step of a plan of several steps: that step is the final report, and an
        # engineer cannot write it. A single-step plan (one researcher step asked to compute) can
        # still move.
        final_report = n == len(plan.sub_tasks) and len(plan.sub_tasks) > 1
        allowed = reassignment_enabled(runtime.context) and not final_report
        sub_state = researcher_graph.invoke(
            {**invoke_input, "engineer_reassignment_allowed": allowed}, context=runtime.context)
        need = sub_state.get("needs_engineer") if allowed else None
        if need and not getattr(sub_state.get("current_step_verdict"), "fulfilled", False):
            # The researcher cannot execute code: run the step ONCE with the engineer instead
            # of spending its remaining attempts (`planning/agent_check.py`).
            reassigned = {"from": "researcher", "to": "engineer", "at": "run_time",
                          "source": need.get("source"), "matched": need.get("matched"),
                          "researcher_attempts": sub_state.get("attempts", 0)}
            print(f"[deep_research] step {n}: the researcher's {need.get('source')} says the work needs code "
                  f"({need.get('matched')!r}) → reassigned to the engineer for this step",
                  file=sys.stderr, flush=True)
            step = step.model_copy(update={"sub_task_agent": "engineer", "reassigned_from": "researcher"})
            tasks = list(plan.sub_tasks)
            tasks[n - 1] = step
            plan = plan.model_copy(update={"sub_tasks": tasks})
            sub_state = self_debug_graph.invoke({**invoke_input, "step": step}, context=runtime.context)
    else:
        sub_state = None
    return sub_state, step, plan, reassigned


def _step_result(state, runtime, plan, step, n, sub_state, reassigned):
    """The outcome, summary and checkpoint of a step whose sub-graph ran."""
    step_verdict = sub_state.get("current_step_verdict")
    fulfilled = bool(getattr(step_verdict, "fulfilled", False))

    # A researcher report can pass the GOAL gate yet carry a claim the
    # claim_auditor could not get revised within budget. That fails the step
    # ONLY when the claim is deterministically absent from the run's sources
    # (`claim_audit_failed`; the deliverable already carries an
    # `## UNVERIFIED CLAIMS` annotation). A surviving hard claim whose numbers
    # DO exist in the run's outputs — the auditor's meaning-level doubt, or a
    # run with `claim_audit_halts=False` — leaves the step fulfilled and is
    # surfaced as `advisory_claims`, which `analysis_status.json` lists under
    # `flagged_steps` (additive; `status` semantics unchanged).
    claim_failed = bool(sub_state.get("claim_audit_failed", False))
    if claim_failed:
        fulfilled = False

    # Engineer steps only: requirements the step evaluator judged IMPOSSIBLE with the
    # installed environment / the available data complete the step with the
    # substitution recorded, instead of the engineer spending the rest of its budget on
    # a demand nothing can satisfy (`StepVerdict.infeasible_requirements`; all-or-nothing
    # — a step with one still-fixable unmet requirement stays failed). The researcher
    # shares the schema but not the channel, so its verdicts are read exactly as before.
    substituted = (
        infeasible_substitutions(step_verdict, step, sub_state)
        if step.sub_task_agent == "engineer" and not claim_failed
        else []
    )
    # The engine's own audit of the claim (`self_debug/infeasibility_check.py`): a
    # CHECKED absence is a fact, an UNVERIFIED one is the evaluator's word. The latter is
    # allowed, but counted — more than `max_unverified_infeasible_steps` of them in one
    # plan and the run halts instead of quietly producing a paper whose every hard step
    # was declared impossible by assertion.
    audit = sub_state.get("infeasibility_check") or {}
    infeasibility_status = audit.get("status") if substituted else None
    if substituted and infeasibility_status == "unverified":
        from cmbagent_lg.self_debug.infeasibility_check import unverified_budget_spent

        if unverified_budget_spent(state.get("step_outcomes", []), runtime.context):
            print(f"[deep_research] step {n}: refusing a THIRD unverified infeasibility "
                  f"claim in this plan — halting instead of accepting it",
                  file=sys.stderr, flush=True)
            substituted = []
            infeasibility_status = "unverified_budget_spent"
    if substituted:
        fulfilled = True

    outcome = {
        "step_number": n,
        "fulfilled": fulfilled,
        "attempts": sub_state.get("attempts", 0),
        "escalated": sub_state.get("escalated", False),
    }
    outcome.update(evaluator_outcome_fields(sub_state))
    if sub_state.get("engineer_model_failures_total"):
        # Engineer calls that timed out or got a 5xx after their retry (each failed its attempt).
        outcome["engineer_model_errors"] = sub_state["engineer_model_failures_total"]
    outcome.update(_reassignment_and_input_fields(step, sub_state, reassigned))
    if substituted:
        # Reaches the run summary, the report (through the step summary above) and
        # `analysis_status.json`'s `infeasible_steps`: a step completed by substitution
        # must never read as "everything asked for was delivered".
        outcome["infeasible_requirements"] = list(substituted)
        justification = getattr(step_verdict, "infeasible_justification", None)
        if justification:
            outcome["infeasible_justification"] = justification
        # `verified_absent` | `unverified` — the difference between a checked fact and an
        # assertion, carried into `analysis_status.json` so a reader can tell them apart.
        outcome["infeasibility_status"] = infeasibility_status or "unverified"
        if audit.get("absent"):
            outcome["infeasibility_absent_symbols"] = list(audit["absent"])
        print(f"[deep_research] step {n} completed with {len(substituted)} requirement(s) "
              f"recorded as infeasible in this environment "
              f"({outcome['infeasibility_status']})", file=sys.stderr, flush=True)
    # Every input-sourced demotion the claim auditor made, carried to the status file:
    # the one model judgement that can weaken a deterministic finding is auditable.
    demotions = sub_state.get("input_sourced_demotions") or []
    if demotions:
        outcome["input_sourced_demotions"] = list(demotions)
    if fulfilled and step.sub_task_agent == "researcher":
        audit = sub_state.get("current_claim_audit")
        surviving = audit.hard() if audit is not None else []
        if surviving:
            outcome["advisory_claims"] = [c.statement for c in surviving]
    if not fulfilled:
        # Surface the most useful diagnostic.
        ev = sub_state.get("current_execution_verdict")
        sv = step_verdict
        model_reason = _engineer_model_reason(sub_state)
        if model_reason:
            # The engineer's model call failed on the step's last attempt (two in a row, or one on the attempt cap):
            # that, not the evaluator's judgement of the stand-in script or an earlier verdict, is the reason.
            outcome["reason"] = model_reason
            outcome["model_failure"] = {"role": "engineer", "reason": model_reason}
        elif claim_failed:
            audit = sub_state.get("current_claim_audit")
            hard = audit.hard() if audit is not None else []
            outcome["reason"] = (
                "unverified claims — "
                + "; ".join(c.statement for c in hard[:3])
                if hard else "unverified claims in the report"
            )
            outcome["unverified_claims"] = [c.statement for c in hard]
        elif infeasibility_status == "unverified_budget_spent":
            outcome["reason"] = (
                "refused: this plan has already passed its allowance of UNVERIFIED "
                "infeasibility claims, and this one names nothing the engine could "
                "check — "
                + (getattr(sv, "infeasible_justification", None) or "no justification")
            )
            outcome["infeasibility_status"] = infeasibility_status
        else:
            # `current_step_verdict` is written when a step is JUDGED and never
            # cleared within the step, so after a later attempt CRASHED it still
            # holds an earlier attempt's judgement. Preferring it unconditionally
            # reported the wrong cause to the operator and -- through
            # `_exhaustion_brief` -- sent the adaptive replanner after the wrong
            # problem: Project7 died on a 41.2 TiB allocation in `parafac` but
            # reported a stale verdict about `n_jobs` from eight minutes earlier.
            # When the last attempt failed in EXECUTION, that failure is the reason.
            _sv_text = (
                getattr(sv, "feedback", None)
                or (getattr(sv, "unmet_requirements", None) or [None])[0]
            )
            _ev_text = getattr(ev, "error_summary", None)
            _ev_failed = getattr(ev, "status", None) == "failure"
            outcome["reason"] = (
                (_ev_text or _sv_text) if _ev_failed else (_sv_text or _ev_text)
            ) or "step not fulfilled"
        # A step that spent its WHOLE attempt budget is the one failure the adaptive
        # replanner can answer; `route_after_review` reads this flag (and the diagnostic
        # fields beside it) instead of halting the run. See `_step_exhausted`.
        if _step_exhausted(sub_state, step, runtime.context):
            outcome["exhausted"] = True
            outcome["unmet_requirements"] = list(
                getattr(sv, "unmet_requirements", None) or [])
            # Names the API pre-flight PROVED absent from this environment — the most
            # actionable thing a rewritten plan can be told not to depend on.
            bounced = sub_state.get("api_preflight_bounced") or {}
            if bounced:
                outcome["api_preflight_findings"] = sorted(bounced)
            # The code gate's own infeasibility finding, if that is what stopped it.
            signal = sub_state.get("execution_infeasible_signal")
            if signal:
                outcome["data_infeasible"] = signal

    new_summaries = state.get("step_summaries", []) + [_build_step_summary(step, sub_state)]
    new_outcomes = state.get("step_outcomes", []) + [outcome]
    # Checkpoint after every completed step so a later crash is recoverable.
    _checkpoint(state.get("work_dir"), plan, new_outcomes, new_summaries)
    out = {
        "step_summaries": new_summaries,
        "step_outcomes": new_outcomes,
        "step_index": n + 1,
    }
    if reassigned:
        out["plan"] = plan          # the step now names the agent that ran it
    return out


def _reassignment_and_input_fields(step: Step, sub_state: dict, reassigned: Optional[dict]) -> dict:
    """Step-outcome fields for a researcher step moved to the engineer (`reassigned`: at "plan"
    by the plan check, or at "run_time") and for the missing-input gate: `missing_input` when the
    step ended because a provided input was found nowhere, `input_searches` for every search the
    gate ran (a found location means the attempt was retried, not ended). Empty otherwise."""
    out: dict = {}
    if reassigned:
        out["reassigned"] = dict(reassigned)
    elif getattr(step, "reassigned_from", None):
        out["reassigned"] = {"from": step.reassigned_from, "to": step.sub_task_agent, "at": "plan"}
    record = sub_state.get("missing_input_record")
    if sub_state.get("unfulfillable") or record:
        rec = record or {}
        search = rec.get("search") or {}
        out["missing_input"] = {"missing": rec.get("missing"), "searched_roots": search.get("roots"),
                                "entries_seen": search.get("entries_seen"), "truncated": search.get("truncated")}
    elif sub_state.get("data_fixer_format") == "missing":
        out["missing_input"] = {"missing": None, "by": "data_fixer"}
    searches = sub_state.get("missing_input_searches")
    if searches:
        out["input_searches"] = list(searches)
    return out


# ── linear router (not wired into the graph; see `route_after_review`) ─────


def after_step(
    state: DeepResearchState, runtime: Runtime[PlanContext]
) -> str:
    """Halt on step failure; END when the plan is exhausted; else next step.

    The original linear router. The graph routes on `route_after_review`, which
    reduces to exactly this when adaptive planning is disabled.
    """
    outcomes = state.get("step_outcomes", [])
    if outcomes and not outcomes[-1].get("fulfilled", False):
        return END
    if state.get("step_index", 1) > len(state["plan"].sub_tasks):
        return END
    return "run_step"


# ══════════════════════════════════════════════════════════════════════════
# Adaptive planning — ON BY DEFAULT, gated by `PlanContext.adaptive_planning`.
#
#   run_step ─► adaptive_review ─(route_after_review)─► replan_tail ─► format_tail
#                    │ no / disabled                                        │
#                    └──────────► run_step / END ◄──── merge_plan ◄─────────┘
#                                              (route_after_merge)
#
# When adaptive planning is off (or the plan is exhausted / the step failed),
# `adaptive_review` early-returns with NO LLM call and `route_after_review`
# collapses to `after_step`. The one failure that does NOT collapse is a step that
# exhausted its attempt budget: it takes the replan branch once (bounded by the same
# `max_adaptations`) rather than halting the run — see `_replannable_exhaustion`.
#
# Termination has two INDEPENDENT backstops: the plan-length cap
# (`ctx.maximum_number_of_steps_in_plan`) enforced by `merge_plan`, and the
# max-adaptations cap (`ctx.max_adaptations`) enforced by `route_after_review`.
# ══════════════════════════════════════════════════════════════════════════


# Pinned non-lite and overridable only from the env: a lite reviewer mis-triggers
# replans and a lite replanner writes a weaker tail. `PlanContext.planner_model`
# / `plan_reviewer_model` configure the INITIAL planning loop and are ignored
# here; the formatter does use `ctx.formatter_model`.
_DEFAULT_ADAPTIVE_REVIEWER_MODEL = "gemini-3.1-pro-preview"   # critic role
_DEFAULT_ADAPTIVE_PLANNER_MODEL = "gemini-3.6-flash"          # generator role

# Used when the run context is unavailable (e.g. a router unit-tested with
# `runtime=None`). Mirrors the `PlanContext.max_adaptations` default.
_DEFAULT_MAX_ADAPTATIONS = 3


def _reviewer_model(ctx=None) -> str:
    """Model for `adaptive_review` reasoning: `PlanContext.adaptive_reviewer_model`, else the env
    override, else the non-lite default."""
    return (getattr(ctx, "adaptive_reviewer_model", None) or os.environ.get("ADAPTIVE_REVIEWER_MODEL")
            or _DEFAULT_ADAPTIVE_REVIEWER_MODEL)


def _planner_model(ctx=None) -> str:
    """Model for `replan_tail` reasoning: `PlanContext.adaptive_planner_model`, else the env
    override, else the non-lite default."""
    return (getattr(ctx, "adaptive_planner_model", None) or os.environ.get("ADAPTIVE_PLANNER_MODEL")
            or _DEFAULT_ADAPTIVE_PLANNER_MODEL)


# ── run-context helpers (tolerate runtime=None in unit tests) ──────────────


def _context(runtime) -> Optional[PlanContext]:
    """The `PlanContext` for this run, or None when called without a runtime."""
    return getattr(runtime, "context", None) if runtime is not None else None


def _max_adaptations(runtime) -> int:
    """The max-adaptations backstop for this run (ctx override or default)."""
    ctx = _context(runtime)
    return getattr(ctx, "max_adaptations", _DEFAULT_MAX_ADAPTATIONS) if ctx else _DEFAULT_MAX_ADAPTATIONS


def _available_agent_names(runtime) -> set:
    """The agent names a step may be assigned to — derived the same way
    `_build_dynamic_plan_model` / the planner does. Falls back to the default
    team when no run context is available."""
    ctx = _context(runtime)
    agents = getattr(ctx, "available_agents", None) if ctx else None
    return {name for name, _ in (agents or DEFAULT_AVAILABLE_AGENTS)}


def _step_key(step: Step) -> tuple:
    """Content identity of a step for prefix-collision de-duplication: two steps
    are 'the same work' iff sub_task, agent and bullet_points match."""
    return (step.sub_task, step.sub_task_agent, tuple(step.bullet_points))


def _last_step_failed(state: DeepResearchState) -> bool:
    outcomes = state.get("step_outcomes", [])
    return bool(outcomes) and not outcomes[-1].get("fulfilled", False)


# ── exhaustion → replan instead of halt ────────────────────────────────────
#
# A step that burns its whole engineer budget used to end the RUN: `self_debug`'s goal
# gate ENDs on exhaustion, `adaptive_review` early-returned on any failed step, and
# `route_after_review` halted. Eight breast-cancer-fleet projects died exactly there,
# spending 30% of all attempt wall-clock re-arguing with the step evaluator. The plan
# was the thing that needed rewriting, and the replanner was the one component the
# failure switched off. These two predicates re-enable it for that case only.


def _step_exhausted(sub_state: dict, step: Step, ctx) -> bool:
    """Did this step fail in a way a REWRITTEN PLAN can answer?

    Two shapes qualify:
      * it spent its whole attempt budget — the engineer kept trying and the evaluator
        kept refusing, so the requirement, not the code, is what has to change. NOTE,
        deliberately, that this does NOT care which gate did the rejecting. A step that
        crashed eight times at the CODE gate is replannable on exactly the same terms as
        one the goal gate refused eight times, and that is the point rather than an
        oversight: `StepVerdict.infeasible_requirements` only exists on the goal-gate
        path, so a crashed attempt has no infeasible channel BY CONSTRUCTION, and
        replan-on-exhaustion is the only backstop it has. On the metabric fleet roughly
        half of the failed attempts in these loops never produced a `StepVerdict` at all
        (case3 6 of 8, case5 4 of 8, case4 3 of 7), so a mitigation confined to the
        verdict schema would leave half the wasted wall-clock untouched;
      * the code gate stopped it on a repeated DATA infeasibility
        (`execution_infeasible_signal`, see `self_debug/data_infeasibility.py`): a fit
        that will not converge on this cohort is a fact about the data, and the plan is
        the only thing left that can change. It stops early, so its `attempts` are
        BELOW the cap — without this clause an early stop would halt the run sooner
        than the eight-attempt grind it replaces.

    Excluded, because a rewrite could only make them worse: a step the terminal
    `missing_input` node marked `unfulfillable` and a `data_fixer` "missing" verdict
    (the required input does not exist — replanning around it is pressure to invent a
    stand-in), the researcher's claim audit (a report with unverified numbers is not a
    planning problem), and any non-engineer step (the channel is the engineer path's).
    """
    if sub_state.get("unfulfillable"):
        return False
    if _engineer_model_reason(sub_state):     # the model server failed, not the plan
        return False
    if sub_state.get("data_fixer_format") == "missing":
        return False
    if sub_state.get("claim_audit_failed"):
        return False
    if getattr(step, "sub_task_agent", None) != "engineer":
        return False
    if sub_state.get("execution_infeasible_signal"):
        return True
    max_n = getattr(ctx, "max_n_attempts", 3) if ctx is not None else 3
    return sub_state.get("attempts", 0) >= max_n


def _engineer_model_reason(sub_state: dict) -> str | None:
    """Why the step's last engineer attempt had no code because its model call failed, else None: the
    consecutive failures that ended the step (`engineer_model_failed`), or a single failure on the last attempt
    the cap allowed (`engineer_model_failures` still above 0: an answered call resets it)."""
    if sub_state.get("engineer_model_failed"):
        return sub_state["engineer_model_failed"]
    if (sub_state.get("engineer_model_failures") or 0) > 0:
        return sub_state.get("engineer_model_last_error") or "the engineer model call failed on the last attempt"
    return None


def _replannable_exhaustion(state: DeepResearchState, runtime) -> bool:
    """May the last (failed) step be answered by REWRITING THE TAIL instead of halting?

    True only when adaptive planning is on, the step exhausted its budget (`exhausted`,
    stamped on the outcome by `run_step`) and the adaptation budget has room. Everything
    else halts exactly as before — a disabled-adaptive run (see
    `tests/test_adaptive_disabled.py`), a structurally unfulfillable step, a spent
    `ctx.max_adaptations`. The cap is checked HERE, before `adaptive_review`'s LLM call,
    so a run with no budget left pays nothing to discover that.
    """
    ctx = _context(runtime)
    if not getattr(ctx, "adaptive_planning", True):
        return False
    outcomes = state.get("step_outcomes", [])
    if not outcomes or outcomes[-1].get("fulfilled", False):
        return False
    if not outcomes[-1].get("exhausted"):
        return False
    return len(state.get("adaptive_history", [])) < _max_adaptations(runtime)


def _exhaustion_brief(outcome: dict) -> str:
    """What the reviewer and the replanner are told about an exhausted step: that the
    run halts unless the tail changes, what the evaluator kept rejecting, and which
    names the environment is PROVEN not to have."""
    bits = [
        "IMPORTANT — the step just completed FAILED and the engineer cannot be sent "
        f"back to it (it used {outcome.get('attempts')} attempt(s)). The run HALTS here "
        "unless the remaining plan is rewritten, so that is the decision in front of "
        "you: a tail that reaches the overall task another way, or the halt."
    ]
    reason = outcome.get("reason")
    if reason:
        bits.append(f"The evaluator's last reason: {reason}")
    unmet = outcome.get("unmet_requirements") or []
    if unmet:
        bits.append("Requirements it kept rejecting:\n"
                    + "\n".join(f"  - {u}" for u in unmet))
    signal = outcome.get("data_infeasible")
    if signal:
        bits.append(
            "The failure was a property of the DATA, not a code fault — the engine "
            f"classified it as `{signal}` and stopped retrying because the same fault "
            "came back. Retrying the same design cannot fix it: the rewritten tail has "
            "to ask for something the data can actually support (a simpler model, "
            "fewer interaction terms, pooled categories, or the quantity dropped and "
            "reported as unestimable).")
    findings = outcome.get("api_preflight_findings") or []
    if findings:
        bits.append(
            "The pre-execution API check PROVED these names absent from the installed "
            "environment, so nothing in a rewritten tail may depend on them:\n"
            + "\n".join(f"  - {f}" for f in findings))
    bits.append(
        "Re-proposing the failed step as written will fail the same way. Prefer a tail "
        "that reaches the goal with what IS installed and what the data actually "
        "contains — a different method, a simpler quantity, or dropping an impossible "
        "requirement and saying so. Never propose fabricating or substituting data, and "
        "never propose deleting what earlier steps produced.")
    return "\n\n".join(bits)


_FORMATTER_SYS_REVIEW = (
    "You are a formatter. Convert the reviewer's prose into an AdaptiveReview "
    "object. Preserve the decision and every recommendation. Do not invent new "
    "facts. If needs_adaptation is not explicitly stated, infer it conservatively "
    "(default to False when unclear)."
)
_FORMATTER_SYS_TAIL = (
    "You are a formatter. Convert the planner's prose into a Plan object (an "
    "ordered list of steps). Preserve every step, its agent, and its instructions. "
    "Do not invent new facts. If a field is not explicitly stated, infer it "
    "conservatively from context."
)


# ── adaptive review ────────────────────────────────────────────────────────


@timed_node("adaptive_review")
def adaptive_review(
    state: DeepResearchState, runtime: Runtime[PlanContext]
) -> DeepResearchState:
    """Decide whether the remaining plan should be rewritten after this step.

    EARLY-RETURN (no LLM call, no state change) when adaptive planning is off or
    there is nothing to review — plan exhausted / last step just ran, or the step
    failed and the run will halt.

    The ONE exception is a step that failed by exhausting its attempt budget
    (`_replannable_exhaustion`): the run halts unless the tail is rewritten, so this is
    precisely when the replanner is needed, and the review runs even with no tail left
    — a rewritten tail may be what finishes the task. `merge_plan`'s length cap and
    `ctx.max_adaptations` still bound it, and halt remains the fallback when the
    reviewer has nothing to change.

    Otherwise a critic reasoning model writes a PROSE assessment and a formatter
    structures it into `AdaptiveReview`. Fail-safe: any failure ⇒ a
    no-adaptation verdict so the run simply continues.
    """
    ctx = _context(runtime)
    plan = state["plan"]

    exhausted = _replannable_exhaustion(state, runtime)
    if not getattr(ctx, "adaptive_planning", True) or (   # disabled → zero-overhead
        not exhausted
        and (
            state.get("step_index", 1) > len(plan.sub_tasks)  # exhausted / last step
            or _last_step_failed(state)                       # step failed → run halts
        )
    ):
        return {}

    n_done = state.get("step_index", 1) - 1                  # steps executed so far
    remaining = plan.sub_tasks[n_done:]                      # the tail not yet run
    last_summary = (state.get("step_summaries") or ["(no summary)"])[-1]
    last_outcome = (state.get("step_outcomes") or [{}])[-1]
    remaining_fmt = (
        Plan(sub_tasks=remaining).format() if remaining else "(no steps remain)"
    )

    # Critic reasoning model → PROSE assessment (guarded: a provider error or
    # refusal must never abort the run).
    prose = None
    try:
        reasoner = chat_model(_reviewer_model(ctx), "critic")
        msg = reasoner.invoke(
            [
                SystemMessage(adaptive_reviewer_instructions(ctx)),
                HumanMessage(adaptive_reviewer_user(
                    last_summary, last_outcome, remaining_fmt,
                    # Only on the exhaustion path; None leaves the prompt unchanged.
                    failure_block=_exhaustion_brief(last_outcome) if exhausted else None,
                )),
            ],
            config={"tags": ["adaptive_review"]},
        )
        prose = msg.text
    except Exception as e:  # noqa: BLE001 — a bad review must not kill the run
        print(f"[adaptive_review] reasoning failed — fail-safe (no adaptation): {e}",
              file=sys.stderr, flush=True)
        prose = None
    if not prose:
        return {"adaptive_review": AdaptiveReview(
            needs_adaptation=False, recommendations=[],
            reason="review reasoning failed — fail-safe: continue")}

    # Formatter structures the prose into AdaptiveReview (fail-safe).
    review = None
    try:
        structured = _formatter(ctx).with_structured_output(AdaptiveReview)
        review = structured.invoke(
            [SystemMessage(_FORMATTER_SYS_REVIEW), HumanMessage(prose)],
            config={"tags": ["format_adaptive_review"]},
        )
    except Exception as e:  # noqa: BLE001 — a parse failure must not kill the run
        print(f"[adaptive_review] formatter failed — fail-safe (no adaptation): {e}",
              file=sys.stderr, flush=True)
        review = None
    if review is None:
        review = AdaptiveReview(
            needs_adaptation=False, recommendations=[],
            reason="review failed — fail-safe: continue")
    return {"adaptive_review": review}


# ── tail replan (PROSE) ────────────────────────────────────────────────────


@timed_node("replan_tail")
def replan_tail(
    state: DeepResearchState, runtime: Runtime[PlanContext]
) -> DeepResearchState:
    """Rewrite the remaining steps in light of the recommendations — PROSE only.

    The generator reasoning model writes prose; `format_tail` structures it into
    a `Plan`. The reasoning model NEVER calls `with_structured_output` directly.
    """
    ctx = _context(runtime)
    plan = state["plan"]
    n_done = state.get("step_index", 1) - 1
    remaining = plan.sub_tasks[n_done:]
    review = state["adaptive_review"]
    # The replanner is told the same thing the reviewer was when the tail is being
    # rewritten BECAUSE a step ran out of attempts, not because a result suggested a
    # better next step.
    last_outcome = (state.get("step_outcomes") or [{}])[-1]
    failure_block = (
        _exhaustion_brief(last_outcome)
        if _replannable_exhaustion(state, runtime) else None
    )

    # Bound the replanner's completed-steps context exactly as run_step bounds
    # the carryover, so up to `max_adaptations` re-plans don't each re-send every
    # prior summary in full.
    _summaries, _ = _bound_summaries(
        list(state.get("step_summaries", [])),
        getattr(ctx, "carryover_recent_full", None),
    )
    completed_fmt = "\n".join(_summaries) or "(none)"
    remaining_fmt = (
        Plan(sub_tasks=remaining).format() if remaining else "(no steps remain)"
    )

    try:
        reasoner = chat_model(_planner_model(ctx), "generator")
        msg = reasoner.invoke(
            [
                SystemMessage(adaptive_replanner_instructions(ctx)),
                HumanMessage(
                    adaptive_replanner_user(completed_fmt, remaining_fmt,
                                            review.recommendations,
                                            failure_block=failure_block)
                ),
            ],
            config={"tags": ["replan_tail"]},
        )
        raw = msg.text
    except Exception as e:  # noqa: BLE001 — a failed replan is a no-op, never a crash
        print(f"[replan_tail] reasoning failed — fail-safe (keep existing tail): {e}",
              file=sys.stderr, flush=True)
        raw = ""
    return {"raw_tail": raw}


# ── tail formatter (PROSE → Plan) ──────────────────────────────────────────


@timed_node("format_tail")
def format_tail(
    state: DeepResearchState, runtime: Runtime[PlanContext]
) -> DeepResearchState:
    """Structure the replanner's prose tail into a `Plan` (`new_tail`).

    Reuses `format_plan`'s dynamic-agent structured-output path (the formatter
    constrains `sub_task_agent` to `Literal[*ctx.available_agents]`). On a parse
    failure — or on empty prose from `replan_tail` — emits an empty tail, which
    `merge_plan` treats as "keep the existing remaining tail".
    """
    raw = state.get("raw_tail")
    if raw is None:
        # A caller/test set `new_tail` directly without prose — pass through
        # unchanged; do not clobber the tail.
        return {}
    if not raw:
        # `replan_tail` ran but its reasoning failed. Empty tail → merge_plan
        # keeps the existing remaining tail.
        return {"new_tail": Plan(sub_tasks=[])}

    ctx = _context(runtime)
    try:
        DynPlan = _build_dynamic_plan_model(ctx.available_agents)
        structured = _formatter(ctx).with_structured_output(DynPlan)
        dyn = structured.invoke(
            [SystemMessage(_FORMATTER_SYS_TAIL), HumanMessage(raw)],
            config={"tags": ["format_tail"]},
        )
        # The plan check of `format_plan`, on the replanned tail (whose last step is the
        # plan's last step, the final report).
        from cmbagent_lg.planning.agent_check import reassign_code_steps

        tail, _ = reassign_code_steps(Plan(**dyn.model_dump()), ctx, label="format_tail")
        return {"new_tail": tail}
    except Exception as e:  # noqa: BLE001 — a parse failure is a no-op, never a crash
        print(f"[format_tail] formatter failed — fail-safe (keep existing tail): {e}",
              file=sys.stderr, flush=True)
        return {"new_tail": Plan(sub_tasks=[])}


# ── merge ──────────────────────────────────────────────────────────────────


@timed_node("merge_plan")
def merge_plan(
    state: DeepResearchState, runtime: Runtime[PlanContext]
) -> DeepResearchState:
    """Splice the fixed completed prefix and the (validated, bounded) replanned tail.

    Applies, IN ORDER, the merge-time safety rules:
      1. an empty replan tail is a NO-OP — keep the existing remaining tail
         rather than collapsing the plan to just the completed prefix;
      2. reject a replan whose steps name an unknown agent (such a step would
         dead-end) — keep the prior remaining tail;
      3. drop any new-tail step content-identical to a completed (frozen) prefix
         step, so already-done work is never re-executed;
      4. bound the merged length by `ctx.maximum_number_of_steps_in_plan`,
         truncating the TAIL only (the frozen prefix is never touched).
    """
    ctx = _context(runtime)
    plan = state["plan"]
    n_done = state.get("step_index", 1) - 1                  # steps executed so far
    completed_prefix = plan.sub_tasks[:n_done]              # held fixed
    existing_tail = list(plan.sub_tasks[n_done:])           # current remaining

    # `step_outcomes` is a PLAIN-REPLACE channel: after n_done executed steps it
    # holds exactly n_done outcomes. Catch a reducer change here rather than
    # silently mis-freezing the prefix. (Skipped when a caller — e.g. a focused
    # merge unit test — didn't thread outcomes through.)
    outcomes = state.get("step_outcomes")
    if outcomes is not None:
        assert len(outcomes) == n_done, (
            f"step_outcomes/step_index desync: len={len(outcomes)} != n_done={n_done} "
            "(step_outcomes must stay a plain-replace channel)"
        )

    new_tail = list(state["new_tail"].sub_tasks)

    if not new_tail:
        # Empty replan — no-op, keep the existing tail.
        new_tail = list(existing_tail)
    elif any(s.sub_task_agent not in _available_agent_names(runtime) for s in new_tail):
        # Unknown agent in the tail — reject the whole replan.
        new_tail = list(existing_tail)
    else:
        # Strip any step that re-adds already-completed (frozen) work.
        prefix_keys = {_step_key(s) for s in completed_prefix}
        new_tail = [s for s in new_tail if _step_key(s) not in prefix_keys]
        if not new_tail:
            # De-dup emptied the tail — fall back to the no-op (never collapse).
            new_tail = list(existing_tail)

    merged_tasks = list(completed_prefix) + new_tail

    # Enforce the plan-length cap by truncating the tail (never the prefix).
    cap = getattr(ctx, "maximum_number_of_steps_in_plan", None) if ctx else None
    if cap is not None and len(merged_tasks) > cap:
        keep = max(cap - len(completed_prefix), 0)
        new_tail = new_tail[:keep]
        merged_tasks = list(completed_prefix) + new_tail

    merged = Plan(sub_tasks=merged_tasks)

    history = list(state.get("adaptive_history", []))
    history.append(
        {
            "after_step": n_done,
            "recommendations": list(state["adaptive_review"].recommendations),
            # Audit trail of what the adaptation swapped.
            "old_tail": [s.sub_task for s in existing_tail],
            "new_tail": [s.sub_task for s in new_tail],
        }
    )
    return {"plan": merged, "adaptive_history": history}


# ── adaptive routers ───────────────────────────────────────────────────────


def route_after_review(state: DeepResearchState, runtime: Runtime[PlanContext]) -> str:
    """Halt on failure / when the plan is exhausted; else adapt-or-advance.

    With adaptive planning disabled (`state["adaptive_review"]` absent) this
    reduces to exactly `after_step`. Adapt-or-advance is gated by two backstops:
    the max-adaptations cap (`ctx.max_adaptations`), and the empty-
    recommendations guard — `needs_adaptation=True` with nothing actionable is
    treated as "no adaptation".

    One failure is NOT an unconditional halt: a step that exhausted its attempt budget
    (`_replannable_exhaustion`) gets one pass at the replanner first, since rewriting
    the tail is the only thing left that can finish the task. HALT STAYS THE FALLBACK —
    adaptive planning off, no replan budget, a structurally unfulfillable step, or a
    reviewer with nothing to change all end the run here, exactly as before.
    """
    outcomes = state.get("step_outcomes", [])
    last_failed = bool(outcomes) and not outcomes[-1].get("fulfilled", False)
    replannable = last_failed and _replannable_exhaustion(state, runtime)
    if last_failed and not replannable:
        return END
    if not last_failed and state.get("step_index", 1) > len(state["plan"].sub_tasks):
        return END
    # Max-adaptations backstop — stop adapting once the cap is reached. Unreachable
    # while `last_failed` (the cap is part of `_replannable_exhaustion`), but kept
    # explicit so the two cannot drift apart.
    if len(state.get("adaptive_history", [])) >= _max_adaptations(runtime):
        return END if last_failed else "run_step"
    review = state.get("adaptive_review")
    # Only adapt when the reviewer both asked to AND gave something to act on.
    if (
        review is not None
        and getattr(review, "needs_adaptation", False)
        and getattr(review, "recommendations", None)
    ):
        return "replan_tail"
    return END if last_failed else "run_step"


def route_after_merge(state: DeepResearchState, runtime: Runtime[PlanContext]) -> str:
    """After merging the new tail, continue executing unless nothing remains."""
    if state.get("step_index", 1) > len(state["plan"].sub_tasks):
        return END
    return "run_step"


__all__ = [
    "run_step",
    "after_step",
    "adaptive_review",
    "replan_tail",
    "format_tail",
    "merge_plan",
    "route_after_review",
    "route_after_merge",
]
