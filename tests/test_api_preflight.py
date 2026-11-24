"""Gate test for the deterministic API pre-flight, BEFORE execution.

Between `format_engineer` and `executor`, `api_preflight` verifies with `ast` + `importlib` and
NO model call that every third-party symbol the freshly-written code references exists in the
environment. A proven-absent name means the code is never run; the engineer gets the real API
facts and re-emits. The false-positive guard is the most important test here: blocking correct
code burns an attempt, which is far worse than a miss.
"""

import importlib
import importlib.machinery
import inspect
import os
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Step
from cmbagent_lg.self_debug import api_introspect as sd_api_introspect
from cmbagent_lg.self_debug import nodes as sd_nodes
from cmbagent_lg.self_debug.api_preflight import (
    absent_instance_attributes,
    absent_signature_mismatches,
    absent_symbols,
    api_preflight,
    build_report,
    extract_api_references,
    resolve_dotted_call_by_func_name,
    route_after_api_preflight,
    route_after_format_engineer,
)
from cmbagent_lg.self_debug.prompts import render_retry_context
from cmbagent_lg.self_debug.schemas import EngineerResponse, ExecutionVerdict, StepVerdict

ENV_FLAG = "CMBAGENT_LG_API_PREFLIGHT"
REMOTE_ENV = "CMBAGENT_LG_REMOTE_EXECUTOR"

# A real, always-installed module with a name that does NOT exist in it — the shape of a real
# failure (a function removed from the installed release), but version-stable.
ABSENT_CODE = """import json

with open('data/in.json') as fh:
    payload = json.load_all(fh)
print(len(payload))
"""

ABSENT_FROM_CODE = """from json import load_all

print(load_all(open('data/in.json')))
"""


class _Env:
    """Set/restore env vars around a block (the flags are process-global)."""

    def __init__(self, **kv):
        self.kv = kv
        self.saved = {}

    def __enter__(self):
        for k, v in self.kv.items():
            self.saved[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _rt(ctx):
    return types.SimpleNamespace(context=ctx)


def _state(code, **kw):
    st = {
        "step": Step(sub_task="parse the payload", sub_task_agent="engineer",
                     bullet_points=["load the file"]),
        "current_code": EngineerResponse(python_code=code, code_explanation="x"),
        "attempts": 1,
    }
    st.update(kw)
    return st


# ── the instance-shape fixture: a library, and a faithful introspector ──────
#
# Synthetic, because what is under test is which `(callable, attr)` questions the engine
# considers honest; pinning that to a real package's release would measure the environment
# instead. `Widget.gone` is the method removed in this release; `Dyn` synthesises attributes at
# run time, so its class cannot be asked; `make_unknown` is the unresolvable factory.

FIXTURE_NAME = "apipf_fixture"
FIXTURE_SRC = """
class Widget:
    def kept(self):
        return 1

class Dyn:
    def __getattr__(self, name):
        return lambda *a, **k: None

def make() -> Widget:
    return Widget()

def make_dyn() -> Dyn:
    return Dyn()

def make_unknown():          # no return annotation -> nothing to resolve
    return Widget()

def strict_call(a, b, *, target=None, verbose=False):
    '''A callable with NO **kwargs — the STRICT_SIG shape (`sc.pp.normalize_total`).'''
    return a + b

def loose_call(a, **kwargs):
    '''A callable ending in **kwargs — the KWARGS_PASSTHROUGH shape (`sc.pl.pca`).'''
    return a

import functools as _ft

def _varkw_wrapper(fn):
    @_ft.wraps(fn)
    def inner(*a, **kw):
        return fn(*a, **kw)
    return inner

class _Accessor:
    '''A `CachedAccessor`-shaped descriptor: `w.plot(...)` calls an OBJECT, not a function.'''
    def __get__(self, obj, owner=None):
        return lambda *a, **k: None

class Strict:
    '''A class whose constructor has no **kwargs — the `RipsComplex(max_edge_distance=)` shape.'''
    def __init__(self, a=0, *, target=None):
        self.a = a
    def kept(self, x=1, *, flag=False):
        return x
    def loose(self, **kw):
        return kw
    @_varkw_wrapper
    def wrapped(self, x=1):
        return x
    @property
    def prop(self):
        return lambda **k: None
    plot = _Accessor()
    def clone(self):
        '''Returns
        -------
        self
        '''
        return self

class StrictSub(Strict):
    def only_on_sub(self):
        return 1

class Boostish:
    '''A callable `inspect.signature` cannot see through (Boost.Python-shaped).'''
    __module__ = "apipf_fixture"
    __qualname__ = "boost_like"
    __name__ = "boost_like"
    def __call__(self, *a, **k):
        return None

boost_like = Boostish()
boost_like.__qualname__ = "boost_like"
boost_like.__name__ = "boost_like"

class WidgetSub(Widget):
    def kept(self, **kw):       # a subclass that takes different keywords
        return 2

def make_strict():
    '''make_strict( (int)n) -> Strict :
        a Boost.Python-style header, machine form only.'''
    return Strict()

def make_strict_rtype():
    '''Builds one.

    :rtype: Strict
    '''
    return Strict()
"""


def _install_fixture():
    """Register `apipf_fixture` in `sys.modules`, importable and with a real `__spec__`.

    The spec matters: `_skip_root` calls `find_spec`, which RAISES for a module whose
    `__spec__` is None — the fixture would then be skipped and the test would pass vacuously.
    """
    mod = types.ModuleType(FIXTURE_NAME)
    mod.__spec__ = importlib.machinery.ModuleSpec(FIXTURE_NAME, None)
    exec(compile(FIXTURE_SRC, f"<{FIXTURE_NAME}>", "exec"), mod.__dict__)
    sys.modules[FIXTURE_NAME] = mod
    return mod


def _stub_callable_class_local(spec: dict) -> str:
    """Stand-in for `api_introspect.introspect_callable_class_local`, to its stated contract.

    The source resolves it by MODULE ATTRIBUTE, so rebinding it here is the same seam, not a
    bypass. Reproduced faithfully — resolve the callable, take a class directly or a function's
    return annotation, refuse any class that synthesises attributes, and report only a PROVABLE
    absence — so the static half is exercised against its contract rather than against whatever
    the environment happens to hold.
    """
    dotted, attr = spec.get("callable", ""), spec.get("attr", "")
    if not dotted or not attr:
        return ""
    mod_path, _, name = dotted.rpartition(".")
    try:
        obj = getattr(importlib.import_module(mod_path), name, None)
    except Exception:  # noqa: BLE001 — an unimportable module answers nothing
        return ""
    if obj is None:
        return ""
    if inspect.isclass(obj):
        cls = obj
    else:
        ann = getattr(obj, "__annotations__", {}).get("return")
        cls = ann if inspect.isclass(ann) else None
    if cls is None:
        return ""
    if spec.get("static_only", True) and any(
        "__getattr__" in vars(k) or "__getattribute__" in vars(k)
        for k in cls.__mro__ if k is not object
    ):
        return ""
    if hasattr(cls, attr):
        return ""
    return (f"CLASS {cls.__module__}.{cls.__name__}  (produced by {dotted})\n"
            f"ABSENT: {cls.__name__}.{attr} does NOT exist on this class in the INSTALLED "
            f"version.\nALL PUBLIC ATTRIBUTES: "
            + ", ".join(n for n in sorted(dir(cls)) if not n.startswith("_")))


class _Introspector:
    """Install the fixture module + the introspector stub for the duration of a block."""

    def __enter__(self):
        _install_fixture()
        self.saved = getattr(sd_api_introspect, "introspect_callable_class_local", None)
        sd_api_introspect.introspect_callable_class_local = _stub_callable_class_local
        return self

    def __exit__(self, *exc):
        if self.saved is None:
            delattr(sd_api_introspect, "introspect_callable_class_local")
        else:
            sd_api_introspect.introspect_callable_class_local = self.saved
        sys.modules.pop(FIXTURE_NAME, None)


# ── OFF ⇒ nothing runs, nothing changes ─────────────────────────────────────


def test_off_is_unreachable_and_the_prompt_is_byte_identical():
    ctx = PlanContext(main_task="t", enable_api_preflight=False)
    assert ctx.enable_api_preflight is False, "explicit OFF must skip the node"
    assert ctx.max_api_preflight_bounces == 4, "the loop must ship bounded"

    with _Env(**{ENV_FLAG: None, REMOTE_ENV: None}):
        # The gate never enters the node…
        assert route_after_format_engineer(_state(ABSENT_CODE), _rt(ctx)) == "executor"
        # …and the node itself is inert even if reached (`node_elapsed_s` is the
        # @timed_node bookkeeping every node returns).
        out = api_preflight(_state(ABSENT_CODE), _rt(ctx))
        assert set(out) <= {"node_elapsed_s"}, out

    # The prompt seam's default argument reproduces the old call exactly.
    kw = dict(
        attempts=2, max_attempts=3, last_code="import json\n",
        last_stderr="boom", last_stdout="", last_returncode=1, last_timed_out=False,
        last_fix_suggestion="check it", error_history=["boom"],
    )
    assert render_retry_context(**kw) == render_retry_context(**kw, api_preflight_report=None)
    assert "API PRE-FLIGHT" not in render_retry_context(**kw)
    print("ok test_off_is_unreachable_and_the_prompt_is_byte_identical")


# ── ON + a genuinely absent symbol ⇒ proven, reported, not executed ─────────


def test_absent_symbol_is_proven_and_blocks_execution():
    ctx = PlanContext(main_task="t", enable_api_preflight=True)

    with _Env(**{ENV_FLAG: None, REMOTE_ENV: None}):
        assert absent_symbols(ABSENT_CODE) == [("json", "load_all")]
        assert absent_symbols(ABSENT_FROM_CODE) == [("json", "load_all")]

        report = build_report([("json", "load_all")])
        assert report is not None
        # The facts that matter, all of them from the installed module itself.
        assert "ABSENT: json.load_all" in report, report
        assert "json.load_all does NOT exist in the INSTALLED version" in report, report
        assert "version=" in report, "the installed version must be stated"
        for real in ("load", "loads", "dumps"):
            assert real in report, f"the real symbol {real!r} is missing from:\n{report}"
        assert "DIFFERENT ORDER" in report, "the argument-order warning is required"
        assert "load_all(" not in report, "a non-existent symbol was given a signature"

        # The node refuses to execute and hands the facts back.
        st = _state(ABSENT_CODE)
        out = api_preflight(st, _rt(ctx))
        assert out.get("api_preflight_report"), out
        assert out.get("api_preflight_bounces") == 1
        st.update(out)
        assert route_after_api_preflight(st, _rt(ctx)) == "engineer", (
            "a proven-absent symbol must NOT reach the executor"
        )

        # The env var is an equivalent switch.
        with _Env(**{ENV_FLAG: "1"}):
            out_env = api_preflight(_state(ABSENT_CODE), _rt(PlanContext(main_task="t")))
        assert out_env.get("api_preflight_report"), "the env var must enable the check"

    # And it lands in the engineer's retry block, replacing the (stale) outcome section.
    block = render_retry_context(
        attempts=2, max_attempts=3, last_code=ABSENT_CODE,
        last_stderr="an OLDER attempt's traceback", last_stdout="old stdout",
        last_returncode=1, last_timed_out=False, last_fix_suggestion="stale",
        error_history=["stale"], api_preflight_report=report,
    )
    assert "API PRE-FLIGHT" in block and "ABSENT: json.load_all" in block
    assert "an OLDER attempt's traceback" not in block, (
        "a stale traceback must not be attributed to code that never ran"
    )
    assert "**stdout:**" not in block
    print("ok test_absent_symbol_is_proven_and_blocks_execution")


# ── the false-positive guard ────────────────────────────────────────────────


# Idiomatic scientific code that must pass through UNTOUCHED. Anything reported here is a
# false positive: correct code blocked, one attempt burned, for nothing.
CLEAN_SNIPPETS = {
    "numpy basics": """
import numpy as np
x = np.linspace(0, 1, 100)
y = np.sin(2 * np.pi * x) + np.random.default_rng(0).normal(0, 0.1, x.size)
print(np.mean(y), np.std(y), np.linalg.norm(y))
cov = np.cov(np.vstack([x, y]))
print(np.linalg.eigvalsh(cov), np.float64(1.0), np.newaxis is None)
""",
    "scipy fit + stats": """
import numpy as np
from scipy.optimize import curve_fit
from scipy import stats
import scipy.integrate

def model(t, a, b):
    return a * np.exp(-b * t)

t = np.linspace(0, 5, 50)
popt, pcov = curve_fit(model, t, model(t, 2.0, 0.5))
print(popt, np.sqrt(np.diag(pcov)))
print(stats.norm.cdf(1.0), stats.ks_2samp(t, t).pvalue)
print(scipy.integrate.quad(lambda z: z ** 2, 0, 1))
""",
    "pandas": """
import pandas as pd
df = pd.read_csv('data/in.csv')
df = df.dropna().groupby('g').agg({'v': 'mean'}).reset_index()
print(df.describe(), pd.concat([df, df]).shape)
print(pd.to_numeric(df['v'], errors='coerce').sum())
""",
    "matplotlib": """
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
fig, ax = plt.subplots(figsize=(6, 4))
ax.errorbar([1, 2], [3, 4], yerr=[0.1, 0.2], fmt='o')
ax.set_xlabel('x'); ax.legend()
fig.savefig('data/out.png', dpi=150)
plt.close(fig)
""",
    "stdlib": """
import json, os, math, itertools, collections, csv
from pathlib import Path
from collections import defaultdict

p = Path('data') / 'out.json'
p.write_text(json.dumps({'a': 1}))
print(os.path.join('a', 'b'), math.sqrt(2), math.tau)
print(list(itertools.combinations(range(3), 2)))
print(collections.Counter('abc'), defaultdict(list))
print(csv.QUOTE_MINIMAL, os.environ.get('HOME'))
""",
    "submodule attribute chains": """
import numpy as np
import scipy.special
import os.path
print(np.random.default_rng(1).integers(0, 5))
print(scipy.special.erf(1.0), scipy.special.gamma(3.0))
print(os.path.basename('/a/b'), np.linalg.det(np.eye(2)))
""",
    # More distinct modules than `_MAX_MODULE_IMPORTS`. Running out of import budget must
    # degrade to "cannot prove", never to a fabricated ABSENT verdict.
    "more modules than the import budget": """
import json, os, sys, math, re, csv, io, time, base64, hashlib, textwrap, random
import statistics, functools, itertools, collections, datetime, pathlib, shutil
import numpy as np
import scipy.integrate
import scipy.optimize
import scipy.signal
print(json.dumps({}), os.sep, sys.platform, math.pi, re.escape('a'), csv.excel)
print(io.StringIO, time.time(), base64.b64encode(b''), hashlib.sha256(b'').hexdigest())
print(textwrap.dedent(''), random.random(), statistics.mean([1, 2]))
print(functools.reduce, itertools.chain, collections.OrderedDict, datetime.timezone.utc)
print(pathlib.Path('.'), shutil.which('ls'), np.pi)
print(scipy.integrate.trapezoid, scipy.optimize.brentq, scipy.signal.welch)
""",
    # Dotted from-imports of submodules that really are there. The removed-submodule path
    # ((J) below) only ever engages when the import FAILS, and every one of these succeeds —
    # this is the guard that it stays that way for the ordinary case.
    "dotted from-imports of real submodules": """
from os.path import join, basename
from json.decoder import JSONDecodeError
from collections.abc import Mapping
from numpy.linalg import norm
from scipy.special import erf
print(join('a', 'b'), basename('/a/b'), JSONDecodeError, Mapping)
print(norm([3.0, 4.0]), erf(1.0))
""",
    "method calls on instances (not module attributes)": """
import numpy as np
import pandas as pd
a = np.arange(10).reshape(2, 5)
print(a.mean(axis=0).tolist(), a.T.shape, a.astype(float).sum())
s = pd.Series([1, 2, 3])
print(s.rolling(2).mean().to_numpy().ravel())
""",
    # The INSTANCE path. Every one of these is either correct code or an unprovable question,
    # and each is a way the check could invent a verdict about an object it cannot see.
    "instance method that EXISTS": """
import apipf_fixture as fx
w = fx.make()
print(w.kept())
""",
    "instance method that exists, via from-import": """
from apipf_fixture import make
w = make()
print(w.kept())
""",
    # One binding = one object. Two bindings and the object at the access site is a different
    # one, so the producing call says nothing about it.
    "variable reassigned later": """
import apipf_fixture as fx
w = fx.make()
w = w.kept()
print(w.gone())
""",
    "factory with no resolvable return type": """
import apipf_fixture as fx
w = fx.make_unknown()
print(w.gone())
""",
    "factory reached through a local alias": """
import apipf_fixture as fx
factory = fx.make
w = factory()
print(w.gone())
""",
    "getattr-accessed object": """
import apipf_fixture as fx
w = fx.make()
print(getattr(w, 'gone')())
""",
    "setattr-monkeypatched object": """
import apipf_fixture as fx
w = fx.make()
setattr(w, 'gone', lambda: 1)
print(w.gone())
""",
    "hasattr feature probe": """
import apipf_fixture as fx
w = fx.make()
print(w.gone() if hasattr(w, 'gone') else w.kept())
""",
    "try-guarded instance probe": """
import apipf_fixture as fx
w = fx.make()
try:
    print(w.gone())
except AttributeError:
    print(w.kept())
""",
    # `__init__` can put anything on an instance, so an attribute ASSIGNED on the object is
    # present at run time however absent it is from the class.
    "attribute assigned on the instance": """
import apipf_fixture as fx
w = fx.make()
w.gone = lambda: 1
print(w.gone())
""",
    # A bare read is the instance-attribute case in general (`self.x = ...` in `__init__`),
    # which the class cannot adjudicate. Only CALLED attributes are ever probed.
    "attribute read, never called": """
import apipf_fixture as fx
w = fx.make()
print(w.gone)
""",
    # `w.gone` here is a READ whose result is called; `gone` itself is not the method being
    # invoked. Same shape as `adata.var_names.intersection(...)`, which must neither be judged
    # nor allowed to spend the probe budget.
    "chained read, then call": """
import apipf_fixture as fx
w = fx.make()
print(w.gone.anything())
""",
    "tuple unpacking": """
import apipf_fixture as fx
a, b = fx.make(), fx.make()
print(a.gone(), b.gone())
""",
    "class that synthesises its attributes": """
import apipf_fixture as fx
d = fx.make_dyn()
print(d.gone())
""",
    # The real-package version of the line above: `DataFrame.append` IS gone from pandas 2.x,
    # but DataFrame synthesises attributes for its columns, so the class is not judgeable and
    # this must pass silently either way.
    "pandas DataFrame is unjudgeable": """
import pandas as pd
frame = pd.DataFrame({'a': [1, 2]})
print(frame.append({'a': 3}, ignore_index=True))
""",
    # ── call shape: valid usage of a strict signature must run untouched ────
    "call-shape: valid kwargs on a strict signature": """
import apipf_fixture as fx
print(fx.strict_call(1, 2, target=3, verbose=True))
""",
    "call-shape: valid positional count on a strict signature": """
import apipf_fixture as fx
print(fx.strict_call(1, 2))
""",
    # A **kwargs signature is the KWARGS_PASSTHROUGH boundary (e.g. `sc.pl.pca`'s `dpi`) —
    # a permissive signature says any keyword MAY be valid, so this must stay silent even though
    # the keyword looks wrong.
    "call-shape: unknown kwarg on a **kwargs signature stays silent": """
import apipf_fixture as fx
print(fx.loose_call(1, definitely_not_a_real_param=2))
""",
    # `**extra` unpacked INTO the call is not a static fact about which keywords are passed.
    "call-shape: **extra unpacked into the call is unprovable": """
import apipf_fixture as fx
extra = {'bogus': 1}
print(fx.strict_call(1, 2, **extra))
""",
    # `*args` unpacked into the call makes the positional COUNT unprovable too.
    "call-shape: *args unpacking makes the positional count unprovable": """
import apipf_fixture as fx
args = (1, 2)
print(fx.strict_call(*args, target=3))
""",
}


def test_correct_code_is_never_flagged():
    ctx = PlanContext(main_task="t", enable_api_preflight=True)
    with _Env(**{ENV_FLAG: None, REMOTE_ENV: None}), _Introspector():
        for name, code in CLEAN_SNIPPETS.items():
            found = absent_symbols(code)
            assert found == [], f"FALSE POSITIVE in {name!r}: {found}"
            inst = absent_instance_attributes(code)
            assert inst == [], f"FALSE POSITIVE (instance) in {name!r}: {[i[:2] for i in inst]}"
            sig = absent_signature_mismatches(code)
            assert sig == [], f"FALSE POSITIVE (call shape) in {name!r}: {sig}"
            out = api_preflight(_state(code), _rt(ctx))
            assert out.get("api_preflight_report") is None, f"{name} was bounced"
            assert route_after_api_preflight(_state(code, **out), _rt(ctx)) == "executor", (
                f"{name} must run"
            )
    print(f"ok test_correct_code_is_never_flagged: {len(CLEAN_SNIPPETS)} snippets, 0 false positives")


# ── ambiguous / dynamic ⇒ prove nothing ─────────────────────────────────────


AMBIGUOUS = {
    # A star-import can silently supply any bare name, including one shadowing an alias.
    "star import": "from json import *\nimport json\nprint(json.load_all)\n",
    # Dynamic access: the attribute name is not a static fact.
    "getattr": "import json\nfn = getattr(json, 'load_all', None)\nprint(fn)\n",
    # Monkeypatched: genuinely absent from the package, legitimately present at run time.
    "setattr": "import json\nsetattr(json, 'load_all', lambda f: [])\nprint(json.load_all(1))\n",
    # The name is not the module by the time it is used.
    "rebound alias": "import json\njson = {'load_all': 1}\nprint(json.load_all)\n",
    "shadowing parameter": "import json\ndef go(json):\n    return json.load_all\nprint(go)\n",
    "loop target": "import json\nfor json in []:\n    print(json.load_all)\n",
    # Probing for an API and falling back is idiomatic; it is not a bug.
    "try-guarded access": (
        "import json\ntry:\n    x = json.load_all\nexcept AttributeError:\n    x = json.load\n"
    ),
    "try-guarded import": (
        "try:\n    import ujson as json\nexcept ImportError:\n    import json\n"
        "print(json.load_all)\n"
    ),
    # Two bindings of the same name — which module is live depends on the branch.
    "conditional import": (
        "import sys\nif sys.version_info >= (3, 12):\n    import json\nelse:\n"
        "    import simplejson as json\nprint(json.load_all)\n"
    ),
    # A module that is not installed is a DIFFERENT (recoverable) failure.
    "unimportable module": "import totally_not_a_real_pkg_xyz as z\nprint(z.anything)\n",
    # Cross-step imports of engineer-written code must never be imported to be checked.
    "workspace-local module": "import step_1\nprint(step_1.helper())\n",
    "relative import": "from . import sibling\nprint(sibling.thing)\n",
    # Attributes of objects, not of modules.
    "attribute of a call result": "import json\nprint(json.loads('{}').load_all)\n",
    "attribute of a subscript": "import json\nd = {}\nprint(d['k'].load_all)\n",
    # Nothing to parse.
    "syntax error": "import json\nthis is not python(((\n",
    "empty": "",
}


def test_ambiguous_access_proves_nothing():
    ctx = PlanContext(main_task="t", enable_api_preflight=True)
    with _Env(**{ENV_FLAG: None, REMOTE_ENV: None}):
        for name, code in AMBIGUOUS.items():
            found = absent_symbols(code, work_dir="/nonexistent/work")
            assert found == [], f"{name!r} must not be reported absent, got {found}"
            out = api_preflight(_state(code), _rt(ctx))
            assert out.get("api_preflight_report") is None, f"{name} was bounced"
    # The extractor itself refuses to reason under a star-import.
    assert extract_api_references("from json import *\nimport json\njson.x\n") == ([], [])
    print(f"ok test_ambiguous_access_proves_nothing: {len(AMBIGUOUS)} cases all pass through")


# ── a submodule REMOVED from an installed package ───────────────────────────
#
# A submodule deleted from an installed release does not import, so without this check
# `absent_symbols` returns [] and the doomed script is waved through to the executor.
# Built on DISK rather than as a synthetic `sys.modules` entry because `find_spec` is the entire
# proof and it answers from the path finders — a hand-made module object would leave the one
# thing that matters unexercised.

PKG = "apipf_disk"
PKG_FILES = {
    "__init__.py": "",
    "sub/__init__.py": "VALUE = 1\n",
    "sub/real.py": "class Thing:\n    pass\n",
    # EXISTS, and raises when its body runs. This is the case a naive `try: import` gets
    # WRONG — the import fails exactly as a removed submodule's does, but the name is there
    # and rewriting the import would fix nothing. Only `find_spec`, which never executes the
    # body, can tell the two apart.
    "sub/broken.py": "import definitely_not_installed_xyz\n\n\nclass Thing:\n    pass\n",
}


class _DiskPackage:
    """Create `apipf_disk` in a temp dir and put it on `sys.path` for the duration of a block."""

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        for rel, src in PKG_FILES.items():
            path = Path(self.tmp.name) / PKG / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(src)
        sys.path.insert(0, self.tmp.name)
        importlib.invalidate_caches()
        return self

    def __exit__(self, *exc):
        while self.tmp.name in sys.path:
            sys.path.remove(self.tmp.name)
        for name in [n for n in sys.modules if n == PKG or n.startswith(PKG + ".")]:
            sys.modules.pop(name, None)
        importlib.invalidate_caches()
        self.tmp.cleanup()


def test_removed_submodule_is_proven_from_its_parent():
    ctx = PlanContext(main_task="t", enable_api_preflight=True)
    with _Env(**{ENV_FLAG: None, REMOTE_ENV: None}), _DiskPackage():
        # PROVEN: the parent imports cleanly, has no such attribute, and `find_spec` says the
        # submodule does not exist. Reported as `(parent, leaf)` — the pair the report can
        # actually answer, since the leaf's own contents are unknowable.
        gone = f"from {PKG}.sub.gone import Thing\nprint(Thing)\n"
        assert absent_symbols(gone) == [(f"{PKG}.sub", "gone")], absent_symbols(gone)

        report = build_report(absent_symbols(gone))
        assert report and f"ABSENT: {PKG}.sub.gone" in report, report

        st = _state(gone)
        out = api_preflight(st, _rt(ctx))
        assert out.get("api_preflight_report"), "a removed submodule must not reach the executor"
        st.update(out)
        assert route_after_api_preflight(st, _rt(ctx)) == "engineer"

        # The safety case that matters most: the submodule EXISTS and its body raises. Same
        # ImportError from the outside, opposite verdict — flagging it would block correct code
        # and send the engineer chasing an import that was never the problem.
        broken = f"from {PKG}.sub.broken import Thing\nprint(Thing)\n"
        assert absent_symbols(broken) == [], (
            "a submodule that exists but fails to execute was flagged as removed"
        )
        assert api_preflight(_state(broken), _rt(ctx)).get("api_preflight_report") is None

        # …and neither is a submodule that is simply fine.
        real = f"from {PKG}.sub.real import Thing\nprint(Thing)\n"
        assert absent_symbols(real) == []

        # The attribute-chain spelling of the same fact, through the same proof.
        chain = f"import {PKG}.sub\nprint({PKG}.sub.gone.Thing)\n"
        assert absent_symbols(chain) == [(f"{PKG}.sub", "gone")], absent_symbols(chain)
        chain_broken = f"import {PKG}.sub\nprint({PKG}.sub.broken.Thing)\n"
        assert absent_symbols(chain_broken) == [], absent_symbols(chain_broken)

    # A missing TOP-LEVEL package stays out of scope: nothing is installed, so nothing can be
    # said about it, and escalation (not a bounce) is the recoverable path.
    with _Env(**{ENV_FLAG: None, REMOTE_ENV: None}):
        for missing in ("from definitely_not_installed_xyz import thing\nprint(thing)\n",
                        "from definitely_not_installed_xyz.sub import thing\nprint(thing)\n"):
            assert absent_symbols(missing) == [], missing
    print("ok test_removed_submodule_is_proven_from_its_parent")


# ── the instance shape: a removed METHOD is proven and blocks execution ─────


INSTANCE_ABSENT_CODE = """import apipf_fixture as fx

w = fx.make()
print(w.gone())
"""

INSTANCE_ABSENT_FROM_CODE = """from apipf_fixture import make

w = make()
print(w.gone())
"""


def test_absent_instance_method_is_proven_and_blocks_execution():
    """`obj.method()` is invisible to the module-alias walk (`w` is not an imported alias), so
    without the instance path the doomed call goes straight to the executor."""
    ctx = PlanContext(main_task="t", enable_api_preflight=True)
    with _Env(**{ENV_FLAG: None, REMOTE_ENV: None}), _Introspector():
        assert absent_symbols(INSTANCE_ABSENT_CODE) == [], (
            "the module walk is not supposed to see the instance shape"
        )

        for label, code in (("attribute chain", INSTANCE_ABSENT_CODE),
                            ("from-import", INSTANCE_ABSENT_FROM_CODE)):
            found = absent_instance_attributes(code)
            assert len(found) == 1, f"{label}: expected one probe, got {[f[:2] for f in found]}"
            dotted, attr, report = found[0]
            assert (dotted, attr) == ("apipf_fixture.make", "gone"), (dotted, attr)
            assert "ABSENT: Widget.gone" in report, report
            # The stub's own wording, which the real introspector does not use: proof the source
            # resolves it through the MODULE ATTRIBUTE rather than binding it at import time.
            assert "(produced by apipf_fixture.make)" in report, (
                "the introspector was not reached through `api_introspect.<attr>`"
            )

            # The facts reach the engineer through the SAME block as the module shape.
            block = build_report([], found)
            assert block is not None and "API PRE-FLIGHT" in block
            assert "ABSENT: Widget.gone" in block and "kept" in block, block

            st = _state(code)
            out = api_preflight(st, _rt(ctx))
            assert out.get("api_preflight_report"), f"{label}: the node did not bounce"
            assert out.get("api_preflight_bounces") == 1
            st.update(out)
            assert route_after_api_preflight(st, _rt(ctx)) == "engineer", (
                f"{label}: a proven-absent method must NOT reach the executor"
            )

        # Both shapes at once still yield ONE block carrying both sets of facts.
        both = "import json\n" + INSTANCE_ABSENT_CODE + "print(json.load_all(open('x')))\n"
        merged = build_report(absent_symbols(both), absent_instance_attributes(both))
        assert "ABSENT: json.load_all" in merged and "ABSENT: Widget.gone" in merged, merged

        # The probe budget is real: many candidates, at most `_MAX_CLASS_PROBES` questions.
        many = "import apipf_fixture as fx\n" + "".join(
            f"w{i} = fx.make()\nprint(w{i}.gone{i}())\n" for i in range(6)
        )
        assert len(absent_instance_attributes(many)) <= 2, "the probe budget is not enforced"
    print("ok test_absent_instance_method_is_proven_and_blocks_execution")


# ── the call shape: an invented keyword or wrong arity on a REAL callable ───
#
# A signature with no **kwargs rejects an invented keyword, which is PROVABLE before execution.
# One ending in **kwargs forwards it several frames deeper, which is not provable and must stay
# silent (the KWARGS_PASSTHROUGH snippets in CLEAN_SNIPPETS above).

SIGNATURE_MISMATCH_KWARG_CODE = """import apipf_fixture as fx

fx.strict_call(1, 2, bogus=3)
"""

SIGNATURE_MISMATCH_ARITY_CODE = """import apipf_fixture as fx

fx.strict_call(1, 2, 3, 4)
"""


def test_signature_mismatch_is_proven_and_blocks_execution():
    ctx = PlanContext(main_task="t", enable_api_preflight=True)
    with _Env(**{ENV_FLAG: None, REMOTE_ENV: None}), _Introspector():
        # The invented-keyword shape (the `verbose`/`normalize_total` case).
        found = absent_signature_mismatches(SIGNATURE_MISMATCH_KWARG_CODE)
        assert len(found) == 1, found
        dotted, report = found[0]
        assert dotted == "apipf_fixture.strict_call", dotted
        assert "INVALID: keyword(s) not in the INSTALLED signature: bogus" in report, report
        assert "VALID PARAMETER NAMES" in report, report
        for real in ("a", "b", "target", "verbose"):
            assert real in report, f"the real parameter {real!r} is missing from:\n{report}"

        block = build_report([], [], found)
        assert block is not None and "API PRE-FLIGHT" in block and "bogus" in block, block

        st = _state(SIGNATURE_MISMATCH_KWARG_CODE)
        out = api_preflight(st, _rt(ctx))
        assert out.get("api_preflight_report"), "the node did not bounce"
        assert out.get("api_preflight_bounces") == 1
        st.update(out)
        assert route_after_api_preflight(st, _rt(ctx)) == "engineer", (
            "a proven keyword/arity mismatch must NOT reach the executor"
        )

        # The arity shape.
        found_arity = absent_signature_mismatches(SIGNATURE_MISMATCH_ARITY_CODE)
        assert len(found_arity) == 1, found_arity
        _, arity_report = found_arity[0]
        assert "INVALID: 4 positional argument(s) given" in arity_report, arity_report
        assert "takes at most 2" in arity_report, arity_report
    print("ok test_signature_mismatch_is_proven_and_blocks_execution")


def test_resolve_dotted_call_by_func_name():
    """Finds the callable a bare-named TypeError refers to, from the failing script's own call
    sites — the seam `api_introspect.retry_report`'s call-shape branch uses."""
    code = "import apipf_fixture as fx\nfx.strict_call(1, 2, bogus=3)\n"
    assert resolve_dotted_call_by_func_name(code, "strict_call") == "apipf_fixture.strict_call"
    # Not called anywhere -> unresolvable.
    assert resolve_dotted_call_by_func_name(code, "nope") is None
    # Two DIFFERENT dotted paths ending in the same bare name -> genuinely ambiguous (this
    # function only resolves the STRING path from trusted aliases; it does not check the
    # attribute actually exists — that verification is the introspector's job, downstream).
    ambiguous = (
        "import apipf_fixture as fx\nimport json\n"
        "fx.strict_call(1, 2)\njson.strict_call(1, 2)\n"
    )
    assert resolve_dotted_call_by_func_name(ambiguous, "strict_call") is None, (
        "two different resolved dotted paths for the same bare name must be treated as ambiguous"
    )
    print("ok test_resolve_dotted_call_by_func_name")


# ── a bounce is attributable after the fact ─────────────────────────────────


def test_a_bounce_persists_the_rejected_code():
    """`codebase/step_N.py` is written by the EXECUTOR, which a bounced step never reaches — so
    without this the rejected code leaves no trace at all."""
    ctx = PlanContext(main_task="t", enable_api_preflight=True)
    with _Env(**{ENV_FLAG: None, REMOTE_ENV: None}):
        with tempfile.TemporaryDirectory() as tmp:
            out = api_preflight(_state(ABSENT_CODE, work_dir=tmp), _rt(ctx))
            assert out.get("api_preflight_bounces") == 1
            py = Path(tmp) / "codebase" / "step_1_bounced_1.py"
            log = Path(tmp) / "codebase" / "step_1_bounced_1.log"
            assert py.exists(), f"the rejected script was not kept: {list(Path(tmp).rglob('*'))}"
            assert py.read_text() == ABSENT_CODE, "a different script was recorded"
            assert "ABSENT: json.load_all" in log.read_text(), "the reason was not recorded"

            # The index is the bounce number and the step number is honoured, so two bounces of
            # the same step do not overwrite each other.
            out2 = api_preflight(
                _state(ABSENT_CODE, work_dir=tmp, step_number=4, api_preflight_bounces=1),
                _rt(ctx),
            )
            assert out2.get("api_preflight_bounces") == 2
            assert (Path(tmp) / "codebase" / "step_4_bounced_2.py").exists()
            assert py.read_text() == ABSENT_CODE, "the first bounce was overwritten"

        # An unwritable work_dir (a FILE where the directory should be) must not raise, and
        # above all must not suppress the bounce — the artifact is bookkeeping, the bounce is
        # the safety property.
        with tempfile.NamedTemporaryFile(suffix=".notadir") as fh:
            out3 = api_preflight(_state(ABSENT_CODE, work_dir=fh.name), _rt(ctx))
            assert out3.get("api_preflight_report"), "a failed write suppressed the bounce"
            assert out3.get("api_preflight_bounces") == 1
    print("ok test_a_bounce_persists_the_rejected_code")


# ── the bounds ──────────────────────────────────────────────────────────────


def test_the_check_cannot_loop_forever():
    ctx = PlanContext(main_task="t", enable_api_preflight=True, max_api_preflight_bounces=2,
                      max_n_attempts=5)
    with _Env(**{ENV_FLAG: None, REMOTE_ENV: None}):
        st = _state(ABSENT_CODE, api_preflight_bounces=0)
        for expected in (1, 2):
            out = api_preflight(st, _rt(ctx))
            assert out.get("api_preflight_bounces") == expected, out
            st["api_preflight_bounces"] = out["api_preflight_bounces"]
        # Cap reached: the same absent symbol now DEGRADES TO EXECUTION.
        out = api_preflight(st, _rt(ctx))
        assert out.get("api_preflight_report") is None, "the cap was not enforced"
        assert route_after_api_preflight(_state(ABSENT_CODE, **out), _rt(ctx)) == "executor"

        # The LAST attempt is bounced at most once (the engineer's re-ask after it does not
        # count as an attempt), so the step still ends having executed a script.
        spent = _state(ABSENT_CODE, attempts=3, api_preflight_bounces=0)
        ctx3 = PlanContext(main_task="t", enable_api_preflight=True, max_n_attempts=3)
        last = api_preflight(spent, _rt(ctx3))
        assert last.get("api_preflight_report") and last.get("api_preflight_last_bounced") is True
        again = _state(ABSENT_CODE, attempts=3, api_preflight_bounces=1,
                       api_preflight_last_bounced=True)
        assert api_preflight(again, _rt(ctx3)).get("api_preflight_report") is None
        # A single-attempt budget has no retry prompt to carry the report: never bounced.
        ctx1 = PlanContext(main_task="t", enable_api_preflight=True, max_n_attempts=1)
        assert api_preflight(_state(ABSENT_CODE, attempts=1), _rt(ctx1)).get(
            "api_preflight_report") is None
    print("ok test_the_check_cannot_loop_forever: bounce cap and attempt budget both enforced")


# ── remote execution ⇒ ask the guest, never the engine venv ─────────────────


def test_remote_executor_never_answers_from_the_engine_venv():
    """R-17a: under a remote executor the check runs, but every verdict comes from the guest
    sandbox (proven in tests/test_api_remote_introspect.py). What must never happen is the ENGINE
    venv standing in for it — so a guest that cannot answer means the code runs untouched."""
    from cmbagent_lg.self_debug.remote_exec import RemoteExecutor

    ctx = PlanContext(main_task="t", enable_api_preflight=True)
    st = _state(ABSENT_CODE, work_dir="/tmp/apipf-remote/sess")
    saved = RemoteExecutor._request

    def _down(*a, **k):
        raise ConnectionRefusedError("guest down")

    RemoteExecutor._request = _down
    try:
        with _Env(**{ENV_FLAG: "1", REMOTE_ENV: "10.0.0.5:8790"}):
            out = api_preflight(st, _rt(ctx))
            assert out.get("api_preflight_report") is None, (
                "with a remote executor the engine venv is NOT the environment the code will run "
                f"in; judging the guest's API from it could block correct code — got {out}"
            )
    finally:
        RemoteExecutor._request = saved
    with _Env(**{ENV_FLAG: "1", REMOTE_ENV: None}):
        assert api_preflight(_state(ABSENT_CODE), _rt(ctx)).get("api_preflight_report")
    print("ok test_remote_executor_never_answers_from_the_engine_venv")


# ── end-to-end through the REAL compiled graph ──────────────────────────────


# Attempt 1 references a name the installed release does not have — and would leave a marker
# file behind if it ever ran. Attempt 2 is correct.
MARKER = "IT_EXECUTED.txt"
DOOMED = f"""import json
from pathlib import Path
Path({MARKER!r}).write_text('the doomed script ran')
print(json.load_all(open('x')))
"""
FIXED = """import json
print(json.loads('{"ok": 1}'))
"""


class _StubVerdicts:
    """`with_structured_output(schema).invoke(...)` → a canned object per schema."""

    def __init__(self, mapping):
        self.mapping = mapping
        self.schema = None

    def with_structured_output(self, schema):
        self.schema = schema
        return self

    def invoke(self, msgs, config=None):
        return self.mapping[self.schema]


def _drive_graph(enable: bool):
    """Run the REAL compiled graph over `DOOMED` then `FIXED` with the LLM calls stubbed.

    Returns `(engineer_prompts, marker_exists, final_state)`.
    """
    from cmbagent_lg.self_debug.graph import graph

    prompts = []
    codes = [DOOMED, FIXED]

    class _Proposer:
        def invoke(self, msgs, config=None):
            prompts.append(msgs[0].content)
            return types.SimpleNamespace(text=codes[min(len(prompts) - 1, len(codes) - 1)])

    def _formatter(_ctx):
        # Echo the proposer's text back as the structured code, verbatim.
        holder = {}

        class _F:
            def with_structured_output(self, schema):
                holder["schema"] = schema
                return self

            def invoke(self, msgs, config=None):
                return EngineerResponse(python_code=msgs[1].content, code_explanation="x")

        return _F()

    verdicts = _StubVerdicts({
        ExecutionVerdict: ExecutionVerdict(status="success"),
        StepVerdict: StepVerdict(fulfilled=True, unmet_requirements=[], feedback="ok"),
    })

    saved = (sd_nodes._proposer, sd_nodes._formatter, sd_nodes._critic)
    sd_nodes._proposer = lambda _c: _Proposer()
    sd_nodes._formatter = _formatter
    sd_nodes._critic = lambda _c: verdicts
    try:
        with tempfile.TemporaryDirectory() as tmp, _Env(**{ENV_FLAG: None, REMOTE_ENV: None}):
            ctx = PlanContext(
                main_task="t", enable_api_preflight=enable,
                # Isolate the seam under test: no data preflight/intent-gate LLM calls,
                # no vision reviewer.
                enable_preflight=False, enable_intent_gate=False, vlm_enabled=False,
                max_n_attempts=3,
            )
            state = {
                "step": Step(sub_task="parse the payload", sub_task_agent="engineer",
                             bullet_points=["load the file"]),
                "step_number": 1,
                "work_dir": tmp,
            }
            out = graph.invoke(state, context=ctx)
            return prompts, (Path(tmp) / MARKER).exists(), out
    finally:
        sd_nodes._proposer, sd_nodes._formatter, sd_nodes._critic = saved


def test_end_to_end_graph_never_executes_the_doomed_script():
    prompts, marker, out = _drive_graph(enable=True)
    assert not marker, (
        "the doomed script EXECUTED — pre-flight must block it before the executor"
    )
    assert len(prompts) == 2, f"expected exactly one bounce, got {len(prompts)} engineer passes"
    assert "API PRE-FLIGHT" in prompts[1], "the report never reached the engineer"
    assert "ABSENT: json.load_all" in prompts[1]
    assert "API PRE-FLIGHT" not in prompts[0], "attempt 1 must be untouched"
    # The corrected attempt really did run, and the bounce is consumed, not sticky.
    assert out["execution_returncode"] == 0, out.get("execution_stderr")
    assert out.get("api_preflight_report") is None, "the report leaked past its attempt"
    assert out["current_step_verdict"].fulfilled
    print("ok test_end_to_end_graph_never_executes_the_doomed_script: marker absent, facts delivered")


def test_end_to_end_graph_off_is_todays_behaviour():
    """The other half of the OFF guarantee: not just "the node returns nothing" but "the graph
    does what it did before the node existed" — the doomed script IS executed."""
    prompts, marker, out = _drive_graph(enable=False)
    assert marker, "with the flag OFF the script must run, unchanged"
    assert len(prompts) == 1, "OFF must not add an engineer pass"
    assert "API PRE-FLIGHT" not in prompts[0]
    assert out.get("api_preflight_report") is None
    assert out.get("api_preflight_bounces") is None
    print("ok test_end_to_end_graph_off_is_todays_behaviour: doomed script ran, no extra pass")


# ── hardening: alias merge, dotted imports, class-step, ledger ──────────────


def test_alias_merge_and_dotted_imports():
    """`import a` + `import a.b` is ONE binding of `a`; a from-import is judged whatever its
    local name does afterwards; `import a.gone` is judged as a removed submodule."""
    with _Env(**{ENV_FLAG: None, REMOTE_ENV: None}), _DiskPackage():
        merged = f"import {PKG}\nimport {PKG}.sub\nprint({PKG}.sub.gone)\n"
        assert absent_symbols(merged) == [(f"{PKG}.sub", "gone")], absent_symbols(merged)
        # Two DIFFERENT targets under one name stay ambiguous.
        two = f"import {PKG} as a\nimport {PKG}.sub as a\nprint(a.gone)\n"
        assert absent_symbols(two) == [], absent_symbols(two)
        # A rebinding still untrusts the alias.
        rebound = f"import {PKG}\nimport {PKG}.sub\n{PKG} = 1\nprint({PKG}.sub.gone)\n"
        assert absent_symbols(rebound) == [], absent_symbols(rebound)
        # The from-import statement raises on its own, however `gone` is rebound later.
        frm = f"from {PKG}.sub import gone\ngone = 1\nprint(gone)\n"
        assert absent_symbols(frm) == [(f"{PKG}.sub", "gone")], absent_symbols(frm)
        # `import a.b.gone` — the statement is the reference.
        dotted = f"import {PKG}.sub.gone\nprint(1)\n"
        assert absent_symbols(dotted) == [(f"{PKG}.sub", "gone")], absent_symbols(dotted)
        ctx = PlanContext(main_task="t", enable_api_preflight=True)
        assert api_preflight(_state(dotted), _rt(ctx)).get("api_preflight_report")
        # …but not one that merely fails to execute, nor one that is fine.
        assert absent_symbols(f"import {PKG}.sub.broken\nprint(1)\n") == []
        assert absent_symbols(f"import {PKG}.sub.real\nprint(1)\n") == []
    print("ok test_alias_merge_and_dotted_imports")


def test_class_step_walk_and_constructor_rendering():
    """`mod.Class.gone(...)` is one provable step past a class; a class callable renders as its
    constructor so the report speaks the traceback's language."""
    ctx = PlanContext(main_task="t", enable_api_preflight=True)
    with _Env(**{ENV_FLAG: None, REMOTE_ENV: None}), _Introspector():
        out = api_preflight(_state("import apipf_fixture as fx\nprint(fx.Widget.gone())\n"), _rt(ctx))
        assert out.get("api_preflight_report") and "ABSENT: Widget.gone" in out["api_preflight_report"], out
        assert out.get("api_preflight_bounced") == {"Widget.gone": 1}, out.get("api_preflight_bounced")
        # A class attribute that EXISTS, and a subclass-only method, stay silent.
        for ok in ("import apipf_fixture as fx\nprint(fx.Widget.kept)\n",
                   "import apipf_fixture as fx\nprint(fx.Strict.only_on_sub)\n"):
            assert api_preflight(_state(ok), _rt(ctx)).get("api_preflight_report") is None, ok
        out = api_preflight(_state("import apipf_fixture as fx\nfx.Strict(bogus=1)\n"), _rt(ctx))
        rep = out.get("api_preflight_report") or ""
        assert "(constructor Strict.__init__)" in rep and "bogus" in rep, rep
    print("ok test_class_step_walk_and_constructor_rendering")


def test_import_ledger_over_budget_is_unknown_not_absent():
    """Budget spent ⇒ the question was never asked. No bounce, no raise."""
    with _Env(**{ENV_FLAG: None, REMOTE_ENV: None, "CMBAGENT_LG_API_IMPORT_BUDGET_S": "0"}), \
            _DiskPackage():
        for name in [n for n in sys.modules if n == PKG or n.startswith(PKG + ".")]:
            sys.modules.pop(name, None)
        code = f"import {PKG}\nprint({PKG}.sub.gone)\n"
        assert absent_symbols(code) == [], "an exhausted budget produced a verdict"
        ctx = PlanContext(main_task="t", enable_api_preflight=True)
        assert api_preflight(_state(code), _rt(ctx)).get("api_preflight_report") is None
    with _Env(**{ENV_FLAG: None, REMOTE_ENV: None, "CMBAGENT_LG_API_IMPORT_BUDGET_S": None}), \
            _DiskPackage():
        assert absent_symbols(f"import {PKG}\nprint({PKG}.sub.gone)\n") == [(f"{PKG}.sub", "gone")]
    print("ok test_import_ledger_over_budget_is_unknown_not_absent")


def test_per_symbol_bounce_budget():
    """The same finding coming back is executed; a NEW finding bounces, up to the ceiling."""
    ctx = PlanContext(main_task="t", enable_api_preflight=True, max_n_attempts=9)
    assert ctx.max_api_preflight_bounces == 4
    with _Env(**{ENV_FLAG: None, REMOTE_ENV: None}):
        st = _state(ABSENT_CODE, api_preflight_bounces=1, api_preflight_bounced={"json.load_all": 1})
        assert api_preflight(st, _rt(ctx)).get("api_preflight_report") is None, (
            "a finding already reported to this step must not bounce it again")
        st = _state(ABSENT_CODE, api_preflight_bounces=0)
        for i in range(1, 5):
            code = f"import json\nprint(json.load_all_{i}(1))\n"
            st["current_code"] = EngineerResponse(python_code=code, code_explanation="x")
            out = api_preflight(st, _rt(ctx))
            assert out.get("api_preflight_bounces") == i, (i, out)
            st.update(out)
        assert set(st["api_preflight_bounced"]) == {f"json.load_all_{i}" for i in range(1, 5)}
        st["current_code"] = EngineerResponse(python_code="import json\nprint(json.load_all_9(1))\n",
                                              code_explanation="x")
        assert api_preflight(st, _rt(ctx)).get("api_preflight_report") is None, "the ceiling"
        # Old checkpoints carry no `api_preflight_bounced`; None reads as empty.
        st = _state(ABSENT_CODE, api_preflight_bounced=None)
        assert api_preflight(st, _rt(ctx)).get("api_preflight_bounces") == 1
    print("ok test_per_symbol_bounce_budget")


# ── hardening: typed receivers ──────────────────────────────────────────────


TYPED_BOUNCED = {
    "absent method on a constructed instance": (
        "import apipf_fixture as fx\nw = fx.Widget()\nprint(w.gone())\n", "ABSENT: Widget.gone"),
    "absent method, receiver bound inside a function": (
        "import apipf_fixture as fx\ndef go():\n    w = fx.Widget()\n    return w.gone()\nprint(go())\n",
        "ABSENT: Widget.gone"),
    "kwarg on a plain method": (
        "import apipf_fixture as fx\ns = fx.Strict()\nprint(s.kept(1, bogus=2))\n",
        "INVALID: keyword(s) not in the INSTALLED signature: bogus"),
    "produced through a Boost-style header": (
        "import apipf_fixture as fx\ns = fx.make_strict()\nprint(s.gone())\n", "ABSENT: Strict.gone"),
    "produced through :rtype:": (
        "import apipf_fixture as fx\ns = fx.make_strict_rtype()\nprint(s.gone())\n", "ABSENT: Strict.gone"),
    "produced through a typed receiver's method (Returns self)": (
        "import apipf_fixture as fx\ns = fx.Strict()\nt = s.clone()\nprint(t.gone())\n", "ABSENT: Strict.gone"),
    "same class bound twice agrees": (
        "import apipf_fixture as fx\ns = fx.Strict()\ns = fx.Strict(2)\nprint(s.gone())\n",
        "ABSENT: Strict.gone"),
}

TYPED_SILENT = {
    "parameter": "import apipf_fixture as fx\ndef go(w):\n    return w.gone()\nprint(go(fx.Widget()))\n",
    "getattr-touched": "import apipf_fixture as fx\nw = fx.Widget()\nprint(getattr(w, 'x'), w.gone())\n",
    "delattr-touched": "import apipf_fixture as fx\nw = fx.Widget()\ndelattr(w, 'x')\nprint(w.gone())\n",
    "disagreeing bindings": "import apipf_fixture as fx\nw = fx.Widget()\nw = fx.Dyn()\nprint(w.gone())\n",
    "global declaration": "import apipf_fixture as fx\nw = fx.Widget()\ndef go():\n    global w\n    w = 1\n    return w.gone()\nprint(go())\n",
    "bound inside try": "import apipf_fixture as fx\ntry:\n    w = fx.Widget()\nexcept Exception:\n    w = None\nprint(w.gone())\n",
    "attribute assigned on the receiver": "import apipf_fixture as fx\nw = fx.Widget()\nw.gone = 1\nprint(w.gone())\n",
    "a loaded subclass defines it": "import apipf_fixture as fx\ns = fx.Strict()\nprint(s.only_on_sub())\n",
    "the script defines a class of that name": "import apipf_fixture as fx\nclass Widget:\n    pass\nw = fx.Widget()\nprint(w.gone())\n",
    "__getattr__ class": "import apipf_fixture as fx\nd = fx.Dyn()\nprint(d.gone())\n",
    "kwarg on an accessor": "import apipf_fixture as fx\ns = fx.Strict()\nprint(s.plot(bogus=1))\n",
    "kwarg on a property": "import apipf_fixture as fx\ns = fx.Strict()\nprint(s.prop(bogus=1))\n",
    "kwarg on a **kwargs method": "import apipf_fixture as fx\ns = fx.Strict()\nprint(s.loose(bogus=1))\n",
    "kwarg on a wrapped-varkw method": "import apipf_fixture as fx\ns = fx.Strict()\nprint(s.wrapped(bogus=1))\n",
    "kwarg the method accepts": "import apipf_fixture as fx\ns = fx.Strict()\nprint(s.kept(x=2, flag=True))\n",
    "** unpacked into the call": "import apipf_fixture as fx\ns = fx.Strict()\nkw = {}\nprint(s.kept(**kw))\n",
    "read of an absent attribute on a class whose instances carry a __dict__": (
        "import apipf_fixture as fx\ns = fx.Strict()\nprint(s.gone)\n"),
    "Signature.empty is never typed": "import apipf_fixture as fx\nw = fx.make_unknown()\nprint(w.gone())\n",
    "receiver reached through a subscript of a non-axes binding": (
        "import apipf_fixture as fx\nws = [fx.Widget()]\nprint(ws[0].gone())\n"),
    "receiver from a list literal": "import apipf_fixture as fx\nw = [fx.Widget()]\nprint(w.gone())\n",
    "comprehension variable is its own scope": (
        "import apipf_fixture as fx\nw = fx.Widget()\nprint([w.kept() for w in [1, 2]])\n"),
    "call-rooted receiver is never typed": "import apipf_fixture as fx\nprint(fx.Strict().clone().gone())\n",
    # A factory DECLARES `Widget`; a loaded subclass takes other keywords on `kept`, so the
    # object handed back may be one of those.
    "factory-declared class with a subclass override": (
        "import apipf_fixture as fx\nw = fx.make()\nprint(w.kept(bogus=1))\n"),
}


def test_typed_receivers_bounce_only_when_proven():
    ctx = PlanContext(main_task="t", enable_api_preflight=True)
    with _Env(**{ENV_FLAG: None, REMOTE_ENV: None}), _Introspector():
        for name, (code, needle) in TYPED_BOUNCED.items():
            out = api_preflight(_state(code), _rt(ctx))
            rep = out.get("api_preflight_report") or ""
            assert needle in rep, f"{name!r}: expected {needle!r} in:\n{rep}"
        for name, code in TYPED_SILENT.items():
            out = api_preflight(_state(code), _rt(ctx))
            assert out.get("api_preflight_report") is None, (
                f"FALSE POSITIVE ({name!r}):\n{out.get('api_preflight_report')}")
    print(f"ok test_typed_receivers_bounce_only_when_proven: {len(TYPED_BOUNCED)} bounced, "
          f"{len(TYPED_SILENT)} silent")


def test_matplotlib_subplots_and_legend_rules():
    """The witness-gated matplotlib entries: `subplots` shape rule and the `legend` forwarder."""
    try:
        import matplotlib  # noqa: F401
        matplotlib.use("Agg")
        import matplotlib.pyplot  # noqa: F401
    except Exception:  # noqa: BLE001
        print("skip test_matplotlib_subplots_and_legend_rules: matplotlib not installed")
        return
    ctx = PlanContext(main_task="t", enable_api_preflight=True)
    hdr = "import matplotlib.pyplot as plt\n"
    bounced = {
        "1x1": hdr + "fig, ax = plt.subplots()\nax.boxplot([1], labels=['a'])\n",
        "1d subscript": hdr + "fig, axes = plt.subplots(1, 2)\naxes[1].boxplot([1], labels=['a'])\n",
        "1d tuple unpack": hdr + "fig, (a, b) = plt.subplots(2, 1)\nb.boxplot([1], labels=['a'])\n",
        "2d subscript": hdr + "fig, axes = plt.subplots(2, 2)\naxes[0, 1].boxplot([1], labels=['a'])\n",
        "2d nested unpack": hdr + "fig, ((a, b), (c, d)) = plt.subplots(2, 2)\nd.boxplot([1], labels=['a'])\n",
        "iteration over .flat": hdr + "fig, axes = plt.subplots(2, 2)\nfor ax in axes.flat:\n    ax.boxplot([1], labels=['a'])\n",
        "enumerate": hdr + "fig, axes = plt.subplots(1, 3)\nfor i, ax in enumerate(axes):\n    ax.boxplot([1], labels=['a'])\n",
        "legend forwarder on pyplot": hdr + "plt.legend(loc='best', alpha=0.9)\n",
        "legend forwarder on Axes": hdr + "fig, ax = plt.subplots()\nax.legend(['x'], alpha=0.9)\n",
        "figure": hdr + "fig, ax = plt.subplots()\nfig.savefigure('x.png')\n",
    }
    silent = {
        "squeeze=": hdr + "fig, ax = plt.subplots(1, 1, squeeze=False)\nax.boxplot([1], labels=['a'])\n",
        "subplot_kw=": hdr + "fig, ax = plt.subplots(subplot_kw={'projection': 'polar'})\nax.set_rmax(2)\n",
        "non-literal shape": hdr + "n = 2\nfig, axes = plt.subplots(1, n)\naxes[0].boxplot([1], labels=['a'])\n",
        "2d indexed with one subscript": hdr + "fig, axes = plt.subplots(2, 2)\naxes[0].boxplot([1], labels=['a'])\n",
        "wrong-arity unpack": hdr + "fig, (a, b, c) = plt.subplots(1, 2)\nc.boxplot([1], labels=['a'])\n",
        "legend whitelists handles/labels": hdr + "plt.legend(handles=[], labels=[], loc='best')\n",
        # `gca()` DECLARES `Axes`, a plugin point whose projections register subclasses as
        # they are used — the declared class says nothing about the object handed back.
        "plt.gca is never judged": hdr + "ax = plt.gca()\nax.boxplot([1], labels=['a'])\nax.set_zlabel('z')\n",
        "valid keyword": hdr + "fig, ax = plt.subplots()\nax.boxplot([1], tick_labels=['a'])\nax.plot([1], [1], linewidth=2)\n",
    }
    with _Env(**{ENV_FLAG: None, REMOTE_ENV: None}):
        for name, code in bounced.items():
            rep = api_preflight(_state(code), _rt(ctx)).get("api_preflight_report") or ""
            assert rep, f"{name!r} was not bounced"
        for name, code in silent.items():
            rep = api_preflight(_state(code), _rt(ctx)).get("api_preflight_report")
            assert rep is None, f"FALSE POSITIVE ({name!r}):\n{rep}"
    print("ok test_matplotlib_subplots_and_legend_rules")


# ── hardening: missing top-level packages ───────────────────────────────────


def test_missing_package_policies_and_refusals():
    from cmbagent_lg.self_debug import api_history

    MISSING = "totally_not_a_real_pkg_xyz"
    code = f"import {MISSING}\nprint({MISSING}.go())\n"
    with _Env(**{ENV_FLAG: None, REMOTE_ENV: None, "CMBAGENT_LG_API_MISSING_PACKAGE": None}), \
            tempfile.TemporaryDirectory() as tmp:
        def run(policy, c=code, **ctx_kw):
            ctx = PlanContext(main_task="t", enable_api_preflight=True,
                              api_preflight_missing_package=policy, **ctx_kw)
            return api_preflight(_state(c, work_dir=tmp), _rt(ctx)).get("api_preflight_report")
        assert run("pass") is None
        rep = run("report")
        assert rep and f"PACKAGE {MISSING!r} is NOT INSTALLED" in rep, rep
        # `auto`: silent until the environment has been SEEN to lack it…
        assert run("auto") is None
        api_history._append(api_history.store_path(PlanContext(main_task="t"), tmp), {
            "kind": "no_module", "dist": MISSING, "ver": "absent", "cls": "", "module": MISSING,
            "attr": "", "kwarg": "", "ts": 1})
        assert run("auto"), "an observed missing package must bounce under auto"
        # …and never when the run can install: escalation, the explicit context knob, or its
        # env twin (which overrides the knob). No agent-NAME convention is consulted.
        assert run("auto", enable_escalation=True) is None
        assert run("auto", api_preflight_can_install=True) is None
        assert run("auto", available_agents=[("engineer", "x"), ("installer", "pip")]), (
            "an agent called 'installer' is not a statement that this run can install")
        with _Env(CMBAGENT_LG_API_CAN_INSTALL="1"):
            assert run("auto") is None
        with _Env(CMBAGENT_LG_API_CAN_INSTALL="0"):
            assert run("auto", api_preflight_can_install=True), "the env twin overrides the field"
        # The refusal shapes, all under the strictest policy.
        for name, c in {
            "try-guarded": f"try:\n    import {MISSING}\nexcept ImportError:\n    {MISSING} = None\nprint(1)\n",
            "inside a function": f"def go():\n    import {MISSING}\n    return {MISSING}\nprint(1)\n",
            "sys.path touched": f"import sys\nsys.path.append('lib')\nimport {MISSING}\nprint(1)\n",
            "importlib.import_module": f"import importlib\nimport {MISSING}\nm = importlib.import_module('x')\nprint(m)\n",
            "subprocess": f"import subprocess\nimport {MISSING}\nprint(subprocess.run)\n",
            "stdlib name": "import this_is_not_stdlib_but_json\nimport json\nprint(1)\n".replace(
                "this_is_not_stdlib_but_json", "json"),
        }.items():
            assert run("report", c) is None, f"FALSE POSITIVE ({name!r})"
        # Resolves inside the workspace ⇒ engineer-written, never judged.
        (Path(tmp) / "codebase").mkdir(parents=True, exist_ok=True)
        (Path(tmp) / "codebase" / f"{MISSING}.py").write_text("def go(): pass\n")
        assert run("report") is None, "a workspace module was reported missing"
        # No work_dir ⇒ cannot tell where the script runs from ⇒ silent.
        ctx = PlanContext(main_task="t", enable_api_preflight=True, api_preflight_missing_package="report")
        assert api_preflight(_state(code), _rt(ctx)).get("api_preflight_report") is None
    print("ok test_missing_package_policies_and_refusals")


if __name__ == "__main__":
    test_off_is_unreachable_and_the_prompt_is_byte_identical()
    test_absent_symbol_is_proven_and_blocks_execution()
    test_correct_code_is_never_flagged()
    test_ambiguous_access_proves_nothing()
    test_removed_submodule_is_proven_from_its_parent()
    test_absent_instance_method_is_proven_and_blocks_execution()
    test_signature_mismatch_is_proven_and_blocks_execution()
    test_resolve_dotted_call_by_func_name()
    test_a_bounce_persists_the_rejected_code()
    test_the_check_cannot_loop_forever()
    test_remote_executor_never_answers_from_the_engine_venv()
    test_end_to_end_graph_never_executes_the_doomed_script()
    test_end_to_end_graph_off_is_todays_behaviour()
    test_alias_merge_and_dotted_imports()
    test_class_step_walk_and_constructor_rendering()
    test_import_ledger_over_budget_is_unknown_not_absent()
    test_per_symbol_bounce_budget()
    test_typed_receivers_bounce_only_when_proven()
    test_matplotlib_subplots_and_legend_rules()
    test_missing_package_policies_and_refusals()
    print("ALL test_api_preflight PASSED")
