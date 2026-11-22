"""PlanContext.researcher_extra_tools: loading, wrapping and binding of the ReAct researcher's extra tools.

    pytest tests/test_researcher_extra_tools.py
"""
import json
import sys
import textwrap
import types
from pathlib import Path

from langchain_core.messages import AIMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Step
from cmbagent_lg.researcher import react_researcher as RR

MODULE = textwrap.dedent('''
    TOOL_GUIDANCE = "LIBRARY GUIDANCE: search, then read."

    def search(query: str, k: int = 5) -> str:
        """Keyword search over the library."""
        return "hit for %s (k=%d) at arxiv:1234.5678#2.p1" % (query, k)

    def huge() -> str:
        """Returns too much."""
        return "x" * 50_000

    def broken(paper: str) -> str:
        """Always fails."""
        raise RuntimeError("index missing")

    def list_workspace() -> str:
        """Clashes with the workspace tool."""
        return "should never be bound"

    def tools():
        return [search, huge, broken, list_workspace]
''')


def _module(tmp_path, monkeypatch, name="fake_lib_tools"):
    (tmp_path / f"{name}.py").write_text(MODULE)
    monkeypatch.syspath_prepend(str(tmp_path))
    RR._EXTRA_LOADED.clear()
    sys.modules.pop(name, None)
    return f"{name}:tools"


def test_load_wrap_cap_record(tmp_path, monkeypatch):
    spec = _module(tmp_path, monkeypatch)
    ctx = PlanContext(main_task="t", researcher_extra_tools=spec, researcher_extra_tool_chars=2000)
    seen = []
    tools, guidance = RR._extra_tools(ctx, seen, {"list_workspace", "read_file"})
    names = [t.__name__ for t in tools]
    assert names == ["search", "huge", "broken"]          # the clash is skipped
    assert "LIBRARY GUIDANCE" in guidance
    by = {t.__name__: t for t in tools}
    assert "arxiv:1234.5678#2.p1" in by["search"](query="halo gas", k=3)
    out = by["huge"]()
    assert len(out) < 2200 and "result cut at 2000 of 50000" in out
    assert by["broken"](paper="x") == "broken error: RuntimeError: index missing"
    assert [s["tool"] for s in seen] == ["search", "huge", "broken"]
    assert seen[0]["path"] == 'search:{"k": 3, "query": "halo gas"}'
    # the schema the model sees comes from the original signature and docstring
    schema = convert_to_openai_tool(by["search"])["function"]
    assert schema["name"] == "search" and "Keyword search" in schema["description"]
    assert set(schema["parameters"]["properties"]) == {"query", "k"}
    assert schema["parameters"]["required"] == ["query"]


def test_bad_spec_is_skipped(capsys):
    RR._EXTRA_LOADED.clear()
    ctx = PlanContext(main_task="t", researcher_extra_tools="no_such_module_xyz:tools")
    tools, guidance = RR._extra_tools(ctx, [], set())
    assert tools == [] and guidance == ""
    assert "not loaded" in capsys.readouterr().err


def test_env_knob(monkeypatch):
    monkeypatch.setenv("CMBAGENT_LG_RESEARCHER_EXTRA_TOOLS", "a.b:tools")
    monkeypatch.setenv("CMBAGENT_LG_RESEARCHER_MAX_EXTRA_TOOL_CALLS", "3")
    ctx = PlanContext(main_task="t")
    assert ctx.researcher_extra_tools == "a.b:tools" and ctx.researcher_max_extra_tool_calls == 3


class _Bound:
    """A model that calls `search` once, then writes the report."""

    def __init__(self, log):
        self.log, self.n = log, 0

    def bind_tools(self, tools):
        self.log["bound"] = [getattr(t, "__name__", "?") for t in tools]
        return self

    def invoke(self, messages, config=None):
        self.n += 1
        self.log.setdefault("systems", []).append(messages[0].content)
        if self.n == 1:
            return AIMessage(content="", tool_calls=[{"name": "search", "args": {"query": "gas fraction"},
                                                      "id": "c1"}])
        return AIMessage(content="# Report\nOur f_gas agrees with arxiv:1234.5678#2.p1.")


def test_researcher_binds_and_logs(tmp_path, monkeypatch):
    spec = _module(tmp_path, monkeypatch, "fake_lib_tools2")
    work = tmp_path / "work"
    for d in ("data", "reports", "logs"):
        (work / d).mkdir(parents=True)
    log = {}
    model = _Bound(log)
    monkeypatch.setattr(RR, "_proposer", lambda ctx: model)
    ctx = PlanContext(main_task="t", researcher_extra_tools=spec, vlm_enabled=False)
    step = Step(sub_task="Compare the gas fraction with the literature", sub_task_agent="researcher",
                bullet_points=["compare"])
    state = {"step": step, "work_dir": str(work), "step_number": 1, "attempts": 0}
    out = RR.react_researcher(state, types.SimpleNamespace(context=ctx))
    assert "search" in log["bound"] and "list_workspace" in log["bound"]
    assert "LIBRARY GUIDANCE" in log["systems"][0] and "Reference tools (READ-ONLY)" in log["systems"][0]
    assert "agrees with arxiv:1234.5678#2.p1" in out["current_report"]
    rec = json.loads((work / "logs" / "step_1_researcher_tools.json").read_text())
    assert rec["sources_read"][0]["tool"] == "search" and "hit for gas fraction" in rec["sources_read"][0]["output"]
