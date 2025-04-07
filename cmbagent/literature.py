import re
import requests
from typing import List, Dict, Tuple

def process_tex_file_with_references(fname_tex, fname_bib, perplexity, nparagraphs=None):
    """
    Processes a LaTeX file by inserting `\\cite{}` references and generating a corresponding .bib file.

    This pipeline:
    - Loads a .tex file as a string.
    - Extracts paragraph-like lines using `_extract_paragraphs_from_tex_content()`.
    - Applies a `perplexity()` function to each paragraph to generate updated text and arXiv citations.
    - Uses `_replace_references_with_cite()` to insert `\\cite{}` commands and update the BibTeX content.
    - Replaces the corresponding lines in the original LaTeX string.
    - Saves the modified .tex file (overwrites original).
    - Writes a BibTeX file named `bibliography.bib`.

    Args:
        fname_tex (str): Path to the input .tex file. Example: 'main.tex'

    Notes:
        The BibTeX file name is hardcoded to 'bibliography.bib' to match the citation style expected in the LaTeX source.

    TODO:
        Replace perplexity placeholder.
    """

    with open(fname_tex, "r", encoding="utf-8") as f:
        str_tex = f.read()
    
    str_bib = ''                             # initialize str that will beocme the .bib file

    para_dict = _extract_paragraphs_from_tex_content(str_tex)

    for kpara, para in para_dict.items():
        # FIXME replace with real perplexity
        # perplexity = lambda x : (x, ['https://arxiv.org/abs/1708.01913', 'https://arxiv.org/html/2408.07749v1', 'http://arxiv.org/pdf/1307.1847', 'https://arxiv.org/abs/2407.12090', 'https://arxiv.org/abs/2111.01154', 'http://www.arxiv.org/pdf/2409.03523', 'https://arxiv.org/abs/1410.3485', 'https://arxiv.org/html/2410.00795v1', 'http://arxiv.org/pdf/1512.05356', 'https://arxiv.org/abs/2008.08582'])
        para, citations = perplexity(para)    # para is the paragprah after being passed thru perplexity. citations is list of citations
        para, str_bib = _replace_references_with_cite(para, citations, str_bib)

        # replace para in tex file with the new para
        lines = str_tex.splitlines(keepends=True)
        lines[kpara] = para
        str_tex = ''.join(lines)
        if nparagraphs is not None and kpara >= nparagraphs:
            break
    
    # (over)write files
    with open(fname_tex, 'w', encoding='utf-8') as f:
        f.write(str_tex)
    with open(fname_bib, 'w', encoding='utf-8') as f:
        f.write(str_bib)


def _extract_paragraphs_from_tex_content(tex_content: str) -> dict:
    """
    Returns a dictionary mapping 0-indexed line numbers to lines that are likely part of a paragraph.
    
    Args:
        tex_content (str): LaTeX source as a string.

    Returns:
        dict: {line_number: line_text} for lines considered paragraph content.
    """
    paragraph_lines = {}
    lines = tex_content.splitlines(keepends=True)

    for i, raw_line in enumerate(lines):
        line = raw_line.strip()

        if not line:
            continue

        if line.startswith('%'):
            continue

        if re.match(r'\\(begin|end|section|subsection|label|caption|ref|title|author|documentclass|usepackage|newcommand|section|subsection|subsubsection|affiliation|keywords|bibliography|centering|includegraphics)', line):
            continue

        if re.search(r'\\(item|enumerate)', line):    # consider removing?
            continue

        if re.search(r'(figure|table|equation|align|tabular)', line):
            continue

        if re.match(r'^\$.*\$$', line) or re.match(r'^\\\[.*\\\]$', line):
            continue

        paragraph_lines[i] = raw_line   # append the raw_line

    return paragraph_lines

#### ARXIV AND REFERENCES ####

def _arxiv_url_to_bib(citations: List[str]) -> Tuple[Dict[int, str], List[str]]:
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

def _replace_grouped_citations(content: str, bib_keys: List[str]) -> str:
    """
    Replaces runs like [1][2][3] with a single sorted `\\cite{key1,key2,key3}`, sorted by year.
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
        return f" \\cite{{{','.join(sorted_keys)}}}"

    # Match sequences like [1][2][3]
    pattern = r'(?:\[\d+\])+'
    return re.sub(pattern, replacer, content)

def _replace_references_with_cite(content: str, citations: List[str], bibtex_file_str: str) -> Tuple[str, str]:
    """
    Replaces numeric reference markers like [1] in the content with LaTeX-style `\\cite{...}`,
    and appends corresponding BibTeX entries to the bibtex string.

    Args:
        content (str): A paragraph of text containing references like [1], [2], etc. (1-indexed).
        citations (List[str]): A list of arXiv URLs corresponding to the reference numbers. (0-indexed).
        bibtex_file_str (str): A string representing the contents of a .bib file.

    Returns:
        Tuple[str, str]:
            - The updated content with [N] replaced by `\\cite{BibTeXKey}`.
            - The updated BibTeX string with new entries appended.
    """
    bib_keys, bib_strs = _arxiv_url_to_bib(citations)

    # Replace all references with \cite{bibkey}
    content = _replace_grouped_citations(content, bib_keys)

    # Append all BibTeX entries to the .bib string
    bibtex_file_str = bibtex_file_str.rstrip() + '\n\n' + '\n\n'.join(bib_strs)

    return content, bibtex_file_str

### END OF ARXIV AND REFERENCES ###
