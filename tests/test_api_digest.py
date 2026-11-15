"""Gate test for the deterministic API digest (`enable_api_digest`), injected BEFORE the
engineer writes code.

The earliest of three independently switchable interception points for the same fault:

    enable_api_digest      facts in the prompt     → the stale call is never written
    enable_api_preflight   check the emitted code  → the stale call is never RUN
    api_introspect_retry   explain the traceback   → the stale call runs, fails, is explained
"""

import os
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Step
from cmbagent_lg.self_debug import nodes as sd_nodes
from cmbagent_lg.self_debug.api_digest import (
    build_digest,
    candidate_modules,
    digest_for_step,
    enabled,
)
from cmbagent_lg.self_debug.schemas import EngineerResponse

DIGEST_FLAG = "CMBAGENT_LG_API_DIGEST"
PREFLIGHT_FLAG = "CMBAGENT_LG_API_PREFLIGHT"
REMOTE_ENV = "CMBAGENT_LG_REMOTE_EXECUTOR"

# A task that names its module explicitly, and one that names no library whatsoever.
TASK_NAMED = (
    "Read data/angles.csv and evaluate a special function at each row, recording the time "
    "taken and a random subsample for the statistics section.\n"
    "Evaluate the values with scipy.special.\n"
)
TASK_UNNAMED = (
    "Using the group's existing loader data/legacy_loader.py, read the spectrum in "
    "data/spectrum.csv and compute the integral.\n"
    "Load the columns via data/legacy_loader.py (adapt it if it does not run on this stack).\n"
)

# A real legacy helper of the shape a workspace script takes: the package it depends on is
# stated ONLY here, in code on disk, never in the task text.
LEGACY_HELPER = """
# Group loader (2018 pipeline).
import json
from json import decoder


def load_columns(path):
    with open(path) as fh:
        return json.load(fh), decoder.JSONDecoder
"""


class _Env:
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


def _step(task=TASK_NAMED):
    head, _, rest = task.partition("\n")
    return Step(sub_task=head, sub_task_agent="engineer",
                bullet_points=[b for b in rest.splitlines() if b.strip()])


def _workspace(helper: str | None = LEGACY_HELPER):
    """A temp work_dir with `data/` and optionally a legacy helper in it."""
    tmp = tempfile.mkdtemp()
    data = Path(tmp) / "data"
    data.mkdir()
    if helper:
        (data / "legacy_loader.py").write_text(helper)
    return tmp


# ── OFF ⇒ nothing, and the prompt is unchanged ──────────────────────────────


def test_off_produces_no_digest():
    ctx = PlanContext(main_task="t")
    assert ctx.enable_api_digest is False, "the flag must ship OFF"
    assert ctx.api_digest_max_chars == 6000
    with _Env(**{DIGEST_FLAG: None, REMOTE_ENV: None}):
        assert enabled(ctx) is False
        assert digest_for_step(ctx, _step()) is None
    print("ok test_off_produces_no_digest")


# ── ON ⇒ real facts from the environment ────────────────────────────────────


def test_digest_states_the_installed_reality():
    with _Env(**{DIGEST_FLAG: None, REMOTE_ENV: None}):
        named, roots = candidate_modules(TASK_NAMED, "")
        # "time", "random" and "statistics" are stdlib module names AND ordinary English words;
        # treating a bare prose word as a package reference would spend budget on noise.
        for word in ("time", "random", "statistics"):
            assert word not in roots, f"bare stdlib word {word!r} was taken as a package"
        # `scipy.special` is dotted, so it is picked up as a NAMED module when scipy is installed.
        digest = build_digest(TASK_NAMED, "", 6000)

    if digest is None:  # scipy absent from this venv — the rest of the file still applies
        print("ok test_digest_states_the_installed_reality: SKIPPED (no candidate package here)")
        return

    assert "INSTALLED API DIGEST" in digest
    assert "INSTALLED VERSIONS:" in digest
    # Real facts, generated from the module object — the version actually installed.
    import scipy  # noqa: PLC0415

    assert scipy.__version__ in digest, f"the exact installed version must be stated:\n{digest}"
    assert "PUBLIC NAMES" in digest, "a named module must get its real inventory"
    # …and the names are the module's own.
    import scipy.special  # noqa: PLC0415

    assert "erf" in digest and "gamma" in digest
    assert "sph_harm_y" in digest or "sph_harm" in digest
    for fake in ("definitely_not_a_real_scipy_name", "sph_harm_z"):
        assert fake not in digest, "the digest must contain only names read off the module"
    print("ok test_digest_states_the_installed_reality: exact version + real public names")


def test_prompt_text_names_no_library():
    """Every library in a digest must come from the run — the task, the workspace or the
    environment — never from a rule written for one known fault, so the static wording must not
    mention any package."""
    from cmbagent_lg.self_debug import api_digest as mod

    static = mod._HEADER + mod._STEER
    for lib in ("numpy", "scipy", "pandas", "sklearn", "scikit", "matplotlib", "Bio",
                "biopython", "joblib", "np.", "torch", "astropy"):
        assert lib.lower() not in static.lower(), f"{lib!r} is hard-coded in the prompt text"
    # An empty environment reference produces nothing at all rather than a generic lecture.
    with _Env(**{DIGEST_FLAG: None, REMOTE_ENV: None}):
        assert build_digest("Compute the mean of some numbers and print it.", "", 6000) is None
    print("ok test_prompt_text_names_no_library")


# ── the workspace source ────────────────────────────────────────────────────


def test_workspace_helpers_supply_candidates_the_task_never_names():
    """The task names no library, but the helper it says to reuse imports one — only reading
    the real file on disk closes that gap."""
    with _Env(**{DIGEST_FLAG: None, REMOTE_ENV: None}):
        wd = _workspace()
        named, roots = candidate_modules(TASK_UNNAMED, "", wd)
        assert "json" in roots, f"the helper's own import must be found: {roots}"
        assert any(n.startswith("json") for n in named), named
        digest = build_digest(TASK_UNNAMED, "", 6000, wd)
        assert digest and "json" in digest

        # Without the workspace there is nothing to go on — which is exactly the gap.
        bare = candidate_modules(TASK_UNNAMED, "")
        assert bare == ([], []), f"the task text alone names no package: {bare}"

        # The helper is PARSED, never imported: a helper with a side effect must not run.
        wd2 = _workspace("import json\nraise SystemExit('THIS HELPER WAS EXECUTED')\n")
        assert build_digest(TASK_UNNAMED, "", 6000, wd2) is not None
        # Workspace-local modules are never treated as packages to digest.
        wd3 = _workspace("import legacy_sibling\nimport json\n")
        _, r3 = candidate_modules(TASK_UNNAMED, "", wd3)
        assert "legacy_sibling" not in r3
    print("ok test_workspace_helpers_supply_candidates_the_task_never_names")


# ── the token budget ────────────────────────────────────────────────────────


def test_the_budget_is_honoured_and_degrades_cheapest_first():
    with _Env(**{DIGEST_FLAG: None, REMOTE_ENV: None}):
        wd = _workspace()
        sizes = {}
        for cap in (400, 800, 1500, 3000, 6000, 12000):
            d = build_digest(TASK_NAMED, "", cap, wd) or ""
            sizes[cap] = len(d)
            assert len(d) <= cap, f"cap {cap} exceeded: {len(d)} chars"
            if d:
                # The version manifest is the highest value-per-token tier and must survive
                # every cap at which anything is emitted at all.
                assert "INSTALLED VERSIONS:" in d, f"the manifest was dropped at cap {cap}"
        # Monotonic: a bigger budget never yields a smaller digest.
        caps = sorted(sizes)
        for a, b in zip(caps, caps[1:]):
            assert sizes[b] >= sizes[a], f"digest shrank from cap {a} to {b}: {sizes}"
        # A tiny budget must still be safe (never a half-truncated name list).
        assert sizes[400] == 0 or "INSTALLED VERSIONS:" in (build_digest(TASK_NAMED, "", 400, wd) or "")
    print(f"ok test_the_budget_is_honoured_and_degrades_cheapest_first: {sizes}")


def test_measured_cost_is_reported():
    """The added prompt size is the feature's real cost, so it is reported here rather than
    left to a one-off script."""
    with _Env(**{DIGEST_FLAG: None, REMOTE_ENV: None}):
        wd = _workspace()
        for cap in (3000, 6000):
            d = build_digest(TASK_NAMED, "", cap, wd) or ""
            # ~3.9 chars/token measured with tiktoken cl100k over real digests.
            print(f"   cap={cap:5d} → {len(d):5d} chars ≈ {len(d) / 3.9:4.0f} tokens")
    print("ok test_measured_cost_is_reported")


# ── nothing fabricated ──────────────────────────────────────────────────────


def test_no_name_is_invented():
    with _Env(**{DIGEST_FLAG: None, REMOTE_ENV: None}):
        digest = build_digest("Parse a file with json.decoder.", "", 6000) or ""
        if digest:
            import json as _json  # noqa: PLC0415

            real = {n for n in dir(_json.decoder) if not n.startswith("_")}
            body = digest.split("PUBLIC NAMES", 1)[-1]
            listed = {t.strip() for t in body.replace("\n", " ").split(",") if t.strip().isidentifier()}
            invented = listed - real - {"json", "decoder"}
            assert not invented, f"names not on the module appeared in the digest: {invented}"
    print("ok test_no_name_is_invented")


# ── remote ⇒ no-op ──────────────────────────────────────────────────────────


def test_remote_executor_is_a_no_op():
    ctx = PlanContext(main_task="t", enable_api_digest=True)
    with _Env(**{DIGEST_FLAG: "1", REMOTE_ENV: "10.0.0.5:8790"}):
        assert enabled(ctx) is False
        assert digest_for_step(ctx, _step()) is None
    with _Env(**{DIGEST_FLAG: "1", REMOTE_ENV: None}):
        assert enabled(ctx) is True
    print("ok test_remote_executor_is_a_no_op")


# ── enable_api_preflight and enable_api_digest are independent ──────────────


def _engineer_prompt(ctx, work_dir, attempts=0):
    """Drive the REAL `nodes.engineer` and return the system prompt it built."""
    captured = {}

    class _FakeLLM:
        def invoke(self, msgs, config=None):
            captured["system"] = msgs[0].content
            return types.SimpleNamespace(text="```python\nprint(1)\n```")

    state = {
        "step": _step(TASK_UNNAMED),
        "attempts": attempts,
        "work_dir": work_dir,
        "current_code": EngineerResponse(python_code="import json\n", code_explanation="x"),
    }
    saved = sd_nodes._proposer
    sd_nodes._proposer = lambda _c: _FakeLLM()
    try:
        sd_nodes.engineer(state, types.SimpleNamespace(context=ctx))
    finally:
        sd_nodes._proposer = saved
    return captured["system"]


def test_E_and_G_are_independently_toggleable():
    from cmbagent_lg.self_debug.api_preflight import _enabled as e_enabled

    wd = _workspace()
    matrix = {}
    with _Env(**{DIGEST_FLAG: None, PREFLIGHT_FLAG: None, REMOTE_ENV: None}):
        for g in (False, True):
            for e in (False, True):
                ctx = PlanContext(main_task="t", enable_api_digest=g, enable_api_preflight=e)
                matrix[(g, e)] = (enabled(ctx), e_enabled(ctx), _engineer_prompt(ctx, wd))
    for (g, e), (g_on, e_on, _) in matrix.items():
        assert g_on is g, f"digest flag leaked: enable_api_digest={g} → {g_on}"
        assert e_on is e, f"preflight flag leaked: enable_api_preflight={e} → {e_on}"

    base = matrix[(False, False)][2]
    # enable_api_preflight acts only after the code exists, so it must not touch the
    # FIRST-attempt prompt; enable_api_digest must add the digest and nothing else.
    assert matrix[(False, True)][2] == base, "E perturbed the first-attempt prompt"
    with_g = matrix[(True, False)][2]
    assert "INSTALLED API DIGEST" in with_g, "G did not inject"
    assert "INSTALLED API DIGEST" not in base
    # EXACT: deleting the injected digest from the digest-on prompt must give back the OFF
    # prompt character for character, so the digest is provably the ONLY difference.
    with _Env(**{DIGEST_FLAG: None, PREFLIGHT_FLAG: None, REMOTE_ENV: None}):
        injected = digest_for_step(
            PlanContext(main_task="t", enable_api_digest=True), _step(TASK_UNNAMED), wd
        )
    assert injected, "the digest under test could not be rebuilt"
    assert with_g.replace(injected + "\n", "", 1) == base, (
        "the injection perturbed the rest of the engineer prompt"
    )
    # Both on: both effects present and neither disabled the other.
    assert "INSTALLED API DIGEST" in matrix[(True, True)][2]
    assert matrix[(True, True)][0] and matrix[(True, True)][1]
    print("ok test_E_and_G_are_independently_toggleable: 4/4 flag combinations behave")


def test_digest_is_first_attempt_only():
    wd = _workspace()
    with _Env(**{DIGEST_FLAG: None, PREFLIGHT_FLAG: None, REMOTE_ENV: None}):
        ctx = PlanContext(main_task="t", enable_api_digest=True)
        first = _engineer_prompt(ctx, wd, attempts=0)   # node bumps to attempt 1
        later = _engineer_prompt(ctx, wd, attempts=2)   # node bumps to attempt 3
    assert "INSTALLED API DIGEST" in first
    assert "INSTALLED API DIGEST" not in later, (
        "re-sending the inventory on every retry multiplies the one cost most likely to "
        "disqualify this strategy, for the least value — the retry has a real traceback"
    )
    print("ok test_digest_is_first_attempt_only")


if __name__ == "__main__":
    test_off_produces_no_digest()
    test_digest_states_the_installed_reality()
    test_prompt_text_names_no_library()
    test_workspace_helpers_supply_candidates_the_task_never_names()
    test_the_budget_is_honoured_and_degrades_cheapest_first()
    test_measured_cost_is_reported()
    test_no_name_is_invented()
    test_remote_executor_is_a_no_op()
    test_E_and_G_are_independently_toggleable()
    test_digest_is_first_attempt_only()
    print("ALL test_api_digest PASSED")
