"""RT3 attack 1 + 2: frozen-prefix integrity and no re-execution.

Central invariant under test:
    "completed steps are FROZEN — never modified, never re-executed; only the
     not-yet-run tail changes."

step_index semantics found in deep_research/nodes.py::run_step:
    * 1-based pointer to the NEXT step to run (default 1).
    * run_step executes plan.sub_tasks[step_index-1], appends one summary and
      one outcome, then returns step_index = n+1 (POST-increment).
    * therefore after k executed steps step_index == k+1 and
      merge_plan's `n_done = step_index - 1 == k` == exactly the # executed.

This driver runs the REAL compiled adaptive graph with fake (deterministic,
no-LLM) execute_step / adaptive_review / replan_tail. It records every step
index+name actually executed, then checks:

  (A) every executed step ended up in the FINAL plan's frozen prefix,
      byte-identical and in order  -> executed_names == final.prefix_names
  (B) no step index is executed twice (no re-entry into a completed index)
  (C) the frozen prefix length after each merge == number of steps executed
      before that merge (the off-by-one target: n_done = step_index-1)

Scenarios: adapt after the FIRST step, after the LAST-but-one step, and the
edge where only 1 step remains, plus a double-adaptation run.

Run:
    python tests/adv_rt3_freeze_boundary.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Plan, Step
from cmbagent_lg.deep_research import schemas as adaptive_schemas
from cmbagent_lg.deep_research import nodes as adaptive_nodes
from cmbagent_lg.deep_research.graph import build_deep_research_graph


def _step(name):
    return Step(sub_task=name, sub_task_agent="engineer", bullet_points=[f"do {name}"])


def _plan(*names):
    return Plan(sub_tasks=[_step(n) for n in names])


class Harness:
    """Deterministic fakes with full instrumentation."""

    def __init__(self, adapt_after=(), tail_factory=None):
        # adapt_after: set of executed-step-counts after which to adapt.
        self.adapt_after = set(adapt_after)
        self.tail_factory = tail_factory or (lambda k: _plan(f"t{k}a", f"t{k}b"))
        self.executed = []          # list of (step_index, sub_task_name)
        self.merge_prefix_lens = [] # frozen-prefix length observed at each merge
        self.executed_count = 0

    def execute_step(self, state, runtime):
        n = state.get("step_index", 1)
        step = state["plan"].sub_tasks[n - 1]
        self.executed.append((n, step.sub_task))
        self.executed_count += 1
        summaries = state.get("step_summaries", []) + [f"sum:{n}:{step.sub_task}"]
        outcomes = state.get("step_outcomes", []) + [
            {"step_number": n, "fulfilled": True}
        ]
        return {"step_summaries": summaries, "step_outcomes": outcomes,
                "step_index": n + 1}

    def adaptive_review(self, state, runtime):
        # executed_count reflects steps run so far (== n_done at this point).
        needs = self.executed_count in self.adapt_after
        review = adaptive_schemas.AdaptiveReview(
            needs_adaptation=needs,
            recommendations=[f"adapt-after-{self.executed_count}"] if needs else [],
        )
        return {"adaptive_review": review}

    def replan_tail(self, state, runtime):
        # Record the frozen-prefix boundary the merge is ABOUT to use.
        n_done = state.get("step_index", 1) - 1
        self.merge_prefix_lens.append(n_done)
        return {"new_tail": self.tail_factory(self.executed_count)}


def run_graph(original, harness):
    saved = (adaptive_nodes.run_step, adaptive_nodes.adaptive_review,
             adaptive_nodes.replan_tail)
    adaptive_nodes.run_step = harness.execute_step
    adaptive_nodes.adaptive_review = harness.adaptive_review
    adaptive_nodes.replan_tail = harness.replan_tail
    try:
        compiled = build_deep_research_graph()
        return compiled.invoke({"plan": original, "work_dir": None},
                               context=PlanContext(main_task="rt3"))
    finally:
        (adaptive_nodes.run_step, adaptive_nodes.adaptive_review,
         adaptive_nodes.replan_tail) = saved


def check_invariant(label, original, harness, final):
    exec_names = [name for (_, name) in harness.executed]
    exec_idxs = [i for (i, _) in harness.executed]
    final_names = [s.sub_task for s in final["plan"].sub_tasks]

    # (B) no index executed twice
    assert len(exec_idxs) == len(set(exec_idxs)), (
        f"[{label}] a completed step index was re-executed: {exec_idxs}"
    )
    # indices must be a contiguous 1..k run (monotone, never decreasing)
    assert exec_idxs == list(range(1, len(exec_idxs) + 1)), (
        f"[{label}] executed indices are not a clean 1..k run: {exec_idxs}"
    )

    # (A) every executed step is the frozen prefix of the FINAL plan,
    #     byte-identical and in order.
    frozen_prefix = final_names[: len(exec_names)]
    assert frozen_prefix == exec_names, (
        f"[{label}] FROZEN-PREFIX VIOLATION\n"
        f"  executed : {exec_names}\n"
        f"  final[:k]: {frozen_prefix}\n"
        f"  full plan: {final_names}"
    )

    # (C) each merge froze exactly the # executed at that moment.
    hist = final.get("adaptive_history", [])
    hist_after = [h["after_step"] for h in hist]
    assert hist_after == harness.merge_prefix_lens, (
        f"[{label}] merge after_step / prefix-len mismatch: "
        f"history={hist_after} observed={harness.merge_prefix_lens}"
    )
    print(f"ok [{label}] executed={exec_names} final={final_names} "
          f"merges_after={hist_after}")


def main():
    # 1) adapt after the FIRST step (n_done == 1)
    orig = _plan("s1", "s2", "s3", "s4")
    h = Harness(adapt_after={1})
    check_invariant("adapt-after-first", orig, h, run_graph(orig, h))

    # 2) adapt after the LAST-BUT-ONE step of a 4-step plan (n_done == 3)
    orig = _plan("s1", "s2", "s3", "s4")
    h = Harness(adapt_after={3}, tail_factory=lambda k: _plan(f"t{k}only"))
    check_invariant("adapt-after-last-but-one", orig, h, run_graph(orig, h))

    # 3) EDGE: only 1 step remains at adaptation time (3-step plan, adapt after 2)
    orig = _plan("s1", "s2", "s3")
    h = Harness(adapt_after={2}, tail_factory=lambda k: _plan(f"repl{k}"))
    check_invariant("one-step-remaining", orig, h, run_graph(orig, h))

    # 4) EDGE: replan returns an EMPTY tail after step 2 of 3. Post-V4 fix an
    #    empty replan is a NO-OP: the existing remaining tail ([s3]) is kept, the
    #    plan is NEVER collapsed to just the frozen prefix. The frozen-prefix
    #    invariant still holds (s1, s2 unchanged) — only the incidental collapse
    #    behaviour changed.
    orig = _plan("s1", "s2", "s3")
    h = Harness(adapt_after={2}, tail_factory=lambda k: Plan(sub_tasks=[]))
    final = run_graph(orig, h)
    check_invariant("empty-new-tail", orig, h, final)
    assert [s.sub_task for s in final["plan"].sub_tasks] == ["s1", "s2", "s3"], final

    # 5) DOUBLE adaptation: adapt after step 1 and again after step 2.
    orig = _plan("s1", "s2", "s3", "s4", "s5")
    h = Harness(adapt_after={1, 2})
    check_invariant("double-adaptation", orig, h, run_graph(orig, h))

    print("ALL adv_rt3_freeze_boundary invariant checks PASSED "
          "(frozen prefix HOLDS across boundaries)")


if __name__ == "__main__":
    main()
