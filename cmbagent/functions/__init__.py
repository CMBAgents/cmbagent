"""CMBAgent function registration system.

This package contains modular function definitions organized by purpose:
- execution_control: Flow control and agent transitions
- planning: Planning workflow management
- ideas: Idea recording and storage
- keywords: AAS keyword extraction and recording
- status: Status tracking and workflow control
- utils: File and plot handling utilities
"""

from .registration import register_functions_to_agents
from .utils import load_docstrings, load_plots

__all__ = [
    'register_functions_to_agents',
    'load_docstrings',
    'load_plots',
]
