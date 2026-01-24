"""Utility functions for file and plot handling."""

import os
import re
import ast
from pathlib import Path


def extract_file_path_from_source(source):
    """
    Extracts the file path from the top comment in the source code.
    Expects a line like: "# filename: codebase/module.py"

    Parameters:
        source (str): The source code of the file.

    Returns:
        str or None: The file path if found, else None.
    """
    match = re.search(r'^#\s*filename:\s*(.+)$', source, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return None


def extract_functions_docstrings_from_file(file_path):
    """
    Parses the given Python file and extracts docstrings from all top-level function
    definitions (including methods in classes) without capturing nested (internal) functions.
    Also extracts the file path from the file's top comment.

    Parameters:
        file_path (str): Path to the Python file.

    Returns:
        dict: A dictionary with two keys:
              - "file_path": the file path extracted from the comment.
              - "functions": a dictionary mapping function names to their docstrings.
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        source = f.read()

    # Extract the file path from the comment at the top of the file
    file_path_from_comment = extract_file_path_from_source(source)

    # Parse the AST
    tree = ast.parse(source, filename=file_path)
    functions = {}

    # Process only top-level statements
    for node in tree.body:
        # Capture top-level function definitions.
        if isinstance(node, ast.FunctionDef):
            functions[node.name] = ast.get_docstring(node)
        # Optionally, capture methods inside classes.
        elif isinstance(node, ast.ClassDef):
            for subnode in node.body:
                if isinstance(subnode, ast.FunctionDef):
                    qualified_name = f"{node.name}.{subnode.name}"
                    functions[qualified_name] = ast.get_docstring(subnode)

    return {"file_path": file_path_from_comment, "functions": functions}


def load_docstrings(directory: str = "codebase"):
    """
    Loads all top-level function docstrings from Python files in *directory*
    without executing any code.  If a file can't be parsed, its error is
    stored in an `"error"` field.
    """
    all_docstrings = {}

    for file in os.listdir(directory):
        if file.endswith(".py") and not file.startswith("__"):
            module     = file[:-3]                  # drop '.py'
            file_path  = os.path.join(directory, file)
            try:
                all_docstrings[module] = extract_functions_docstrings_from_file(file_path)
            except Exception as err:
                all_docstrings[module] = {
                    "file_path": file_path,
                    "functions": {},               # ALWAYS a dict
                    "error": f"{err.__class__.__name__}: {err}",
                }
    return all_docstrings


def load_plots(directory: str) -> list:
    """
    Recursively searches for image files (png, jpg, jpeg, gif) in directory and all subdirectories.
    Excludes checkpoint files from Jupyter notebooks.
    Returns plots sorted by modification time (oldest first).
    """
    image_extensions = ('.png', '.jpg', '.jpeg', '.gif')
    directory = Path(directory)
    image_files = [path for path in directory.rglob('*')
                   if path.suffix.lower() in image_extensions
                   and '.ipynb_checkpoints' not in str(path)]

    # Sort by modification time (oldest first)
    image_files.sort(key=lambda x: x.stat().st_mtime)

    return [str(path) for path in image_files]
