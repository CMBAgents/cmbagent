"""The observed-absence store (`self_debug/api_history.py`): what a failed run leaves for every
later step, and the guarantees that make it safe to share between four workers.

Recording is verified against the installed package; reading is version-pinned; a torn line is
skipped; the prompt tier is a no-op while the store is empty; the recorder never raises.
"""

import importlib.metadata
import json
import os
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Step
from cmbagent_lg.self_debug import api_history as ah
from cmbagent_lg.self_debug import nodes as sd_nodes
from cmbagent_lg.self_debug.api_preflight import api_preflight
from cmbagent_lg.self_debug.schemas import EngineerResponse

from tests.test_api_preflight import _Env, _Introspector, _rt, _state, ENV_FLAG, REMOTE_ENV

NUMPY_VER = importlib.metadata.version("numpy")


def _ctx(**kw):
    return PlanContext(main_task="t", enable_api_preflight=True, **kw)


def test_record_is_verified_and_read_back():
    code = "import numpy as np\nprint(np.gone_xyz(1))\n"
    stderr = ("Traceback (most recent call last):\n  File \"s.py\", line 2, in <module>\n"
              "AttributeError: module 'numpy' has no attribute 'gone_xyz'\n")
    with _Env(**{REMOTE_ENV: None, ah.ENV_CACHE_DIR: None}), tempfile.TemporaryDirectory() as tmp:
        rec = ah.record_from_run(_ctx(), code, stderr, "", tmp)
        assert rec and rec["kind"] == "module_attr" and rec["attr"] == "gone_xyz", rec
        assert rec["dist"] == "numpy" and rec["ver"] == NUMPY_VER, rec
        path = ah.store_path(_ctx(), tmp)
        assert path == Path(tmp) / "logs" / "api_cache" / ah.STORE_NAME
        lines = path.read_text().splitlines()
        assert len(lines) == 1 and len(lines[0]) < 4096
        # Same fault again: a second line, ONE fact with seen=2 on read.
        ah.record_from_run(_ctx(), code, stderr, "", tmp)
        facts = ah.read_facts(path)
        assert len(facts) == 1 and facts[0]["seen"] == 2, facts
        assert ah.facts_for(path) == facts, "the pin matches this environment"

        # A fact that is NOT true here is refused at record time: the attribute exists.
        ok = "import numpy as np\nprint(np.zeros(1))\n"
        assert ah.record_from_run(_ctx(), ok, stderr.replace("gone_xyz", "zeros"), "", tmp) is None
        # A try-guarded probe is not a fact about the API.
        guarded = "import numpy as np\ntry:\n    print(np.gone_xyz(1))\nexcept AttributeError:\n    pass\n"
        assert ah.record_from_run(_ctx(), guarded, stderr, "", tmp) is None
        # A class the script defines itself is never recorded against the library.
        own = "import numpy as np\nclass ndarray:\n    pass\nx = ndarray()\nx.gone()\n"
        assert ah.record_from_run(_ctx(), own, "AttributeError: 'ndarray' object has no attribute 'gone'",
                                  "", tmp) is None
        assert len(path.read_text().splitlines()) == 2
    print("ok test_record_is_verified_and_read_back")


def test_torn_lines_and_version_mismatch_are_ignored():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / ah.STORE_NAME
        good = {"kind": "module_attr", "dist": "numpy", "ver": NUMPY_VER, "cls": "", "module": "numpy",
                "attr": "gone_xyz", "kwarg": "", "suggest": ["ones"], "ts": 5}
        stale = dict(good, ver="0.0.1", attr="stale_attr")
        path.write_text(json.dumps(good) + "\n" + '{"kind": "module_attr", "dist": "nu'  # torn
                        + "\n" + json.dumps(stale) + "\n" + "not json at all\n" + json.dumps(good) + "\n")
        facts = ah.read_facts(path)
        assert [f["attr"] for f in facts] == ["gone_xyz", "stale_attr"], facts
        assert facts[0]["seen"] == 2
        assert [f["attr"] for f in ah.facts_for(path)] == ["gone_xyz"], (
            "a fact pinned to another version leaked into this environment")
        # Unreadable store ⇒ empty, never an error.
        assert ah.read_facts(Path(tmp) / "missing.jsonl") == []
        assert ah.facts_for(None) == []
    print("ok test_torn_lines_and_version_mismatch_are_ignored")


def test_kwarg_fact_needs_function_identity():
    """A keyword fact applies only to the exact function (module.qualname) it was observed on —
    never to another callable that shares the bare name."""
    with _Env(**{ENV_FLAG: None, REMOTE_ENV: None}), _Introspector(), \
            tempfile.TemporaryDirectory() as tmp:
        ctx = _ctx()
        path = ah.store_path(ctx, tmp)
        path.parent.mkdir(parents=True)
        fact = {"kind": "kwarg", "dist": "numpy", "ver": NUMPY_VER, "cls": "", "module": "apipf_fixture",
                "attr": "boost_like", "kwarg": "bogus", "func": "apipf_fixture.boost_like", "ts": 1}
        path.write_text(json.dumps(fact) + "\n")
        code = "import apipf_fixture as fx\nprint(fx.boost_like(1, bogus=2))\n"
        # Without the store the signature path cannot judge a Boost-shaped callable…
        assert api_preflight(_state(code), _rt(ctx)).get("api_preflight_report") is None
        # …with it, the observed fact bounces the call.
        rep = api_preflight(_state(code, work_dir=tmp), _rt(ctx)).get("api_preflight_report") or ""
        assert "INVALID: keyword 'bogus'" in rep and "observed" in rep, rep
        # Same bare name, different function ⇒ silent.
        other = "import apipf_fixture as fx\nprint(fx.strict_call(1, 2, target=3))\n"
        assert api_preflight(_state(other, work_dir=tmp), _rt(ctx)).get("api_preflight_report") is None
        # A different keyword ⇒ silent.
        kw2 = "import apipf_fixture as fx\nprint(fx.boost_like(1, fine=2))\n"
        assert api_preflight(_state(kw2, work_dir=tmp), _rt(ctx)).get("api_preflight_report") is None
    print("ok test_kwarg_fact_needs_function_identity")


def test_recorder_never_raises_and_prompt_tier_is_silent_when_empty():
    with _Env(**{REMOTE_ENV: None, ah.ENV_CACHE_DIR: None}), tempfile.TemporaryDirectory() as tmp:
        ctx = _ctx()
        for garbage in ("\x00\xff" * 200, "TypeError: (() got an unexpected keyword argument ''",
                        "AttributeError: module '' has no attribute ''", None, ""):
            assert ah.record_from_run(ctx, "import os\n", garbage, garbage, tmp) is None
        assert ah.record_from_run(ctx, "this is not python(((", "AttributeError: module 'numpy' "
                                  "has no attribute 'x'", "", tmp) is None
        assert ah.record_from_run(ctx, "import os\n", "boom", "", "/dev/null/not/a/dir") is None
        assert ah.prompt_tier(ctx, tmp) is None, "an empty store must add nothing"
        step = Step(sub_task="t", sub_task_agent="engineer", bullet_points=["x"])
        assert sd_nodes._first_attempt_api_facts(ctx, step, tmp, 1) is None
        assert sd_nodes._first_attempt_api_facts(ctx, step, tmp, 2) is None

        # Once a fact exists it is rendered — on the first attempt only, and only if wanted.
        ah._append(ah.store_path(ctx, tmp), {
            "kind": "instance_attr", "dist": "numpy", "ver": NUMPY_VER, "cls": "numpy.ndarray",
            "module": "", "attr": "values", "kwarg": "", "suggest": ["var"], "ts": 1})
        tier = sd_nodes._first_attempt_api_facts(ctx, step, tmp, 1)
        assert tier and "KNOWN ABSENT IN THIS ENVIRONMENT" in tier and "`ndarray` has no attribute `values`" in tier, tier
        assert "closest existing: var" in tier
        assert sd_nodes._first_attempt_api_facts(ctx, step, tmp, 2) is None
        with _Env(CMBAGENT_LG_API_HISTORY_PROMPT="0"):
            assert ah.prompt_tier(ctx, tmp) is None
        assert ah.prompt_tier(PlanContext(main_task="t", api_history_in_prompt=False), tmp) is None
    print("ok test_recorder_never_raises_and_prompt_tier_is_silent_when_empty")


def test_shared_cache_dir_and_remote_guard():
    with tempfile.TemporaryDirectory() as shared, tempfile.TemporaryDirectory() as wd:
        ctx = PlanContext(main_task="t", api_preflight_cache_dir=shared)
        assert ah.store_path(ctx, wd) == Path(shared) / ah.STORE_NAME
        with _Env(**{ah.ENV_CACHE_DIR: shared}):
            assert ah.store_path(PlanContext(main_task="t"), wd) == Path(shared) / ah.STORE_NAME
        assert ah.store_path(PlanContext(main_task="t"), None) is None
        # Under a remote executor this process is not the environment that failed: no record.
        with _Env(**{REMOTE_ENV: "10.0.0.5:8790"}):
            assert ah.record_from_run(ctx, "import numpy as np\nnp.gone_xyz()\n",
                                      "AttributeError: module 'numpy' has no attribute 'gone_xyz'",
                                      "", wd) is None
        assert not (Path(shared) / ah.STORE_NAME).exists()
    print("ok test_shared_cache_dir_and_remote_guard")


if __name__ == "__main__":
    test_record_is_verified_and_read_back()
    test_torn_lines_and_version_mismatch_are_ignored()
    test_kwarg_fact_needs_function_identity()
    test_recorder_never_raises_and_prompt_tier_is_silent_when_empty()
    test_shared_cache_dir_and_remote_guard()
    print("ALL test_api_history PASSED")
