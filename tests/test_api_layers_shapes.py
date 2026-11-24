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


