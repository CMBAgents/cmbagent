"""Compiled researcher graph — two gates, one shared retry budget.

    writer ─► step_evaluator ─ goal MISS ─► writer (until attempts == max_n_attempts)
                    │
                 goal MET
                    ▼
              claim_auditor ─ unsupported claim + budget ─► writer (revise)
                    └─ clean / advisory / exhausted ─► END

No execution_evaluator (no code), no escalation. Total writer passes = at most
`ctx.max_n_attempts`.

The writer at START is selected by `PlanContext.researcher_mode`: `"react"`
(default) is the tool-using, grounded writer; `"plain"` is the single-`invoke`
node. Both loop back to the same `step_evaluator`, so the retry budget and the
downstream contract are identical either way — only the entry edge differs.
"""

from langgraph.graph import StateGraph, START, END

from cmbagent_lg.context import PlanContext
from cmbagent_lg.researcher.claim_auditor import (
    claim_auditor,
    route_after_claim_auditor,
)
from cmbagent_lg.researcher.nodes import (
    researcher,
    step_evaluator,
    route_after_step_evaluator,
    writer_node_name,
)
from cmbagent_lg.researcher.react_researcher import react_researcher
from cmbagent_lg.researcher.state import ResearcherState


def _route_start(state: ResearcherState, runtime) -> str:
    """Entry edge: the run's report writer (see `writer_node_name`)."""
    return writer_node_name(runtime.context)


graph = (
    StateGraph(ResearcherState, context_schema=PlanContext)
    .add_node("researcher", researcher)
    .add_node("react_researcher", react_researcher)
    .add_node("step_evaluator", step_evaluator)
    .add_node("claim_auditor", claim_auditor)
    .add_conditional_edges(
        START,
        _route_start,
        {"researcher": "researcher", "react_researcher": "react_researcher"},
    )
    .add_edge("researcher", "step_evaluator")
    .add_edge("react_researcher", "step_evaluator")
    # goal MET → claim_auditor (if enabled) | END; goal MISS → writer | END
    .add_conditional_edges(
        "step_evaluator",
        route_after_step_evaluator,
        {"researcher": "researcher", "react_researcher": "react_researcher",
         "claim_auditor": "claim_auditor", END: END},
    )
    # claim clean/advisory → END; unsupported-HARD + budget → writer (revise);
    # unsupported-HARD + exhausted → END (report annotated, step flagged failed)
    .add_conditional_edges(
        "claim_auditor",
        route_after_claim_auditor,
        {"researcher": "researcher", "react_researcher": "react_researcher", END: END},
    )
    .compile()
)
