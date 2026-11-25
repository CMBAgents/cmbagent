"""Demo-dataset gate: a script that loads BUNDLED EXAMPLE data never runs.

One breast-cancer-fleet script fitted lifelines' Rossi recidivism demo
(`lifelines.datasets.load_rossi()`) instead of the real cohort. It exited 0, printed
plausible survival numbers, and the step evaluator — which reads the code (head and tail),
stdout and the file list — passed it on its plausible numbers. That is the one failure mode whose output is
indistinguishable from success, so it has to be caught in the SOURCE, before the run.

The check lives in `static_preflight` beside the syntax and prints-anything questions
and rejects exactly like them (rc 1, empty stdout, the diagnosis on stderr, the failure
log on disk → `execution_evaluator`), so the retry machinery is untouched.

It is conservative in three ways, each covered below: `sklearn.datasets.make_*`
synthetic generators and the generic directory readers (`torchvision.datasets.ImageFolder`)
are legitimate and pass; a sub-task that NAMES the dataset stands the gate down (a plan
may legitimately say "cluster sklearn's iris data"); and
`PlanContext.reject_demo_datasets=False` disables it.

Run:
    python tests/test_demo_dataset_gate.py
"""

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Step
from cmbagent_lg.self_debug import nodes as sd_nodes
from cmbagent_lg.self_debug.schemas import EngineerResponse
from cmbagent_lg.self_debug.static_preflight import (
    STATIC_MARKER,
    demo_dataset_message,
    demo_dataset_offender,
    route_after_static_preflight,
    static_preflight,
)

# The fleet's own script, reduced to its first three lines.
ROSSI = """from lifelines.datasets import load_rossi
from lifelines import CoxPHFitter

if __name__ == "__main__":
    df = load_rossi()
    cph = CoxPHFitter().fit(df, duration_col="week", event_col="arrest")
    print(cph.summary)
"""

REAL_DATA = """import pandas as pd
from lifelines import CoxPHFitter

if __name__ == "__main__":
    df = pd.read_parquet("data/cohort.parquet")
    print(df.shape, list(df.columns))
    print(CoxPHFitter().fit(df, duration_col="t", event_col="e").summary)
"""


def _rt(ctx):
    return types.SimpleNamespace(context=ctx)


def _step(sub_task="fit a Cox model to the cohort", bullets=("print the summary",)):
    return Step(sub_task=sub_task, sub_task_agent="engineer", bullet_points=list(bullets))


def _state(code, step=None, **kw):
    st = {
        "step": step or _step(),
        "current_code": EngineerResponse(python_code=code, code_explanation="x"),
        "attempts": 1,
        "step_number": 1,
    }
    st.update(kw)
    return st


@pytest.fixture
def no_subprocess(monkeypatch):
    """The executor must never be reached: any Popen is a test failure."""

    def _boom(*a, **k):
        raise AssertionError("subprocess.Popen was called — the script was executed")

    monkeypatch.setattr(sd_nodes.subprocess, "Popen", _boom)
    monkeypatch.delenv("CMBAGENT_LG_API_PREFLIGHT", raising=False)
    monkeypatch.delenv("CMBAGENT_LG_REMOTE_EXECUTOR", raising=False)


# ── (1) what the detector flags ────────────────────────────────────────────────


@pytest.mark.parametrize("code,expected", [
    # The measured case, through the import.
    (ROSSI, "lifelines.datasets.load_rossi"),
    # A bare call after a star import from a datasets module.
    ("from lifelines.datasets import *\ndf = load_rossi()\nprint(df)\n", "lifelines.datasets.load_rossi"),
    ("from sklearn.datasets import *\nprint(load_wine())\n", "sklearn.datasets.load_wine"),
    # The generic rule: any package's datasets / sampledata submodule, called without a file argument.
    ("import anypkg.datasets as ds\nprint(ds.load_example())\n", "anypkg.datasets.load_example"),
    ("from anypkg.sub.sampledata import fetch_demo\nprint(fetch_demo('small'))\n",
     "anypkg.sub.sampledata.fetch_demo"),
    # sklearn, every spelling.
    ("from sklearn.datasets import load_iris\nX = load_iris()\n",
     "sklearn.datasets.load_iris"),
    ("import sklearn.datasets\nprint(sklearn.datasets.load_breast_cancer())\n",
     "sklearn.datasets.load_breast_cancer"),
    ("from sklearn import datasets\nprint(datasets.fetch_openml('mnist_784'))\n",
     "sklearn.datasets.fetch_openml"),
    ("import sklearn.datasets as skd\nprint(skd.load_wine())\n",
     "sklearn.datasets.load_wine"),
    # seaborn's online demo repo.
    ("import seaborn as sns\nprint(sns.load_dataset('tips'))\n", "seaborn.load_dataset"),
    ("from seaborn import load_dataset\nprint(load_dataset('penguins'))\n",
     "seaborn.load_dataset"),
    # statsmodels, through its api alias.
    ("import statsmodels.api as sm\nd = sm.datasets.get_rdataset('mtcars')\nprint(d.data)\n",
     "statsmodels.api.datasets.get_rdataset"),
    # plotly / bokeh / keras / torchvision / sksurv.
    ("import plotly.express as px\nprint(px.data.gapminder())\n",
     "plotly.express.data.gapminder"),
    ("from bokeh.sampledata.autompg import autompg\nprint(autompg)\n",
     "bokeh.sampledata.autompg.autompg"),
    ("import bokeh.sampledata.iris\nprint(1)\n", "bokeh.sampledata.iris"),
    ("from keras.datasets import mnist\nprint(mnist.load_data())\n",
     "keras.datasets.mnist"),
    ("import torchvision\nd = torchvision.datasets.CIFAR10('.', download=True)\nprint(d)\n",
     "torchvision.datasets.CIFAR10"),
    ("from sksurv.datasets import load_whas500\nprint(load_whas500())\n",
     "sksurv.datasets.load_whas500"),
])
def test_offenders_are_named(code, expected):
    assert demo_dataset_offender(code) == expected


@pytest.mark.parametrize("code", [
    # Reading the project's own data.
    REAL_DATA,
    "import pandas as pd\nprint(pd.read_csv('data/cohort.csv').shape)\n",
    # Synthetic generators are a legitimate way to make data, not demo data.
    "from sklearn.datasets import make_classification\nX, y = make_classification()\nprint(X.shape)\n",
    "import sklearn.datasets as skd\nX, y = skd.make_blobs(n_samples=10)\nprint(X)\n",
    # File-based sklearn helpers read a real path.
    "from sklearn.datasets import load_svmlight_file\nprint(load_svmlight_file('data/x.svm'))\n",
    # Generic directory readers point at the project's own files.
    "import torchvision\nd = torchvision.datasets.ImageFolder('data/images')\nprint(len(d))\n",
    # Importing the module without touching a dataset.
    "import sklearn.datasets\nfrom sklearn.linear_model import LogisticRegression\nprint(1)\n",
    "import lifelines.datasets\nprint('not used')\n",
    # The engineer's own loader, named like nothing in the tables.
    "def load_data(p):\n    return open(p).read()\nprint(load_data('data/x.csv'))\n",
    # A similarly-named module that is not a demo module.
    "import mypkg.datasets\nprint(mypkg.datasets.load_cohort('data/c.parquet'))\n",
    "from mypkg.datasets import load_table\np = 'data/t.csv'\nprint(load_table(p))\n",
    "from mypkg.datasets import load_table\nprint(load_table(path='t.csv'))\n",
    # A top-level package named `datasets` is not a submodule of one.
    "import datasets\nprint(datasets.load_dataset('data/local_dir'))\n",
    # A star import, but the script binds the name it calls itself.
    "from anypkg.datasets import *\ndef load_mine():\n    return 1\nprint(load_mine())\n",
    # The script's own function with a demo-looking name, and no import.
    "def load_rossi():\n    return open('data/x.csv').read()\nprint(load_rossi())\n",
    # Unparseable code is the syntax check's business, never this one's.
    'x = "unterminated\nprint(x)\n',
    "",
])
def test_legitimate_scripts_pass(code):
    assert demo_dataset_offender(code) is None


# ── (2) the rejection, end to end through the node ─────────────────────────────


def test_demo_dataset_script_is_not_executed(tmp_path, no_subprocess):
    ctx = PlanContext(main_task="t")
    st = _state(ROSSI, work_dir=str(tmp_path))
    out = static_preflight(st, _rt(ctx))

    assert out["static_preflight_rejected"] == "demo_dataset"
    # The shape of a crashed run — the retry path reads these unchanged.
    assert out["execution_returncode"] == 1
    assert out["execution_stdout"] == ""
    assert out["execution_timed_out"] is False
    assert out["data_manifest"] == []
    err = out["execution_stderr"]
    assert "lifelines.datasets.load_rossi" in err, err      # names the offending call
    assert "data/" in err, "must tell the engineer to read the project's own data"
    assert STATIC_MARKER in err and "not executed" in err

    st.update(out)
    assert route_after_static_preflight(st, _rt(ctx)) == "execution_evaluator"

    # The audit trail: the final failure names directly, no canonical step_1.*
    codebase = tmp_path / "codebase"
    assert (codebase / "step_1_failure_1.py").read_text() == ROSSI
    log = (codebase / "step_1_failure_1.log").read_text()
    assert "returncode=1" in log and "load_rossi" in log
    assert not (codebase / "step_1.py").exists()


def test_a_real_data_script_passes_the_gate(tmp_path, no_subprocess):
    ctx = PlanContext(main_task="t")
    st = _state(REAL_DATA, work_dir=str(tmp_path))
    out = static_preflight(st, _rt(ctx))
    assert out["static_preflight_rejected"] is None
    assert "execution_stderr" not in out, "a pass-through must not touch the executor's fields"


# ── (3) the three ways the gate stands down ───────────────────────────────────


def test_a_sub_task_that_names_the_dataset_stands_the_gate_down(tmp_path, no_subprocess):
    """A plan may legitimately ask for a library dataset; then it IS this run's data."""
    ctx = PlanContext(main_task="t")
    code = "from sklearn.datasets import load_iris\nprint(load_iris().data.shape)\n"
    # Named by the accessor.
    st = _state(code, step=_step("cluster sklearn.datasets.load_iris with k-means"),
                work_dir=str(tmp_path))
    assert static_preflight(st, _rt(ctx))["static_preflight_rejected"] is None
    # Named the way a human writes it, in a bullet point.
    st = _state(code, step=_step("cluster the flowers", ("use the iris data",)),
                work_dir=str(tmp_path))
    assert static_preflight(st, _rt(ctx))["static_preflight_rejected"] is None
    # NOT named → still refused.
    st = _state(code, step=_step("cluster the provided cohort", ("read data/cohort.csv",)),
                work_dir=str(tmp_path))
    assert static_preflight(st, _rt(ctx))["static_preflight_rejected"] == "demo_dataset"


def test_flag_off_disables_only_this_check(tmp_path, no_subprocess):
    ctx = PlanContext(main_task="t", reject_demo_datasets=False)
    assert static_preflight(_state(ROSSI), _rt(ctx))["static_preflight_rejected"] is None
    # The other two questions are unaffected.
    assert static_preflight(
        _state('x = "oops\n'), _rt(ctx))["static_preflight_rejected"] == "syntax"
    assert static_preflight(
        _state("import numpy as np\nnp.save('data/x.npy', np.arange(3))\n"),
        _rt(ctx))["static_preflight_rejected"] == "no_stdout"


def test_a_demo_dataset_beats_the_silent_check(tmp_path, no_subprocess):
    """Both defects at once: the wrong-data message is the more useful one."""
    ctx = PlanContext(main_task="t")
    silent_rossi = "from lifelines.datasets import load_rossi\ndf = load_rossi()\n"
    out = static_preflight(_state(silent_rossi, work_dir=str(tmp_path)), _rt(ctx))
    assert out["static_preflight_rejected"] == "demo_dataset"
    assert "load_rossi" in out["execution_stderr"]


def test_non_engineer_steps_are_passed_through():
    ctx = PlanContext(main_task="t")
    st = _state(ROSSI, step=Step(sub_task="write it up", sub_task_agent="researcher",
                                 bullet_points=["prose"]))
    assert static_preflight(st, _rt(ctx))["static_preflight_rejected"] is None


def test_message_is_actionable():
    msg = demo_dataset_message("lifelines.datasets.load_rossi")
    assert "lifelines.datasets.load_rossi" in msg
    assert "data/" in msg and "example dataset" in msg.lower()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
