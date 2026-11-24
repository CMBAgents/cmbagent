"""Four propose-critique nodes for the planner ↔ plan_reviewer loop.

Flow (ends on planner — the plan is the deliverable):

    planner ─► format_plan ─► [END if round==num_rounds else continue]
                              │
                              ▼
                          plan_reviewer ─► format_review ─► planner …

Generators write free-form prose; tiny downstream formatters convert that
prose into typed Pydantic objects via `with_structured_output`.

`format_review` appends `(current_plan, current_review)` to `state["history"]`
so every subsequent planner pass sees the **full** prior transcript, not just
the latest review — which is what catches the planner re-introducing a mistake
an earlier reviewer already corrected.
"""

from typing import List, Literal, Optional, Tuple

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime
from pydantic import BaseModel, Field, create_model

from cmbagent_lg.context import PlanContext
from cmbagent_lg.llms import chat_model
from cmbagent_lg.planning.agent_check import reassign_code_steps
from cmbagent_lg.planning.prompts import (
    planner_instructions,
    plan_reviewer_instructions,
)
from cmbagent_lg.planning.schemas import Plan, Review, Step
from cmbagent_lg.planning.state import PlanState
from cmbagent_lg.prompt_utils import schema_field_brief
from cmbagent_lg.timing import timed_node


# Per-role chat models from the run context (None → llms._DEFAULT_MODEL).
# chat_model caches by (model, role), so importing the package builds no clients.
def _proposer(ctx: PlanContext):
    return chat_model(ctx.planner_model, "generator")


def _critic(ctx: PlanContext):
    return chat_model(ctx.plan_reviewer_model, "critic")


def _formatter(ctx: PlanContext):
    return chat_model(ctx.formatter_model, "formatter")


# ── dynamic agent constraint ────────────────────────────────────────────


def _agent_brief(available: List[Tuple[str, str]]) -> str:
    """Render the agent inventory for injection into planner/reviewer prompts."""
    return "\n".join(f"  - **{name}**: {desc}" for name, desc in available)


def _build_dynamic_plan_model(available: List[Tuple[str, str]]):
    """Build a `Plan` Pydantic model whose `Step.sub_task_agent` is constrained
    to `Literal[*available_agents]` for this run.

    Built per-invocation from `PlanContext.available_agents`, so a different
    agent team can be plugged in without recompiling the graph.
    """
    names = tuple(name for name, _ in available)
    if not names:
        raise ValueError("PlanContext.available_agents is empty — no agents to plan with.")

    DynStep = create_model(
        "DynStep",
        sub_task=(str, Field(description="The sub-task to be performed.")),
        sub_task_agent=(
            Literal[names],  # type: ignore[valid-type]
            Field(description="Name of the agent in charge of the sub-task."),
        ),
        bullet_points=(
            List[str],
            Field(description="Bullet-point instructions for the agent."),
        ),
        code_execution_timeout=(
            Optional[int],
            Field(default=None, description="Max seconds for code execution. None = default."),
        ),
        __base__=BaseModel,
    )
    DynPlan = create_model(
        "DynPlan",
        sub_tasks=(List[DynStep], Field(description="Ordered list of plan steps.")),
        __base__=BaseModel,
    )
    return DynPlan


# ── generators ──────────────────────────────────────────────────────────


def _render_history(history) -> str:
    """Render the full prior-rounds transcript for the planner's prompt."""
    if not history:
        return ""
    lines = ["**Prior rounds (for your context — do not repeat past mistakes):**\n"]
    for i, (plan, review) in enumerate(history, start=1):
        lines.append(f"--- Round {i} ---")
        lines.append(plan.format())
        lines.append("")
        lines.append(review.format())
        lines.append("")
    lines.append(
        "Now produce the revised plan, taking into account **every** review above. "
        "Pay special attention to the most recent review."
    )
    return "\n".join(lines)


@timed_node("planner")
def planner(state: PlanState, runtime: Runtime[PlanContext]) -> PlanState:
    ctx = runtime.context
    round_n = state.get("round", 0) + 1
    history = state.get("history", [])

    system = planner_instructions(ctx, recommendations=_render_history(history))
    system += (
        "\n\n-----AVAILABLE AGENTS-------------\n"
        "You must assign each step to **exactly one** of these agents — no others:\n"
        f"{_agent_brief(ctx.available_agents)}\n"
        "----------------------------------"
    )
    user = (
        "Produce the plan now, in the format described above. Write in natural prose — "
        "a downstream specialist will extract structured fields. Make sure every "
        "Step covers:\n\n" + schema_field_brief(Step)
    )
    msg = _proposer(runtime.context).invoke(
        [SystemMessage(system), HumanMessage(user)],
        config={"tags": ["planner"]},
    )
    return {"raw_plan": msg.text, "round": round_n}


@timed_node("plan_reviewer")
def plan_reviewer(state: PlanState, runtime: Runtime[PlanContext]) -> PlanState:
    ctx = runtime.context
    plan = state["current_plan"]
    system = plan_reviewer_instructions(ctx, proposed_plan=plan.format())
    system += (
        "\n\n-----AVAILABLE AGENTS-------------\n"
        "The plan may only assign work to these agents — flag any step that "
        "names someone else, or assigns work outside an agent's stated capability:\n"
        f"{_agent_brief(ctx.available_agents)}\n"
        "----------------------------------"
    )
    user = (
        "Critique the plan above. Write in natural prose — a downstream specialist "
        "will extract structured fields. Be sure to cover:\n\n"
        + schema_field_brief(Review)
    )
    msg = _critic(runtime.context).invoke(
        [SystemMessage(system), HumanMessage(user)],
        config={"tags": ["plan_reviewer"]},
    )
    return {"raw_review": msg.text}


# ── formatters ──────────────────────────────────────────────────────────


@timed_node("format_plan")
def format_plan(state: PlanState, runtime: Runtime[PlanContext]) -> PlanState:
    """Format the raw plan, building the model dynamically so `sub_task_agent`
    is constrained to `Literal[*ctx.available_agents]` at the structured-output
    boundary. Converted back to the static `Plan` type so downstream nodes and
    consumers keep getting the same shape.
    """
    ctx = runtime.context
    DynPlan = _build_dynamic_plan_model(ctx.available_agents)
    structured = _formatter(runtime.context).with_structured_output(DynPlan)
    sys = SystemMessage(
        "You are a formatter. Convert the user's text into a Plan object. "
        "Preserve all substantive content. Do not invent new facts. "
        "If a field is not explicitly stated, infer it conservatively from context."
    )
    dyn = structured.invoke(
        [sys, HumanMessage(state["raw_plan"])],
        config={"tags": ["format_plan"]},
    )
    # Round-trip the DynPlan through dict into the static Plan so the rest of
    # the graph and external callers see a stable type.
    plan = Plan(**dyn.model_dump())
    # Researcher steps (other than the final report) that ask for code work go to the
    # engineer; deterministic, no model call.
    plan, moved = reassign_code_steps(plan, ctx, label="format_plan")
    out: PlanState = {"current_plan": plan}
    if moved:
        out["plan_reassignments"] = list(state.get("plan_reassignments") or []) + [
            {**r, "round": state.get("round")} for r in moved]
    return out


@timed_node("format_review")
def format_review(state: PlanState, runtime: Runtime[PlanContext]) -> PlanState:
    """Format the review via `with_structured_output(Review)`, then snapshot
    (plan, review) into `history` so every subsequent planner pass sees the
    full prior transcript (not just the latest review)."""
    sys = SystemMessage(
        "You are a formatter. Convert the user's text into a Review object. "
        "Preserve all substantive content. Do not invent new facts. "
        "If a field is not explicitly stated, infer it conservatively from context."
    )
    structured = _formatter(runtime.context).with_structured_output(Review)
    review: Review = structured.invoke(
        [sys, HumanMessage(state["raw_review"])],
        config={"tags": ["format_review"]},
    )
    history = list(state.get("history", []))
    history.append((state["current_plan"], review))
    return {"current_review": review, "history": history}


# ── router ──────────────────────────────────────────────────────────────


def route_after_format_plan(state: PlanState, runtime: Runtime[PlanContext]) -> str:
    """After format_plan, decide review-or-stop.

    `num_rounds` counts **review cycles**, and the loop always ends on a planner
    pass, so total planner passes = num_rounds + 1. With `num_rounds=2`:
    plan_v1 → review_1 → plan_v2 → review_2 → plan_v3 (final).
    """
    from langgraph.graph import END

    if state["round"] <= runtime.context.num_rounds:
        return "plan_reviewer"
    return END
