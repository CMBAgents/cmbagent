"""Static pre-flight: a script that does not parse, or never prints, is a failed attempt
WITHOUT a run.

Measured on the qm9-joint fleet (918 failed execution attempts): 47 "silent scripts"
(rc 0, wrote files, printed nothing — then rejected by the step evaluator as
unverifiable) and 9 SyntaxErrors (f-string quoting, unterminated strings). Each cost a
subprocess run plus the execution_evaluator and step_evaluator calls before the engineer
got feedback the parser could have given for free.

`static_preflight` runs on EVERY attempt right after `format_engineer`, before the API
pre-flight (so a syntax error never spends an environment probe), and a rejection is
shaped exactly like a crashed run — rc 1, empty stdout, the diagnosis on stderr, the
failure log on disk — routed to `execution_evaluator`, so nothing downstream changes.
"""

import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Step
from cmbagent_lg.self_debug import nodes as sd_nodes
from cmbagent_lg.self_debug.prompts import render_retry_context
from cmbagent_lg.self_debug.schemas import EngineerResponse, ExecutionVerdict, StepVerdict
from cmbagent_lg.self_debug.static_preflight import (
    NO_STDOUT_MESSAGE,
    STATIC_MARKER,
    route_after_static_preflight,
    static_preflight,
    syntax_error_report,
    writes_stdout,
)

ENV_FLAG = "CMBAGENT_LG_API_PREFLIGHT"
REMOTE_ENV = "CMBAGENT_LG_REMOTE_EXECUTOR"

# The fleet's own shapes: an f-string whose inner quotes close the outer one on a
# pre-3.12 grammar is the classic, but the parser-independent one is the unterminated
# string in the middle of an otherwise fine file.
BROKEN = """import numpy as np

x = np.arange(10)
label = "mean of x
print(f"mean = {x.mean():.3f} {label}")
"""

SILENT = """import numpy as np

if __name__ == "__main__":
    x = np.arange(10)
    np.save("data/x.npy", x)
"""

PRINTS = """import numpy as np

if __name__ == "__main__":
    x = np.arange(10)
    print(f"mean = {x.mean():.3f}")
"""


def _rt(ctx):
    return types.SimpleNamespace(context=ctx)


def _step(agent="engineer"):
    return Step(sub_task="compute the mean", sub_task_agent=agent, bullet_points=["print it"])


def _state(code, agent="engineer", **kw):
    st = {
        "step": _step(agent),
        "current_code": EngineerResponse(python_code=code, code_explanation="x"),
        "attempts": 1,
        "step_number": 1,
    }
    st.update(kw)
    return st


@pytest.fixture
def no_subprocess(monkeypatch):
    """The executor must never be reached: any Popen is a test failure."""

    def _boom(*a, **k):
        raise AssertionError("subprocess.Popen was called — the script was executed")

    monkeypatch.setattr(sd_nodes.subprocess, "Popen", _boom)
    monkeypatch.delenv(ENV_FLAG, raising=False)
    monkeypatch.delenv(REMOTE_ENV, raising=False)


# ── (1) a broken script is refused, with the line number, and leaves a record ──


def test_syntax_error_is_not_executed(tmp_path, no_subprocess):
    ctx = PlanContext(main_task="t")
    st = _state(BROKEN, work_dir=str(tmp_path))
    out = static_preflight(st, _rt(ctx))

    assert out["static_preflight_rejected"] == "syntax"
    # The shape of a crashed run: the evaluator/retry path reads these unchanged.
    assert out["execution_returncode"] == 1
    assert out["execution_stdout"] == ""
    assert out["execution_timed_out"] is False
    assert out["data_manifest"] == []
    err = out["execution_stderr"]
    assert err.startswith("SyntaxError at line 4:"), err
    assert 'label = "mean of x' in err, "the offending line must be quoted"
    assert STATIC_MARKER in err and "was not executed" in err

    st.update(out)
    assert route_after_static_preflight(st, _rt(ctx)) == "execution_evaluator"

    # On disk: the FINAL failure names, no canonical step_1.* (the executor never wrote it).
    codebase = tmp_path / "codebase"
    assert (codebase / "step_1_failure_1.py").read_text() == BROKEN
    log = (codebase / "step_1_failure_1.log").read_text()
    assert "returncode=1" in log and "SyntaxError at line 4" in log
    assert not (codebase / "step_1.py").exists() and not (codebase / "step_1.log").exists()
    assert not (tmp_path / "data" / "x.npy").exists()

    # The engineer's retry block carries the diagnosis like any other stderr.
    block = render_retry_context(
        attempts=2, max_attempts=3, last_code=BROKEN, last_stderr=err, last_stdout="",
        last_returncode=1, last_timed_out=False, last_fix_suggestion=None, error_history=[err],
    )
    assert "SyntaxError at line 4" in block and 'label = "mean of x' in block


def test_syntax_error_report_shapes():
    assert syntax_error_report(PRINTS) is None
    assert syntax_error_report("") is None
    r = syntax_error_report("def f(:\n    pass\n")
    assert r.startswith("SyntaxError at line 1: ") and "def f(:" in r, r
    r = syntax_error_report("if True:\nprint(1)\n")
    assert r.startswith("IndentationError at line 2"), r
    assert STATIC_MARKER in r
    # A null byte is refused by the interpreter too (SyntaxError on 3.12, ValueError
    # before); report it rather than crash.
    r = syntax_error_report("print(1)\x00\n")
    assert "null bytes" in r and STATIC_MARKER in r, r


# ── (2) a silent script is refused; a printing one passes ──────────────────────


def test_silent_script_is_rejected_and_printing_script_passes(tmp_path, no_subprocess):
    ctx = PlanContext(main_task="t")
    st = _state(SILENT, work_dir=str(tmp_path))
    out = static_preflight(st, _rt(ctx))
    assert out["static_preflight_rejected"] == "no_stdout"
    assert out["execution_stderr"] == NO_STDOUT_MESSAGE
    assert "PRINT YOUR RESULTS" in NO_STDOUT_MESSAGE and STATIC_MARKER in NO_STDOUT_MESSAGE
    assert out["execution_returncode"] == 1 and out["execution_stdout"] == ""
    st.update(out)
    assert route_after_static_preflight(st, _rt(ctx)) == "execution_evaluator"
    assert (tmp_path / "codebase" / "step_1_failure_1.py").read_text() == SILENT

    ok = _state(PRINTS, work_dir=str(tmp_path), attempts=2)
    out = static_preflight(ok, _rt(ctx))
    assert out["static_preflight_rejected"] is None
    assert "execution_stderr" not in out, "a pass-through must not touch the executor's fields"
    ok.update(out)
    # Continues to the API pre-flight (default ON), or the executor when that is off.
    assert route_after_static_preflight(ok, _rt(ctx)) == "api_preflight"
    assert route_after_static_preflight(
        ok, _rt(PlanContext(main_task="t", enable_api_preflight=False))) == "executor"
    assert not (tmp_path / "codebase" / "step_1_failure_2.py").exists()


def test_require_stdout_off_keeps_the_syntax_check(tmp_path, no_subprocess):
    ctx = PlanContext(main_task="t", require_stdout=False)
    assert static_preflight(_state(SILENT), _rt(ctx))["static_preflight_rejected"] is None
    assert static_preflight(_state(BROKEN), _rt(ctx))["static_preflight_rejected"] == "syntax"


# ── (3) every plausible way of writing to stdout counts ────────────────────────


@pytest.mark.parametrize("code", [
    "import sys\nsys.stdout.write('mean = 1.0\\n')\n",
    "import sys\nsys.stdout.writelines(['a\\n'])\n",
    "import sys\nprint('x', file=sys.stdout)\n",
    "print('x', file=None)\n",
    "from pprint import pprint\npprint({'a': 1})\n",
    "import pprint\npprint.pprint({'a': 1})\n",
    "import logging, sys\nlogging.basicConfig(stream=sys.stdout)\nlogging.info('x')\n",
    "import logging, sys\nh = logging.StreamHandler(sys.stdout)\nlogging.getLogger().addHandler(h)\n",
    "from rich.console import Console\nConsole().print('x')\n",
    "import subprocess\nsubprocess.run(['Rscript', 'a.R'])\n",
    "import os\nos.system('Rscript a.R')\n",
    # Prints inside a function are still prints (whether or not it is called is a run-time
    # question the check does not try to answer).
    "def main():\n    print(1)\n\nif __name__ == '__main__':\n    main()\n",
    "def unused():\n    print(1)\n",
])
def test_stdout_writers_pass(code, no_subprocess):
    assert writes_stdout(code), code
    out = static_preflight(_state(code), _rt(PlanContext(main_task="t")))
    assert out["static_preflight_rejected"] is None, code


@pytest.mark.parametrize("code", [
    "",
    "import numpy as np\nnp.save('data/x.npy', np.zeros(3))\n",
    # Redirected prints do not reach stdout.
    "import sys\nprint('x', file=sys.stderr)\n",
    "with open('data/out.txt', 'w') as fh:\n    print('x', file=fh)\n",
    # Prints only in comments and string literals are not calls.
    "# print(1)\ns = 'print(1)'\n",
    # logging without a stdout handler goes to stderr, which the step evaluator never sees.
    "import logging\nlogging.warning('done')\n",
])
def test_silent_shapes_are_rejected(code, no_subprocess):
    assert not writes_stdout(code), code
    out = static_preflight(_state(code), _rt(PlanContext(main_task="t")))
    assert out["static_preflight_rejected"] == "no_stdout", code


# ── (4) the researcher path is untouched ───────────────────────────────────────


def test_researcher_step_is_untouched(tmp_path, no_subprocess):
    ctx = PlanContext(main_task="t")
    for code in (BROKEN, SILENT):
        st = _state(code, agent="researcher", work_dir=str(tmp_path))
        out = static_preflight(st, _rt(ctx))
        assert out["static_preflight_rejected"] is None
        assert "execution_stderr" not in out
    assert not list(tmp_path.rglob("step_*")), "nothing may be written for a researcher step"


# ── the attempt-1 data-safety fact survives a refused attempt 1 ────────────────


def test_input_dir_empty_fact_is_taken_on_a_refused_attempt_1(tmp_path, no_subprocess):
    """The executor captures `input_dir_empty` on attempt 1 ONLY. If attempt 1 never runs,
    attempt 2's executor would never set it — so the refusal takes the same reading."""
    ctx = PlanContext(main_task="t", enable_preflight=False)
    out = static_preflight(_state(BROKEN, work_dir=str(tmp_path)), _rt(ctx))
    assert out["input_dir_empty"] is True

    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "in.csv").write_text("a,b\n1,2\n")
    out = static_preflight(_state(BROKEN, work_dir=str(tmp_path)), _rt(ctx))
    assert out["input_dir_empty"] is False

    # Never on a later attempt, never when already known, never without a work_dir.
    assert "input_dir_empty" not in static_preflight(
        _state(BROKEN, work_dir=str(tmp_path), attempts=2), _rt(ctx))
    assert "input_dir_empty" not in static_preflight(
        _state(BROKEN, work_dir=str(tmp_path), input_dir_empty=False), _rt(ctx))
    assert "input_dir_empty" not in static_preflight(_state(BROKEN), _rt(ctx))


# ── the failure log write never suppresses the refusal ─────────────────────────


def test_an_unwritable_work_dir_does_not_suppress_the_refusal(tmp_path, no_subprocess):
    not_a_dir = tmp_path / "file.notadir"
    not_a_dir.write_text("x")
    out = static_preflight(_state(BROKEN, work_dir=str(not_a_dir)), _rt(PlanContext(main_task="t")))
    assert out["static_preflight_rejected"] == "syntax"


# ── end to end through the REAL compiled graph ─────────────────────────────────
#
# Attempt 1 does not parse, attempt 2 is silent (and would leave a marker file if it ever
# ran), attempt 3 prints. Exactly ONE subprocess, three engineer passes, and the two
# refusals reach the engineer as ordinary failed attempts with the diagnosis on stderr.

MARKER = "SILENT_RAN.txt"
SILENT_WITH_MARKER = f"""from pathlib import Path
Path({MARKER!r}).write_text('the silent script ran')
"""


class _Critic:
    """`with_structured_output(schema).invoke(prompt)`: the execution verdict follows the
    marker the way the template instructs; the step verdict is always fulfilled."""

    def __init__(self):
        self.schema = None
        self.exec_prompts = []

    def with_structured_output(self, schema):
        self.schema = schema
        return self

    def invoke(self, msgs, config=None):
        if self.schema is ExecutionVerdict:
            # The template's own instructions name the marker, so judge the OUTCOME
            # section only — the stderr the executor (or the refusal) produced.
            outcome = msgs[0].content.split("----- EXECUTION OUTCOME", 1)[-1]
            self.exec_prompts.append(outcome)
            if STATIC_MARKER in outcome:
                return ExecutionVerdict(status="failure", failure_kind="other",
                                        error_summary="static check", fix_suggestion="fix it")
            return ExecutionVerdict(status="success")
        return StepVerdict(fulfilled=True, unmet_requirements=[], feedback="ok")


def test_end_to_end_graph_runs_only_the_third_attempt(tmp_path, monkeypatch):
    from cmbagent_lg.self_debug.graph import graph

    monkeypatch.delenv(ENV_FLAG, raising=False)
    monkeypatch.delenv(REMOTE_ENV, raising=False)

    prompts = []
    codes = [BROKEN, SILENT_WITH_MARKER, PRINTS]

    class _Proposer:
        def invoke(self, msgs, config=None):
            prompts.append(msgs[0].content)
            return types.SimpleNamespace(text=codes[min(len(prompts) - 1, len(codes) - 1)])

    class _Formatter:
        def with_structured_output(self, schema):
            return self

        def invoke(self, msgs, config=None):
            return EngineerResponse(python_code=msgs[1].content, code_explanation="x")

    critic = _Critic()
    popen_calls = []
    real_popen = subprocess.Popen

    def _counting_popen(*a, **k):
        popen_calls.append(a[0])
        return real_popen(*a, **k)

    monkeypatch.setattr(sd_nodes, "_proposer", lambda _c: _Proposer())
    monkeypatch.setattr(sd_nodes, "_formatter", lambda _c: _Formatter())
    monkeypatch.setattr(sd_nodes, "_critic", lambda _c: critic)
    monkeypatch.setattr(sd_nodes.subprocess, "Popen", _counting_popen)

    ctx = PlanContext(
        main_task="t", enable_preflight=False, enable_intent_gate=False, vlm_enabled=False,
        max_n_attempts=3,
    )
    out = graph.invoke({"step": _step(), "step_number": 1, "work_dir": str(tmp_path)}, context=ctx)

    assert len(popen_calls) == 1, f"expected exactly one execution, got {len(popen_calls)}"
    assert not (tmp_path / MARKER).exists(), "the silent script EXECUTED"
    assert len(prompts) == 3
    assert "SyntaxError at line 4" in prompts[1] and 'label = "mean of x' in prompts[1]
    assert "never prints anything" in prompts[2]
    # The evaluator saw both refusals with the marker, then the real run.
    assert [STATIC_MARKER in p for p in critic.exec_prompts] == [True, True, False]
    assert out["execution_returncode"] == 0 and "mean = 4.500" in out["execution_stdout"]
    assert out["static_preflight_rejected"] is None, "a refusal leaked past its attempt"
    assert out["current_step_verdict"].fulfilled
    assert len(out["error_history"]) == 2

    codebase = tmp_path / "codebase"
    assert (codebase / "step_1_failure_1.py").read_text() == BROKEN
    assert (codebase / "step_1_failure_2.py").read_text() == SILENT_WITH_MARKER
    assert NO_STDOUT_MESSAGE in (codebase / "step_1_failure_2.log").read_text()
    assert (codebase / "step_1.py").read_text() == PRINTS
    assert "mean = 4.500" in (codebase / "step_1.log").read_text()
