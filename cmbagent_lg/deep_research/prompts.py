"""Prompt builders for the adaptive reviewer and the tail replanner.

Both reasoning models write free-form PROSE; a downstream formatter converts it
into the typed `AdaptiveReview` / `Plan` objects.
"""

from __future__ import annotations

from typing import List

from cmbagent_lg.context import PlanContext


def adaptive_reviewer_instructions(ctx: PlanContext) -> str:
    return (
        "You are the adaptive plan reviewer in a step-by-step research system.\n"
        f"The overall task is:\n{ctx.main_task}\n\n"
        "A plan is being executed one step at a time. You are called right after "
        "a step finishes. Given a summary of what that step produced and the list "
        "of steps that have not yet run, decide whether the remaining steps should "
        "be rewritten so the plan still makes sense in light of the latest result.\n\n"
        "Adapt only when the new information genuinely changes what should happen "
        "next (e.g. a result invalidates a later step, or suggests a better one). "
        "If the remaining steps are still appropriate, do not adapt.\n\n"
        "NEVER recommend an adaptation whose PURPOSE is to discard, delete, clean "
        "up, or move artifacts that earlier steps already produced — those outputs "
        "are what the run must still be holding when it ends. (Recomputing or "
        "correcting an artifact in place is fine; removing it is not.) If an "
        "earlier step already produced what a later step was meant to produce, the "
        "remaining step is already SATISFIED: recommend dropping it (or reducing it "
        "to a read-only verification) — never recommend removing the outputs. A "
        "research run must END holding its results, not tidy them away.\n\n"
        "Preserve the separation of expensive work from its presentation: when the "
        "visualization, post-processing, or reporting of a result an earlier step "
        "already computed and saved should change, recommend rewriting that cheap "
        "presentation step to read the saved artifacts back from disk — do NOT "
        "recommend re-running the expensive computation that produced them, and do "
        "NOT recommend folding the presentation back into a compute step.\n\n"
        "Write your assessment in natural prose — a downstream specialist will "
        "extract the structured decision. State clearly whether the remaining "
        "steps need to be rewritten and, if so, give concrete recommendations for "
        "the remaining steps only."
    )


def adaptive_reviewer_user(last_summary: str, last_outcome: dict, remaining_fmt: str,
                           failure_block: str | None = None) -> str:
    """The reviewer's turn. `failure_block` is set only when the step being reviewed
    FAILED by exhausting its attempt budget, in which case the choice is a rewritten
    tail or a halted run; None renders the prompt exactly as before."""
    failure = f"----- WHY YOU ARE BEING CALLED -----\n{failure_block}\n\n" if failure_block else ""
    return (
        f"{failure}"
        "----- STEP JUST COMPLETED (summary) -----\n"
        f"{last_summary}\n\n"
        f"Outcome: {last_outcome}\n\n"
        "----- REMAINING STEPS (not yet executed) -----\n"
        f"{remaining_fmt}\n\n"
        "Decide whether the remaining steps need to be rewritten. Explain your "
        "decision, and if the tail should change give concrete recommendations "
        "for the remaining steps only."
    )


def adaptive_replanner_instructions(ctx: PlanContext) -> str:
    return (
        "You are the planner in an adaptive research system, rewriting the tail "
        "of a plan mid-execution.\n"
        f"The overall task is:\n{ctx.main_task}\n\n"
        "The already-completed steps are FIXED and must not be reproposed. Produce "
        "a new ordered list of steps to REPLACE the remaining (not-yet-executed) "
        "steps, incorporating the reviewer's recommendations. Keep the new tail "
        "focused and minimal — only what is still needed to finish the task.\n\n"
        "Two hard rules: (1) the replacement steps must PRESERVE every deliverable "
        "the completed steps produced — never propose a step whose purpose is to "
        "delete, remove, clean up, or move an earlier step's output files; those "
        "are the results the run must end holding (replacing a wrong artifact with "
        "a corrected one is allowed; discarding it is not). (2) If the remaining work is already "
        "done, return an EMPTY tail (no steps) — a plan that has produced its "
        "deliverables is COMPLETE, not in need of teardown or tidying. Never "
        "manufacture cleanup/verification-by-deletion work to fill the tail.\n\n"
        "Keep expensive work separate from its presentation: a step that "
        "visualizes, post-processes, or reports a result an earlier step already "
        "computed and saved must READ those saved artifacts from disk — never fold "
        "such presentation back into a step that re-runs the expensive computation, "
        "and never merge a heavy computation and its presentation into one step. If "
        "you add or rewrite a presentation/post-processing step, have it load the "
        "existing outputs rather than recompute them.\n\n"
        "Write the replacement tail in natural prose — a downstream specialist "
        "will extract the structured steps. For each step describe the sub-task, "
        "which agent runs it, and the bullet-point instructions."
    )


def adaptive_replanner_user(
    completed_fmt: str, remaining_fmt: str, recommendations: List[str],
    failure_block: str | None = None,
) -> str:
    """The replanner's turn. `failure_block` is set only when the tail is being
    rewritten BECAUSE a step ran out of attempts (not because a result suggested a
    better next step); None renders the prompt exactly as before."""
    recs = "\n".join(f"- {r}" for r in recommendations) or "(none)"
    failure = f"----- WHY THE TAIL IS BEING REWRITTEN -----\n{failure_block}\n\n" if failure_block else ""
    return (
        f"{failure}"
        "----- COMPLETED STEPS (fixed — do not reproduce) -----\n"
        f"{completed_fmt}\n\n"
        "----- CURRENT REMAINING STEPS (to be replaced) -----\n"
        f"{remaining_fmt}\n\n"
        "----- REVIEWER RECOMMENDATIONS -----\n"
        f"{recs}\n\n"
        "Now describe the replacement tail as an ordered list of steps."
    )
