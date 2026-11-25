"""Two engineer rules, in generic wording: a call into an installed package is not wrapped in a
broad `except` that hides its error (b13-1 qwen r31 raised its own RuntimeError around every
library call, so the library's error reached the retry only as text), and a published fitting
formula, table or calibrated relation comes from an installed package's implementation, not
from coefficients typed from memory (b12-3 deepseek invented a different table in every run).
No model call.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Step
from cmbagent_lg.self_debug.prompts import engineer_instructions


def _prompt():
    step = Step(sub_task="compute a quantity", sub_task_agent="engineer", bullet_points=["b"])
    return " ".join(engineer_instructions(PlanContext(main_task="t"), step, retry_context="").split())


def test_no_broad_except_around_installed_packages():
    p = _prompt()
    assert "Do NOT wrap calls into installed packages in a broad `except Exception`" in p
    assert "re-raise with `raise ... from err`" in p


def test_published_relations_come_from_an_installed_implementation():
    p = _prompt()
    assert "use the implementation an installed package provides" in p
    assert "Do NOT reproduce numerical coefficients from memory" in p
    assert "print each one with its source" in p
