"""The image reviewer's Gemini thinking level is its own (`PlanContext.vlm_thinking_level`, "high"
by default) while every other critic call keeps the critic role's "low". At "low" Gemini flagged 5
of 28 figures with a labelled defect, at "high" 15 of 28. No model call: the reviewer's client is
captured, and the Gemini clients are only built.
"""

import os
import sys
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cmbagent_lg import llms
from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Step
from cmbagent_lg.self_debug.schemas import ImageReview
from cmbagent_lg.vlm import reviewer


def test_chat_model_thinking_override_is_gemini_only_and_per_client(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-not-used")
    monkeypatch.delenv("LLM_ENDPOINTS", raising=False)
    high = llms.chat_model("gemini-3.1-pro-preview", "critic", "high")
    low = llms.chat_model("gemini-3.1-pro-preview", "critic")
    assert high.thinking_level == "high" and low.thinking_level == "low" and high is not low
    # A Gemini 2.x model takes no thinking level at all.
    assert getattr(llms.chat_model("gemini-2.5-pro", "critic", "high"), "thinking_level", None) is None


def test_the_reviewer_asks_for_its_own_level(tmp_path, monkeypatch):
    seen = []

    class _Model:
        def with_structured_output(self, schema):
            return self

        def invoke(self, msgs, config=None):
            return ImageReview(needs_revision=False, issues=[], suggestions=[])

    def _fake(model, role, thinking_level=None):
        seen.append((model, role, thinking_level))
        return _Model()

    monkeypatch.setattr(reviewer, "chat_model", _fake)
    (tmp_path / "data").mkdir()
    png = tmp_path / "data" / "f.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    monkeypatch.setattr(reviewer, "images_from_manifest", lambda *a, **k: [png])
    monkeypatch.setattr(reviewer, "with_images", lambda text, imgs, root=None: text)
    state = {"step": Step(sub_task="plot", sub_task_agent="engineer", bullet_points=["b"]),
             "step_number": 1, "work_dir": str(tmp_path), "data_manifest": ["data/f.png"]}
    for ctx, want in ((PlanContext(main_task="t"), "high"),
                      (PlanContext(main_task="t", vlm_thinking_level="medium", vlm_model="gemini-x"), "medium"),
                      (PlanContext(main_task="t", vlm_thinking_level=None), None)):
        reviewer.image_reviewer(state, types.SimpleNamespace(context=ctx))
        assert seen[-1][1:] == ("critic", want), seen
    assert seen[1][0] == "gemini-x" and seen[0][0] == reviewer._DEFAULT_REVIEW_MODEL
