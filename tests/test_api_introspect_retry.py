"""Gate test for `api_introspect.retry_report`: deterministic API introspection injected into
the PLAIN engineer's retry.

It parses the module+symbol off a traceback and runs importlib + inspect + difflib with NO model
call. Driven through the real `render_retry_context` and the real `nodes.engineer` node with the
LLM stubbed. Assertions are made against stdlib modules wherever possible, so they are
version-stable in any venv.
"""

import difflib
import json
import os
import subprocess
import sys
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Step
from cmbagent_lg.self_debug import nodes as sd_nodes
from cmbagent_lg.self_debug.api_introspect import (
    CLASS_SNIPPET,
    SIGNATURE_SNIPPET,
    classify_api_error,
    introspect_artist_kwarg_local,
    introspect_call_signature_local,
    introspect_callable_class_local,
    introspect_full_class_local,
    introspect_local,
    module_shape_report,
    parse_api_error,
    retry_report,
)
from cmbagent_lg.self_debug.prompts import render_retry_context
from cmbagent_lg.self_debug.schemas import EngineerResponse, ExecutionVerdict


ENV_FLAG = "CMBAGENT_LG_API_INTROSPECT_RETRY"
REMOTE_ENV = "CMBAGENT_LG_REMOTE_EXECUTOR"
# A leftover from a local engine/broker run. CI never sets it. Drop it at import so a
# developer shell cannot make `module_shape_report` (and friends) look broken.
os.environ.pop(REMOTE_ENV, None)

# A real, always-installed module with a name that does NOT exist in it: the shape of a function
# removed from the installed release, but version-stable.
ATTR_TB = """Traceback (most recent call last):
  File "/w/codebase/step_1.py", line 4, in <module>
    payload = json.load_all(fh)
              ^^^^^^^^^^^^^
AttributeError: module 'json' has no attribute 'load_all'
"""

IMPORT_TB = """Traceback (most recent call last):
  File "/w/codebase/step_1.py", line 2, in <module>
    from json import load_all
ImportError: cannot import name 'load_all' from 'json' (/usr/lib/python3.12/json/__init__.py)
"""

# Not API-shaped: no module+symbol pair to read off, so acting on it would mean guessing.
LOGIC_TB = """Traceback (most recent call last):
  File "/w/codebase/step_1.py", line 9, in <module>
    rate = hits / total
           ~~~~~^~~~~~~
ZeroDivisionError: division by zero
"""
# BARE (undotted) ModuleNotFoundError: names an uninstalled PACKAGE, not a removed API. There is
# nothing to introspect — the fix is to install it — so this must keep parsing as nothing.
MISSING_MOD_TB = """Traceback (most recent call last):
  File "/w/codebase/step_1.py", line 1, in <module>
    import cobaya
ModuleNotFoundError: No module named 'cobaya'
"""
# DOTTED ModuleNotFoundError: the parent package IS installed and the leaf was REMOVED. Both
# facts are in the text, so the ordinary module-shape report applies. Stdlib parent, so the
# assertions hold in any venv.
SUBMODULE_TB = """Traceback (most recent call last):
  File "/w/codebase/step_1.py", line 1, in <module>
    import json.decoder_all
ModuleNotFoundError: No module named 'json.decoder_all'
"""
# CPython appends one of these tails depending on why the leaf is missing; neither changes the
# two facts before it.
SUBMODULE_TAIL_TB = ("ModuleNotFoundError: No module named 'Bio.SeqUtils.CodonUsage'; "
                     "'Bio.SeqUtils' is not a package\n")
# Nothing to import, so nothing to report: the parent is absent too.
SUBMODULE_NO_PARENT_TB = """Traceback (most recent call last):
  File "/w/codebase/step_1.py", line 1, in <module>
    import nosuchpkg_zz.sub.leaf
ModuleNotFoundError: No module named 'nosuchpkg_zz.sub.leaf'
"""
INSTANCE_ATTR_TB = """Traceback (most recent call last):
  File "/w/codebase/step_1.py", line 7, in <module>
    df = df.append(row)
AttributeError: 'DataFrame' object has no attribute 'append'
"""
PANDAS_CODE = "import pandas as pd\ndf = pd.read_csv('data/x.csv')\ndf = df.append(row)\n"

# Instance shape against a STDLIB class, so the assertions hold in any venv.
COUNTER_TB = """Traceback (most recent call last):
  File "/w/codebase/step_1.py", line 5, in <module>
    top = counts.sort_values(ascending=False)
AttributeError: 'Counter' object has no attribute 'sort_values'
"""
COUNTER_CODE = "from collections import Counter\ncounts = Counter(words)\ncounts.sort_values()\n"


class _Env:
    """Set/restore env vars around a block (the flag is process-global)."""

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


def _retry(report=None):
    """The retry block as the plain engineer renders it after a crash."""
    return render_retry_context(
        attempts=2,
        max_attempts=3,
        last_code="import json\npayload = json.load_all(open('f'))\n",
        last_stderr=ATTR_TB,
        last_stdout="",
        last_returncode=1,
        last_timed_out=False,
        last_fix_suggestion="Check the function name.",
        error_history=["AttributeError on json.load_all"],
        api_introspect_report=report,
    )


# ── OFF ⇒ byte-identical ────────────────────────────────────────────────────


def test_off_leaves_the_retry_context_byte_identical():
    ctx = PlanContext(main_task="t", api_introspect_retry=False)
    assert ctx.api_introspect_retry is False, "explicit OFF must not introspect"

    with _Env(**{ENV_FLAG: None, REMOTE_ENV: None}):
        assert retry_report(ctx, ATTR_TB) is None, "OFF must not introspect"
        assert retry_report(ctx, IMPORT_TB) is None

    baseline = _retry(report=None)
    assert "API REPORT" not in baseline
    # The seam's default argument must reproduce the old call exactly.
    old_call = render_retry_context(
        attempts=2, max_attempts=3,
        last_code="import json\npayload = json.load_all(open('f'))\n",
        last_stderr=ATTR_TB, last_stdout="", last_returncode=1, last_timed_out=False,
        last_fix_suggestion="Check the function name.",
        error_history=["AttributeError on json.load_all"],
    )
    assert old_call == baseline, "adding the parameter changed the default rendering"
    print("ok test_off_leaves_the_retry_context_byte_identical: flag default False, prompt unchanged")


# ── ON ⇒ the real installed API is injected ─────────────────────────────────


def test_on_injects_a_report_naming_the_real_symbols():
    ctx_field = PlanContext(main_task="t", api_introspect_retry=True)

    with _Env(**{ENV_FLAG: None, REMOTE_ENV: None}):
        report = retry_report(ctx_field, ATTR_TB)
    assert report is not None, "an AttributeError on a module must produce a report"
    # The facts that matter: the name is GONE, and the names that DO exist are listed.
    assert "ABSENT: json.load_all" in report, report
    assert "json.load_all does NOT exist in the INSTALLED version" in report, report
    for real in ("load", "loads", "dumps"):
        assert real in report, f"the real symbol {real!r} is missing from:\n{report}"
    # Real, not fabricated: the report is generated from the module object itself, so a name
    # that does not exist must not appear as a candidate.
    assert "load_all(" not in report, "a non-existent symbol was reported with a signature"

    # The env var is an equivalent switch.
    with _Env(**{ENV_FLAG: "1", REMOTE_ENV: None}):
        via_env = retry_report(PlanContext(main_task="t"), ATTR_TB)
    assert via_env is not None and "ABSENT: json.load_all" in via_env

    # The ImportError shape is parsed too.
    assert parse_api_error(ATTR_TB) == ("json", "load_all")
    assert parse_api_error(IMPORT_TB) == ("json", "load_all")
    with _Env(**{ENV_FLAG: "1", REMOTE_ENV: None}):
        assert "ABSENT: json.load_all" in (retry_report(PlanContext(main_task="t"), IMPORT_TB) or "")

    # And it actually lands in the engineer's retry block, under the stderr it explains.
    block = _retry(report=report)
    assert "API REPORT" in block and "ABSENT: json.load_all" in block
    assert block.index("**stderr:**") < block.index("API REPORT") < block.index(
        "**stdout:**"
    ), "the report must sit between the stderr it explains and the stdout"
    print("ok test_on_injects_a_report_naming_the_real_symbols: ABSENT + real candidates injected")


# ── unparseable ⇒ nothing injected ──────────────────────────────────────────


def test_unparseable_error_injects_nothing():
    ctx = PlanContext(main_task="t", api_introspect_retry=True)
    with _Env(**{ENV_FLAG: None, REMOTE_ENV: None}):
        for name, tb in (
            ("ZeroDivisionError", LOGIC_TB),
            # Only the BARE form is unparseable; the DOTTED one is the module shape (below).
            ("ModuleNotFoundError (bare, uninstalled package)", MISSING_MOD_TB),
            # Parses as the INSTANCE shape, but with no code the class cannot be proven.
            ("instance AttributeError, no code", INSTANCE_ATTR_TB),
            ("empty stderr", ""),
            ("None stderr", None),
        ):
            assert parse_api_error(tb) is None, f"{name} must not parse as a MODULE API error"
            assert retry_report(ctx, tb) is None, f"{name} must inject nothing"
    # …and the retry context is then exactly the un-augmented one.
    assert _retry(report=None) == _retry(report=None)
    print("ok test_unparseable_error_injects_nothing: no guess is made from a non-API traceback")


# ── dotted ModuleNotFoundError = a REMOVED submodule ────────────────────────
#
# A submodule removed wholesale raises ModuleNotFoundError, not AttributeError, so it needs its
# own shape — otherwise the hook sees nothing at all.


def test_dotted_module_not_found_is_the_module_shape():
    ctx = PlanContext(main_task="t", api_introspect_retry=True)
    # Anchored (stderr) and prefix-less (stdout) forms both read the parent and the leaf.
    assert classify_api_error(SUBMODULE_TB) == ("module", "json", "decoder_all")
    assert classify_api_error("No module named 'json.decoder_all'\n", allow_bare=True) == (
        "module", "json", "decoder_all")
    # The bare form is still NOT accepted on stderr — that path stays strict.
    assert classify_api_error("No module named 'json.decoder_all'\n") is None
    # CPython's trailing variants do not disturb the two facts before them.
    assert classify_api_error(SUBMODULE_TAIL_TB) == ("module", "Bio.SeqUtils", "CodonUsage")
    assert classify_api_error(
        "ModuleNotFoundError: No module named 'a.b'; 'a' has no attribute 'b'") == (
        "module", "a", "b")
    # An UNINSTALLED package has no dot, nothing to introspect, and a different fix (install
    # it) — preflight/escalation own that, so it must still parse as nothing.
    assert classify_api_error(MISSING_MOD_TB) is None
    assert classify_api_error(MISSING_MOD_TB, allow_bare=True) is None
    assert parse_api_error(MISSING_MOD_TB) is None

    with _Env(**{ENV_FLAG: None, REMOTE_ENV: None}):
        report = retry_report(ctx, SUBMODULE_TB)
        assert report is not None, "a removed SUBMODULE must produce a report"
        assert "ABSENT: json.decoder_all" in report, report
        assert "decoder" in report, f"the real submodule is missing from:\n{report}"
        # Same shape on stdout only (caught and printed), which exits 0 with empty stderr.
        via_stdout = retry_report(ctx, "", None, "oops: No module named 'json.decoder_all'\n")
        assert via_stdout is not None and "ABSENT: json.decoder_all" in via_stdout

        # The PARENT does not import either: `introspect_local` says only "could NOT be
        # imported", which is exactly what the traceback already said. Inject nothing.
        assert classify_api_error(SUBMODULE_NO_PARENT_TB) == (
            "module", "nosuchpkg_zz.sub", "leaf"), "the text still parses"
        assert retry_report(ctx, SUBMODULE_NO_PARENT_TB) is None, (
            "an unimportable parent yields no actionable fact — inject nothing")

    # The flag-OFF invariant holds for the new shape too.
    with _Env(**{ENV_FLAG: None, REMOTE_ENV: None}):
        assert retry_report(PlanContext(main_task="t", api_introspect_retry=False),
                            SUBMODULE_TB) is None, "OFF must be OFF"
    print("ok test_dotted_module_not_found_is_the_module_shape: dotted parses, bare does not")


# ── leading-WORD matching, so a semantic rename is actually surfaced ────────


def test_leading_word_matching_surfaces_a_semantic_rename():
    """difflib alone scores a renamed symbol against its replacement below the cutoff, so the
    report would claim no similar name exists with the replacement in the same module. Synthetic
    module, so no third-party package is needed."""
    names = ["CodonAdaptationIndex", "GC123", "GC_skew", "Seq", "gc_fraction",
             "molecular_weight", "nt_search", "seq1", "seq3", "translate"]
    mod = types.ModuleType("fake_sequtils_zz")
    mod.__version__ = "1.87"
    for n in names:
        setattr(mod, n, type(n, (), {}) if n[0].isupper() else (lambda *a, **k: None))
    sys.modules["fake_sequtils_zz"] = mod
    try:
        # The premise of the change: difflib alone does NOT find it.
        assert difflib.get_close_matches("CodonUsage", names, cutoff=0.5) == []
        report = introspect_local("fake_sequtils_zz", "CodonUsage")
        assert "ABSENT: fake_sequtils_zz.CodonUsage" in report, report
        assert "CLOSEST EXISTING NAMES" in report, report
        candidates = report.split("CLOSEST EXISTING NAMES")[1].split("ALL PUBLIC NAMES")[0]
        assert "CodonAdaptationIndex" in candidates, report

        # …and it must not have turned a normal miss into noise: json's candidate list is still
        # exactly difflib's two hits, with no leading-word additions.
        json_report = introspect_local("json", "load_all")
        block = json_report.split("CLOSEST EXISTING NAMES")[1].split("ALL PUBLIC NAMES")[0]
        listed = [l.strip().split("(")[0] for l in block.splitlines()
                  if l.startswith("  ") and not l.startswith("      ")]
        assert listed == ["load", "loads"], listed
    finally:
        sys.modules.pop("fake_sequtils_zz", None)
    print("ok test_leading_word_matching_surfaces_a_semantic_rename: CodonAdaptationIndex found, "
          "json unchanged")


# ── the PRE-execution entry point: class proven from the CALLABLE ───────────
#
# `api_preflight` reads `obj.method()` out of code that has NOT run, so there is no traceback
# naming the type — only the call that produced `obj`. Synthetic package, so the test does not
# depend on what is installed in this venv.


def _fake_pkg_zz():
    """A class, a factory annotated with its NAME (PEP 563), a factory with an ambiguous union
    annotation, and a class that synthesises attributes."""
    mod = types.ModuleType("fake_ad_zz")
    mod.__version__ = "0.13.2"

    class Widget:
        def merge(self): ...
        def write_h5ad(self, p): ...

    class Sheet:
        def __getattr__(self, k):   # like pandas.DataFrame's column access
            return 1

    def read_widget(path) -> "Widget": ...        # annotation is the STRING "Widget"
    def read_any(path) -> "Widget | Sheet": ...   # genuinely ambiguous — two classes
    def read_unannotated(path): ...
    def open_sheet(path) -> "Sheet": ...

    for obj in (Widget, Sheet, read_widget, read_any, read_unannotated, open_sheet):
        obj.__module__ = "fake_ad_zz"
        setattr(mod, obj.__name__, obj)
    sys.modules["fake_ad_zz"] = mod
    return mod


def test_introspect_callable_class_local_proves_the_class_or_says_nothing():
    _fake_pkg_zz()
    try:
        f = introspect_callable_class_local
        # A factory whose return annotation is a bare name: resolved against its own module.
        rep = f({"callable": "fake_ad_zz.read_widget", "attr": "concatenate",
                 "static_only": True})
        assert "ABSENT: Widget.concatenate" in rep, rep
        assert "read_widget() -> Widget" in rep, rep
        assert "merge" in rep, f"the real attributes are missing from:\n{rep}"
        assert "may not be a method at all" in rep, rep

        # A constructor IS its own answer.
        ctor = f({"callable": "fake_ad_zz.Widget", "attr": "concatenate", "static_only": True})
        assert "ABSENT: Widget.concatenate" in ctor, ctor
        assert ctor.startswith("CLASS fake_ad_zz.Widget"), ctor

        for name, spec in (
            # A union return annotation names two classes, so nothing is provable.
            ("union annotation", {"callable": "fake_ad_zz.read_any", "attr": "concatenate"}),
            ("no annotation", {"callable": "fake_ad_zz.read_unannotated", "attr": "x"}),
            ("unresolvable module", {"callable": "nosuchpkg_zz.read", "attr": "x"}),
            ("unresolvable attribute", {"callable": "fake_ad_zz.nope", "attr": "x"}),
            ("attribute EXISTS", {"callable": "fake_ad_zz.Widget", "attr": "merge"}),
            # __getattr__ means class-level absence proves NOTHING — the pre-execution caller
            # has no runtime evidence, so it must be refused there.
            ("__getattr__ class under static_only",
             {"callable": "fake_ad_zz.open_sheet", "attr": "anything", "static_only": True}),
            ("empty spec", {}),
            ("no attr", {"callable": "fake_ad_zz.Widget"}),
        ):
            assert f(spec) == "", f"{name} must say nothing"

        # …and that refusal is scoped to `static_only`: the retry-time path, which already has
        # an AttributeError proving the lookup failed, is unaffected.
        loose = f({"callable": "fake_ad_zz.open_sheet", "attr": "anything"})
        assert "ABSENT: Sheet.anything" in loose, loose
    finally:
        sys.modules.pop("fake_ad_zz", None)
    print("ok test_introspect_callable_class_local_proves_the_class_or_says_nothing")


# ── the class's OWN api vs what it inherited from a builtin ─────────────────
#
# For a `dict` subclass, a bare name dump mixes inherited `dict` methods with the class's own
# API, unmarked and without signatures. An invented method name is not reachable from the real
# one by any spelling rule, so a looser matcher cannot help; rendering the class's own api is
# what surfaces the answer.


def _fake_cai_pkg_zz():
    """A `dict` subclass with two real methods, plus a class with no builtin base."""
    mod = types.ModuleType("fake_cai_zz")
    mod.__version__ = "1.87"

    class CaiIndexZZ(dict):
        def calculate(self, sequence):
            """Calculate and return the CAI (float) for the provided DNA sequence."""

        def optimize(self, sequence, seq_type="DNA", strict=True):
            """Return a new DNA sequence with preferred codons only."""

    class PlainZZ:                       # no builtin base: the unchanged path
        def merge(self, other):
            """Merge another object into this one."""

        def to_frame(self):
            """Return the contents as a table."""

    for obj in (CaiIndexZZ, PlainZZ):
        obj.__module__ = "fake_cai_zz"
        setattr(mod, obj.__name__, obj)
    sys.modules["fake_cai_zz"] = mod
    return mod


def _class_report_zz(type_name, attr):
    from cmbagent_lg.self_debug.api_introspect import introspect_class_local
    return introspect_class_local({"type": type_name, "attr": attr,
                                   "modules": ["fake_cai_zz"],
                                   "froms": [["fake_cai_zz", type_name]]})


def test_own_api_is_rendered_in_full_when_nothing_matched():
    _fake_cai_pkg_zz()
    try:
        # The premise: no matcher bridges cai_for_gene -> calculate, and loosening one to try
        # would only buy false candidates.
        assert difflib.get_close_matches("cai_for_gene", ["calculate", "optimize"],
                                         cutoff=0.5) == []
        rep = _class_report_zz("CaiIndexZZ", "cai_for_gene")
        assert "ABSENT: CaiIndexZZ.cai_for_gene" in rep, rep
        assert "CLOSEST EXISTING ATTRIBUTES" not in rep, "nothing matched; do not claim a match"
        assert "THIS CLASS'S OWN PUBLIC API (2)" in rep, rep

        # The decisive fact a bare name dump withholds: signature AND summary line.
        own_block = rep.split("OWN PUBLIC API")[1].split("NOTE:")[0]
        assert "calculate(self, sequence)" in own_block, own_block
        assert "Calculate and return the CAI (float)" in own_block, own_block
        assert "optimize(self, sequence, seq_type='DNA', strict=True)" in own_block, own_block
        # …and the 11 inherited dict methods are NOT rendered as if they were this API.
        for noise in ("clear", "fromkeys", "setdefault", "popitem", "keys"):
            assert noise not in own_block, f"{noise!r} is a dict method, not this class's API"

        # The exhaustive list is KEPT, but partitioned rather than presented as 13 equals.
        assert "ALL PUBLIC ATTRIBUTES (13):" in rep, rep
        assert "defined by this class (2): calculate, optimize" in rep, rep
        assert "inherited from dict, NOT this library's API (11):" in rep, rep
        for inherited in ("clear", "copy", "fromkeys", "get", "items", "keys", "pop",
                          "popitem", "setdefault", "update", "values"):
            assert inherited in rep.split("ALL PUBLIC ATTRIBUTES")[1], inherited
        assert "may not be a method at all" in rep, "the module-function NOTE must survive"
    finally:
        sys.modules.pop("fake_cai_zz", None)
    print("ok test_own_api_is_rendered_in_full_when_nothing_matched: calculate() surfaced with "
          "its signature, dict methods marked inherited")


def test_a_matched_candidate_still_takes_the_measured_working_path():
    """When a candidate DOES match, the own-api fallback must stay out of the way."""
    _fake_cai_pkg_zz()
    try:
        rep = _class_report_zz("CaiIndexZZ", "calculte")     # a typo difflib DOES bridge
        assert "CLOSEST EXISTING ATTRIBUTES" in rep, rep
        assert "calculate(self, sequence)" in rep, rep
        assert "OWN PUBLIC API" not in rep, "the fallback must not fire when a candidate matched"
        assert "No similarly-named attribute found" not in rep, rep
    finally:
        sys.modules.pop("fake_cai_zz", None)
    print("ok test_a_matched_candidate_still_takes_the_measured_working_path")


def test_class_without_a_builtin_base_is_unchanged_in_shape():
    """No builtin base ⇒ nothing is inherited ⇒ the exhaustive list keeps its single-line form."""
    _fake_cai_pkg_zz()
    try:
        rep = _class_report_zz("PlainZZ", "concatenate")
        assert "ABSENT: PlainZZ.concatenate" in rep, rep
        assert "inherited from" not in rep, rep
        assert "defined by this class" not in rep, rep
        tail = rep.split("ALL PUBLIC ATTRIBUTES (2):")[1].strip()
        assert tail == "merge, to_frame", tail
        # The own-api fallback still gives the caller something actionable to read.
        assert "THIS CLASS'S OWN PUBLIC API (2)" in rep and "merge(self, other)" in rep, rep
        # Bounded: the block never runs past the 12-entry cap, whatever the class size.
        own_lines = [l for l in rep.split("OWN PUBLIC API")[1].split("NOTE:")[0].splitlines()
                     if l.startswith("  ")]
        assert len(own_lines) <= 12, len(own_lines)
    finally:
        sys.modules.pop("fake_cai_zz", None)
    print("ok test_class_without_a_builtin_base_is_unchanged_in_shape")


def test_the_own_api_block_is_capped_for_a_large_class():
    """A large class's own-api block must stay bounded and SAY it is truncated, since it lands
    in a retry prompt."""
    mod = types.ModuleType("fake_big_zz")
    mod.__version__ = "9.9"

    body = {f"m{i:02d}": (lambda self: None) for i in range(30)}
    Big = type("BigZZ", (), body)
    Big.__module__ = "fake_big_zz"
    mod.BigZZ = Big
    sys.modules["fake_big_zz"] = mod
    try:
        from cmbagent_lg.self_debug.api_introspect import introspect_class_local
        rep = introspect_class_local({"type": "BigZZ", "attr": "zzz_gone",
                                      "modules": ["fake_big_zz"], "froms": []})
        assert "THIS CLASS'S OWN PUBLIC API (30, first 12 shown)" in rep, rep
        own_lines = [l for l in rep.split("OWN PUBLIC API")[1].split("NOTE:")[0].splitlines()
                     if l.startswith("  ")]
        assert len(own_lines) == 12, len(own_lines)
        assert "ALL PUBLIC ATTRIBUTES (30):" in rep, "the exhaustive list is still there"
    finally:
        sys.modules.pop("fake_big_zz", None)
    print("ok test_the_own_api_block_is_capped_for_a_large_class")


def test_the_two_matching_blocks_have_not_drifted():
    """SNIPPET and CLASS_SNIPPET must each stay self-contained, so the candidate-matching block
    is deliberately duplicated. Nothing but this test stops the copies diverging."""
    def _block(text):
        lines = text.splitlines()
        start = next(i for i, l in enumerate(lines) if "difflib.get_close_matches(symbol" in l)
        end = next(i for i, l in enumerate(lines) if i > start and "close = close[:12]" in l)
        # Code only: the comments say the same thing but are wrapped to different indents.
        return [l.strip() for l in lines[start:end + 1]
                if l.strip() and not l.strip().startswith("#")]
    from cmbagent_lg.self_debug.api_introspect import SNIPPET
    assert _block(SNIPPET) == _block(CLASS_SNIPPET), "the duplicated matching blocks diverged"
    print(f"ok test_the_two_matching_blocks_have_not_drifted: {len(_block(SNIPPET))} lines identical")


def test_class_snippet_main_dispatches_on_the_spec_shape():
    """`python -c CLASS_SNIPPET <json>` is the CLI contract for answering from another
    interpreter; one entry point must serve both report kinds. Stdlib-only spec."""
    def _run(spec):
        return subprocess.run([sys.executable, "-c", CLASS_SNIPPET, json.dumps(spec)],
                              capture_output=True, text=True, timeout=60).stdout
    instance = _run({"type": "Counter", "attr": "sort_values",
                     "modules": ["collections"], "froms": []})
    assert "ABSENT: Counter.sort_values" in instance, instance
    callable_ = _run({"callable": "collections.Counter", "attr": "sort_values",
                      "static_only": True})
    assert "ABSENT: Counter.sort_values" in callable_, callable_
    assert "most_common" in callable_, callable_
    print("ok test_class_snippet_main_dispatches_on_the_spec_shape")


# ── instance-method removals, resolved from the script's OWN imports ────────


def test_instance_attribute_resolved_from_the_scripts_own_imports():
    """`'X' object has no attribute 'y'` names the TYPE, not the module. The failing script's
    imports are FACTS, so if exactly one imported module provides a class with that exact
    __name__, the resolution is proven rather than guessed."""
    ctx = PlanContext(main_task="t", api_introspect_retry=True)
    with _Env(**{ENV_FLAG: None, REMOTE_ENV: None}):
        # Guaranteed case: stdlib, so this assertion holds in every venv.
        report = retry_report(ctx, COUNTER_TB, COUNTER_CODE)
        assert report is not None, "an imported stdlib class must resolve"
        assert "CLASS collections.Counter" in report, report
        assert "ABSENT: Counter.sort_values" in report, report
        assert "most_common" in report, f"real attributes missing from:\n{report}"

        # Identity dedupe: the same class reached two ways is still ONE unambiguous answer.
        both = retry_report(ctx, COUNTER_TB,
                            "import collections\nfrom collections import Counter\n")
        assert both is not None and "ABSENT: Counter.sort_values" in both

        # A real removal, when the installed pandas is new enough to have dropped it.
        import pandas as pd
        if hasattr(pd.DataFrame, "append"):
            print(f"  (skipped pandas check — installed pandas {pd.__version__} still has "
                  f"DataFrame.append)")
        else:
            real = retry_report(ctx, INSTANCE_ATTR_TB, PANDAS_CODE)
            assert real is not None, "the pandas DataFrame.append case must produce a report"
            assert "CLASS pandas.DataFrame" in real, real
            assert "ABSENT: DataFrame.append" in real, real
            assert f"version='{pd.__version__}'" in real, real
            # It must not imply the answer is necessarily a method — pd.concat is not one.
            assert "may not be a method at all" in real, real
            print(f"  (pandas {pd.__version__}: DataFrame.append correctly reported ABSENT)")
    print("ok test_instance_attribute_resolved_from_the_scripts_own_imports")


def test_instance_attribute_stays_silent_when_it_cannot_be_proven():
    """Every case where resolution would require a GUESS must inject nothing."""
    ctx = PlanContext(main_task="t", api_introspect_retry=True)
    # Two DIFFERENT classes named Formatter → genuinely ambiguous.
    ambiguous_tb = INSTANCE_ATTR_TB.replace("DataFrame", "Formatter").replace("append", "fmt")
    cases = (
        ("ambiguous type name", ambiguous_tb, "import logging\nimport string\n"),
        ("type from no imported module", INSTANCE_ATTR_TB, "import json\nimport math\n"),
        ("builtin/NoneType", "AttributeError: 'NoneType' object has no attribute 'shape'\n",
         "import json\n"),
        ("attribute actually EXISTS on the class",
         COUNTER_TB.replace("sort_values", "most_common"), COUNTER_CODE),
        ("unparseable code", COUNTER_TB, "import collections\nthis is not python("),
        ("empty code", COUNTER_TB, ""),
    )
    with _Env(**{ENV_FLAG: None, REMOTE_ENV: None}):
        import logging, string
        assert logging.Formatter is not string.Formatter, "the ambiguity case must be real"
        for name, tb, code in cases:
            assert retry_report(ctx, tb, code) is None, f"{name} must inject nothing"
    print("ok test_instance_attribute_stays_silent_when_it_cannot_be_proven: "
          f"{len(cases)} unprovable cases all silent")


# ── the CALL shape: an invented keyword or wrong arity on a REAL callable ───
#
# Two call shapes must be told apart. A strict signature with no **kwargs rejects an invented
# keyword with a TypeError — provable, and reported here. A signature ending in **kwargs
# forwards the keyword several frames deeper, where it raises AttributeError, not TypeError;
# that is the artist_kwarg shape below, and it must never classify as "signature".
# Synthetic module, so the test does not depend on scanpy being installed.

SIG_UNEXPECTED_KWARG_TB = """Traceback (most recent call last):
  File "/w/codebase/step_1.py", line 2, in <module>
    fsz.strict_fn(1, 2, bogus=3)
TypeError: strict_fn() got an unexpected keyword argument 'bogus'
"""
SIG_BAD_ARITY_TB = """Traceback (most recent call last):
  File "/w/codebase/step_1.py", line 2, in <module>
    fsz.strict_fn(1, 2, 3, 4)
TypeError: strict_fn() takes 2 positional arguments but 4 were given
"""
SIG_KWARG_CODE = "import fake_sig_zz as fsz\nfsz.strict_fn(1, 2, bogus=3)\n"
SIG_ARITY_CODE = "import fake_sig_zz as fsz\nfsz.strict_fn(1, 2, 3, 4)\n"

# An AttributeError, not a TypeError: Python's call binding never raises "unexpected keyword
# argument" for a signature that has **kwargs, so this must never classify as the signature
# shape.
DPI_TB = """Traceback (most recent call last):
  File "/w/codebase/step_1.py", line 44, in run_pipeline
    sc.pl.pca(adata, color='cell_type', show=False, save='pca.png', dpi=150)
  File "/w/site-packages/matplotlib/artist.py", line 1271, in _update_props
    raise AttributeError(
AttributeError: PathCollection.set() got an unexpected keyword argument 'dpi'
"""
DPI_CODE = "import scanpy as sc\nsc.pl.pca(adata, dpi=150)\n"


def _fake_sig_zz():
    """A strict-signature function and a **kwargs-permissive one."""
    mod = types.ModuleType("fake_sig_zz")
    mod.__version__ = "1.0"

    def strict_fn(a, b, *, target=None, verbose=False):
        """A strict callable: no **kwargs, the STRICT_SIG shape."""
        return a + b

    def loose_fn(a, **kwargs):
        """A permissive callable: ends in **kwargs, the KWARGS_PASSTHROUGH shape."""
        return a

    for obj in (strict_fn, loose_fn):
        obj.__module__ = "fake_sig_zz"
        setattr(mod, obj.__name__, obj)
    sys.modules["fake_sig_zz"] = mod
    return mod


def test_dotted_typeerror_is_the_signature_shape():
    assert classify_api_error(SIG_UNEXPECTED_KWARG_TB) == ("signature", "strict_fn", "bogus")
    assert classify_api_error(SIG_BAD_ARITY_TB) == ("signature", "strict_fn", "")
    # The boundary: an AttributeError carrying the same MESSAGE SHAPE must parse as the
    # artist_kwarg kind, never as "signature" — that stays scoped to a real "TypeError: "
    # prefix, which Python raises only when the signature has no **kwargs.
    assert classify_api_error(DPI_TB) == ("artist_kwarg", "PathCollection", "dpi")
    assert classify_api_error(DPI_TB)[0] != "signature", "must never be misclassified as STRICT_SIG"
    # The BARE (stdout, prefix-less) form is accepted for NEITHER shape: `str(e)` carries no
    # exception class name, so the message alone cannot tell the provable TypeError case from
    # the AttributeError one.
    assert classify_api_error(
        "strict_fn() got an unexpected keyword argument 'bogus'\n", allow_bare=True) is None
    assert classify_api_error(
        "strict_fn() takes 2 positional arguments but 4 were given\n", allow_bare=True) is None
    assert classify_api_error("PathCollection.set() got an unexpected keyword argument 'dpi'\n",
                              allow_bare=True) is None, "ARTIST_SET bare form is out of scope"
    # allow_bare only ADDS the bare patterns; DPI_TB's real "AttributeError: " prefix still
    # matches the anchored artist_kwarg pattern regardless.
    assert classify_api_error(DPI_TB, allow_bare=True) == ("artist_kwarg", "PathCollection", "dpi")
    print("ok test_dotted_typeerror_is_the_signature_shape")


def test_signature_mismatch_report_names_the_real_parameters():
    ctx = PlanContext(main_task="t", api_introspect_retry=True)
    _fake_sig_zz()
    try:
        with _Env(**{ENV_FLAG: None, REMOTE_ENV: None}):
            report = retry_report(ctx, SIG_UNEXPECTED_KWARG_TB, SIG_KWARG_CODE)
            assert report is not None, "an invented keyword on a strict signature must report"
            assert "INVALID: 'bogus' is not a parameter" in report, report
            for real in ("a", "b", "target", "verbose"):
                assert real in report, f"the real parameter {real!r} is missing from:\n{report}"

            arity_report = retry_report(ctx, SIG_BAD_ARITY_TB, SIG_ARITY_CODE)
            assert arity_report is not None, "a wrong arity call must report"
            assert "INVALID:" in arity_report, arity_report

            # Without the code, the dotted callable cannot be resolved from a bare function
            # name — same "no code, no guess" rule as the instance shape.
            assert retry_report(ctx, SIG_UNEXPECTED_KWARG_TB, None) is None

            # The boundary case end to end: the **kwargs shape must not be reported via the
            # signature path. It IS reported via the artist_kwarg path (asserted below).
            sig_report_only = retry_report(ctx, DPI_TB, DPI_CODE)
            assert sig_report_only is not None, (
                "the artist_kwarg path now reports this case (see below) — it must not "
                "silently vanish")
            assert "INVALID: 'dpi' is not a parameter" not in sig_report_only, (
                "must never look like the STRICT_SIG report")

            # OFF stays OFF for the new shape too.
            assert retry_report(PlanContext(main_task="t", api_introspect_retry=False),
                                SIG_UNEXPECTED_KWARG_TB, SIG_KWARG_CODE) is None
    finally:
        sys.modules.pop("fake_sig_zz", None)
    print("ok test_signature_mismatch_report_names_the_real_parameters")


# ── the ARTIST_SET shape (a distinct code path) ─────────────────────────────
#
# `DPI_TB` is real matplotlib: the exact `AttributeError` `Artist._update_props` raises for an
# invalid `.set()` kwarg. Requires matplotlib, an ordinary installed dependency here.


def test_artist_kwarg_report_names_the_real_properties():
    ctx = PlanContext(main_task="t", api_introspect_retry=True)
    with _Env(**{ENV_FLAG: None, REMOTE_ENV: None}):
        # Direct: the snippet entry point, proven from the class the traceback names.
        rep = introspect_artist_kwarg_local({"cls_name": "PathCollection", "kwarg": "dpi"})
        assert "ABSENT: PathCollection.set_dpi() does not exist" in rep, rep
        assert "VALID SETTABLE PROPERTIES on PathCollection" in rep, rep
        for real in ("alpha", "linewidth", "edgecolor", "facecolor"):
            assert real in rep, f"a real PathCollection property {real!r} is missing from:\n{rep}"
        # The provenance line names the actual matplotlib mechanism, not a made-up rule.
        assert "Artist._update_props" in rep, rep
        # 'dpi' IS real, just on the SAVE path — proven from FigureCanvasBase.print_figure's own
        # signature (Figure.savefig also ends in **kwargs and does not name it).
        assert "savefig" in rep.lower() and "print_figure" in rep, rep

        # End to end: no code needed, unlike the instance/signature shapes — the class comes
        # from matplotlib's own closed set, not the failing script's imports.
        full = retry_report(ctx, DPI_TB, code=None)
        assert full is not None, "artist_kwarg must not require the failing script's code"
        assert "ABSENT: PathCollection.set_dpi" in full, full
        assert "API REPORT" in full, full
        # Distinct steer from the module/instance/signature shapes: must NOT claim the top-level
        # name (sc.pl.pca) is wrong — only that the specific kwarg is invalid deeper in.
        assert "top-level function name is wrong" not in full or "not evidence" in full, full
        assert "Do not resubmit the same call" in full, full

        same = retry_report(ctx, DPI_TB, DPI_CODE)
        assert "ABSENT: PathCollection.set_dpi" in same, same

        # OFF stays OFF for this shape too.
        assert retry_report(PlanContext(main_task="t", api_introspect_retry=False),
                            DPI_TB, DPI_CODE) is None
    print("ok test_artist_kwarg_report_names_the_real_properties")


def test_artist_kwarg_stays_silent_when_it_cannot_be_proven():
    with _Env(**{ENV_FLAG: None, REMOTE_ENV: None}):
        # A VALID PathCollection property must never be reported as absent.
        assert introspect_artist_kwarg_local(
            {"cls_name": "PathCollection", "kwarg": "alpha"}) == "", (
            "a real Artist property must never be flagged")
        # A class name that resolves to nothing in matplotlib's own Artist tree: silent.
        assert introspect_artist_kwarg_local(
            {"cls_name": "TotallyBogusArtistClassZZ", "kwarg": "dpi"}) == ""
        # Empty spec / missing fields: silent, never raises.
        assert introspect_artist_kwarg_local({}) == ""
        assert introspect_artist_kwarg_local({"cls_name": "PathCollection"}) == ""
        assert introspect_artist_kwarg_local({"kwarg": "dpi"}) == ""

        # Real ambiguity: matplotlib.axis.Axis vs mpl_toolkits.mplot3d.axis3d.Axis. ARTIST_SNIPPET
        # never imports mpl_toolkits itself, so this only fires when something else already
        # loaded it — which is the scenario being guarded against.
        import mpl_toolkits.mplot3d.axis3d  # noqa: F401 — imported for its side effect on Artist.__subclasses__
        assert introspect_artist_kwarg_local(
            {"cls_name": "Axis", "kwarg": "whatever_zz"}) == "", (
            "an ambiguous class name must be refused, not guessed")

        # retry_report end to end: unparseable / non-artist errors inject nothing via this path.
        ctx = PlanContext(main_task="t", api_introspect_retry=True)
        assert retry_report(ctx, LOGIC_TB) is None
        assert retry_report(ctx, "") is None
        assert retry_report(ctx, None) is None
    print("ok test_artist_kwarg_stays_silent_when_it_cannot_be_proven")


def test_introspect_call_signature_local_directly():
    """The introspector's own contract: prove-or-silent in BOTH modes."""
    _fake_sig_zz()
    try:
        # Retry-time mode (`already_failed`): render the signature once a crash already proved
        # something, but refuse if the RESOLVED signature disagrees with the crash.
        rep = introspect_call_signature_local(
            {"callable": "fake_sig_zz.strict_fn", "already_failed": True,
             "invalid_kwarg": "bogus"})
        assert "INVALID: 'bogus'" in rep, rep
        assert "VALID PARAMETER NAMES" in rep and "target" in rep, rep

        # A **kwargs signature stays silent even in already-failed mode — the resolved
        # callable could not be the one that actually raised "unexpected keyword argument".
        assert introspect_call_signature_local(
            {"callable": "fake_sig_zz.loose_fn", "already_failed": True,
             "invalid_kwarg": "whatever"}) == ""

        # A claimed-invalid kwarg that IS a real parameter disagrees with the crash — silent.
        assert introspect_call_signature_local(
            {"callable": "fake_sig_zz.strict_fn", "already_failed": True,
             "invalid_kwarg": "target"}) == ""

        # Pre-execution mode (no `already_failed`): PROVE from kwargs/n_positional directly.
        proactive = introspect_call_signature_local(
            {"callable": "fake_sig_zz.strict_fn", "kwargs": ["bogus"]})
        assert "INVALID: keyword(s) not in the INSTALLED signature: bogus" in proactive, proactive

        # The KWARGS_PASSTHROUGH boundary in pre-execution mode too.
        assert introspect_call_signature_local(
            {"callable": "fake_sig_zz.loose_fn", "kwargs": ["whatever"]}) == ""

        # Valid usage: silent.
        assert introspect_call_signature_local(
            {"callable": "fake_sig_zz.strict_fn", "kwargs": ["target", "verbose"],
             "n_positional": 2}) == ""

        # Unresolvable / empty spec: silent, never raises.
        assert introspect_call_signature_local({}) == ""
        assert introspect_call_signature_local({"callable": "nosuchpkg_zz.fn"}) == ""
    finally:
        sys.modules.pop("fake_sig_zz", None)
    print("ok test_introspect_call_signature_local_directly")


def test_signature_snippet_dispatches_via_subprocess():
    """Same CLI contract as CLASS_SNIPPET, so it can be answered from another interpreter.
    `math.sqrt` is stdlib and version-stable."""
    def _run(spec):
        return subprocess.run([sys.executable, "-c", SIGNATURE_SNIPPET, json.dumps(spec)],
                              capture_output=True, text=True, timeout=60).stdout
    bad = _run({"callable": "math.sqrt", "kwargs": ["bogus"]})
    assert "INVALID: keyword(s) not in the INSTALLED signature: bogus" in bad, bad
    ok = _run({"callable": "math.sqrt", "n_positional": 1})
    assert ok.strip() == "", f"valid usage must be silent, got: {ok!r}"
    print("ok test_signature_snippet_dispatches_via_subprocess")


# ── caught-and-printed faults: exit 0, empty stderr, error on STDOUT ────────
#
# The engineer wraps the script in try/except and prints the error, so the process exits 0 with
# an EMPTY stderr and the step comes back as a goal-miss rather than a crash. Unmitigated, the
# run hand-rolls the missing function and can finish "success" with wrong values.

CAUGHT_STDOUT = ("Loading configuration...\n"
                 "An error occurred: module 'scipy.special' has no attribute 'sph_harm'\n"
                 "Falling back to a manual implementation.\n")


def test_caught_and_printed_error_on_stdout_is_still_seen():
    ctx = PlanContext(main_task="t", api_introspect_retry=True)
    with _Env(**{ENV_FLAG: None, REMOTE_ENV: None}):
        # The printed form has NO exception-name prefix, so the strict stderr patterns miss it…
        assert classify_api_error(CAUGHT_STDOUT) is None
        # …and the bare form, accepted only for stdout, reads the same two facts off it.
        assert classify_api_error(CAUGHT_STDOUT, allow_bare=True) == (
            "module", "scipy.special", "sph_harm")

        # stderr empty + the error on stdout ⇒ still reported.
        report = retry_report(ctx, "", None, CAUGHT_STDOUT)
        assert report is not None, "a caught-and-printed API fault must still be seen"
        assert "ABSENT: scipy.special.sph_harm" in report, report

        # stdout is consulted only as a FALLBACK: a real traceback still wins.
        both = retry_report(ctx, ATTR_TB, None, CAUGHT_STDOUT)
        assert "ABSENT: json.load_all" in both, "stderr must take precedence over stdout"

        # The bare form is NOT accepted on stderr — that path stays strict.
        assert retry_report(ctx, CAUGHT_STDOUT, None, None) is None
    print("ok test_caught_and_printed_error_on_stdout_is_still_seen")


def test_success_path_retry_also_gets_the_report():
    """A caught-and-printed run exits 0, so it reaches the engineer as a GOAL-MISS retry — a
    separate branch, which must consult the API hook too."""
    from cmbagent_lg.self_debug.schemas import StepVerdict

    captured = {}

    class _FakeLLM:
        def invoke(self, msgs, config=None):
            captured["system"] = msgs[0].content
            return types.SimpleNamespace(text="```python\nprint(1)\n```")

    def _run(ctx):
        state = {
            "step": Step(sub_task="compute the harmonics", sub_task_agent="engineer",
                         bullet_points=["use scipy"]),
            "attempts": 1,
            "current_code": EngineerResponse(python_code="import scipy.special\n",
                                             code_explanation="x"),
            # Ran CLEANLY (exit 0) — the API error was caught and printed.
            "current_execution_verdict": ExecutionVerdict(status="success"),
            "current_step_verdict": StepVerdict(fulfilled=False,
                                                unmet_requirements=["values look wrong"],
                                                feedback="the numbers are sign-flipped"),
            "execution_stderr": "",
            "execution_stdout": CAUGHT_STDOUT,
            "execution_returncode": 0,
            "execution_timed_out": False,
            "error_history": [],
        }
        saved = sd_nodes._proposer
        sd_nodes._proposer = lambda _c: _FakeLLM()
        try:
            sd_nodes.engineer(state, types.SimpleNamespace(context=ctx))
        finally:
            sd_nodes._proposer = saved
        return captured["system"]

    with _Env(**{ENV_FLAG: None, REMOTE_ENV: None}):
        on = _run(PlanContext(main_task="t", api_introspect_retry=True))
        off = _run(PlanContext(main_task="t", api_introspect_retry=False))
    assert "ABSENT: scipy.special.sph_harm" in on, (
        "a goal-miss retry after a caught-and-printed API fault must still get the facts")
    assert "API REPORT" not in off, "OFF must leave the success-path retry untouched"
    print("ok test_success_path_retry_also_gets_the_report")


# ── remote execution ⇒ refuse, do not answer from the wrong venv ────────────


def test_remote_executor_refuses_instead_of_answering_from_the_engine_venv():
    """R-17a: under a remote executor the report now comes from the guest sandbox (proven in
    tests/test_api_remote_introspect.py). The invariant kept here is the original one: the
    engine's venv must never answer for code that ran elsewhere, so a guest that cannot be
    reached — or no session to reach it about — injects nothing."""
    from cmbagent_lg.self_debug.remote_exec import RemoteExecutor

    ctx = PlanContext(main_task="t", api_introspect_retry=True)
    saved = RemoteExecutor._request

    def _down(*a, **k):
        raise ConnectionRefusedError("guest down")

    RemoteExecutor._request = _down
    try:
        with _Env(**{ENV_FLAG: "1", REMOTE_ENV: "10.0.0.5:8790"}):
            assert retry_report(ctx, ATTR_TB) is None, "no session — nothing to ask"
            assert retry_report(ctx, ATTR_TB, work_dir="/tmp/apiir-remote/sess") is None, (
                "with a remote executor the engine's venv is NOT the environment the code ran "
                "in; reporting it would be authoritative and wrong"
            )
    finally:
        RemoteExecutor._request = saved
    with _Env(**{ENV_FLAG: "1", REMOTE_ENV: None}):
        assert retry_report(ctx, ATTR_TB) is not None, "the local path must still work"
    print("ok test_remote_executor_refuses_instead_of_answering_from_the_engine_venv")


# ── module_shape_report appends the class's own API for a class candidate ───
#
# A constructor-only nudge leaves the engineer guessing the method name, so when the top
# candidate IS a class its own method inventory is appended too. Fake modules throughout.


def _fake_class_replacement_mod():
    """A `dict`-subclass "replacement": a real constructor plus two own methods, so the closest
    existing name is provably a class with a non-trivial API."""
    class Calculatorr(dict):
        def __init__(self, sequences, table=None):
            super().__init__()

        def calculate(self, sequence):
            """Compute the metric for one sequence."""

        def optimize(self, sequence):
            """Return an optimized sequence."""

    Calculatorr.__module__ = "fake_classmod_zz"
    mod = types.ModuleType("fake_classmod_zz")
    mod.__version__ = "1.0"
    mod.Calculatorr = Calculatorr
    sys.modules["fake_classmod_zz"] = mod
    return mod


def test_module_shape_report_appends_class_api_for_a_class_candidate():
    _fake_class_replacement_mod()
    try:
        # "Calculator" (missing) vs "Calculatorr" (installed) — a plain one-character typo,
        # well within difflib's own cutoff, so this does not lean on the leading-word matcher.
        with _Env(**{REMOTE_ENV: None}):
            report = module_shape_report("fake_classmod_zz", "Calculator")
        assert report is not None
        assert "CLOSEST EXISTING NAMES" in report, report
        assert "Calculatorr" in report.split("CLOSEST EXISTING NAMES")[1], report
        # The class-API expansion: constructor AND method inventory, not just the constructor.
        assert "CLASS fake_classmod_zz.Calculatorr" in report, report
        assert "CONSTRUCTOR: Calculatorr(sequences, table=None)" in report, report
        assert "calculate(self, sequence)" in report, report
        assert "optimize(self, sequence)" in report, report
        # dict's own methods are not presented as this library's API.
        assert "inherited from dict, NOT this library's API" in report, report
    finally:
        sys.modules.pop("fake_classmod_zz", None)
    print("ok test_module_shape_report_appends_class_api_for_a_class_candidate")


def _fake_function_replacement_mod():
    """A plain FUNCTION replacement — the negative case: must not get a class-shaped block."""
    def new_calc(sequence):
        """Compute the metric."""

    new_calc.__module__ = "fake_funcmod_zz"
    mod = types.ModuleType("fake_funcmod_zz")
    mod.__version__ = "1.0"
    mod.new_calc = new_calc
    sys.modules["fake_funcmod_zz"] = mod
    return mod


def test_module_shape_report_skips_class_expansion_for_a_function_candidate():
    _fake_function_replacement_mod()
    try:
        with _Env(**{REMOTE_ENV: None}):
            report = module_shape_report("fake_funcmod_zz", "new_calx")
        assert report is not None
        assert "CLOSEST EXISTING NAMES" in report, report
        assert "new_calc" in report.split("CLOSEST EXISTING NAMES")[1], report
        # No class-shaped block was forced onto a non-class candidate.
        assert "CLASS fake_funcmod_zz" not in report, report
        assert "CONSTRUCTOR:" not in report, report
    finally:
        sys.modules.pop("fake_funcmod_zz", None)
    print("ok test_module_shape_report_skips_class_expansion_for_a_function_candidate")


def test_module_shape_report_unchanged_when_there_is_no_candidate():
    """Nothing matched ⇒ no candidate section, so there is nothing to expand."""
    mod = types.ModuleType("fake_nomatch_zz")
    mod.__version__ = "1.0"
    sys.modules["fake_nomatch_zz"] = mod
    try:
        with _Env(**{REMOTE_ENV: None}):
            report = module_shape_report("fake_nomatch_zz", "totallyabsent")
        assert report is not None
        assert "CLOSEST EXISTING NAMES" not in report, report
        assert "CLASS fake_nomatch_zz" not in report, report
    finally:
        sys.modules.pop("fake_nomatch_zz", None)
    print("ok test_module_shape_report_unchanged_when_there_is_no_candidate")


def test_introspect_full_class_local_direct():
    """Resolves a class and skips gracefully on anything that is not one — the same "prove or
    say nothing" contract as its siblings."""
    _fake_class_replacement_mod()
    try:
        rep = introspect_full_class_local({"callable": "fake_classmod_zz.Calculatorr"})
        assert rep.startswith("CLASS fake_classmod_zz.Calculatorr"), rep
        assert "calculate(self, sequence)" in rep, rep

        for name, spec in (
            ("empty spec", {}),
            ("unresolvable module", {"callable": "nosuchpkg_zz.Thing"}),
            ("unresolvable attribute", {"callable": "fake_classmod_zz.Nope"}),
        ):
            assert introspect_full_class_local(spec) == "", f"{name} must say nothing"
        # A FUNCTION (not a class) must also say nothing — this entry point never renders a
        # class-shaped report for something that is not a class.
        _fake_function_replacement_mod()
        try:
            assert introspect_full_class_local(
                {"callable": "fake_funcmod_zz.new_calc"}) == ""
        finally:
            sys.modules.pop("fake_funcmod_zz", None)
    finally:
        sys.modules.pop("fake_classmod_zz", None)
    print("ok test_introspect_full_class_local_direct")


# ── the seam: the real engineer node ────────────────────────────────────────


def _run_engineer(ctx, stderr=ATTR_TB, code="import json\njson.load_all(f)\n"):
    """Drive the REAL `nodes.engineer` on a code-failure retry; return its system prompt."""
    captured = {}

    class _FakeLLM:
        def invoke(self, msgs, config=None):
            captured["system"] = msgs[0].content
            return types.SimpleNamespace(text="```python\nprint(1)\n```")

    state = {
        "step": Step(sub_task="parse the payload", sub_task_agent="engineer",
                     bullet_points=["load the json"]),
        "attempts": 1,  # the node bumps this to 2 → retry mode
        "current_code": EngineerResponse(python_code=code, code_explanation="x"),
        "current_execution_verdict": ExecutionVerdict(status="failure",
                                                      failure_kind="renamed_api",
                                                      error_summary="AttributeError",
                                                      fix_suggestion="check the name"),
        "execution_stderr": stderr,
        "execution_stdout": "",
        "execution_returncode": 1,
        "execution_timed_out": False,
        "error_history": ["AttributeError on json.load_all"],
    }
    saved = sd_nodes._proposer
    sd_nodes._proposer = lambda _c: _FakeLLM()
    try:
        sd_nodes.engineer(state, types.SimpleNamespace(context=ctx))
    finally:
        sd_nodes._proposer = saved
    return captured["system"]


def test_engineer_node_seam_on_and_off():
    with _Env(**{ENV_FLAG: None, REMOTE_ENV: None}):
        off = _run_engineer(PlanContext(main_task="t", api_introspect_retry=False))
        on = _run_engineer(PlanContext(main_task="t", api_introspect_retry=True))
    assert "API REPORT" not in off, "OFF must not touch the engineer's prompt"
    assert "ABSENT: json.load_all" in on, "ON must inject the report into the engineer's prompt"
    # Everything else about the prompt is unchanged: removing the injected block from the ON
    # prompt must give back the OFF prompt exactly.
    start = on.index("**API REPORT")
    end = on.index("**stdout:**", start)
    assert on[:start] + on[end:] == off, "the injection perturbed the rest of the prompt"
    print("ok test_engineer_node_seam_on_and_off: report is the ONLY difference in the prompt")


def test_engineer_node_passes_the_failing_code_for_the_instance_shape():
    """The instance shape needs the CODE as well as the stderr — the class is resolvable ONLY
    from the script's imports."""
    with _Env(**{ENV_FLAG: None, REMOTE_ENV: None}):
        on = _run_engineer(PlanContext(main_task="t", api_introspect_retry=True),
                           stderr=COUNTER_TB, code=COUNTER_CODE)
        off = _run_engineer(PlanContext(main_task="t", api_introspect_retry=False),
                            stderr=COUNTER_TB, code=COUNTER_CODE)
    assert "ABSENT: Counter.sort_values" in on, (
        "the node did not pass the failing code through to the introspection")
    assert "API REPORT" not in off
    print("ok test_engineer_node_passes_the_failing_code_for_the_instance_shape")


if __name__ == "__main__":
    test_off_leaves_the_retry_context_byte_identical()
    test_on_injects_a_report_naming_the_real_symbols()
    test_unparseable_error_injects_nothing()
    test_dotted_module_not_found_is_the_module_shape()
    test_leading_word_matching_surfaces_a_semantic_rename()
    test_introspect_callable_class_local_proves_the_class_or_says_nothing()
    test_own_api_is_rendered_in_full_when_nothing_matched()
    test_a_matched_candidate_still_takes_the_measured_working_path()
    test_class_without_a_builtin_base_is_unchanged_in_shape()
    test_the_own_api_block_is_capped_for_a_large_class()
    test_the_two_matching_blocks_have_not_drifted()
    test_class_snippet_main_dispatches_on_the_spec_shape()
    test_dotted_typeerror_is_the_signature_shape()
    test_signature_mismatch_report_names_the_real_parameters()
    test_artist_kwarg_report_names_the_real_properties()
    test_artist_kwarg_stays_silent_when_it_cannot_be_proven()
    test_introspect_call_signature_local_directly()
    test_signature_snippet_dispatches_via_subprocess()
    test_caught_and_printed_error_on_stdout_is_still_seen()
    test_success_path_retry_also_gets_the_report()
    test_instance_attribute_resolved_from_the_scripts_own_imports()
    test_instance_attribute_stays_silent_when_it_cannot_be_proven()
    test_remote_executor_refuses_instead_of_answering_from_the_engine_venv()
    test_module_shape_report_appends_class_api_for_a_class_candidate()
    test_module_shape_report_skips_class_expansion_for_a_function_candidate()
    test_module_shape_report_unchanged_when_there_is_no_candidate()
    test_introspect_full_class_local_direct()
    test_engineer_node_seam_on_and_off()
    test_engineer_node_passes_the_failing_code_for_the_instance_shape()
    print("ALL test_api_introspect_retry PASSED")
