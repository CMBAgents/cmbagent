"""Compiled deep_research graph — one Plan in, executed step-by-step out.

    START → run_step → adaptive_review ─(route_after_review)─► replan_tail
                            │ no / disabled                          │
                            ├──────────► run_step (next step)        ▼
                            └──────────► END                     format_tail
                                              ▲                       │
                                              │                       ▼
                                       (route_after_merge) ◄──── merge_plan

`run_step` invokes `self_debug_graph` / `researcher_graph` once per `Step`,
threading an accumulated `previous_steps_execution_summary` through so each step
sees prior steps' code/output and the workspace file manifest. `adaptive_review`
reviews the just-finished step and, when warranted, rewrites the not-yet-run tail
(completed steps stay frozen); with `PlanContext.adaptive_planning` False it
early-returns with no LLM call and `route_after_review` reduces to `after_step`.

`build_deep_research_graph()` looks node functions up on the `nodes` module at
build time (not import-bound), so a caller can monkeypatch them before building —
which the tests rely on. The module-level `graph` is the compiled default.
"""

from langgraph.graph import StateGraph, START, END

from cmbagent_lg.context import PlanContext
from cmbagent_lg.deep_research.state import DeepResearchState


def build_deep_research_graph():
    """Compile and return the deep_research graph (adaptive by default)."""
    from cmbagent_lg.deep_research import nodes

    return (
        StateGraph(DeepResearchState, context_schema=PlanContext)
        .add_node("run_step", nodes.run_step)
        .add_node("adaptive_review", nodes.adaptive_review)
        .add_node("replan_tail", nodes.replan_tail)
        .add_node("format_tail", nodes.format_tail)
        .add_node("merge_plan", nodes.merge_plan)
        .add_edge(START, "run_step")
        .add_edge("run_step", "adaptive_review")
        .add_conditional_edges(
            "adaptive_review",
            nodes.route_after_review,
            {"replan_tail": "replan_tail", "run_step": "run_step", END: END},
        )
        .add_edge("replan_tail", "format_tail")
        .add_edge("format_tail", "merge_plan")
        .add_conditional_edges(
            "merge_plan",
            nodes.route_after_merge,
            {"run_step": "run_step", END: END},
        )
        .compile()
    )


graph = build_deep_research_graph()
