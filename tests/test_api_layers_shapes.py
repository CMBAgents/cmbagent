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


def make():
    return Params()


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


