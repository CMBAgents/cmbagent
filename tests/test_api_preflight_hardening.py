"""The false-positive, robustness and generality guards added to the API pre-flight after the
fleet review — every shape here is a script that RUNS in a real environment and must therefore
stay silent, or a hazard the check must never create (executing engineer code, poisoning the
observed-absence store), or the extension seam a deployment uses instead of editing core.

All shapes reuse the synthetic `apipf_fixture` library from `test_api_preflight.py`; the two
disk fixtures below (an injecting subpackage, a workspace `step_1`) are built in temp dirs.
"""

import importlib.metadata
import json
import os
import sys
import tempfile
import textwrap
import time
import types
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cmbagent_lg.context import PlanContext
from cmbagent_lg.self_debug import api_history as ah
from cmbagent_lg.self_debug import api_introspect as ai
from cmbagent_lg.self_debug import api_preflight as apf
from cmbagent_lg.self_debug import api_types as at
from cmbagent_lg.self_debug.api_preflight import api_preflight
from cmbagent_lg.self_debug.schemas import EngineerResponse

from tests.test_api_preflight import (
    ABSENT_CODE, ENV_FLAG, FIXTURE_NAME, REMOTE_ENV, _Env, _Introspector, _rt, _state,
)

NUMPY_VER = importlib.metadata.version("numpy")

# Extra shapes the base fixture does not need: a constructor that stores its arguments, one
# that takes `**kwargs`, and a factory with no annotation (typed only through an extension).
FIXTURE_EXTRA = """
__version__ = "1.0"

class Estimator:
    def __init__(self, func=None, metric="euclid"):
        self.func = func
        self.metric = metric
    def fit(self):
        self.coef_ = lambda: 1
        return self

class Bag:
    def __init__(self, **kw):
        self.__dict__.update(kw)

def make_plain():
    return Widget()

def make_many():
    return [Widget(), Widget()]
"""


class _Fx(_Introspector):
    """The base fixture plus `FIXTURE_EXTRA`, for the duration of a block."""

    def __enter__(self):
        super().__enter__()
        exec(compile(FIXTURE_EXTRA, f"<{FIXTURE_NAME}+>", "exec"), sys.modules[FIXTURE_NAME].__dict__)
        return self


def _ctx(**kw):
    return PlanContext(main_task="t", enable_api_preflight=True, **kw)


def _report(code, ctx=None, **state_kw):
    return api_preflight(_state(code, **state_kw), _rt(ctx or _ctx())).get("api_preflight_report")


def _silent(cases, ctx=None):
    for name, code in cases.items():
        rep = _report(code, ctx)
        assert rep is None, f"FALSE POSITIVE ({name!r}):\n{rep}"


def _bounced(cases, ctx=None):
    for name, (code, needle) in cases.items():
        rep = _report(code, ctx) or ""
        assert needle in rep, f"{name!r}: expected {needle!r} in:\n{rep}"


# ── conditional shapes: except handlers and environment-conditional branches ─


def test_except_fallbacks_and_version_branches_are_silent():
    fx = "import apipf_fixture as fx\n"
    silent = {
        "from-import in except ImportError": (
            "try:\n    from json import loads_v99 as loader\nexcept ImportError:\n"
            "    from json import load_all as loader\nprint(loader)\n"),
        "module chain in except AttributeError": (
            "import json\ntry:\n    r = json.loads_v99('1')\nexcept AttributeError:\n"
            "    r = json.load_all('1')\nprint(r)\n"),
        "typed kwarg in except TypeError": (
            fx + "s = fx.Strict()\ntry:\n    s.kept(1, flag=True)\nexcept TypeError:\n"
            "    s.kept(1, bogus=True)\n"),
        "hasattr guard on a module": (
            "import json\nif hasattr(json, 'load_all'):\n    print(json.load_all)\nelse:\n"
            "    print(json.load)\n"),
        "hasattr untrusts the module for the walk": (
            "import json\nok = hasattr(json, 'loads')\nprint(json.load_all)\n"),
        "version tuple derived from __version__": (
            "import json\nver = tuple(int(p) for p in json.__version__.split('.')[:2])\n"
            "if ver >= (99, 0):\n    print(json.load_all)\nelse:\n    print(json.load)\n"),
        "major derived through a second name": (
            "import json\nraw = json.__version__\nmajor = int(raw.split('.')[0])\n"
            "if major < 2:\n    print(json.load_all)\n"),
        "sys.version_info": (
            "import json, sys\nif sys.version_info >= (3, 99):\n    print(json.load_all)\n"),
        "TYPE_CHECKING import": (
            "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from json import load_all\n"
            "print(1)\n"),
        "conditional import on a version": (
            "import json\nif int(json.__version__[0]) < 2:\n    from json import load_all\n"
            "print(1)\n"),
        "IfExp on hasattr": (
            "import json\nf = json.load_all if hasattr(json, 'load_all') else json.load\nprint(f)\n"),
        "typed kwarg behind inspect.signature": (
            fx + "import inspect\ns = fx.Strict()\n"
            "if 'bogus' in inspect.signature(s.kept).parameters:\n    s.kept(1, bogus=2)\n"
            "else:\n    s.kept(1)\n"),
        "typed kwarg behind a version tuple": (
            fx + "ver = tuple(int(p) for p in fx.__version__.split('.'))\ns = fx.Strict()\n"
            "if ver >= (9, 9):\n    s.kept(1, flag=True)\nelse:\n    s.kept(1, bogus=True)\n"),
        "module compatibility shim": (
            "import json\njson.load_all = json.load\nprint(json.load_all)\n"),
        "shim guarded by hasattr": (
            "import json\nif not hasattr(json, 'load_all'):\n    json.load_all = json.load\n"
            "print(json.load_all)\n"),
    }
    with _Env(**{ENV_FLAG: None, REMOTE_ENV: None}), _Fx():
        _silent(silent)
        # A plain branch is not an environment question: the fault is still a fact.
        _bounced({
            "plain if": ("import json\nflag = True\nif flag:\n    print(json.load_all)\n",
                         "ABSENT: json.load_all"),
            # Two UNGUARDED module-level imports of one local name both execute; the failing one
            # is judged whatever the other binds.
            "two top-level from-imports": (
                "from json import loads as load_all\nfrom json import load_all\nprint(load_all)\n",
                "ABSENT: json.load_all"),
        })
        # …but a nested (function-level) alternative makes the pair a fallback: silent.
        _silent({"nested alternative": (
            "from json import load_all\ndef go():\n    from json import loads as load_all\n"
            "    return load_all\nprint(go())\n")})
    print("ok test_except_fallbacks_and_version_branches_are_silent")


# ── mutation the script performs at run time ────────────────────────────────


def test_monkeypatching_and_dynamic_namespaces_are_silent():
    fx = "import apipf_fixture as fx\n"
    silent = {
        "class monkeypatched then instance call": (
            fx + "def explain(self):\n    return 1\nfx.Widget.gone = explain\n"
            "w = fx.Widget()\nprint(w.gone())\n"),
        "from-import alias monkeypatched": (
            "from apipf_fixture import Widget\nWidget.gone = lambda self: 1\n"
            "w = Widget()\nprint(w.gone())\n"),
        "class-step call after the patch": (
            fx + "fx.Widget.gone = lambda self: 1\nw = fx.Widget()\nprint(fx.Widget.gone(w))\n"),
        "__dict__.update on the instance": (
            fx + "w = fx.Widget()\nw.__dict__.update({'gone': lambda: 1})\nprint(w.gone())\n"),
        "vars() on the instance": (
            fx + "w = fx.Widget()\nvars(w)['gone'] = lambda: 1\nprint(w.gone())\n"),
        "object.__setattr__": (
            fx + "w = fx.Widget()\nobject.__setattr__(w, 'gone', lambda: 1)\nprint(w.gone())\n"),
        "mutation through an alias": (
            fx + "w = fx.Widget()\nalias = w\nalias.gone = lambda: 1\nprint(w.gone())\n"),
        "globals() rebinding": (
            fx + "w = fx.Widget()\nglobals()['w'] = fx.Dyn()\nprint(w.gone())\n"),
        "exec rebinding": (
            fx + "w = fx.Widget()\nexec('w = fx.Dyn()')\nprint(w.gone())\n"),
        "global rebinding in a helper": (
            fx + "w = fx.Widget()\ndef swap():\n    global w\n    w = fx.Dyn()\nswap()\n"
            "print(w.gone())\n"),
        "nonlocal rebinding in a closure": (
            fx + "def outer():\n    w = fx.Widget()\n    def swap():\n        nonlocal w\n"
            "        w = fx.Dyn()\n    swap()\n    return w.gone()\nprint(outer())\n"),
    }
    with _Env(**{ENV_FLAG: None, REMOTE_ENV: None}), _Fx():
        _silent(silent)
        # The guards are narrow: an untouched instance is still judged.
        _bounced({"untouched": (fx + "w = fx.Widget()\nprint(w.gone())\n", "ABSENT: Widget.gone")})
    print("ok test_monkeypatching_and_dynamic_namespaces_are_silent")


# ── what an instance may carry that its class does not ──────────────────────


def test_instance_attributes_the_class_cannot_answer_for():
    fx = "import apipf_fixture as fx\n"
    silent = {
        "stored constructor argument, called": (
            fx + "e = fx.Estimator(func=len)\nprint(e.func([1]))\n"),
        "an __init__ parameter never passed": (
            fx + "e = fx.Estimator()\nprint(e.metric())\n"),
        "trailing-underscore attribute, called": (
            fx + "e = fx.Estimator().fit()\nprint(e.coef_())\n"),
        "**kwargs constructor with the keyword spelled": (
            fx + "b = fx.Bag(transform=len)\nprint(b.transform([1]))\n"),
        "**kwargs constructor through a literal dict": (
            fx + "opts = {'transform': len}\nb = fx.Bag(**opts)\nprint(b.transform([1]))\n"),
        "**kwargs constructor through an unknown dict": (
            fx + "opts = dict(zip('ab', 'cd'))\nb = fx.Bag(**opts)\nprint(b.gone())\n"),
        "a script-defined class handed to the constructor": (
            fx + "class Mine(fx.Strict):\n    def gone(self):\n        return 1\n"
            "w = fx.Strict(Mine)\nprint(w.gone())\n"),
    }
    with _Env(**{ENV_FLAG: None, REMOTE_ENV: None}), _Fx():
        _silent(silent)
        _bounced({
            "**kwargs constructor, name never spelled": (
                fx + "b = fx.Bag(transform=len)\nprint(b.gone())\n", "ABSENT: Bag.gone"),
            "literal dict without the name": (
                fx + "opts = {'transform': len}\nb = fx.Bag(**opts)\nprint(b.gone())\n",
                "ABSENT: Bag.gone"),
            "stored-argument class, other name": (
                fx + "e = fx.Estimator()\nprint(e.gone())\n", "ABSENT: Estimator.gone"),
        })
    print("ok test_instance_attributes_the_class_cannot_answer_for")


# ── the recorder: no not-fitted facts, no engineer code ─────────────────────


def test_recorder_refuses_instance_reads_and_workspace_modules():
    with _Env(**{REMOTE_ENV: None, ah.ENV_CACHE_DIR: None}), tempfile.TemporaryDirectory() as tmp:
        ctx = _ctx()
        poly = "from numpy.polynomial import Polynomial\np = Polynomial([1, 2])\n"
        err = "AttributeError: 'Polynomial' object has no attribute '{}'\n"
        # A trailing-underscore READ before it is computed, an __init__ parameter, a plain READ:
        # none is a fact about the API.
        for attr, line in (("coef_", "print(p.coef_)"), ("domain", "print(p.domain())"),
                           ("gone", "print(p.gone)")):
            assert ah.record_from_run(ctx, poly + line + "\n", err.format(attr), "", tmp) is None, attr
        assert ah.prompt_tier(ctx, tmp) is None
        # A CALL of a name nothing explains is recorded.
        rec = ah.record_from_run(ctx, poly + "print(p.gone())\n", err.format("gone"), "", tmp)
        assert rec and rec["kind"] == "instance_attr" and rec["attr"] == "gone", rec

        # The non-package parent shape: an extension module has no `__path__`, and that is
        # "not a submodule", not "cannot record".
        rec = ah._module_attr_record("numpy.random.mtrand", "gone_xyz_abc", None)
        assert rec and rec["module"] == "numpy.random.mtrand", rec

        # Read-time re-verification: a fact contradicted by what is loaded is dropped.
        path = ah.store_path(ctx, tmp)
        for fact in ({"kind": "module_attr", "dist": "numpy", "ver": NUMPY_VER, "cls": "",
                      "module": "numpy", "attr": "zeros", "kwarg": "", "ts": 1},
                     {"kind": "instance_attr", "dist": "numpy", "ver": NUMPY_VER,
                      "cls": "numpy.ndarray", "module": "", "attr": "sum", "kwarg": "", "ts": 1}):
            ah._append(path, fact)
        assert not [f for f in ah.facts_for(path) if f["attr"] in ("zeros", "sum")], ah.facts_for(path)

    # A sentinel-writing workspace module must never run, whatever the traceback names.
    with tempfile.TemporaryDirectory() as wd, _Env(**{REMOTE_ENV: None, ah.ENV_CACHE_DIR: None}):
        cb = Path(wd) / "codebase"
        cb.mkdir()
        sentinel = Path(wd) / "SENTINEL"
        for name in ("step_1", "helpers_ws"):
            (cb / f"{name}.py").write_text(
                f"open({str(sentinel)!r}, 'a').write('{name}')\nclass Widget:\n    pass\n"
                f"def make():\n    return Widget()\n")
        sys.path.insert(0, str(cb))
        try:
            ctx = _ctx(api_introspect_retry=True)
            shapes = {
                "import step_1\nstep_1.gone()\n": "AttributeError: module 'step_1' has no attribute 'gone'",
                "import helpers_ws\nhelpers_ws.gone()\n": "AttributeError: module 'helpers_ws' has no attribute 'gone'",
                "import step_1\nw = step_1.make()\nw.gone()\n": "AttributeError: 'Widget' object has no attribute 'gone'",
                "import numpy\nimport step_1\nx = numpy.zeros(1)\nx.gone()\n": "AttributeError: 'ndarray' object has no attribute 'gone'",
                "import step_1.sub\n": "ModuleNotFoundError: No module named 'step_1.sub'",
                "import helpers_ws.sub\n": "ModuleNotFoundError: No module named 'helpers_ws.sub'",
            }
            for code, err in shapes.items():
                ah.record_from_run(ctx, code, err + "\n", "", wd)
                ai.retry_report(ctx, err + "\n", code, "", wd)
                assert not sentinel.exists(), f"engineer code EXECUTED for {err!r}"
                assert "step_1" not in sys.modules and "helpers_ws" not in sys.modules
        finally:
            sys.path.remove(str(cb))
            sys.modules.pop("step_1", None)
            sys.modules.pop("helpers_ws", None)
    print("ok test_recorder_refuses_instance_reads_and_workspace_modules")


# ── an import that injects methods onto a class ─────────────────────────────


def test_guarded_injecting_import_is_made_before_judging():
    pkg = "apipf_inject"
    with tempfile.TemporaryDirectory() as d, _Env(**{ENV_FLAG: None, REMOTE_ENV: None}):
        root = Path(d) / pkg
        root.mkdir()
        (root / "__init__.py").write_text(textwrap.dedent("""
            class Host:
                def kept(self):
                    return 1
            def make() -> Host:
                return Host()
        """))
        (root / "extra.py").write_text(textwrap.dedent("""
            from . import Host
            setattr(Host, "injected", lambda self: 2)
        """))
        sys.path.insert(0, d)
        try:
            # Before anything injects: an absent method is caught.
            rep = _report(f"import {pkg}\nh = {pkg}.make()\nprint(h.gone())\n") or ""
            assert "ABSENT: Host.gone" in rep, rep
            # The script's own try-guarded import of the injecting submodule is made first, so
            # the method exists when the class is judged.
            _silent({
                "try-guarded injecting import": (
                    f"import {pkg}\ntry:\n    from {pkg} import extra\nexcept ImportError:\n"
                    f"    extra = None\nh = {pkg}.make()\nprint(h.injected())\n"),
                "importlib.import_module of the injector": (
                    f"import importlib\nimport {pkg}\nextra = importlib.import_module('{pkg}.extra')\n"
                    f"h = {pkg}.make()\nprint(h.injected())\n"),
            })
        finally:
            sys.path.remove(d)
            for m in [m for m in sys.modules if m == pkg or m.startswith(pkg + ".")]:
                del sys.modules[m]
    print("ok test_guarded_injecting_import_is_made_before_judging")


# ── the extension seam ──────────────────────────────────────────────────────


def test_extension_file_supplies_domain_entries_without_touching_core():
    fx = "import apipf_fixture as fx\n"
    ext = [
        {"path": "apipf_fixture.make_plain", "root": FIXTURE_NAME, "module": FIXTURE_NAME,
         "produces": "apipf_fixture.Widget",
         "witness": {"class": "apipf_fixture.Widget", "method": "kept"}},
        {"path": "apipf_fixture.make_many", "root": FIXTURE_NAME, "module": FIXTURE_NAME,
         "iter_produces": "apipf_fixture.Widget",
         "witness": {"class": "apipf_fixture.Widget", "method": "kept"}},
        # A failed witness disables the entry; malformed entries are skipped.
        {"path": "apipf_fixture.make_unknown", "root": FIXTURE_NAME, "module": FIXTURE_NAME,
         "produces": "apipf_fixture.Widget",
         "witness": {"class": "apipf_fixture.Widget", "method": "no_such_method"}},
        "garbage", {"path": 1}, {"path": "x", "root": "y"},
    ]
    with tempfile.TemporaryDirectory() as d, _Env(**{ENV_FLAG: None, REMOTE_ENV: None}), _Fx():
        f = Path(d) / "ext.json"
        f.write_text(json.dumps(ext))
        plain = fx + "w = fx.make_plain()\nprint(w.gone())\n"
        many = fx + "for w in fx.make_many():\n    print(w.gone())\n"
        unknown = fx + "w = fx.make_unknown()\nprint(w.gone())\n"
        # Without the file: nothing is typed, nothing changes. A malformed file is an empty one.
        _silent({"plain, no file": plain, "many, no file": many, "unknown, no file": unknown})
        bad = Path(d) / "bad.json"
        bad.write_text("{not json")
        _silent({"malformed file": plain}, _ctx(api_preflight_extensions=str(bad)))
        assert at.load_extensions(str(bad)) == 0 and at.load_extensions(None) == 0
        # With it: the declared facts type the receivers; a failed witness disables its entry.
        # Entries are process-global once loaded (a table, like the built-in rows); which file
        # is loaded follows the env-over-context rule, asserted on `_extensions_path` below.
        ctx = _ctx(api_preflight_extensions=str(f))
        _bounced({"produces": (plain, "ABSENT: Widget.gone"),
                  "iter_produces": (many, "ABSENT: Widget.gone")}, ctx)
        _silent({"failed witness": unknown,
                 "existing method": fx + "w = fx.make_plain()\nprint(w.kept())\n"}, ctx)
    print("ok test_extension_file_supplies_domain_entries_without_touching_core")


# ── observed facts: instance attributes and keyword collisions ──────────────


def test_fact_backed_instance_rule_and_kwarg_identity_guards():
    with _Env(**{ENV_FLAG: None, REMOTE_ENV: None}), _Fx(), tempfile.TemporaryDirectory() as tmp:
        ctx = _ctx()
        path = ah.store_path(ctx, tmp)
        path.parent.mkdir(parents=True)
        facts = [
            {"kind": "instance_attr", "dist": "numpy", "ver": NUMPY_VER, "cls": "numpy.ndarray",
             "module": "", "attr": "zzq_no_class_has_this", "kwarg": "", "suggest": ["sum"], "ts": 1},
            {"kind": "instance_attr", "dist": "numpy", "ver": NUMPY_VER, "cls": "numpy.ndarray",
             "module": "", "attr": "upper", "kwarg": "", "ts": 1},
            # A keyword fact whose recorded CLASS is not the receiver's: silent on `Strict`.
            {"kind": "kwarg", "dist": "numpy", "ver": NUMPY_VER, "cls": "apipf_fixture.Widget",
             "module": "", "attr": "kept", "kwarg": "bogus", "func": "apipf_fixture.Strict.kept", "ts": 1},
            # A keyword fact the INSTALLED signature contradicts: silent.
            {"kind": "kwarg", "dist": "numpy", "ver": NUMPY_VER, "cls": "", "module": FIXTURE_NAME,
             "attr": "strict_call", "kwarg": "target", "func": "apipf_fixture.strict_call", "ts": 1},
        ]
        path.write_text("".join(json.dumps(f) + "\n" for f in facts))
        run = lambda code: api_preflight(_state(code, work_dir=tmp), _rt(ctx)).get("api_preflight_report")
        np_ = "import numpy as np\n"
        # Untyped receiver, a name NO loaded class has, the package imported: bounced.
        rep = run(np_ + "x = np.load('data/x.npy')\nprint(x.zzq_no_class_has_this())\n") or ""
        assert "zzq_no_class_has_this" in rep and "ndarray" in rep and "observed" in rep, rep
        for name, code in {
            "a name some loaded class has (str.upper)": np_ + "x = np.load('data/x.npy')\nprint(x.upper())\n",
            "the script defines it": np_ + "class X:\n    def zzq_no_class_has_this(self):\n        pass\n"
                                     "x = np.load('data/x.npy')\nprint(x.zzq_no_class_has_this())\n",
            "the script assigns it": np_ + "x = np.load('data/x.npy')\nx.zzq_no_class_has_this = len\n"
                                     "print(x.zzq_no_class_has_this())\n",
            "the package is not imported": "x = load()\nprint(x.zzq_no_class_has_this())\n",
            "try-guarded": np_ + "x = np.load('data/x.npy')\ntry:\n    x.zzq_no_class_has_this()\n"
                           "except AttributeError:\n    pass\n",
            "read, not called": np_ + "x = np.load('data/x.npy')\nprint(x.zzq_no_class_has_this)\n",
            "kwarg fact on another class": "import apipf_fixture as fx\ns = fx.Strict()\nprint(s.kept(1, bogus=2))\n"
                                           .replace("bogus=2", "flag=True"),
            "kwarg the signature names": "import apipf_fixture as fx\nprint(fx.strict_call(1, 2, target=3))\n",
        }.items():
            rep = run(code)
            assert rep is None, f"FALSE POSITIVE ({name!r}):\n{rep}"
    print("ok test_fact_backed_instance_rule_and_kwarg_identity_guards")


# ── bounds, bookkeeping, knob precedence, the report's NOTE ─────────────────


def test_bounds_bookkeeping_precedence_and_ancestor_note():
    with _Env(**{ENV_FLAG: None, REMOTE_ENV: None}):
        ctx = _ctx()
        # A script beyond the size ceiling is let through unexamined, quickly.
        huge = "import json\n" + "x = json.loads('1')\n" * 20000
        assert len(huge) > apf._MAX_CODE_BYTES
        t0 = time.perf_counter()
        assert _report(huge, ctx) is None
        assert time.perf_counter() - t0 < 2.0
        # Foreign bookkeeping in the checkpoint never raises out of the node.
        out = api_preflight(_state(ABSENT_CODE, api_preflight_bounced=[1, 2]), _rt(ctx))
        assert out.get("api_preflight_report") and out.get("api_preflight_bounced") == {"json.load_all": 1}, out
        out = api_preflight(_state(ABSENT_CODE, api_preflight_bounced="junk"), _rt(ctx))
        assert out.get("api_preflight_report"), out

    # Env over context, for every knob that has a twin.
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        ctx = PlanContext(main_task="t", api_preflight_cache_dir=a)
        assert ah.store_path(ctx, None) == Path(a) / ah.STORE_NAME
        with _Env(**{ah.ENV_CACHE_DIR: b}):
            assert ah.store_path(ctx, None) == Path(b) / ah.STORE_NAME, "the env twin must win"
        with _Env(CMBAGENT_LG_API_MISSING_PACKAGE="pass"):
            assert apf._missing_policy(PlanContext(main_task="t", api_preflight_missing_package="report")) == "pass"
        with _Env(CMBAGENT_LG_API_EXTENSIONS=""):
            assert apf._extensions_path(PlanContext(main_task="t", api_preflight_extensions=a)) is None
        with _Env(CMBAGENT_LG_API_EXTENSIONS=None):
            assert apf._extensions_path(PlanContext(main_task="t", api_preflight_extensions=a)) == a

    # The module report names a parent that HAS the symbol before any lookalike.
    rep = ai.introspect_local("json.decoder", "loads")
    assert "NOTE: 'loads' EXISTS as json.loads" in rep, rep
    # One constant for the plugin-hierarchy guard, in both tiers.
    assert str(at.PLUGIN_HIERARCHY_MIN_SUBCLASSES) in ai.CLASS_SNIPPET
    assert "PLUGIN_HIERARCHY_MIN_SUBCLASSES" not in ai.CLASS_SNIPPET.split("_callable_class_report")[1].split("def ")[1]
    print("ok test_bounds_bookkeeping_precedence_and_ancestor_note")


if __name__ == "__main__":
    test_except_fallbacks_and_version_branches_are_silent()
    test_monkeypatching_and_dynamic_namespaces_are_silent()
    test_instance_attributes_the_class_cannot_answer_for()
    test_recorder_refuses_instance_reads_and_workspace_modules()
    test_guarded_injecting_import_is_made_before_judging()
    test_extension_file_supplies_domain_entries_without_touching_core()
    test_fact_backed_instance_rule_and_kwarg_identity_guards()
    test_bounds_bookkeeping_precedence_and_ancestor_note()
    print("ALL test_api_preflight_hardening PASSED")
