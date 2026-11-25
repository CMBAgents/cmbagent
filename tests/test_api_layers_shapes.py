"""The frozen/built-in `_workspace_root` fix, the retry report for `Cls.method()` errors and for
the shapes that name no function, the submodule candidates, and the engineer's record of every
API report it receives.

Every library here is a small synthetic package written to a temp directory named
`site-packages` (the traceback reader treats frames under such a directory as library code), and
every traceback comes from actually running a script against it in a subprocess. No model, no
network.
"""

import json
import os
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Step
from cmbagent_lg.self_debug import api_history as ah
from cmbagent_lg.self_debug import api_introspect as ai
from cmbagent_lg.self_debug import api_preflight as apf
from cmbagent_lg.self_debug import nodes as sd_nodes
from cmbagent_lg.self_debug.schemas import EngineerResponse, ExecutionVerdict

PKG = "apil_fx"

PKG_INIT = '''
"""Synthetic library for the API-layer tests."""
import ctypes

from . import _impl

__version__ = "1.0"


class FxUnknownArgumentError(ValueError):
    pass


class Base:
    def inherited(self, a=1):
        return a


class Acc(ctypes.Structure):
    _fields_ = [("boost", ctypes.c_double), ("lboost", ctypes.c_double)]


class Holder(ctypes.Structure):
    _fields_ = [("Acc", Acc), ("h", ctypes.c_double)]


def _add(_self, a, b):
    return a + b


_FN = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int)(_add)


def _wrap(f):
    def method_func(self, *args):
        return f(None, *args)
    return method_func


class Params(Base):
    def __init__(self):
        self.flag = False

    def set_x(self, a=1, b=2, neutrino_count=3):
        """Set x from a and b."""
        return a + b

    @property
    def ro(self):
        """A read-only value."""
        return 1.5

    f_AddNeutrino = _wrap(_FN)

    def opts(self, a=1, **kw):
        return a


def make():
    return Params()


def make_hidden():
    return _impl.Hidden()


def need(a, z):
    return a + z


def set_c(mnu=0.06):
    """Store a real number."""
    return ctypes.c_double(mnu)


def configure(cp=None, **params):
    """Set everything at once.

    The wrapped functions are :meth:`.Params.set_x` and :func:`.set_c`.
    """
    for k in params:
        if k not in ("a", "b", "mnu"):
            raise FxUnknownArgumentError(f"Unrecognized parameter: {k}")
    if "mnu" in params:
        set_c(mnu=params["mnu"])
    return Params()


def setup(**kw):
    h = Holder()
    for k, v in kw.items():
        setattr(h, k, v)
    return h
'''

OTHER = '''
class Params:
    def set_x(self, q=1):
        return q

    @property
    def ro(self):
        return 2.0
'''


@pytest.fixture()
def lib(tmp_path, monkeypatch):
    """`apil_fx` (plus `apil_fx.mass_function`, a submodule `__init__` never imports, and an
    unrelated `apil_other` with a same-named class) under `<tmp>/site-packages`, importable here
    and in the subprocess that runs each script."""
    site = tmp_path / "site-packages"
    (site / PKG).mkdir(parents=True)
    (site / PKG / "__init__.py").write_text(textwrap.dedent(PKG_INIT))
    (site / PKG / "mass_function.py").write_text("def massFunction(m):\n    return m\n")
    # A class the package does not export at top level; only its factory returns one.
    (site / PKG / "_impl.py").write_text("class Hidden:\n    def run(self, a=1, b=2):\n"
                                         "        return a\n")
    # Distribution metadata, so the observed-absence store can version a fact about it.
    dist = site / f"{PKG}-1.0.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {PKG}\nVersion: 1.0\n")
    (dist / "top_level.txt").write_text(PKG + "\n")
    (dist / "RECORD").write_text(f"{PKG}/__init__.py,,\n")
    (site / "apil_other").mkdir()
    (site / "apil_other" / "__init__.py").write_text(textwrap.dedent(OTHER))
    monkeypatch.syspath_prepend(str(site))
    monkeypatch.delenv("CMBAGENT_LG_REMOTE_EXECUTOR", raising=False)
    monkeypatch.delenv("CMBAGENT_LG_API_INTROSPECT_RETRY", raising=False)
    for m in [m for m in sys.modules if m.split(".")[0] in (PKG, "apil_other")]:
        sys.modules.pop(m)
    wd = tmp_path / "run"
    (wd / "codebase").mkdir(parents=True)
    yield types.SimpleNamespace(site=site, wd=wd)
    for m in [m for m in sys.modules if m.split(".")[0] in (PKG, "apil_other")]:
        sys.modules.pop(m)


def _run(lib, code):
    """Run `code` as `codebase/step_1.py` the way the executor does; return (code, stderr)."""
    script = lib.wd / "codebase" / "step_1.py"
    script.write_text(code)
    env = dict(os.environ, PYTHONPATH=str(lib.site))
    proc = subprocess.run([sys.executable, str(script)], cwd=str(lib.wd), env=env,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode != 0, "the fixture script was meant to fail"
    return code, proc.stderr


def _retry(lib, code, stderr, why=None):
    ctx = PlanContext(main_task="t", api_introspect_retry=True)
    return ai.retry_report(ctx, stderr, code, "", str(lib.wd), reasons=why)


# ── 1. frozen / built-in modules are not workspace code ─────────────────────


def test_frozen_and_builtin_modules_are_not_workspace_code(lib, monkeypatch):
    monkeypatch.chdir(lib.wd)       # the bench launches the engine with cwd = the run dir
    apf._workspace_cache.clear()
    for name in ("os", "sys", "time", "itertools"):
        assert apf._workspace_root(name, str(lib.wd)) is False, name
    # A real module file inside the workspace still counts.
    (lib.wd / "mymod_ws.py").write_text("X = 1\n")
    monkeypatch.syspath_prepend(str(lib.wd))
    apf._workspace_cache.clear()
    assert apf._workspace_root("mymod_ws", str(lib.wd)) is True

    code = ("import os, sys, time, itertools\nimport apil_fx\n"
            "p = apil_fx.Params()\np.set_x(bogus=1)\n")
    ctx = PlanContext(main_task="t", enable_api_preflight=True,
                      api_preflight_cache_dir=str(lib.wd / "cache"))
    apf._workspace_cache.clear()
    apf._skip_cache.clear()
    findings, _ = apf.collect_findings(code, ctx, str(lib.wd))
    assert [f.kind for f in findings] == ["typed_kwarg"], findings
    assert "Params.set_x" in findings[0].fid


# ── 2. retry report for `Cls.method()` errors ───────────────────────────────


def test_method_kwarg_error_on_an_untyped_receiver_gets_the_signature(lib):
    # The receiver comes from an unannotated factory AND has an attribute assigned, so no
    # pre-flight tier can type it; CPython's message still names the class.
    code, err = _run(lib, "import os\nimport apil_fx\np = apil_fx.make()\np.flag = True\n"
                          "p.set_x(c=3)\n")
    assert "Params.set_x() got an unexpected keyword argument 'c'" in err
    rep = _retry(lib, code, err)
    assert rep and "CALLABLE apil_fx.Params.set_x(a=1, b=2, neutrino_count=3)" in rep, rep
    assert "INVALID: 'c'" in rep and "VALID PARAMETER NAMES (3): a, b, neutrino_count" in rep


def test_method_error_resolution_must_agree_with_the_message(lib):
    code = "import apil_fx\np = apil_fx.make()\np.set_x(a=1)\n"
    # The resolved signature NAMES the keyword: the crash came from elsewhere.
    tb = "TypeError: Params.set_x() got an unexpected keyword argument 'a'\n"
    assert _retry(lib, code, tb) is None
    # Two imported packages export different classes named Params: ambiguous, silent.
    both = "import apil_fx\nimport apil_other\np = apil_fx.make()\np.set_x(c=1)\n"
    tb = "TypeError: Params.set_x() got an unexpected keyword argument 'c'\n"
    assert _retry(lib, both, tb) is None
    # The message names Params, but the method is defined on Base: qualnames disagree.
    tb = "TypeError: Params.inherited() got an unexpected keyword argument 'c'\n"
    assert _retry(lib, code, tb) is None
    # Named correctly (the DEFINING class), it resolves.
    code2, err = _run(lib, "import apil_fx\np = apil_fx.make()\np.inherited(c=1)\n")
    assert "Base.inherited() got an unexpected keyword argument 'c'" in err
    rep = _retry(lib, code2, err)
    assert rep and "apil_fx.Base.inherited(a=1)" in rep, rep


def test_method_kwarg_error_is_recorded_in_history(lib):
    code, err = _run(lib, "import apil_fx\np = apil_fx.make()\np.flag = 1\np.set_x(c=3)\n")
    ctx = PlanContext(main_task="t", api_preflight_cache_dir=str(lib.wd / "cache"))
    rec = ah.record_from_run(ctx, code, err, "", str(lib.wd))
    assert rec and rec["kind"] == "kwarg" and rec["cls"] == "apil_fx.Params", rec
    assert rec["attr"] == "set_x" and rec["kwarg"] == "c"


def test_missing_required_argument_names_the_parameter(lib):
    code, err = _run(lib, "import apil_fx as fx\nfx.need(1)\n")
    rep = _retry(lib, code, err)
    assert rep and "MISSING: required parameter(s) 'z'" in rep and "need(a, z)" in rep, rep
    # The script's OWN function: nothing installed to explain it.
    code, err = _run(lib, "def need(a, z):\n    return a\nneed(1)\n")
    assert _retry(lib, code, err) is None


# ── 3. shapes that name no function ─────────────────────────────────────────


def test_property_without_setter(lib):
    code, err = _run(lib, "import apil_fx\np = apil_fx.make()\np.ro = 3\n")
    assert "has no setter" in err
    rep = _retry(lib, code, err)
    assert rep and "READ-ONLY: Params.ro is a property with NO SETTER" in rep, rep
    assert "A read-only value." in rep and "SETTER METHODS OF Params" in rep


def test_calling_a_property_value(lib):
    code, err = _run(lib, "import apil_fx\np = apil_fx.make()\nv = float(p.ro())\n")
    rep = _retry(lib, code, err)
    assert rep and "NOT A METHOD: Params.ro is a data attribute (a property)" in rep, rep
    # A plain local value called by mistake: nothing installed to explain it.
    code, err = _run(lib, "import apil_fx\nx = 1.5\nx()\n")
    assert _retry(lib, code, err) is None
    # Two imported classes define a property `ro`: ambiguous, silent.
    code, err = _run(lib, "import apil_fx\nimport apil_other\np = apil_fx.make()\np.ro()\n")
    assert _retry(lib, code, err) is None


def test_ctypes_arity_is_checked_against_argtypes(lib):
    code, err = _run(lib, "import apil_fx\np = apil_fx.make()\np.f_AddNeutrino(1)\n")
    assert "this function takes at least 3 arguments (2 given)" in err
    rep = _retry(lib, code, err)
    assert rep and "takes exactly 3 arguments" in rep and "c_void_p, c_int, c_int" in rep, rep
    # The method with the related parameter name is named.
    assert "set_x: neutrino_count=3" in rep
    # A count that disagrees with the wrapper's argtypes: silent.
    tb = err.replace("at least 3 arguments", "at least 5 arguments")
    assert _retry(lib, code, tb) is None


def test_unknown_argument_from_a_forwarder(lib):
    code, err = _run(lib, "import apil_fx\ncp = apil_fx.configure(\n    a=1,\n    neutrino_cnt=3,\n)\n")
    assert "FxUnknownArgumentError: Unrecognized parameter: neutrino_cnt" in err
    rep = _retry(lib, code, err)
    assert rep and "UNKNOWN PARAMETER: 'neutrino_cnt'" in rep, rep
    assert "apil_fx.Params.set_x: a, b, neutrino_count" in rep
    assert "CLOSEST ACCEPTED PARAMETER NAMES: neutrino_count" in rep
    # The call on the line never passed the keyword (no literal, no **): silent.
    tb = err.replace("neutrino_cnt", "other_name")
    assert _retry(lib, code, tb) is None


def test_wrong_value_type_inside_the_library(lib):
    code, err = _run(lib, "import apil_fx\nh = apil_fx.setup(Acc=1)\n")
    assert "expected Acc instance, got int" in err
    rep = _retry(lib, code, err)
    assert rep and "WRONG VALUE TYPE: Acc (class apil_fx.Acc) is required" in rep, rep
    assert "Holder.Acc holds an Acc instance — the failing call passes 'Acc='" in rep
    assert "Acc MEMBERS (2): boost, lboost" in rep

    code, err = _run(lib, "import apil_fx\ncp = apil_fx.configure(a=1, mnu=[0.1])\n")
    assert "must be real number, not list" in err
    rep = _retry(lib, code, err)
    assert rep and "RAISED INSIDE: apil_fx.set_c(mnu=0.06)" in rep, rep
    assert "set_c (default shown): mnu=0.06" in rep


def test_missing_submodule_lists_the_package_submodules(lib):
    code, err = _run(lib, "import apil_fx.massfunctions\n")
    rep = _retry(lib, code, err)
    assert rep and "mass_function  (submodule: import apil_fx.mass_function)" in rep, rep
    assert "SUBMODULES OF apil_fx (1): mass_function" in rep


def test_new_shapes_classify(lib):
    assert ai.classify_api_error("AttributeError: property 'N' of 'P' object has no setter\n") == (
        "property_setter", "P", "N")
    assert ai.classify_api_error("TypeError: 'float' object is not callable\n") == (
        "not_callable", "float", "")
    assert ai.classify_api_error(
        "TypeError: this function takes at least 7 arguments (4 given)\n") == ("ctypes_arity", "7", "4")
    assert ai.classify_api_error("TypeError: expected A instance, got int\n") == (
        "value_type", "A", "int")
    assert ai.classify_api_error("TypeError: must be real number, not list\n") == (
        "value_type", "real number", "list")
    assert ai.classify_api_error(
        "pkg.base.PkgUnknownArgumentError: Unrecognized parameter: n_eff\n") == (
        "unknown_argument", "PkgUnknownArgumentError", "n_eff")
    assert ai.classify_api_error(
        "TypeError: f() missing 2 required positional arguments: 'a' and 'b'\n") == ("signature", "f", "")
    # Free text that merely mentions an unknown parameter is not an exception line.
    assert ai.classify_api_error("warning: Unrecognized parameter: x\n") is None


# ── 4. every API report the engineer receives is logged and kept ─────────────


def _engineer(ctx, wd, **state_kw):
    captured = {}

    class _FakeLLM:
        def invoke(self, msgs, config=None):
            captured["system"] = msgs[0].content
            return types.SimpleNamespace(text="```python\nprint(1)\n```")

    state = {
        "step": Step(sub_task="parse", sub_task_agent="engineer", bullet_points=["x"]),
        "step_number": 2,
        "work_dir": str(wd),
        "attempts": 1,
        "current_code": EngineerResponse(python_code="import json\njson.load_all(f)\n",
                                         code_explanation="x"),
        "current_execution_verdict": ExecutionVerdict(status="failure", failure_kind="renamed_api",
                                                      error_summary="AttributeError",
                                                      fix_suggestion="check"),
        "execution_stderr": "AttributeError: module 'json' has no attribute 'load_all'\n",
        "execution_stdout": "",
        "execution_returncode": 1,
        "execution_timed_out": False,
        "error_history": [],
    }
    state.update(state_kw)
    saved = sd_nodes._proposer
    sd_nodes._proposer = lambda _c: _FakeLLM()
    try:
        out = sd_nodes.engineer(state, types.SimpleNamespace(context=ctx))
    finally:
        sd_nodes._proposer = saved
    return out, captured["system"]


def test_engineer_keeps_every_api_report_it_receives(lib, capfd):
    ctx = PlanContext(main_task="t", api_introspect_retry=True, max_n_attempts=3)
    out, system = _engineer(ctx, lib.wd)
    log = lib.wd / "codebase" / "step_2_attempt_2.retry.log"
    assert out["attempts"] == 2 and log.exists() and "ABSENT: json.load_all" in log.read_text()
    assert "ABSENT: json.load_all" in system
    assert "step 2 attempt 2: retry report injected" in capfd.readouterr().err

    out, system = _engineer(ctx, lib.wd, attempts=2, api_preflight_report="BOUNCE REPORT TEXT")
    assert (lib.wd / "codebase" / "step_2_attempt_3.bounce.log").read_text() == "BOUNCE REPORT TEXT"
    assert out["attempts"] == 3 and "BOUNCE REPORT TEXT" in system

    # A silent retry says why, and writes nothing.
    _engineer(ctx, lib.wd, attempts=1, execution_stderr="ValueError: bad\n")
    assert "no retry report (no API-shaped error in the output)" in capfd.readouterr().err
    # Persistence failing never breaks the attempt.
    (lib.wd / "codebase").chmod(0o500)
    try:
        out, _ = _engineer(ctx, lib.wd, attempts=1)
        assert out["attempts"] == 2
    finally:
        (lib.wd / "codebase").chmod(0o700)


def test_the_reask_after_a_last_attempt_bounce_is_free(lib):
    ctx = PlanContext(main_task="t", api_introspect_retry=False, max_n_attempts=3)
    out, system = _engineer(ctx, lib.wd, attempts=3, api_preflight_report="LAST BOUNCE",
                            api_preflight_last_bounced=True)
    assert out["attempts"] == 3 and "LAST BOUNCE" in system
    # An ordinary bounce still costs an attempt.
    out, _ = _engineer(ctx, lib.wd, attempts=2, api_preflight_report="BOUNCE")
    assert out["attempts"] == 3


# ── 5. review fixes ──────────────────────────────────────────────────────────


def test_a_later_shape_does_not_hide_an_earlier_module_error(lib):
    # The script probes a removed name, catches the AttributeError, and its fallback then calls
    # None: the traceback ends with the later shape, but the module error is the one to answer.
    code, err = _run(lib, "import apil_fx\ntry:\n    f = apil_fx.gone_fn\nexcept AttributeError:\n"
                          "    f = None\n    f(1)\n")
    assert "'NoneType' object is not callable" in err
    assert ai.classify_api_error(err) == ("module", "apil_fx", "gone_fn")
    rep = _retry(lib, code, err)
    assert rep and "apil_fx.gone_fn" in rep and "ABSENT" in rep, rep


def test_a_later_shape_does_not_hide_an_earlier_keyword_error(lib):
    code, err = _run(lib, "import apil_fx\np = apil_fx.make()\np.flag = 1\ntry:\n"
                          "    p.set_x(c=3)\nexcept TypeError:\n    apil_fx.need(1)\n")
    assert "missing 1 required positional argument" in err
    assert ai.classify_api_error(err) == ("signature", "set_x", "c")
    assert ai.kwarg_error_qualifier(err) == "Params"
    ctx = PlanContext(main_task="t", api_preflight_cache_dir=str(lib.wd / "cache"))
    rec = ah.record_from_run(ctx, code, err, "", str(lib.wd))
    assert rec and rec["kind"] == "kwarg" and rec["kwarg"] == "c", rec
    # The retry tries the LAST error first; this one (a call inside an `except`) does not
    # resolve from the script, so the earlier keyword error is answered instead.
    why = []
    rep = _retry(lib, code, err, why)
    assert why == ["need unresolved from the script"], why
    assert rep and "INVALID: 'c'" in rep and "apil_fx.Params.set_x(" in rep, rep


def test_a_later_shape_alone_still_classifies():
    assert ai.classify_api_error("TypeError: 'float' object is not callable\n") == (
        "not_callable", "float", "")
    assert ai.kwarg_error_qualifier("TypeError: P.f() missing 1 required positional argument: "
                                    "'a'\n") == "P"


def test_a_silent_later_shape_still_tries_stdout(lib):
    # stderr's only hit is a later shape that resolves nothing (no script frame); the script
    # caught and printed a module error, which stdout answers.
    code = "import json\ntry:\n    json.load_all\nexcept AttributeError as e:\n    print(e)\n"
    ctx = PlanContext(main_task="t", api_introspect_retry=True)
    why = []
    rep = ai.retry_report(ctx, "TypeError: 'float' object is not callable\n", code,
                          "module 'json' has no attribute 'load_all'\n", str(lib.wd), reasons=why)
    assert rep and "ABSENT: json.load_all" in rep, why
    # An original-kind stderr hit that is silent does not fall back to stdout (unchanged).
    rep = ai.retry_report(ctx, "TypeError: nope() got an unexpected keyword argument 'x'\n", code,
                          "module 'json' has no attribute 'load_all'\n", str(lib.wd))
    assert rep is None


def test_a_script_class_shadowing_a_library_class_is_never_answered(lib):
    shadow = ("import apil_fx\nclass Params:\n    def set_x(self, a=1):\n        return a\n"
              "    @property\n    def ro(self):\n        return 2\n")
    code, err = _run(lib, shadow + "p = Params()\np.set_x(c=1)\n")
    assert "Params.set_x() got an unexpected keyword argument 'c'" in err
    why = []
    assert _retry(lib, code, err, why) is None and "defined by the script" in why[-1], why
    ctx = PlanContext(main_task="t", api_preflight_cache_dir=str(lib.wd / "cache"))
    assert ah.record_from_run(ctx, code, err, "", str(lib.wd)) is None

    code, err = _run(lib, shadow + "p = Params()\np.ro = 3\n")
    assert "has no setter" in err
    assert _retry(lib, code, err) is None

    code, err = _run(lib, shadow + "p = Params()\np.nothing_here()\n")
    assert "'Params' object has no attribute 'nothing_here'" in err
    assert _retry(lib, code, err) is None

    # An attribute the script's own class carries (class body or `self.x = ...`), called.
    for own in ("class C:\n    ro = 2.0\n", "class C:\n    def __init__(self):\n        self.ro = 2.0\n"):
        code, err = _run(lib, "import apil_fx\n" + own + "c = C()\nv = c.ro()\n")
        assert "'float' object is not callable" in err
        assert _retry(lib, code, err) is None, own


def test_history_kwarg_fact_needs_a_signature_without_var_keyword(lib):
    # `Params.opts(self, a=1, **kw)`: call binding never raises the unexpected-keyword error
    # for it, so a traceback saying so is about something else and records nothing.
    code = "import apil_fx\np = apil_fx.make()\np.flag = 1\np.opts(c=3)\n"
    tb = "TypeError: Params.opts() got an unexpected keyword argument 'c'\n"
    ctx = PlanContext(main_task="t", api_preflight_cache_dir=str(lib.wd / "cache"))
    assert ah.record_from_run(ctx, code, tb, "", str(lib.wd)) is None


def test_history_class_fallback_ignores_a_same_named_module_function(lib):
    (lib.site / "apil_kw2").mkdir()
    (lib.site / "apil_kw2" / "__init__.py").write_text("def set_x(q=1):\n    return q\n")
    code, err = _run(lib, "import apil_fx\nimport apil_kw2\napil_kw2.set_x(1)\n"
                          "p = apil_fx.make()\np.flag = 1\np.set_x(c=3)\n")
    ctx = PlanContext(main_task="t", api_preflight_cache_dir=str(lib.wd / "cache"))
    rec = ah.record_from_run(ctx, code, err, "", str(lib.wd))
    assert rec and rec["cls"] == "apil_fx.Params" and rec["kwarg"] == "c", rec


def test_qualified_message_must_agree_on_the_bare_function_path(lib):
    # `need` resolves through the script's call site to the MODULE function `apil_fx.need`,
    # but the message names `Params.need`: not the function that raised, so silent.
    code = "import apil_fx\napil_fx.need(1, w=2)\n"
    tb = "TypeError: Params.need() got an unexpected keyword argument 'w'\n"
    assert _retry(lib, code, tb) is None
    # Unqualified, the same call resolves.
    rep = _retry(lib, code, "TypeError: need() got an unexpected keyword argument 'w'\n")
    assert rep and "need(a, z)" in rep, rep


def test_method_of_a_class_the_package_does_not_export(lib):
    code, err = _run(lib, "import apil_fx\nh = apil_fx.make_hidden()\nh.run(c=1)\n")
    assert "Hidden.run() got an unexpected keyword argument 'c'" in err
    rep = _retry(lib, code, err)
    assert rep and "apil_fx._impl.Hidden.run(a=1, b=2)" in rep, rep


def test_namespace_package_in_the_workspace_is_workspace_code(lib, monkeypatch):
    (lib.wd / "helpers_ns").mkdir()
    (lib.wd / "helpers_ns" / "tools.py").write_text("raise SystemExit('executed')\n")
    monkeypatch.chdir(lib.wd)
    monkeypatch.syspath_prepend(str(lib.wd))   # e.g. an engine started with `python -m`
    apf._workspace_cache.clear()
    assert apf._workspace_root("helpers_ns", str(lib.wd)) is True
    apf._workspace_cache.clear()
    assert apf._workspace_root(PKG, str(lib.wd)) is False


def test_relative_file_origin_is_resolved_against_cwd(lib, monkeypatch):
    import importlib.machinery
    import importlib.util
    (lib.wd / "relmod.py").write_text("X = 1\n")
    spec = importlib.machinery.ModuleSpec("relmod", None, origin="relmod.py")
    spec.has_location = True
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name, *a: spec if name == "relmod" else real(name, *a))
    monkeypatch.chdir(lib.wd)
    apf._workspace_cache.clear()
    assert apf._workspace_root("relmod", str(lib.wd)) is True
    apf._workspace_cache.clear()


def test_two_bounces_at_the_same_attempt_keep_both_reports(lib):
    ctx = PlanContext(main_task="t", api_introspect_retry=False, max_n_attempts=3)
    _engineer(ctx, lib.wd, attempts=2, api_preflight_report="FIRST", api_preflight_bounces=1)
    _engineer(ctx, lib.wd, attempts=3, api_preflight_report="SECOND", api_preflight_bounces=2,
              api_preflight_last_bounced=True)
    cb = lib.wd / "codebase"
    assert (cb / "step_2_attempt_3_b1.bounce.log").read_text() == "FIRST"
    assert (cb / "step_2_attempt_3_b2.bounce.log").read_text() == "SECOND"


def test_long_reports_are_cut_at_a_line():
    text = "\n".join(f"line {i} " + "x" * 90 for i in range(200))
    out = ai._cap_report(text, limit=1000)
    assert len(out) <= 1100 and out.endswith("more lines omitted]")
    assert all(l.startswith("line ") for l in out.splitlines()[:-1])
    assert ai._cap_report("short") == "short"


# ── 6. library errors embedded in other text (b13-1 qwen r31) ────────────────
#
# The engineer wrapped each library call in `except Exception` and raised its own error, so the
# library's error survived only inside the new message; other scripts print the caught error.
# Each script really runs against the synthetic package.


def _run_out(lib, code):
    """Like `_run`, also returning stdout."""
    script = lib.wd / "codebase" / "step_1.py"
    script.write_text(code)
    env = dict(os.environ, PYTHONPATH=str(lib.site))
    proc = subprocess.run([sys.executable, str(script)], cwd=str(lib.wd), env=env,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode != 0, "the fixture script was meant to fail"
    return code, proc.stderr, proc.stdout


_WRAP = ("import apil_fx\np = apil_fx.make()\ntry:\n    p.ro = 3\nexcept Exception as e:\n"
         "    raise RuntimeError(f\"failed to configure the model: {e%s}\")%s\n")


def test_an_exception_repr_inside_another_message_is_read(lib):
    code, err = _run(lib, _WRAP % ("!r", " from None"))
    assert "RuntimeError: failed to configure the model: AttributeError(\"property 'ro'" in err, err
    assert "AttributeError: property" not in err
    assert ai.classify_api_error(err) is None            # the other readers are unchanged
    rep = _retry(lib, code, err)
    assert rep and "READ-ONLY: Params.ro is a property with NO SETTER" in rep, rep


def test_a_message_inside_another_exceptions_message_is_read(lib):
    code, err = _run(lib, _WRAP % ("", " from None"))
    assert "RuntimeError: failed to configure the model: property 'ro' of 'Params'" in err, err
    rep = _retry(lib, code, err)
    assert rep and "READ-ONLY: Params.ro is a property with NO SETTER" in rep, rep


def test_a_repr_inside_a_repr_is_read():
    # Escaped quotes: the inner repr as it appears when the outer exception is itself printed
    # as a repr. Only the matcher is exercised here.
    inner = AttributeError("property 'ro' of 'Params' object has no setter")
    text = "RuntimeError: %r\n" % RuntimeError(f"could not set: {inner!r}")
    assert "\\'ro\\'" in text
    # ...and with the inner quotes escaped instead (a double-quoted outer repr).
    text2 = ('RuntimeError("could not set: AttributeError(\\"property \'ro\' of \'Params\' object has '
             'no setter\\")")\n')
    assert 'AttributeError(\\"property' in text2
    for t in (text, text2):
        hits = ai._all_matches(t, embedded=True)
        assert [(k, m.group("type"), m.group("attr")) for _s, k, m in hits] == [
            ("property_setter", "Params", "ro")], (t, hits)


def test_chained_exceptions_are_read_with_their_own_traceback(lib):
    for tail in (" from e", ""):          # "direct cause" and "during handling" chains
        code, err = _run(lib, "import apil_fx\np = apil_fx.make()\ntry:\n    p.ro = 3\n"
                              "except Exception as e:\n    raise RuntimeError('could not configure')"
                              + tail + "\n")
        assert ("The above exception was the direct cause" in err if tail
                else "During handling of the above exception" in err), err
        rep = _retry(lib, code, err)
        assert rep and "READ-ONLY: Params.ro is a property with NO SETTER" in rep, rep
    # The segment a hit is answered from is its own traceback, up to its line.
    seg = ai._hit_segment(err, err.index("AttributeError: property"))
    assert seg.startswith("Traceback") and seg.rstrip().endswith("has no setter")
    assert "During handling" not in seg


def test_errors_a_script_prints_are_read(lib):
    ctx = PlanContext(main_task="t", api_introspect_retry=True)
    # str(e) on stdout, no traceback, exit 1.
    code, err, out = _run_out(lib, "import sys\nimport apil_fx\np = apil_fx.make()\ntry:\n    p.ro = 3\n"
                                   "except Exception as e:\n    print('error:', e)\n    sys.exit(1)\n")
    assert err == "" and "has no setter" in out
    rep = ai.retry_report(ctx, err, code, out, str(lib.wd))
    assert rep and "READ-ONLY: Params.ro is a property with NO SETTER" in rep, rep
    # repr(e) printed to stderr, no traceback.
    code, err, out = _run_out(lib, "import sys\nimport apil_fx\np = apil_fx.make()\ntry:\n    p.ro = 3\n"
                                   "except Exception as e:\n    print('error:', repr(e), file=sys.stderr)\n"
                                   "    sys.exit(1)\n")
    assert "Traceback" not in err
    rep = ai.retry_report(ctx, err, code, out, str(lib.wd))
    assert rep and "READ-ONLY: Params.ro is a property with NO SETTER" in rep, rep
    # A module error embedded in a wrapper's message keeps the module shape.
    code, err = _run(lib, "import apil_fx\ntry:\n    f = apil_fx.gone_fn\nexcept Exception as e:\n"
                          "    raise RuntimeError(f'setup failed: {e!r}') from None\n")
    rep = _retry(lib, code, err)
    assert rep and "apil_fx.gone_fn" in rep and "ABSENT" in rep, rep


def test_embedded_forms_stay_conservative(lib):
    why = []
    # A property that HAS a setter in the installed class: the introspection disagrees, silent.
    code = "import apil_fx\np = apil_fx.make()\n"
    tb = "RuntimeError: wrapped: AttributeError(\"property 'flag' of 'Params' object has no setter\")\n"
    assert _retry(lib, code, tb, why) is None and why, why
    # The script's own class: never answered by a same-named library class.
    code, err = _run(lib, "import apil_fx\nclass Params:\n    @property\n    def ro(self):\n        return 1\n"
                          "try:\n    Params().ro = 2\nexcept Exception as e:\n"
                          "    raise RuntimeError(f'{e!r}') from None\n")
    assert _retry(lib, code, err) is None
    # Not an exception class: a repr-looking call of something else.
    assert ai._all_matches("Point(\"property 'ro' of 'Params' object has no setter\")\n",
                           embedded=True) == []
    # A bare message on a line that is not an exception line, on stderr: still anchored.
    assert ai._all_matches("note: property 'ro' of 'Params' object has no setter\n",
                           embedded=True) == []
    # The call-shape messages stay out of the bare forms even inside another exception's line.
    for msg in ("set_x() got an unexpected keyword argument 'c'",
                "set_x() takes 2 positional arguments but 3 were given",
                "Line2D.set() got an unexpected keyword argument 'dpi'"):
        assert ai._all_matches(f"RuntimeError: wrapped: {msg}\n", embedded=True) == [], msg
        # ...but a repr carries the class name, so the shape is provable as on a traceback.
    assert [k for _s, k, _m in ai._all_matches(
        "RuntimeError: wrapped: TypeError(\"set_x() got an unexpected keyword argument 'c'\")\n",
        embedded=True)] == ["signature_kw"]
    # An anchored hit is not counted twice by the embedded reader.
    hits = ai._all_matches("AttributeError: property 'ro' of 'Params' object has no setter\n",
                           embedded=True)
    assert len(hits) == 1


# ── 7. an embedded hit is read only where the error that ended the script is ─────
#
# A repr anywhere in stderr used to be read: a handled warning that quoted another call's
# TypeError pinned a signature report on a call the script made correctly.

_QUOTING_WARNING = ("import statistics, sys\nprint(statistics.mean([1, 2, 3]))\n"
                    "sys.stderr.write('warning: fallback used after '\n"
                    "                 'TypeError(\"mean() got an unexpected keyword argument \\'foo\\'\")\\n')\n"
                    "raise ValueError('the step failed for its own reason')\n")


def test_a_quoted_error_in_a_warning_pins_nothing(lib):
    code, err = _run(lib, _QUOTING_WARNING)
    assert "TypeError(\"mean() got an unexpected keyword argument 'foo'\")" in err, err
    assert "ValueError: the step failed" in err
    why = []
    assert _retry(lib, code, err, why) is None, why
    # Only the traceback's own final line is read.
    regions = ai._final_exception_regions(err)
    assert [err[a:b].strip() for a, b in regions] == ["ValueError: the step failed for its own reason"]


def test_an_embedded_keyword_the_script_never_passed_pins_nothing(lib):
    # The repr IS on the final line, but the script never passes `foo` to mean().
    code, err = _run(lib, "import statistics\nprint(statistics.mean([1, 2, 3]))\n"
                          "raise RuntimeError('wrapped: TypeError(\"mean() got an unexpected keyword "
                          "argument \\'foo\\'\")')\n")
    why = []
    assert _retry(lib, code, err, why) is None
    assert any("not passed by the script" in w for w in why), why
    assert not ai._script_passes_kw(code, "mean", "foo")
    assert ai._script_passes_kw("import statistics\nstatistics.mean([1], foo=2)\n", "mean", "foo")
    assert ai._script_passes_kw("P(foo=1)\n", "__init__", "foo", "P")


def test_stdout_is_not_read_for_embedded_forms_when_stderr_has_a_traceback(lib):
    ctx = PlanContext(main_task="t", api_introspect_retry=True)
    code = "import apil_fx\np = apil_fx.make()\nraise ValueError('other')\n"
    out = "note: AttributeError(\"property 'ro' of 'Params' object has no setter\")\n"
    err = "Traceback (most recent call last):\n  File \"step_1.py\", line 3, in <module>\nValueError: other\n"
    assert ai.retry_report(ctx, err, code, out, str(lib.wd)) is None
    # Without a traceback on stderr the printed error is read, as before.
    assert "READ-ONLY" in (ai.retry_report(ctx, "", code, out, str(lib.wd)) or "")


def test_final_exception_regions_follow_each_chained_traceback():
    err = ("warning: AttributeError('x')\nTraceback (most recent call last):\n  File \"a.py\", line 1\n"
           "    p.ro = 3\nAttributeError: first\n  continued\n\nDuring handling of the above exception, "
           "another exception occurred:\n\nTraceback (most recent call last):\n  File \"a.py\", line 4\n"
           "RuntimeError: second\n")
    got = [err[a:b] for a, b in ai._final_exception_regions(err)]
    assert got == ["AttributeError: first\n  continued\n", "RuntimeError: second\n"], got
    assert ai._final_exception_regions("no traceback here") is None
