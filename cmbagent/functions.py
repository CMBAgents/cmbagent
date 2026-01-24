"""Legacy functions.py - imports from modular functions package.

This file is kept for backwards compatibility.
All functionality has been moved to cmbagent/functions/ package.
"""

# Re-export main registration function
from .functions import register_functions_to_agents

# Re-export utility functions (for backwards compatibility if needed)
from .functions.utils import (
    extract_file_path_from_source,
    extract_functions_docstrings_from_file,
    load_docstrings,
    load_plots,
)

__all__ = [
    'register_functions_to_agents',
    'extract_file_path_from_source',
    'extract_functions_docstrings_from_file',
    'load_docstrings',
    'load_plots',
]
