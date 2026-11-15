"""RT3 attack 5: the step_summaries / step_outcomes reducer assumption.

deep_research/nodes.py::run_step accumulates with a READ-MODIFY-WRITE pattern:

    new_outcomes = state.get("step_outcomes", []) + [outcome]
    return {"step_outcomes": new_outcomes, ...}

i.e. it returns the WHOLE accumulated list every step. This is correct ONLY
because `step_outcomes` / `step_summaries` are PLAIN-REPLACE channels in
DeepResearchState (no reducer annotation — contrast
`node_elapsed_s: Annotated[List[NodeTiming], operator.add]` in the same file).

The coupling risk: if anyone ever gave these channels an `operator.add` reducer
(as several sibling channels already have), langgraph would ADD the returned
whole-list to the existing whole-list every step -> quadratic double-counting.
merge_plan's `n_done = step_index - 1` would then diverge from
len(step_outcomes), and the reviewer/replanner would see phantom duplicate
steps.

This script proves both directions with two otherwise-identical langgraph loops
that use run_step's exact append pattern, differing ONLY in the channel reducer.

Run:
    python tests/adv_rt3_reducer_coupling.py
"""

import operator
import os
import sys
from typing import Annotated, List, TypedDict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from langgraph.graph import StateGraph, START, END


N_STEPS = 3


def _run(reducer):
    class S(TypedDict, total=False):
        i: int
        outcomes: List[dict] if reducer is None else Annotated[List[dict], reducer]

    def step(state):
        i = state.get("i", 1)
        # EXACT run_step pattern: return the whole accumulated list.
        outcomes = state.get("outcomes", []) + [{"n": i}]
        return {"outcomes": outcomes, "i": i + 1}

    def route(state):
        return "step" if state.get("i", 1) <= N_STEPS else END

    g = (StateGraph(S)
         .add_node("step", step)
         .add_edge(START, "step")
         .add_conditional_edges("step", route, {"step": "step", END: END})
         .compile())
    return g.invoke({"i": 1})["outcomes"]


def main():
    plain = _run(None)
    added = _run(operator.add)

    plain_ns = [o["n"] for o in plain]
    added_ns = [o["n"] for o in added]
    print(f"  plain-replace (current, CORRECT): outcomes n = {plain_ns}")
    print(f"  operator.add  (hypothetical)     : outcomes n = {added_ns}")

    # Current behavior is correct: exactly one outcome per executed step.
    assert plain_ns == [1, 2, 3], plain_ns

    # Coupling proof: flipping the reducer to operator.add double-counts.
    assert added_ns != [1, 2, 3], added_ns
    assert len(added_ns) > N_STEPS, added_ns
    # step 1's outcome appears multiple times; the list length is quadratic-ish.
    assert added_ns.count(1) > 1, added_ns
    print(f"CONFIRMED (attack 5): with operator.add, step 1 counted "
          f"{added_ns.count(1)}x and len={len(added_ns)} != {N_STEPS}. "
          "run_step's whole-list return is silently coupled to the channels "
          "being PLAIN-REPLACE. Current code is correct but fragile.")


if __name__ == "__main__":
    main()
