"""Compiled propose-critique graph.

    planner ─► format_plan ─► [END if last round else continue]
                              │
                              ▼
                          plan_reviewer ─► format_review ─► planner …

`num_rounds` (from `PlanContext`) counts **review cycles**. The loop always
terminates with a planner pass that has incorporated the last review, so
total planner passes = `num_rounds + 1`.
"""

from langgraph.graph import StateGraph, START, END

from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.state import PlanState
from cmbagent_lg.planning.nodes import (
    planner,
    format_plan,
    plan_reviewer,
    format_review,
    route_after_format_plan,
)


graph = (
    StateGraph(PlanState, context_schema=PlanContext)
    .add_node("planner", planner)
    .add_node("format_plan", format_plan)
    .add_node("plan_reviewer", plan_reviewer)
    .add_node("format_review", format_review)
    .add_edge(START, "planner")
    .add_edge("planner", "format_plan")
    .add_conditional_edges(
        "format_plan",
        route_after_format_plan,
        {"plan_reviewer": "plan_reviewer", END: END},
    )
    .add_edge("plan_reviewer", "format_review")
    .add_edge("format_review", "planner")
    .compile()
)
