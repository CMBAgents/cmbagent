"""CMBAgent workflow modules.

This package contains different execution workflows for CMBAgent:
- one_shot: Single task execution workflow
- planning: Planning and control workflows
- control: Control-only execution
- human_loop: Interactive workflow with human feedback
"""

from .one_shot import one_shot

__all__ = ['one_shot']
