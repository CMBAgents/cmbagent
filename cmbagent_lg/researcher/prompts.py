"""Prompt loaders for the researcher loop. Same shape as self_debug/prompts.py."""

from typing import List, Optional

from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Step
from cmbagent_lg.prompt_utils import SafeDict, load_yaml


_TEMPLATES = "cmbagent_lg.researcher.templates"
RESEARCHER_YAML = load_yaml(_TEMPLATES, "researcher.yaml")
STEP_EVALUATOR_YAML = load_yaml(_TEMPLATES, "step_evaluator.yaml")
CLAIM_AUDITOR_YAML = load_yaml(_TEMPLATES, "claim_auditor.yaml")


def render_retry_context(
    attempts: int,
    max_attempts: int,
    last_report: Optional[str],
    last_step_unmet: Optional[List[str]],
    last_step_feedback: Optional[str],
    last_unsupported_claims: Optional[List[str]] = None,
    last_claim_suggestions: Optional[List[str]] = None,
) -> str:
    """Block injected into the researcher prompt on retries. Empty on first attempt.

    Two retry modes feed this: a goal-miss from step_evaluator (`last_step_*`),
    and — when the goal was met but a report claim could not be grounded — a
    claim-audit revision request (`last_unsupported_claims` /
    `last_claim_suggestions`). They are mutually exclusive in practice: a claim
    revision only happens after a FULFILLED verdict.
    """
    if attempts <= 1:
        return ""

    remaining = max_attempts - attempts + 1
    lines = [
        f"**Attempt {attempts} of {max_attempts} ({remaining} remaining).**",
        "",
    ]
    if attempts >= max_attempts:
        lines += ["**LAST CHANCE — if this attempt fails, the loop terminates.**", ""]

    if last_report:
        # Full prior report, no truncation: the researcher must see exactly what
        # the previous attempt wrote to fix the gaps without losing the good parts.
        lines += [
            "**Your previous attempt's report was:**",
            "```markdown",
            last_report,
            "```",
            "",
        ]

    if last_unsupported_claims:
        # Claim-audit revision: the report MET the goal, so do not rewrite the
        # whole thing — only fix the statements the run's evidence does not back.
        lines += [
            "**Outcome:** the report met the step goal, but the statements below "
            "are NOT supported by the run's evidence (no matching printed value, "
            "figure reading, or source file). For EACH one: point to the printed "
            "value / figure reading / named data file it came from, soften it to "
            "exactly what the evidence supports, or remove it. Do NOT invent a "
            "source, and keep everything that IS supported unchanged.",
            "",
            "**Unsupported statements:**",
        ]
        for c in last_unsupported_claims:
            lines.append(f"  - {c}")
        lines.append("")
        if last_claim_suggestions:
            lines.append("**What each one needs:**")
            for s in last_claim_suggestions:
                lines.append(f"  - {s}")
            lines.append("")
        return "\n".join(lines)

    lines += ["**Outcome:** the report did NOT fulfill the step.", ""]
    if last_step_unmet:
        lines.append("**Unmet requirements:**")
        for r in last_step_unmet:
            lines.append(f"  - {r}")
        lines.append("")
    if last_step_feedback:
        lines += ["**Evaluator feedback:**", last_step_feedback, ""]
    return "\n".join(lines)


def researcher_instructions(
    ctx: PlanContext,
    step: Step,
    retry_context: str,
    previous_steps_execution_summary: str = "",
) -> str:
    return RESEARCHER_YAML["instructions"].format_map(
        SafeDict(
            main_task=ctx.main_task,
            researcher_append_instructions=ctx.researcher_append_instructions,
            current_sub_task=step.sub_task,
            current_instructions="\n".join(f"- {b}" for b in step.bullet_points),
            retry_context=retry_context,
            previous_steps_execution_summary=previous_steps_execution_summary,
        )
    )


def _render_step_history(step_feedback_history: List[str]) -> str:
    if not step_feedback_history:
        return "(this is the first attempt — no prior feedback on this step)"
    return "\n".join(step_feedback_history)


def step_evaluator_instructions(
    ctx: PlanContext,
    step: Step,
    current_report: str,
    step_feedback_history: List[str],
    previous_steps_execution_summary: str = "",
    workspace_manifest: str = "",
    read_sources: str = "",
    quantity_ledger: str = "",
    grounded: bool = False,
) -> str:
    """The evidence arguments are the evaluator's grounding evidence: without
    them it can only ask "is every bullet point covered?", which a fabricated
    number answers just as well as a real one. `grounded=True` swaps the
    GROUNDING policy block for the ledger version, under which a missing number
    is a `suspected_fabrications` hint rather than a failed step: presence is
    decided deterministically afterwards, not by the LLM."""
    policy = STEP_EVALUATOR_YAML["grounding_ledger" if grounded else "grounding_legacy"]
    return STEP_EVALUATOR_YAML["instructions"].format_map(
        SafeDict(
            evaluator_append_instructions=ctx.evaluator_append_instructions,
            grounding_policy=policy.rstrip(),
            quantity_ledger=quantity_ledger or "",
            current_sub_task=step.sub_task,
            current_instructions="\n".join(f"- {b}" for b in step.bullet_points),
            current_report=current_report,
            step_feedback_history=_render_step_history(step_feedback_history),
            previous_steps_execution_summary=(
                previous_steps_execution_summary
                or "(no previous steps — this researcher step ran standalone)"
            ),
            workspace_manifest=workspace_manifest or "(no workspace files)",
            read_sources=read_sources or (
                "(the researcher read no artifacts — every specific value in the "
                "report must therefore come from the captured output above, or "
                "name the file it was taken from)"
            ),
        )
    )


def claim_auditor_instructions(
    ctx: PlanContext,
    step: Step,
    current_report: str,
    previous_steps_execution_summary: str = "",
    workspace_manifest: str = "",
    read_sources: str = "",
    quantity_ledger: str = "",
) -> str:
    """System prompt for the claim_auditor node. Same evidence trio as the
    step_evaluator, plus the report to audit; the evaluator has already accepted
    the report on goal, so this pass judges only per-claim SUPPORT. When a
    `quantity_ledger` is supplied the presence rules ride along: a number the
    ledger found may not be called `unsupported_hard` for absence."""
    rules = CLAIM_AUDITOR_YAML["grounding_rules"].rstrip() if quantity_ledger else ""
    return CLAIM_AUDITOR_YAML["instructions"].format_map(
        SafeDict(
            evaluator_append_instructions=ctx.evaluator_append_instructions,
            quantity_ledger=quantity_ledger or "",
            grounding_rules=rules,
            current_sub_task=step.sub_task,
            current_instructions="\n".join(f"- {b}" for b in step.bullet_points),
            current_report=current_report,
            previous_steps_execution_summary=(
                previous_steps_execution_summary
                or "(no previous steps — this researcher step ran standalone)"
            ),
            workspace_manifest=workspace_manifest or "(no workspace files)",
            read_sources=read_sources or (
                "(the researcher read no artifacts — so any SPECIFIC value in the "
                "report must come from the captured output above or name the file "
                "it was taken from; otherwise it is unsupported)"
            ),
        )
    )
