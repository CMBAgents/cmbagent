import re
import requests
from typing import List, Dict, Tuple

def arxiv_url_to_bib(citations: List[str]) -> Tuple[Dict[int, str], List[str]]:
    """
    Given a list of arXiv URLs, returns BibTeX keys and entries.

    Args:
        citations (List[str]): List of arXiv URLs (abs, pdf, or html variants allowed).

    Returns:
        Tuple[Dict[int, str], List[str]]:
            - A dictionary mapping 1-indexed footnote reference numbers (e.g., 1, 2, ...)
              to their corresponding BibTeX keys.
            - A list of full BibTeX entries (as strings) suitable for inclusion in a .bib file.
    """
    bib_keys = []
    bib_strs = []

    for i, url in enumerate(citations):
        # Convert URL to bibtex url (e.g., from /abs/ or /html/ to /bibtex/)
        bib_url = re.sub(r'\b(abs|html|pdf)\b', 'bibtex', url)

        # Fetch BibTeX entry
        response = requests.get(bib_url)
        if response.status_code != 200:
            raise ValueError(f"Failed to fetch BibTeX for URL: {url}")

        bib_str = response.text.strip()

        # Extract BibTeX key using regex
        match = re.match(r'@[\w]+\{([^,]+),', bib_str)
        if not match:
            raise ValueError(f"Could not extract BibTeX key from: {bib_str[:100]}")

        bib_key = match.group(1)
        bib_keys.append(bib_key)
        bib_strs.append(bib_str)

    return bib_keys, bib_strs

def do_references(content: str, citations: List[str], bibtex_file_str: str) -> Tuple[str, str]:
    """
    Replaces numeric reference markers like [1] in the content with LaTeX-style \cite{...},
    and appends corresponding BibTeX entries to the bibtex string.

    Args:
        content (str): A paragraph of text containing references like [1], [2], etc. (1-indexed).
        citations (List[str]): A list of arXiv URLs corresponding to the reference numbers. (0-indexed).
        bibtex_file_str (str): A string representing the contents of a .bib file.

    Returns:
        Tuple[str, str]:
            - The updated content with [N] replaced by \cite{BibTeXKey}.
            - The updated BibTeX string with new entries appended.

    FIXME:
        This assumes citations appear as individual markers like [1], [2].
        If the LLM generates things like [1][2], you might want to combine them into \cite{key1,key2}.
    """
    bib_keys, bib_strs = arxiv_url_to_bib(citations)

    # Replace all [N] references with \cite{bibkey}
    for i, bib_key in enumerate(bib_keys):
        # Use word boundary to avoid accidental matches inside words
        content = re.sub(fr"\[{i+1}\]", r" \\cite{" + bib_key + "}",  content)   # note: 1-index

    # Append all BibTeX entries to the .bib string
    bibtex_file_str = bibtex_file_str.rstrip() + '\n\n' + '\n\n'.join(bib_strs)

    return content, bibtex_file_str
