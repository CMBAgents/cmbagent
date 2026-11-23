"""The `image_reviewer` node + its router — a bounded revise-the-plot loop.

After a step runs cleanly and produces figures, a vision model reviews them for
concrete, fixable defects (cut-off labels, missing units/legends, wrong scale,
doesn't show what was asked). With a revision request and budget left, control
goes back to the engineer with the issues + suggestions; otherwise on to the
step goal evaluation.

The node lives here; `self_debug.graph` wires it in. Gated by
`PlanContext.vlm_enabled` (the router into this node is only taken when enabled)
and bounded by `PlanContext.max_vlm_review_attempts`.
"""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from cmbagent_lg.context import PlanContext
from cmbagent_lg.llms import chat_model
from cmbagent_lg.self_debug.schemas import ImageReview
from cmbagent_lg.self_debug.state import DebugState
from cmbagent_lg.timing import timed_node
from cmbagent_lg.vlm.images import images_from_manifest, with_images


# Model the image_reviewer judges figures with when `vlm_model` is unset.
# Deliberately NOT the researcher's `view_image` default: this job is catching a
# scientifically WRONG figure (above all, a feature that falls outside the
# plotted range), where the smaller models are unstable under prompt rewording
# and nag about clean plots. Do not downgrade to a lite model.
_DEFAULT_REVIEW_MODEL = "gemini-3.1-pro-preview"


_REVIEW_SYSTEM = """\
You are an image review agent for scientific plots. You are given the figure(s) a
step's code just produced, plus the sub-task they are meant to satisfy. Judge
ONLY visual/scientific correctness — a separate check already confirmed the code
ran cleanly.

For each figure consider: are all labels/titles/ticks readable (not cut off or
overlapping)? do axes have labels (with units where appropriate)? is there a
legend when multiple series are shown? is the scale appropriate (e.g. log when
spanning orders of magnitude)? does the plotted range actually contain the
feature the sub-task is about, or is it cut off / off-screen? does the figure
actually show what the sub-task asked for, and is what it shows
physically/scientifically correct (e.g. an annotation or marker placed on the
feature it claims to identify)?

Request a revision ONLY for concrete, fixable defects — never for subjective
style preferences, and never if the figures are already acceptable. When you do,
give specific issues ("y-axis label is cut off", not "labels have issues") and
concrete plotting fixes the engineer can apply.

The SUB-TASK and its bullets below are CONTEXT — they tell you what the step was
trying to produce. They are NOT the acceptance criteria for the figure and a
bullet NEVER licenses a defect: an unlabelled, unreadable, wrongly-scaled, or
scientifically misleading figure must be flagged even if a bullet is silent
about it or appears to permit it. If a bullet conflicts with figure legibility
or with the sub-task's stated goal, the figure — not the bullet — is what you
judge, so request the revision.

----- SUB-TASK (context) -----
{sub_task}
----- SUB-TASK BULLETS (context — what the step was asked to do, NOT figure acceptance criteria) -----
{instructions}
"""


def _logs_dir(state: DebugState):
    raw = state.get("work_dir")
    if not raw:
        return None
    d = Path(raw).expanduser() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


@timed_node("image_reviewer")
def image_reviewer(state: DebugState, runtime: Runtime[PlanContext]) -> DebugState:
    """Vision-review the figures this step produced → ImageReview verdict.

    Returns an empty update (→ router falls through to step_evaluator) when the
    step produced no images. Otherwise bumps `vlm_review_attempts` and stores
    `current_image_review`.
    """
    ctx = runtime.context
    step = state["step"]
    n = state.get("step_number", 1)

    images = images_from_manifest(
        state.get("work_dir"), state.get("data_manifest", []), ctx.vlm_max_images
    )
    if not images:
        return {}  # nothing to review

    system = _REVIEW_SYSTEM.format(
        sub_task=step.sub_task,
        instructions="\n".join(f"- {b}" for b in step.bullet_points),
    )
    user = with_images(
        "Review the attached figure(s) for the current sub-task and emit a "
        "structured verdict.",
        images,
        root=state.get("work_dir"),
    )
    model = chat_model(ctx.vlm_model or _DEFAULT_REVIEW_MODEL, "critic")
    review: ImageReview = model.with_structured_output(ImageReview).invoke(
        [SystemMessage(system), HumanMessage(content=user)],
        config={"tags": ["image_reviewer"]},
    )

    # Budget counts REVISION REQUESTS, not passes: charging clean reviews could
    # exhaust it on figures that were already fine, leaving a later regression
    # the reviewer DOES catch with no way back to the engineer.
    prior = state.get("vlm_review_attempts", 0)
    attempts = prior + (1 if review.needs_revision else 0)
    # Monotonic per-call index so each review gets its own audit file (a trailing
    # pass must not overwrite the last revision's log).
    seq = state.get("vlm_reviews_logged", 0) + 1

    logs = _logs_dir(state)
    if logs is not None:
        (logs / f"step_{n}_image_review_{seq}.json").write_text(
            json.dumps(review.model_dump(), indent=2)
        )

    return {"current_image_review": review, "vlm_review_attempts": attempts,
            "vlm_reviews_logged": seq}


def route_after_image_reviewer(
    state: DebugState, runtime: Runtime[PlanContext]
) -> str:
    """Plot gate: a fixable figure defect + budget left → engineer (revise the
    plot); otherwise → step_evaluator (judge the goal). Bounded by
    `max_vlm_review_attempts` AND the shared engineer attempt budget."""
    ctx = runtime.context
    review = state.get("current_image_review")
    if review is None or not review.needs_revision:
        return "step_evaluator"
    if state.get("vlm_review_attempts", 0) >= ctx.max_vlm_review_attempts:
        return "step_evaluator"
    if state.get("attempts", 0) >= ctx.max_n_attempts:
        return "step_evaluator"
    return "engineer"


__all__ = ["image_reviewer", "route_after_image_reviewer"]
