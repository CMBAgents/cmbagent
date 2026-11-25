"""format_engineer never ends the run: an answer cut off inside its code block, or a formatter that fails, fails the
attempt with a diagnosis instead (b12-3 qwen r31, 25 Nov 2026: the formatter overflowed copying a cut-off script and
the exception ended the whole run).

    PYTHONPATH=. python -m pytest tests/test_format_engineer_guard.py
"""
import subprocess
import sys
import types

from openai import LengthFinishReasonError

from cmbagent_lg.context import PlanContext
from cmbagent_lg.self_debug import nodes as sd_nodes
from cmbagent_lg.self_debug.schemas import EngineerResponse


class _Overflow(LengthFinishReasonError):
    def __init__(self):
        Exception.__init__(self, "length limit reached")


class _Formatter:
    def __init__(self, exc=None):
        self.calls, self.exc = 0, exc

    def with_structured_output(self, _schema):
        return self

    def invoke(self, msgs, config=None):
        self.calls += 1
        if self.exc:
            raise self.exc
        return EngineerResponse(python_code="print(1)\n", code_explanation="from the formatter")


def _run(monkeypatch, raw, formatter):
    monkeypatch.setattr(sd_nodes, "_formatter", lambda _c: formatter)
    rt = types.SimpleNamespace(context=PlanContext(main_task="t"))
    return sd_nodes.format_engineer({"raw_engineer": raw}, rt)["current_code"]


def _exit_message(code, tmp_path):
    p = tmp_path / "s.py"
    p.write_text(code)
    r = subprocess.run([sys.executable, str(p)], capture_output=True, text=True)
    return r.returncode, r.stderr


CUT = "**Code Explanation:**\n\nLong script.\n\n**Python Code:**\n\n```python\nimport numpy as np\n" + \
      "\n".join(f"x{i} = np.arange({i})" for i in range(300)) + "\ndef broken(:\n"


def test_cut_off_code_block_fails_the_attempt_without_the_formatter(monkeypatch, tmp_path):
    f = _Formatter()
    out = _run(monkeypatch, CUT, f)
    assert f.calls == 0
    rc, err = _exit_message(out.python_code, tmp_path)
    assert rc != 0 and "cut off at the output limit" in err and "shorter, complete script" in err


def test_formatter_overflow_fails_the_attempt_not_the_run(monkeypatch, tmp_path):
    f = _Formatter(exc=_Overflow())
    out = _run(monkeypatch, "Here is my plan, but no code block at all.", f)   # no fence: goes to the formatter
    assert f.calls == 1
    rc, err = _exit_message(out.python_code, tmp_path)
    assert rc != 0 and "formatter failed (_Overflow)" in err


def test_any_formatter_exception_is_absorbed(monkeypatch, tmp_path):
    out = _run(monkeypatch, "no code here", _Formatter(exc=ValueError("schema mismatch")))
    rc, err = _exit_message(out.python_code, tmp_path)
    assert rc != 0 and "ValueError" in err


def test_formatter_still_used_when_it_works(monkeypatch):
    f = _Formatter()
    out = _run(monkeypatch, "no fence, prose with code inline: print(1)", f)
    assert f.calls == 1 and out.python_code == "print(1)\n"


def test_empty_answer_message_unchanged(monkeypatch, tmp_path):
    out = _run(monkeypatch, "   ", _Formatter())
    rc, err = _exit_message(out.python_code, tmp_path)
    assert rc != 0 and "the engineer returned an empty answer: no code was written" in err


def test_closed_fences_are_not_cut_off():
    assert sd_nodes._cut_off_code_block("```python\nprint(1)\n```\n") == 0
    assert sd_nodes._cut_off_code_block("text\n```python\nprint(1)\nprint(2)\n") == 2
    assert sd_nodes._cut_off_code_block("```\nout\n```\n```python\nprint(1)\n```") == 0
