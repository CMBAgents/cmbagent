"""Schema for the adaptive-review decision.

After each executed step the adaptive reviewer emits one of these:
`needs_adaptation` is what the conditional edge routes on, and
`recommendations` feed the tail replan when it is True.
"""

from typing import List
from pydantic import BaseModel, Field


class AdaptiveReview(BaseModel):
    """The reviewer's verdict on whether to rewrite the remaining plan."""

    needs_adaptation: bool = Field(
        description="True if the remaining (not-yet-executed) steps should be "
        "rewritten in light of what the step just completed produced."
    )
    recommendations: List[str] = Field(
        default_factory=list,
        description="Concrete changes to apply to the remaining steps. "
        "Should be empty when needs_adaptation is False.",
    )
    reason: str = Field(
        default="",
        description="One- or two-sentence justification for the decision.",
    )

    def format(self) -> str:
        head = f"**Adaptive review** — needs_adaptation={self.needs_adaptation}"
        if self.reason:
            head += f"\n_{self.reason}_"
        if self.recommendations:
            head += "\n\n**Recommendations:**\n" + "\n".join(
                f"- {r}" for r in self.recommendations
            )
        return head
