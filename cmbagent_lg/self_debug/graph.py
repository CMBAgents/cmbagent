"""Compiled self-debug graph — two gates (`execution_evaluator`, `step_evaluator`)
sharing one retry budget, plus opt-in escape hatches (`escalation`, `data_fixer`,
`image_reviewer`, `preflight`/`api_preflight`) and two always-on deterministic checks:
`static_preflight` before the run (a script that does not parse, loads a bundled demo
dataset, or never prints is a failed attempt without a run) and `output_postcondition`
after it (a clean run that did not write an output path the step declared is bounced
back to the engineer with no evaluator call at all).

`missing_input` is the terminal data-safety node both halves of the anti-fabrication
defence route to: the reactive `safe_loading` layer (default ON, no model call) on a
FAILURE verdict naming an absent input file, and the proactive intent gate on a SUCCESS
verdict for a step that had nothing to load. Disjoint by construction, so they never
double-terminate. A missing PACKAGE is recoverable and explicitly excluded from both.

Total engineer passes = at most `max_n_attempts`; escalation does not consume an attempt.
Plot revisions are additionally bounded by `max_vlm_review_attempts`.
"""

from langgraph.graph import StateGraph, START, END

from cmbagent_lg.context import PlanContext
from cmbagent_lg.self_debug.state import DebugState
from cmbagent_lg.self_debug.nodes import (
    engineer,
    format_engineer,
    executor,
    execution_evaluator,
    step_evaluator,
    missing_input,
    route_after_execution_evaluator,
    route_after_step_evaluator,
    route_after_data_fixer,
)
from cmbagent_lg.self_debug.api_preflight import (
    api_preflight,
    route_after_api_preflight,
)
from cmbagent_lg.self_debug.static_preflight import (
    static_preflight,
    route_after_static_preflight,
)
from cmbagent_lg.self_debug.output_postcondition import (
    output_postcondition,
    route_after_output_postcondition,
)
from cmbagent_lg.self_debug.escalation import escalation
from cmbagent_lg.self_debug.data_fixer import data_fixer
from cmbagent_lg.self_debug.preflight import preflight
from cmbagent_lg.vlm.reviewer import image_reviewer, route_after_image_reviewer


def route_start(state: DebugState, runtime) -> str:
    """Entry gate. `enable_preflight` runs the deterministic file-inspection node before
    the first engineer attempt. With it off, START → engineer."""
    ctx = runtime.context
    if getattr(ctx, "enable_preflight", False):
        return "preflight"
    return "engineer"


graph = (
    StateGraph(DebugState, context_schema=PlanContext)
    .add_node("engineer", engineer)
    .add_node("format_engineer", format_engineer)
    .add_node("executor", executor)
    .add_node("execution_evaluator", execution_evaluator)
    .add_node("image_reviewer", image_reviewer)
    .add_node("step_evaluator", step_evaluator)
    .add_node("escalation", escalation)
    .add_node("data_fixer", data_fixer)
    .add_node("missing_input", missing_input)
    .add_node("preflight", preflight)
    .add_node("api_preflight", api_preflight)
    .add_node("static_preflight", static_preflight)
    .add_node("output_postcondition", output_postcondition)
    .add_conditional_edges(
        START,
        route_start,
        {"preflight": "preflight", "engineer": "engineer"},
    )
    .add_edge("preflight", "engineer")
    .add_edge("engineer", "format_engineer")
    # Both pre-flights sit on the ONE edge where the code exists but has not yet run.
    # The static one (parse + prints-anything, always on, microseconds) goes first: a
    # refused script is a failed attempt and goes to the code gate as if it had crashed,
    # so nothing is spent probing the environment for a script that cannot run. The API
    # pre-flight follows, default ON (`enable_api_preflight`); set False / unset the env
    # twin to skip it.
    .add_edge("format_engineer", "static_preflight")
    .add_conditional_edges(
        "static_preflight",
        route_after_static_preflight,
        {
            "execution_evaluator": "execution_evaluator",
            "api_preflight": "api_preflight",
            "executor": "executor",
        },
    )
    .add_conditional_edges(
        "api_preflight",
        route_after_api_preflight,
        {"engineer": "engineer", "executor": "executor"},
    )
    # The post-condition sits on the one edge where the run is finished and nothing has
    # judged it yet: a declared output the attempt did not write is a fact, so it writes
    # its own verdict and goes straight back to the engineer — a bounce costs neither of
    # the two evaluator calls it otherwise takes to surface, and does not depend on a
    # model agreeing with a `stat()` call.
    .add_edge("executor", "output_postcondition")
    .add_conditional_edges(
        "output_postcondition",
        route_after_output_postcondition,
        {"engineer": "engineer", "execution_evaluator": "execution_evaluator", END: END},
    )
    .add_conditional_edges(
        "execution_evaluator",
        route_after_execution_evaluator,
        {
            "engineer": "engineer",
            "image_reviewer": "image_reviewer",
            "step_evaluator": "step_evaluator",
            "escalation": "escalation",
            "data_fixer": "data_fixer",
            "missing_input": "missing_input",
            END: END,
        },
    )
    .add_edge("escalation", "executor")
    .add_edge("missing_input", END)
    .add_conditional_edges(
        "data_fixer",
        route_after_data_fixer,
        {"engineer": "engineer", END: END},
    )
    .add_conditional_edges(
        "image_reviewer",
        route_after_image_reviewer,
        {"engineer": "engineer", "step_evaluator": "step_evaluator"},
    )
    .add_conditional_edges(
        "step_evaluator",
        route_after_step_evaluator,
        {"engineer": "engineer", END: END},
    )
    .compile()
)
