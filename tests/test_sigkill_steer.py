"""Signal-death steer: a script killed from outside the interpreter says so.

Measured on the qm9-joint fleet: 21 of 918 failed execution attempts died by signal
(cgroup OOM → SIGKILL) with EMPTY stdout and stderr. Popen reports that as rc -9 and
nothing else, so the engineer's retry context showed "returncode -9" and two empty
streams — and the same working set was resubmitted blind. The executor now appends a
`[killed by signal N (NAME) …]` line naming the mechanism, quoting the run's own
`hardware_constraints`, and listing the memory-side escape routes. The timeout branch
(rc 124, its own marker) is untouched, and a signal death that DID leave a traceback is
left to speak for itself.
"""

import os
import subprocess
import sys
import types

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Step
from cmbagent_lg.self_debug import nodes as sd_nodes
from cmbagent_lg.self_debug.nodes import _signal_steer, executor
from cmbagent_lg.self_debug.prompts import render_retry_context
from cmbagent_lg.self_debug.schemas import EngineerResponse

REMOTE_ENV = "CMBAGENT_LG_REMOTE_EXECUTOR"
HW = "Each worker is capped at 8 GB of RAM by a cgroup. 4 CPUs, no GPU.\nNo network."
CODE = "import numpy as np\nx = np.ones((10, 10))\nprint(x.sum())\n"
TRACEBACK = (
    "Traceback (most recent call last):\n  File \"step_1.py\", line 2, in <module>\n"
    "MemoryError\n"
)


def _rt(ctx):
    return types.SimpleNamespace(context=ctx)


def _state(tmp_path, **kw):
    st = {
        "step": Step(sub_task="sum it", sub_task_agent="engineer", bullet_points=["print"]),
        "current_code": EngineerResponse(python_code=CODE, code_explanation="x"),
        "attempts": 1,
        "step_number": 1,
        "work_dir": str(tmp_path),
    }
    st.update(kw)
    return st


class _FakePopen:
    """Stands in for the subprocess: `communicate` returns the scripted streams, or raises
    TimeoutExpired ONCE first when `timeout_once` is set (the executor then drains it)."""

    script = ("", "", 0)
    timeout_once = False

    def __init__(self, *a, **k):
        self.pid = 999_999_999  # never signalled: `_kill_process_group` is patched out
        self.returncode = None
        self._raised = False

    def communicate(self, timeout=None):
        if self.timeout_once and not self._raised:
            self._raised = True
            raise subprocess.TimeoutExpired(cmd="python", timeout=timeout)
        out, err, rc = self.script
        self.returncode = rc
        return out, err


@pytest.fixture
def fake_popen(monkeypatch):
    def _make(stdout, stderr, rc, timeout_once=False):
        cls = type("Popen", (_FakePopen,), {"script": (stdout, stderr, rc),
                                            "timeout_once": timeout_once})
        monkeypatch.setattr(sd_nodes.subprocess, "Popen", cls)
        monkeypatch.setattr(sd_nodes, "_kill_process_group", lambda proc: None)
        return cls

    monkeypatch.delenv(REMOTE_ENV, raising=False)
    return _make


# ── (5) rc -9 with nothing on either stream gets the steer; with a traceback it does not ──


def test_sigkill_with_empty_output_gets_the_steer(tmp_path, fake_popen):
    fake_popen("", "", -9)
    ctx = PlanContext(main_task="t", hardware_constraints=HW)
    out = executor(_state(tmp_path), _rt(ctx))

    assert out["execution_returncode"] == -9, "the signal code must be preserved as-is"
    assert out["execution_timed_out"] is False
    assert out["execution_stdout"] == ""
    err = out["execution_stderr"]
    assert "[killed by signal 9 (SIGKILL) with no Python traceback" in err, err
    assert "cgroup memory cap" in err
    assert '"Each worker is capped at 8 GB of RAM by a cgroup"' in err, (
        "the run's own hardware constraints must be quoted (first sentence)"
    )
    assert "No network" not in err
    for hint in ("float32", "chunk", "joblib"):
        assert hint in err
    assert "[killed: TimeoutExpired" not in err
    # The on-disk log carries the same text the evaluator sees.
    assert "[killed by signal 9" in (tmp_path / "codebase" / "step_1.log").read_text()

    # …and so does the engineer's retry block, under the real returncode.
    block = render_retry_context(
        attempts=2, max_attempts=3, last_code=CODE, last_stderr=err, last_stdout="",
        last_returncode=-9, last_timed_out=False, last_fix_suggestion=None, error_history=[err],
    )
    assert "returncode -9" in block and "[killed by signal 9" in block
    assert "PERFORMANCE failure" not in block, "the timeout steer must not fire on a signal"


def test_sigkill_with_a_traceback_is_left_alone(tmp_path, fake_popen):
    fake_popen("", TRACEBACK, -9)
    out = executor(_state(tmp_path), _rt(PlanContext(main_task="t")))
    assert out["execution_returncode"] == -9
    assert out["execution_stderr"] == TRACEBACK
    assert "[killed by signal" not in out["execution_stderr"]


def test_other_signals_get_the_generic_wording(tmp_path, fake_popen):
    fake_popen("partial output\n", "", -11)
    out = executor(_state(tmp_path), _rt(PlanContext(main_task="t")))
    err = out["execution_stderr"]
    assert "[killed by signal 11 (SIGSEGV) with no Python traceback" in err, err
    assert "native crash" in err and "cgroup memory cap" not in err
    assert out["execution_stdout"] == "partial output\n"


@pytest.mark.parametrize("rc", [0, 1, 2, 137])
def test_ordinary_exit_codes_are_untouched(tmp_path, fake_popen, rc):
    fake_popen("out\n", "warn\n", rc)
    out = executor(_state(tmp_path), _rt(PlanContext(main_task="t")))
    assert out["execution_returncode"] == rc
    assert out["execution_stderr"] == "warn\n"


def test_signal_steer_helper_contract():
    ctx = PlanContext(main_task="t", hardware_constraints="")
    assert _signal_steer(None, "", ctx) == ""
    assert _signal_steer(0, "", ctx) == ""
    assert _signal_steer(124, "", ctx) == ""
    assert _signal_steer(-9, "Traceback (most recent call last):", ctx) == ""
    s = _signal_steer(-9, "", ctx)
    assert s.startswith("\n[killed by signal 9 (SIGKILL)") and s.endswith("]")
    assert "hardware constraints" not in s, "nothing to quote when the field is empty"
    # A signal number the platform does not name still produces a steer.
    assert "[killed by signal 250 (unknown signal)" in _signal_steer(-250, "", ctx)


# ── (6) the timeout steer is intact ────────────────────────────────────────────


def test_timeout_steer_still_applies_for_rc_124(tmp_path, fake_popen):
    fake_popen("", "", -9, timeout_once=True)
    ctx = PlanContext(main_task="t", code_execution_timeout=7)
    out = executor(_state(tmp_path), _rt(ctx))

    assert out["execution_returncode"] == 124
    assert out["execution_timed_out"] is True
    err = out["execution_stderr"]
    assert "[killed: TimeoutExpired after 7s" in err, err
    assert "[killed by signal" not in err, "a timeout kill is explained by the timeout marker alone"

    block = render_retry_context(
        attempts=2, max_attempts=3, last_code=CODE, last_stderr=err, last_stdout="",
        last_returncode=124, last_timed_out=True, last_elapsed_s=7.2, timeout_s=7,
        last_fix_suggestion=None, error_history=[err],
    )
    assert "TIMED OUT" in block and "PERFORMANCE failure" in block and "7s budget" in block


# ── the remote path applies the same steer to the guest's result dict ──────────


def test_remote_result_gets_the_same_steer(tmp_path, monkeypatch):
    from cmbagent_lg.self_debug.remote_exec import RemoteExecutor

    seen = {}

    def _run_code(self, session, step_number, code_files, timeout=120):
        seen["step"] = step_number
        return {"type": "result", "stdout": "", "stderr": "", "returncode": -9,
                "timed_out": False, "elapsed_s": 3.0, "manifest": []}

    monkeypatch.setattr(RemoteExecutor, "run_code", _run_code)
    monkeypatch.setenv(REMOTE_ENV, "10.0.0.5:8790")
    ctx = PlanContext(main_task="t", hardware_constraints=HW)
    out = executor(_state(tmp_path), _rt(ctx))

    assert seen["step"] == 1
    assert out["execution_returncode"] == -9
    err = out["execution_stderr"]
    assert "[killed by signal 9 (SIGKILL)" in err and "8 GB of RAM" in err, err
    assert "[killed by signal 9" in (tmp_path / "codebase" / "step_1.log").read_text()

    # A guest result with a traceback, or a clean exit, is passed through verbatim.
    def _run_code_tb(self, session, step_number, code_files, timeout=120):
        return {"type": "result", "stdout": "", "stderr": TRACEBACK, "returncode": -9,
                "timed_out": False, "elapsed_s": 3.0, "manifest": []}

    monkeypatch.setattr(RemoteExecutor, "run_code", _run_code_tb)
    assert executor(_state(tmp_path), _rt(ctx))["execution_stderr"] == TRACEBACK
