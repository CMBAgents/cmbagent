"""Gate test for R-17a: under `CMBAGENT_LG_REMOTE_EXECUTOR`, both API-bug flags answer from the
GUEST SANDBOX instead of going silent.

Before this, `api_preflight._enabled` and `api_introspect.retry_report` refused outright when a
remote executor was configured — correct, because the engine venv is not the package set the code
ran against, but it also meant the flags were inert on exactly the deployment that ships them.

The environments here are made to DISAGREE on purpose: an `ENGINE_MARK`-only copy of `divergent`
is importable in this process, a `GUEST_MARK`-only copy (with an extra `newapi`) only in the
subprocess the fake guest runs the snippet in. Every assertion below therefore says WHICH
environment answered, not merely that something answered.
"""

import os
import subprocess
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Step
from cmbagent_lg.self_debug import api_introspect as _ai
from cmbagent_lg.self_debug import nodes as sd_nodes
from cmbagent_lg.self_debug.api_preflight import (
    _MAX_REMOTE_MODULE_PROBES,
    _MAX_REMOTE_SIGNATURE_PROBES,
    absent_symbols,
    api_preflight,
)
from cmbagent_lg.self_debug.remote_exec import RemoteExecutor
from cmbagent_lg.self_debug.schemas import EngineerResponse, ExecutionVerdict

REMOTE_ENV = "CMBAGENT_LG_REMOTE_EXECUTOR"
PREFLIGHT_FLAG = "CMBAGENT_LG_API_PREFLIGHT"
RETRY_FLAG = "CMBAGENT_LG_API_INTROSPECT_RETRY"

ENGINE_SRC = ("ENGINE_MARK = 1\n\n\ndef helper():\n    pass\n\n\ndef oldapi(x):\n    pass\n")
GUEST_SRC = "GUEST_MARK = 1\n\n\ndef helper():\n    pass\n\n\ndef newapi():\n    pass\n"

_TMP = tempfile.mkdtemp(prefix="r17a-")
ENGINE_DIR = Path(_TMP) / "engine"
GUEST_DIR = Path(_TMP) / "guest"
NEUTRAL = Path(_TMP) / "cwd"
WORK_DIR = Path(_TMP) / "runs" / "r17a-session"
for d in (ENGINE_DIR, GUEST_DIR, NEUTRAL, WORK_DIR):
    d.mkdir(parents=True, exist_ok=True)
(ENGINE_DIR / "divergent.py").write_text(ENGINE_SRC)
(GUEST_DIR / "divergent.py").write_text(GUEST_SRC)
# Installed in the sandbox and NOWHERE on the engine — the shape of every scientific package on
# the deployed backend image, which carries none of them.
# The LIVE shape (seen on a remote run): a package the ENGINE does not have at all, whose
# submodule is importable in the sandbox, with the symbol removed and a near-namesake in its place —
# `from scipy.special import sph_harm` against scipy 1.17.1, which offers `sph_harm_y`.
(GUEST_DIR / "sandboxpkg").mkdir(exist_ok=True)
(GUEST_DIR / "sandboxpkg" / "__init__.py").write_text("__version__ = '1.17.1'\n")
(GUEST_DIR / "sandboxpkg" / "sub.py").write_text(
    "def deadname_y(n, m, theta, phi):\n    return 0.0\n\n\ndef lpmv(m, v, x):\n    return 0.0\n")
(GUEST_DIR / "guestonly.py").write_text(
    "def parse(text):\n    return text\n\n\nclass Loader:\n    def load(self):\n        return 1\n"
)
sys.path.insert(0, str(ENGINE_DIR))          # this process sees ONLY the engine copy

GONE_CODE = "import divergent\n\nprint(divergent.gone())\n"
NEWAPI_CODE = "import divergent\n\nprint(divergent.newapi())\n"
GUESTONLY_CODE = "import guestonly\n\nprint(guestonly.parse('x', mode=1))\n"
OLDAPI_CODE = "import divergent\n\nprint(divergent.oldapi(1))\n"
DEADNAME_CODE = ("from sandboxpkg.sub import deadname\n\nprint(deadname(0, 0, 0.0, 0.0))\n")
SUBMODULE_CODE = "from sandboxpkg import sub\n\nprint(sub.lpmv(0, 0, 0.0))\n"
DOTTED_CODE = ("import sandboxpkg.sub as s\n\nprint(s.deadname(0, 0, 0.0, 0.0))\n")
# VALID calls: nothing is a finding, so `_MAX_REPORTED` never breaks the loop early — the worst
# case for fan-out, and the ordinary one for correct code.
FANOUT_CODE = "import guestonly\n\n" + "".join(
    f"guestonly.parse('x{i}')\n" for i in range(20)
)
LOADER_CODE = "import guestonly\n\nldr = guestonly.Loader()\nprint(ldr.fit())\n"
GONE_TB = """Traceback (most recent call last):
  File "/w/codebase/step_1.py", line 3, in <module>
    print(divergent.gone())
AttributeError: module 'divergent' has no attribute 'gone'
"""
LOADER_TB = """Traceback (most recent call last):
  File "/w/codebase/step_1.py", line 4, in <module>
    print(ldr.fit())
AttributeError: 'Loader' object has no attribute 'fit'
"""


class _Env:
    """Set/restore env vars around a block (the flags are process-global)."""

    def __init__(self, **kv):
        self.kv, self.saved = kv, {}

    def __enter__(self):
        for k, v in self.kv.items():
            self.saved[k] = os.environ.get(k)
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


class _Guest:
    """Stands in for `RemoteExecutor._request` — the whole engine-side transport under test.

    An `introspect` message is served by running the shipped snippet in a subprocess whose
    PYTHONPATH is the GUEST copy of `divergent`, so its answer cannot have come from here.
    """

    def __init__(self, raises=None, returncode=None, reply_type="introspect_result"):
        self.raises, self.returncode, self.seen = raises, returncode, []
        self.reply_type = reply_type

    def __enter__(self):
        self.saved = RemoteExecutor._request
        guest = self

        def _request(self_, msg, timeout=60):
            guest.seen.append(msg)
            if guest.raises is not None:
                raise guest.raises
            if msg.get("type") != "introspect":
                return {"type": "error", "message": "unexpected"}
            proc = subprocess.run(
                [sys.executable, "-c", msg["code"], *msg["argv"]],
                capture_output=True, text=True, cwd=str(NEUTRAL), timeout=60,
                env={**os.environ, "PYTHONPATH": str(GUEST_DIR)},
            )
            return {"type": guest.reply_type, "stdout": proc.stdout, "stderr": proc.stderr,
                    "returncode": guest.returncode if guest.returncode is not None
                    else proc.returncode}

        RemoteExecutor._request = _request
        return self

    def __exit__(self, *exc):
        RemoteExecutor._request = self.saved


class _NoLocal:
    """Makes any local introspection a hard failure, so 'the remote path never falls back' is
    proven rather than assumed."""

    NAMES = ("introspect_local", "introspect_class_local", "introspect_callable_class_local",
             "introspect_call_signature_local", "introspect_artist_kwarg_local")

    def __enter__(self):
        self.saved = {n: getattr(_ai, n) for n in self.NAMES}

        def _boom(*a, **k):
            raise AssertionError("the remote path called a LOCAL introspector")

        for n in self.NAMES:
            setattr(_ai, n, _boom)
        return self

    def __exit__(self, *exc):
        for n, fn in self.saved.items():
            setattr(_ai, n, fn)


def _rt(ctx):
    return types.SimpleNamespace(context=ctx)


def _state(code, **kw):
    st = {
        "step": Step(sub_task="use the library", sub_task_agent="engineer",
                     bullet_points=["call it"]),
        "current_code": EngineerResponse(python_code=code, code_explanation="x"),
        "attempts": 1,
        "work_dir": str(WORK_DIR),
    }
    st.update(kw)
    return st


# ── the fixture itself must actually diverge, or every test below is vacuous ──


def test_the_two_environments_really_disagree():
    import divergent
    assert hasattr(divergent, "ENGINE_MARK") and not hasattr(divergent, "newapi")
    out = subprocess.run(
        [sys.executable, "-c", "import divergent; print(sorted(n for n in dir(divergent) "
                               "if not n.startswith('_')))"],
        capture_output=True, text=True, cwd=str(NEUTRAL),
        env={**os.environ, "PYTHONPATH": str(GUEST_DIR)},
    ).stdout
    assert "GUEST_MARK" in out and "newapi" in out and "ENGINE_MARK" not in out, out
    print("ok test_the_two_environments_really_disagree")


# ── retry_report: the report comes from the guest ─────────────────────────────


def test_retry_report_answers_from_the_guest_sandbox():
    ctx = PlanContext(main_task="t", api_introspect_retry=True)
    with _Env(**{RETRY_FLAG: None, REMOTE_ENV: "10.0.0.5:8790"}), _Guest() as g, _NoLocal():
        report = _ai.retry_report(ctx, GONE_TB, GONE_CODE, work_dir=str(WORK_DIR))
    assert report is not None, "the remote path must produce a report, not go silent"
    assert "ABSENT: divergent.gone" in report, report
    assert "GUEST_MARK" in report, f"the report is not the guest's:\n{report}"
    assert "ENGINE_MARK" not in report, f"the ENGINE venv answered:\n{report}"
    assert g.seen[0]["type"] == "introspect" and g.seen[0]["session"] == "r17a-session", g.seen[0]
    print("ok test_retry_report_answers_from_the_guest_sandbox")


def test_retry_report_without_a_work_dir_stays_silent():
    """No session means no sandbox to ask. Silence, never the engine venv's answer."""
    ctx = PlanContext(main_task="t", api_introspect_retry=True)
    with _Env(**{RETRY_FLAG: None, REMOTE_ENV: "10.0.0.5:8790"}), _Guest() as g, _NoLocal():
        assert _ai.retry_report(ctx, GONE_TB, GONE_CODE) is None
    assert g.seen == [], "a request was sent with no session"
    print("ok test_retry_report_without_a_work_dir_stays_silent")


def test_a_guest_that_cannot_answer_is_a_no_op():
    """Guest down, a non-zero exit, and a reply that is not an `introspect_result`: skip the
    injection, never fail the step, never fall back. The last case is what keeps an `execute`
    answer — same stdout/returncode shape — from being read as an API report."""
    ctx = PlanContext(main_task="t", api_introspect_retry=True)
    with _Env(**{RETRY_FLAG: None, REMOTE_ENV: "10.0.0.5:8790"}):
        with _Guest(raises=ConnectionRefusedError("guest down")), _NoLocal():
            assert _ai.retry_report(ctx, GONE_TB, GONE_CODE, work_dir=str(WORK_DIR)) is None
        with _Guest(returncode=1), _NoLocal():
            assert _ai.retry_report(ctx, GONE_TB, GONE_CODE, work_dir=str(WORK_DIR)) is None
        with _Guest(reply_type="result"), _NoLocal():
            assert _ai.retry_report(ctx, GONE_TB, GONE_CODE, work_dir=str(WORK_DIR)) is None
    print("ok test_a_guest_that_cannot_answer_is_a_no_op")


def test_the_instance_shape_also_answers_from_the_guest():
    """The module shape is not the whole seam. `CLASS_SNIPPET` takes a different spec — the type,
    the attribute, and the failing script's own imports — and a mis-shipped one would be invisible:
    the guest would exit non-zero and the caller would simply stay quiet."""
    ctx = PlanContext(main_task="t", api_introspect_retry=True)
    with _Env(**{RETRY_FLAG: None, REMOTE_ENV: "10.0.0.5:8790"}), _Guest() as g, _NoLocal():
        report = _ai.retry_report(ctx, LOADER_TB, LOADER_CODE, work_dir=str(WORK_DIR))
    assert report is not None, "the instance shape stayed silent under a remote executor"
    assert "ABSENT: Loader.fit" in report, report
    assert g.seen[0]["type"] == "introspect", g.seen[0]
    # `guestonly` is installed ONLY in the sandbox, so this process could not have answered it.
    with _Env(**{RETRY_FLAG: None, REMOTE_ENV: None}):
        assert _ai.retry_report(ctx, LOADER_TB, LOADER_CODE, work_dir=str(WORK_DIR)) is None
    print("ok test_the_instance_shape_also_answers_from_the_guest")


def test_the_engineer_node_hands_the_session_to_the_guest():
    """The one link the calls above bypass: `nodes.engineer` has to pass `work_dir` down. Without
    it `env_report` has no session, asks nobody, and the whole remote path is silently dead."""
    captured = {}

    class _FakeLLM:
        def invoke(self, msgs, config=None):
            captured["system"] = msgs[0].content
            return types.SimpleNamespace(text="```python\nprint(1)\n```")

    state = _state(
        GONE_CODE,
        current_execution_verdict=ExecutionVerdict(
            status="failure", failure_kind="renamed_api", error_summary="AttributeError",
            fix_suggestion="check the name"),
        execution_stderr=GONE_TB, execution_stdout="", execution_returncode=1,
        execution_timed_out=False, error_history=[],
    )
    ctx = PlanContext(main_task="t", api_introspect_retry=True)
    saved = sd_nodes._proposer
    sd_nodes._proposer = lambda _c: _FakeLLM()
    try:
        with _Env(**{RETRY_FLAG: None, REMOTE_ENV: "10.0.0.5:8790"}), _Guest() as g, _NoLocal():
            sd_nodes.engineer(state, _rt(ctx))
    finally:
        sd_nodes._proposer = saved
    assert "GUEST_MARK" in captured["system"], (
        "the engineer's retry prompt did not carry the guest's report")
    assert g.seen and g.seen[0]["session"] == "r17a-session", g.seen
    print("ok test_the_engineer_node_hands_the_session_to_the_guest")


# ── api_preflight: same seam, before execution ───────────────────────────────


def test_preflight_bounces_on_the_guest_verdict():
    ctx = PlanContext(main_task="t", enable_api_preflight=True)
    with _Env(**{PREFLIGHT_FLAG: None, REMOTE_ENV: "10.0.0.5:8790"}), _Guest(), _NoLocal():
        out = api_preflight(_state(GONE_CODE), _rt(ctx))
    report = out.get("api_preflight_report")
    assert report, out
    assert out.get("api_preflight_bounces") == 1
    assert "ABSENT: divergent.gone" in report and "GUEST_MARK" in report, report
    assert "ENGINE_MARK" not in report, f"the ENGINE venv answered:\n{report}"
    print("ok test_preflight_bounces_on_the_guest_verdict")


def test_the_guest_overrules_an_engine_only_absence():
    """The false positive this whole seam exists to prevent: `newapi` is absent HERE and present
    in the sandbox. The engine's candidate must be dropped, not bounced."""
    with _Env(**{PREFLIGHT_FLAG: None, REMOTE_ENV: None}):
        assert absent_symbols(NEWAPI_CODE) == [("divergent", "newapi")], (
            "the fixture no longer reproduces an engine-only absence"
        )
    ctx = PlanContext(main_task="t", enable_api_preflight=True)
    with _Env(**{PREFLIGHT_FLAG: None, REMOTE_ENV: "10.0.0.5:8790"}), _Guest(), _NoLocal():
        out = api_preflight(_state(NEWAPI_CODE), _rt(ctx))
    assert out.get("api_preflight_report") is None, (
        f"correct code was blocked on the ENGINE venv's verdict: {out}"
    )
    print("ok test_the_guest_overrules_an_engine_only_absence")


def test_preflight_with_an_unreachable_guest_runs_the_code():
    ctx = PlanContext(main_task="t", enable_api_preflight=True)
    with _Env(**{PREFLIGHT_FLAG: None, REMOTE_ENV: "10.0.0.5:8790"}), \
            _Guest(raises=ConnectionRefusedError("guest down")), _NoLocal():
        out = api_preflight(_state(GONE_CODE), _rt(ctx))
    assert out.get("api_preflight_report") is None, out
    print("ok test_preflight_with_an_unreachable_guest_runs_the_code")


def test_a_package_absent_from_the_engine_is_still_probed():
    """`_skip_root` waves through anything `find_spec` cannot see, which is the right rule when the
    engine venv IS the target and fatal when it is not: the backend image has no scientific stack,
    so this gate alone left every probe unreachable under a remote executor."""
    ctx = PlanContext(main_task="t", enable_api_preflight=True)
    with _Env(**{PREFLIGHT_FLAG: None, REMOTE_ENV: "10.0.0.5:8790"}), _Guest() as g, _NoLocal():
        out = api_preflight(_state(GUESTONLY_CODE), _rt(ctx))
    report = out.get("api_preflight_report")
    assert report, f"a sandbox-only package was never asked about: {out}"
    assert "INVALID: keyword(s) not in the INSTALLED signature: mode" in report, report
    assert g.seen and g.seen[0]["type"] == "introspect", g.seen
    with _Env(**{PREFLIGHT_FLAG: None, REMOTE_ENV: None}):
        assert api_preflight(_state(GUESTONLY_CODE), _rt(ctx)).get(
            "api_preflight_report") is None, "locally the package is absent — prove nothing"
    print("ok test_a_package_absent_from_the_engine_is_still_probed")


def test_a_package_the_engine_cannot_import_at_all_is_still_judged():
    """The live failure. `absent_symbols` proves absence by IMPORTING the module here, and the
    backend image has no scientific stack — so `from scipy.special import sph_harm` was dropped at
    the failed import and never reached the guest. The code ran and died on the real ImportError.

    A failed import in THIS venv is not a verdict under a remote executor: it stays a candidate,
    and the sandbox's own report decides.
    """
    with _Env(**{REMOTE_ENV: None}):
        import importlib.util
        assert importlib.util.find_spec("sandboxpkg") is None, (
            "the fixture no longer reproduces a package the engine cannot import")
    ctx = PlanContext(main_task="t", enable_api_preflight=True)
    with _Env(**{PREFLIGHT_FLAG: None, REMOTE_ENV: "10.0.0.5:8790"}), _Guest() as g, _NoLocal():
        out = api_preflight(_state(DEADNAME_CODE), _rt(ctx))
    report = out.get("api_preflight_report")
    assert report, f"the symbol was never asked about: {out}"
    assert "ABSENT: sandboxpkg.sub.deadname" in report, report
    assert "deadname_y" in report, f"the real replacement is not named:\n{report}"
    assert out.get("api_preflight_bounces") == 1, out
    assert g.seen[0]["type"] == "introspect", g.seen[0]
    # Locally the same code proves nothing: a failed import is a different failure, not a rename.
    with _Env(**{PREFLIGHT_FLAG: None, REMOTE_ENV: None}), _Guest() as g2:
        assert api_preflight(_state(DEADNAME_CODE), _rt(ctx)).get("api_preflight_report") is None
    assert g2.seen == [], "the local path sent a remote request"
    print("ok test_a_package_the_engine_cannot_import_at_all_is_still_judged")


def test_the_dotted_form_of_the_same_reference_is_judged_too():
    """`import pkg.sub as s; s.deadname(...)` is the same question written the other way."""
    ctx = PlanContext(main_task="t", enable_api_preflight=True)
    with _Env(**{PREFLIGHT_FLAG: None, REMOTE_ENV: "10.0.0.5:8790"}), _Guest(), _NoLocal():
        out = api_preflight(_state(DOTTED_CODE), _rt(ctx))
    assert "ABSENT: sandboxpkg.sub.deadname" in (out.get("api_preflight_report") or ""), out
    print("ok test_the_dotted_form_of_the_same_reference_is_judged_too")


def test_importing_a_submodule_is_not_an_absent_symbol():
    """The false positive the candidate rule opens: `from pkg import sub` is valid while
    `hasattr(pkg, "sub")` is False in a fresh interpreter, so only an import in the SANDBOX can
    tell a live submodule from a removed name."""
    ctx = PlanContext(main_task="t", enable_api_preflight=True)
    with _Env(**{PREFLIGHT_FLAG: None, REMOTE_ENV: "10.0.0.5:8790"}), _Guest() as g, _NoLocal():
        out = api_preflight(_state(SUBMODULE_CODE), _rt(ctx))
    assert out.get("api_preflight_report") is None, (
        f"correct code was blocked because a submodule is not an attribute: {out}")
    assert g.seen, "the guest was never asked — the candidate never left the engine"
    print("ok test_importing_a_submodule_is_not_an_absent_symbol")


def test_a_symbol_the_job_still_has_is_judged_by_the_sandbox_anyway():
    """The mirror of the case above, and why a successful import HERE cannot end the question
    either: `oldapi` is importable in this process and gone from the sandbox. While the Job's own
    `hasattr` short-circuited the check, the guest was never asked and the step ran code that could
    not work — measured as `absent_symbols=[]`, zero guest round-trips.
    """
    with _Env(**{PREFLIGHT_FLAG: None, REMOTE_ENV: None}):
        assert absent_symbols(OLDAPI_CODE) == [], (
            "the fixture no longer reproduces a symbol the Job has and the sandbox lacks")
    ctx = PlanContext(main_task="t", enable_api_preflight=True)
    with _Env(**{PREFLIGHT_FLAG: None, REMOTE_ENV: "10.0.0.5:8790"}), _Guest() as g, _NoLocal():
        out = api_preflight(_state(OLDAPI_CODE), _rt(ctx))
    report = out.get("api_preflight_report")
    assert report, f"the Job's own copy answered and the sandbox was never consulted: {out}"
    assert "ABSENT: divergent.oldapi" in report, report
    assert "GUEST_MARK" in report and "ENGINE_MARK" not in report, report
    assert g.seen and g.seen[0]["type"] == "introspect", g.seen
    print("ok test_a_symbol_the_job_still_has_is_judged_by_the_sandbox_anyway")


def test_the_remote_probe_fan_out_is_bounded():
    """Each remote probe is a sandbox container launch issued BEFORE the step runs, so the local
    call-shape fan-out (20) would add tens of seconds to every step.

    The ceiling is the two remote probe budgets together — module candidates and call shapes.
    Deduplication is what keeps a script that calls one function twenty times to a single probe.
    """
    ctx = PlanContext(main_task="t", enable_api_preflight=True)
    with _Env(**{PREFLIGHT_FLAG: None, REMOTE_ENV: "10.0.0.5:8790"}), _Guest() as g, _NoLocal():
        api_preflight(_state(FANOUT_CODE), _rt(ctx))
    ceiling = _MAX_REMOTE_MODULE_PROBES + _MAX_REMOTE_SIGNATURE_PROBES
    assert 0 < len(g.seen) <= ceiling, f"{len(g.seen)} guest round-trips for one step"
    print(f"ok test_the_remote_probe_fan_out_is_bounded: {len(g.seen)} round-trips")


# ── the local path is untouched ──────────────────────────────────────────────


def test_local_execution_still_answers_from_this_process():
    ctx = PlanContext(main_task="t", api_introspect_retry=True)
    with _Env(**{RETRY_FLAG: None, REMOTE_ENV: None}), _Guest() as g:
        report = _ai.retry_report(ctx, GONE_TB, GONE_CODE, work_dir=str(WORK_DIR))
    assert report and "ENGINE_MARK" in report and "GUEST_MARK" not in report, report
    assert g.seen == [], "the local path sent a remote request"
    print("ok test_local_execution_still_answers_from_this_process")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\nALL REMOTE-INTROSPECT TESTS PASSED")
