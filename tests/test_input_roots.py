"""PlanContext.input_roots (provided input outside the work dir) and the empty-engineer-answer guard.

    PYTHONPATH=. python -m pytest tests/test_input_roots.py
"""
import tempfile
import types
from pathlib import Path

from cmbagent_lg.context import PlanContext
from cmbagent_lg.self_debug import nodes
from cmbagent_lg.self_debug.preflight import existing_input_roots
from cmbagent_lg.self_debug.prompts import render_input_roots_block


def test_existing_input_roots_keeps_only_directories():
    d = tempfile.mkdtemp()
    ctx = PlanContext(main_task="t", input_roots=[d, "/no/such/dir"])
    assert existing_input_roots(ctx) == [Path(d).resolve()]
    assert existing_input_roots(PlanContext(main_task="t")) == []


def test_input_roots_block_only_when_declared():
    d = tempfile.mkdtemp()
    assert d in render_input_roots_block(PlanContext(main_task="t", input_roots=[d]))
    assert render_input_roots_block(PlanContext(main_task="t")) == ""


def test_empty_data_dir_is_not_missing_input_when_roots_exist():
    d = tempfile.mkdtemp()
    wd = Path(tempfile.mkdtemp())
    (wd / "data").mkdir()
    with_roots = PlanContext(main_task="t", input_roots=[d])
    assert nodes._initial_input_dir_empty({"attempts": 1}, with_roots, wd / "data", {}) is False
    assert nodes._initial_input_dir_empty({"attempts": 1, "step": None}, PlanContext(main_task="t"), wd / "data", {}) is True


def test_empty_engineer_answer_is_a_failing_script():
    out = nodes.format_engineer({"raw_engineer": "  \n"}, types.SimpleNamespace(context=PlanContext(main_task="t")))
    code = out["current_code"].python_code
    try:
        exec(code, {"__name__": "__main__"})
    except SystemExit as e:
        assert "empty answer" in str(e)
    else:
        raise AssertionError("the placeholder script must fail")
