"""Researcher steps that need code (`planning/agent_check.py`).

(ii) the plan check after format_plan: a researcher step other than the last that asks to
compute / load / read data files / run code / persist results becomes an engineer step;
(i) at run time, a researcher verdict judged unfulfilled whose feedback or report says the
work needs code ends the researcher loop, and run_step runs the step once with the engineer.

    PYTHONPATH=. python -m pytest tests/test_researcher_code_steps.py
"""
import types

import pytest
from langgraph.graph import END

from cmbagent_lg.context import PlanContext
from cmbagent_lg.deep_research import nodes as dr_nodes
from cmbagent_lg.planning import agent_check as ac
from cmbagent_lg.planning import nodes as plan_nodes
from cmbagent_lg.planning.schemas import Plan, Step
from cmbagent_lg.prompt_utils import schema_field_brief
from cmbagent_lg.researcher import nodes as r_nodes
from cmbagent_lg.self_debug.schemas import EngineerResponse, StepVerdict


def _rt(ctx):
    return types.SimpleNamespace(context=ctx)


def _ctx(**kw):
    return PlanContext(main_task="t", **kw)


ENG1 = Step(sub_task="Load the halo catalogue and measure the mass function", sub_task_agent="engineer",
            bullet_points=["Write data/hmf.csv"])
REPORT = Step(sub_task="Write the final report", sub_task_agent="researcher",
              bullet_points=["Summarise the mass function computed in step 1 and the fit quality",
                             "Discuss the systematics; do not recompute anything"])
COMPUTE_R = Step(sub_task="Compute summary statistics of the power spectra", sub_task_agent="researcher",
                 bullet_points=["Load data/pk.csv", "Save the table to data/stats.csv"])
LIT_R = Step(sub_task="Review the literature on baryonic feedback", sub_task_agent="researcher",
             bullet_points=["Compare the results of step 1 with published suppression values",
                            "Explain which assumptions differ"])


# ── (ii) the plan check ───────────────────────────────────────────────────


def test_step_code_asks():
    assert set(ac.step_code_asks(COMPUTE_R)) == {"compute", "load", "persist"}
    assert ac.step_code_asks(REPORT) == []          # "computed in step 1", "do not recompute"
    assert ac.step_code_asks(LIT_R) == []
    run = Step(sub_task="Run the MCMC chains", sub_task_agent="researcher", bullet_points=[])
    read = Step(sub_task="Interpret", sub_task_agent="researcher", bullet_points=["Read snap_0077.hdf5 headers"])
    assert ac.step_code_asks(run) == ["run code"] and ac.step_code_asks(read) == ["read files"]
    no = Step(sub_task="Discuss", sub_task_agent="researcher", bullet_points=["without loading any file"])
    assert ac.step_code_asks(no) == []


@pytest.mark.parametrize("text", [
    "Review the literature on halo mass functions and summarize the methods used to calculate them",
    "Discuss how to compute the baryonic suppression in the literature",
    "Explain the computational load and memory requirements",
    "Read the tables in Schaye et al. 2023 and summarize the reported calibration",
    "Read the output files from steps 1-2 and write the final report",
    "Write a short literature summary and save it to a markdown file literature.md",
    "Write the findings into the report file",
    "Calculate analytically the expected scaling from the self-similar model",
    "Interpret the power spectra computed in step 1 and compare with published results",
    "Analyze the computed statistics; you need not compute them again",
])
def test_literature_and_discussion_steps_do_not_ask_for_code(text):
    assert ac.step_code_asks(Step(sub_task=text, sub_task_agent="researcher", bullet_points=[])) == []


@pytest.mark.parametrize("text, label", [
    ("Compute the mean and scatter of the halo masses", "compute"),
    ("Using the step-1 catalogue, calculate the stellar-to-halo mass ratio", "compute"),
    ("Summarise the method and then compute the suppression at k=1 h/Mpc", "compute"),
    ("1. Load the halo catalogue and tabulate the masses", "load"),
    ("Read the snapshot headers in snap_0077.hdf5", "read files"),
    ("Run the MCMC chains for the HOD fit", "run code"),
    ("Save the statistics to data/stats.csv", "persist"),
])
def test_imperative_code_asks_are_found(text, label):
    assert label in ac.step_code_asks(Step(sub_task=text, sub_task_agent="researcher", bullet_points=[]))


def test_a_middle_compute_step_moves_to_the_engineer_and_the_report_stays(capsys):
    plan = Plan(sub_tasks=[ENG1, COMPUTE_R, LIT_R, REPORT])
    new, recs = ac.reassign_code_steps(plan, _ctx())
    assert [s.sub_task_agent for s in new.sub_tasks] == ["engineer", "engineer", "researcher", "researcher"]
    assert new.sub_tasks[1].reassigned_from == "researcher" and new.sub_tasks[3].reassigned_from is None
    assert recs == [{"step_number": 2, "from": "researcher", "to": "engineer", "at": "plan_check",
                     "asks": ["compute", "load", "persist"], "sub_task": COMPUTE_R.sub_task}]
    assert "reassigned to the engineer" in capsys.readouterr().err
    assert plan.sub_tasks[1].sub_task_agent == "researcher"            # the input plan is not mutated


def test_the_last_step_is_never_moved_even_if_it_asks_for_code():
    plan = Plan(sub_tasks=[ENG1, COMPUTE_R])
    new, recs = ac.reassign_code_steps(plan, _ctx())
    assert new is plan and recs == []
    single = Plan(sub_tasks=[COMPUTE_R])
    assert ac.reassign_code_steps(single, _ctx())[0] is single


def test_the_check_needs_an_engineer_and_the_knob():
    plan = Plan(sub_tasks=[COMPUTE_R, REPORT])
    assert ac.reassign_code_steps(plan, _ctx(available_agents=[("researcher", "r")]))[1] == []
    assert ac.reassign_code_steps(plan, _ctx(reassign_researcher_code_steps=False))[1] == []


def test_format_plan_applies_the_check(monkeypatch):
    class _S:
        def invoke(self, *a, **k):
            return plan_nodes._build_dynamic_plan_model(_ctx().available_agents)(
                sub_tasks=[COMPUTE_R.model_dump(exclude={"reassigned_from"}),
                           REPORT.model_dump(exclude={"reassigned_from"})])

    class _F:
        def with_structured_output(self, *a, **k):
            return _S()
    monkeypatch.setattr(plan_nodes, "_formatter", lambda ctx: _F())
    out = plan_nodes.format_plan({"raw_plan": "x", "round": 1}, _rt(_ctx()))
    assert [s.sub_task_agent for s in out["current_plan"].sub_tasks] == ["engineer", "researcher"]
    assert out["plan_reassignments"][0]["step_number"] == 1 and out["plan_reassignments"][0]["round"] == 1


def test_the_internal_field_is_not_in_the_planner_brief():
    assert "reassigned_from" not in schema_field_brief(Step)
    assert "sub_task_agent" in schema_field_brief(Step)
    assert Plan.model_validate_json(Plan(sub_tasks=[ENG1]).model_dump_json()).sub_tasks[0].reassigned_from is None


# ── (i) the run-time signal ───────────────────────────────────────────────


@pytest.mark.parametrize("report", [
    "I cannot execute code, so the statistics below could not be computed.",
    "As an AI language model I am unable to run Python scripts or read the data files.",
    "We do not have the ability to execute the analysis code in this environment.",
    "Unfortunately code execution is not available to me.",
])
def test_the_report_saying_it_cannot_run_code_is_a_signal(report):
    v = StepVerdict(fulfilled=False, unmet_requirements=["the statistics are missing"], feedback="incomplete")
    sig = ac.needs_code_signal(v, report)
    assert sig and sig["source"] == "report"


@pytest.mark.parametrize("feedback", [
    "This step requires executing code to compute the statistics from data/pk.csv.",
    "No code was executed, so none of the numbers exist.",
    "The researcher cannot execute Python code; this step should be assigned to the engineer.",
    "Computing these values requires running a script on the catalogue.",
])
def test_the_verdict_saying_the_step_needs_code_is_a_signal(feedback):
    v = StepVerdict(fulfilled=False, unmet_requirements=["numbers"], feedback=feedback)
    assert ac.needs_code_signal(v, "a report", COMPUTE_R)["source"] == "verdict"
    # ... but only on a step whose text asks for code work
    assert ac.needs_code_signal(v, "a report", LIT_R) is None
    assert ac.needs_code_signal(v, "a report", REPORT) is None


@pytest.mark.parametrize("feedback,report", [
    ("The report omits the discussion of the systematics in step 2.", "Results: the slope is 1.2."),
    ("Numbers in the report do not match data/fit.json.", "We do not have access to the raw data of Smith et al."),
    ("The report should run to at least three sections.", "The analysis is summarised below."),
])
def test_ordinary_goal_misses_are_not_a_signal(feedback, report):
    v = StepVerdict(fulfilled=False, unmet_requirements=["x"], feedback=feedback)
    assert ac.needs_code_signal(v, report, COMPUTE_R) is None


@pytest.mark.parametrize("feedback,report,step", [
    # ReAct researchers have read_file: "cannot read" means the file is missing
    ("Report lacks the final numbers.", "I cannot read the file data/answer.json because it is not listed.",
     COMPUTE_R),
    ("Values not grounded.", "We do not have access to the raw simulation data files, so we quote the paper.",
     COMPUTE_R),
    # a literature step's report declining new calculations is a normal sentence
    ("Missing citations for the literature claims.",
     "We cannot perform new calculations here; we rely on published values.", LIT_R),
    # a final report asked to quote the scripts' values is not asked to write code
    ("The report must include the values obtained from running the scripts in step 2.", "Results.", REPORT),
])
def test_report_and_verdict_sentences_that_are_not_signals(feedback, report, step):
    v = StepVerdict(fulfilled=False, unmet_requirements=["x"], feedback=feedback)
    assert ac.needs_code_signal(v, report, step) is None


def test_a_compute_step_report_declining_calculations_is_a_signal():
    v = StepVerdict(fulfilled=False, unmet_requirements=["stats"], feedback="incomplete")
    sig = ac.needs_code_signal(v, "We cannot perform the calculations on the catalogue files.", COMPUTE_R)
    assert sig and sig["source"] == "report"


def test_a_fulfilled_verdict_is_never_a_signal():
    v = StepVerdict(fulfilled=True, unmet_requirements=[], feedback="requires executing code next time")
    assert ac.needs_code_signal(v, "I cannot execute code") is None


def test_the_router_ends_on_the_signal_and_otherwise_retries():
    ctx = _ctx(max_n_attempts=3)
    miss = StepVerdict(fulfilled=False, unmet_requirements=["x"], feedback="f")
    assert r_nodes.route_after_step_evaluator(
        {"current_step_verdict": miss, "attempts": 1, "needs_engineer": {"source": "report"}}, _rt(ctx)) == END
    assert r_nodes.route_after_step_evaluator(
        {"current_step_verdict": miss, "attempts": 1}, _rt(ctx)) == r_nodes.writer_node_name(ctx)


def _researcher_sub(verdict, attempts=1, need=None):
    sub = {"current_step_verdict": verdict, "attempts": attempts, "step_number": 1,
           "current_report": "I cannot execute code."}
    if need:
        sub["needs_engineer"] = need
    return sub


def _engineer_sub(step, fulfilled=True):
    return {"current_step_verdict": StepVerdict(fulfilled=fulfilled, unmet_requirements=[], feedback="ok"),
            "attempts": 2, "step_number": 1, "step": step,
            "current_code": EngineerResponse(python_code="print(1)", code_explanation="x"),
            "execution_stdout": "1\n"}


def test_run_step_gives_the_step_to_the_engineer_once(monkeypatch, tmp_path, capsys):
    plan = Plan(sub_tasks=[COMPUTE_R, REPORT])
    miss = StepVerdict(fulfilled=False, unmet_requirements=["stats"], feedback="requires executing code")
    seen = {}

    def researcher_invoke(inp, **k):
        seen["researcher"] = inp
        return _researcher_sub(miss, need={"source": "verdict", "matched": "requires executing code", "attempt": 1})

    def engineer_invoke(inp, **k):
        seen["engineer"] = inp
        return _engineer_sub(inp["step"])
    monkeypatch.setattr(dr_nodes.researcher_graph, "invoke", researcher_invoke, raising=False)
    monkeypatch.setattr(dr_nodes.self_debug_graph, "invoke", engineer_invoke, raising=False)
    out = dr_nodes.run_step({"plan": plan, "step_index": 1, "work_dir": str(tmp_path)}, _rt(_ctx()))
    assert seen["researcher"]["engineer_reassignment_allowed"] is True
    assert seen["engineer"]["step"].sub_task_agent == "engineer"
    o = out["step_outcomes"][-1]
    assert o["fulfilled"] is True and o["attempts"] == 2
    assert o["reassigned"] == {"from": "researcher", "to": "engineer", "at": "run_time", "source": "verdict",
                               "matched": "requires executing code", "researcher_attempts": 1}
    assert out["plan"].sub_tasks[0].sub_task_agent == "engineer"
    assert out["plan"].sub_tasks[1].sub_task_agent == "researcher"
    assert "### Step 1 (engineer)" in out["step_summaries"][-1]
    assert "reassigned to the engineer" in capsys.readouterr().err


def test_run_step_never_moves_the_final_report_of_a_plan_of_several_steps(monkeypatch, tmp_path):
    plan = Plan(sub_tasks=[ENG1, REPORT])
    miss = StepVerdict(fulfilled=False, unmet_requirements=["x"], feedback="requires executing code")
    seen = {}

    def researcher_invoke(inp, **k):
        seen["allowed"] = inp["engineer_reassignment_allowed"]
        return _researcher_sub(miss, 3, need={"source": "report", "matched": "cannot execute code"})

    def boom(*a, **k):
        raise AssertionError("the engineer must not run")
    monkeypatch.setattr(dr_nodes.researcher_graph, "invoke", researcher_invoke, raising=False)
    monkeypatch.setattr(dr_nodes.self_debug_graph, "invoke", boom, raising=False)
    out = dr_nodes.run_step({"plan": plan, "step_index": 2, "work_dir": str(tmp_path)}, _rt(_ctx()))
    assert seen["allowed"] is False
    assert "reassigned" not in out["step_outcomes"][-1] and "plan" not in out


def test_run_step_moves_a_single_step_researcher_plan(monkeypatch, tmp_path):
    plan = Plan(sub_tasks=[COMPUTE_R])
    miss = StepVerdict(fulfilled=False, unmet_requirements=["stats"], feedback="incomplete")
    monkeypatch.setattr(dr_nodes.researcher_graph, "invoke",
                        lambda inp, **k: _researcher_sub(miss, need={"source": "report", "matched": "m"}),
                        raising=False)
    monkeypatch.setattr(dr_nodes.self_debug_graph, "invoke", lambda inp, **k: _engineer_sub(inp["step"]),
                        raising=False)
    out = dr_nodes.run_step({"plan": plan, "step_index": 1, "work_dir": str(tmp_path)}, _rt(_ctx()))
    assert out["step_outcomes"][-1]["reassigned"]["at"] == "run_time"
    assert out["plan"].sub_tasks[0].sub_task_agent == "engineer"


def test_format_tail_applies_the_check(monkeypatch):
    class _S:
        def invoke(self, *a, **k):
            return plan_nodes._build_dynamic_plan_model(_ctx().available_agents)(
                sub_tasks=[COMPUTE_R.model_dump(exclude={"reassigned_from"}),
                           REPORT.model_dump(exclude={"reassigned_from"})])

    class _F:
        def with_structured_output(self, *a, **k):
            return _S()
    monkeypatch.setattr(dr_nodes, "_formatter", lambda ctx: _F())
    out = dr_nodes.format_tail({"raw_tail": "x"}, _rt(_ctx()))
    tail = out["new_tail"].sub_tasks
    assert [s.sub_task_agent for s in tail] == ["engineer", "researcher"]
    assert tail[0].reassigned_from == "researcher"


def test_run_step_without_the_signal_keeps_the_researcher(monkeypatch, tmp_path):
    plan = Plan(sub_tasks=[REPORT])
    miss = StepVerdict(fulfilled=False, unmet_requirements=["x"], feedback="thin")
    monkeypatch.setattr(dr_nodes.researcher_graph, "invoke", lambda inp, **k: _researcher_sub(miss, 3), raising=False)

    def boom(*a, **k):
        raise AssertionError("the engineer must not run")
    monkeypatch.setattr(dr_nodes.self_debug_graph, "invoke", boom, raising=False)
    out = dr_nodes.run_step({"plan": plan, "step_index": 1, "work_dir": str(tmp_path)}, _rt(_ctx()))
    assert "reassigned" not in out["step_outcomes"][-1] and "plan" not in out


def test_run_step_records_a_plan_time_reassignment(monkeypatch, tmp_path):
    moved = COMPUTE_R.model_copy(update={"sub_task_agent": "engineer", "reassigned_from": "researcher"})
    plan = Plan(sub_tasks=[moved, REPORT])
    monkeypatch.setattr(dr_nodes.self_debug_graph, "invoke", lambda inp, **k: _engineer_sub(inp["step"]),
                        raising=False)
    out = dr_nodes.run_step({"plan": plan, "step_index": 1, "work_dir": str(tmp_path)}, _rt(_ctx()))
    assert out["step_outcomes"][-1]["reassigned"] == {"from": "researcher", "to": "engineer", "at": "plan"}


def test_run_step_records_the_missing_input_and_the_searches(monkeypatch, tmp_path):
    plan = Plan(sub_tasks=[ENG1])
    sub = _engineer_sub(ENG1, fulfilled=False)
    sub.update(unfulfillable=True,
               missing_input_record={"missing": "x.hdf5", "notes": "n",
                                     "search": {"roots": ["/r"], "entries_seen": 10, "truncated": None}},
               missing_input_searches=[{"attempt": 1, "path": "/r/a/x.hdf5", "found": False, "close": False,
                                        "locations": []}])
    monkeypatch.setattr(dr_nodes.self_debug_graph, "invoke", lambda inp, **k: sub, raising=False)
    out = dr_nodes.run_step({"plan": plan, "step_index": 1, "work_dir": str(tmp_path)}, _rt(_ctx()))
    o = out["step_outcomes"][-1]
    assert o["missing_input"] == {"missing": "x.hdf5", "searched_roots": ["/r"], "entries_seen": 10,
                                  "truncated": None}
    assert o["input_searches"][0]["path"] == "/r/a/x.hdf5"


def test_the_researcher_evaluator_sets_the_signal_only_when_allowed(monkeypatch, tmp_path):
    miss = StepVerdict(fulfilled=False, unmet_requirements=["stats"], feedback="incomplete")

    class _S:
        def invoke(self, *a, **k):
            return miss

    class _C:
        def with_structured_output(self, *a, **k):
            return _S()
    monkeypatch.setattr(r_nodes, "_critic", lambda ctx: _C())
    base = {"step": COMPUTE_R, "step_number": 1, "work_dir": str(tmp_path), "attempts": 1,
            "current_report": "I cannot execute code, so no statistics were computed."}
    ctx = _ctx()
    out = r_nodes.step_evaluator({**base, "engineer_reassignment_allowed": True}, _rt(ctx))
    assert out["needs_engineer"]["source"] == "report" and out["needs_engineer"]["attempt"] == 1
    assert "needs_engineer" not in r_nodes.step_evaluator(base, _rt(ctx))           # standalone researcher run
    off = _ctx(reassign_researcher_code_steps=False)
    assert "needs_engineer" not in r_nodes.step_evaluator({**base, "engineer_reassignment_allowed": True}, _rt(off))
