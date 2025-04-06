import re
import requests
from typing import List, Dict, Tuple

def arxiv_url_to_bib(citations: List[str]) -> Tuple[Dict[int, str], List[str]]:
    """
    Given a list of arXiv URLs, returns BibTeX keys and entries.

    Args:
        citations (List[str]): List of arXiv URLs (abs, pdf, or html variants allowed).

    Returns:
        Tuple[List[str], List[str]]:
            - A list of BibTeX keys (as strings).
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

def replace_grouped_citations(content: str, bib_keys: List[str]) -> str:
    """
    Replaces runs like [1][2][3] with a single sorted \cite{key1,key2,key3}, sorted by year.
    Works for single refs like [1] too.

    Args:
        content (str): The paragraph containing [N] citation markers (1-indexed).
        bib_keys (List[str]): List of BibTeX keys corresponding to citations (0-indexed).

    Returns:
        str: Updated content with grouped citations merged and sorted by year.
    """

    def extract_year(key: str) -> int:
        """Extracts a 4-digit year from a BibTeX key (or returns a large number if missing)."""
        match = re.search(r'\d{4}', key)
        return int(match.group()) if match else float('inf')

    def replacer(match):
        numbers = re.findall(r'\[(\d+)\]', match.group())  # ['1', '2', '3']
        keys = [bib_keys[int(n) - 1] for n in numbers]     # adjust for 1-indexed
        sorted_keys = sorted(keys, key=extract_year)
        return f"\\cite{{{','.join(sorted_keys)}}}"

    # Match sequences like [1][2][3]
    pattern = r'(?:\[\d+\])+'
    return re.sub(pattern, replacer, content)

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
    """
    bib_keys, bib_strs = arxiv_url_to_bib(citations)

    # Replace all references with \cite{bibkey}
    content = replace_grouped_citations(content, bib_keys)

    # Append all BibTeX entries to the .bib string
    bibtex_file_str = bibtex_file_str.rstrip() + '\n\n' + '\n\n'.join(bib_strs)

    return content, bibtex_file_str
