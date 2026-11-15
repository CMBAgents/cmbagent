"""Multimodal grounding for cmbagent_lg.

cmbagent_lg is otherwise text-only: agents see code + stdout + a file-path
manifest, but not the *contents* of the plots they produce. While
`PlanContext.vlm_enabled` holds (the default), this module hands the generated
PNGs to the vision-capable consumers — the report writers and the
`image_reviewer` node — so they can read trends off the plots.

Provider-agnostic: it emits the LangChain multimodal content-block format
(`{"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}`),
which ChatGoogleGenerativeAI, ChatOpenAI and ChatAnthropic all accept.
"""

from cmbagent_lg.vlm.images import collect_images, images_from_manifest, with_images
from cmbagent_lg.vlm.reviewer import image_reviewer, route_after_image_reviewer

__all__ = [
    "collect_images",
    "images_from_manifest",
    "with_images",
    "image_reviewer",
    "route_after_image_reviewer",
]
