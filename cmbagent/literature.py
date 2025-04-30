import re
import requests
from typing import List, Dict, Tuple

# def process_tex_file_with_references(fname_tex, fname_bib, perplexity, nparagraphs=None):
#     """
#     Processes a LaTeX file by inserting `\\citep{}` references and generating a corresponding .bib file.

#     This pipeline:
#     - Loads a .tex file as a string.
#     - Extracts paragraph-like lines using `_extract_paragraphs_from_tex_content()`.
#     - Applies a `perplexity()` function to each paragraph to generate updated text and arXiv citations.
#     - Uses `_replace_references_with_cite()` to insert `\\citep{}` commands and update the BibTeX content.
#     - Replaces the corresponding lines in the original LaTeX string.
#     - Saves the modified .tex file (overwrites original).
#     - Writes a BibTeX file named `bibliography.bib`.

#     Args:
#         fname_tex (str): Path to the input .tex file. Example: 'main.tex'

#     Notes:
#         The BibTeX file name is hardcoded to 'bibliography.bib' to match the citation style expected in the LaTeX source.

#     TODO:
#         Replace perplexity placeholder.
#     """

#     with open(fname_tex, "r", encoding="utf-8") as f:
#         str_tex = f.read()
    
#     str_bib = ''                             # initialize str that will beocme the .bib file

#     para_dict = _extract_paragraphs_from_tex_content(str_tex)

#     count = 0
#     for kpara, para in para_dict.items():
#         if count == 0:
#             count += 1
#             continue  # skip the first paragraph

#         # Try calling perplexity up to two times.
#         for attempt in range(2):
#             para, citations = para, []#perplexity(para)
#             print(f"kpara: {kpara}")
#             print(f"Para: {para}")
#             print(f"Citations: {citations}")
#             if para is not None:
#                 break  # exit the loop if the call was successful
#         else:
#             # If after two attempts it still fails, skip this paragraph.
#             count += 1
#             continue
#         para, str_bib = _replace_references_with_cite(para, citations, str_bib)

#         lines = str_tex.splitlines(keepends=True)
#         lines[kpara] = para
#         str_tex = ''.join(lines)

#         count += 1
#         if nparagraphs is not None and count >= nparagraphs:
#             break
        
#     # (over)write files
#     with open(fname_tex, 'w', encoding='utf-8') as f:
#         f.write(str_tex)
#     with open(fname_bib, 'w', encoding='utf-8') as f:
#         f.write(str_bib)

def process_tex_file_with_references(fname_tex, fname_bib, perplexity, nparagraphs=None):
    """
    Processes a LaTeX file by inserting `\\citep{}` references and generating a corresponding .bib file.
    
    This pipeline:
      - Loads a .tex file as a list of lines.
      - Extracts paragraph-like lines using `_extract_paragraphs_from_tex_content()`, which returns a dict
        mapping 0-indexed line numbers to the corresponding line text.
      - For each identified paragraph, applies a perplexity function to generate updated text and citations.
      - Uses `_replace_references_with_cite()` to insert `\\citep{}` commands and update the BibTeX content.
      - Updates the corresponding line (using its original line index) in the list of lines.
      - Writes the modified .tex file and an updated bibliography file.
    
    Args:
        fname_tex (str): Path to the input .tex file.
        fname_bib (str): Path to the output .bib file.
        perplexity (callable): A function that processes a paragraph.
        nparagraphs (int, optional): Maximum number of paragraphs to process.
    """
    # Read file as a list of lines
    with open(fname_tex, "r", encoding="utf-8") as f:
        lines = f.readlines()
    
    # Join lines to get the full text for paragraph extraction
    full_text = ''.join(lines)
    para_dict = _extract_paragraphs_from_tex_content(full_text)
    
    str_bib = ''  # initialize string for the .bib file content
    count = 0
    
    # Iterate through the extracted paragraphs in order of their line numbers
    for kpara in sorted(para_dict.keys()):
        # Optionally skip the first paragraph (or any others)
        if count == 0:
            count += 1
            continue
        
        para = para_dict[kpara]
        print("\n\n")
        print('-'*100)
        print(f"kpara: {kpara}")
        print(f"Processing paragraph: {para}")
        
        # Try to process the paragraph using the perplexity function (placeholder shown here)
        for attempt in range(2):
            # Replace the following line with your actual perplexity call if needed.
            # new_para, citations = para, []  # e.g., new_para, citations = perplexity(para)
            new_para, citations = perplexity(para)
            if new_para is not None:
                break  # exit the retry loop if successful
        else:
            # Skip this paragraph if processing fails after two attempts
            count += 1
            continue
        
        # Replace citation markers in the paragraph and update the BibTeX content
        new_para, str_bib = _replace_references_with_cite(new_para, citations, str_bib)
        
        # Update the line in the list only if the line index is valid
        if kpara < len(lines):
            print(f"\nUpdating line: {lines[kpara]}")
            lines[kpara] = new_para
            print(f"\nwith paragraph: {new_para}")
        else:
            print(f"Warning: line index {kpara} is out of range (only {len(lines)} lines).")
        
        count += 1
        if nparagraphs is not None and count >= nparagraphs:
            break

    # Reassemble the text and write the updated files
    new_tex = ''.join(lines)
    with open(fname_tex, "w", encoding="utf-8") as f:
        f.write(new_tex)
    with open(fname_bib, "w", encoding="utf-8") as f:
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

# def _arxiv_url_to_bib(citations: List[str]) -> Tuple[Dict[int, str], List[str]]:
#     """
#     Given a list of arXiv URLs, returns BibTeX keys and entries.

#     Args:
#         citations (List[str]): List of arXiv URLs (abs, pdf, or html variants allowed).

#     Returns:
#         Tuple[List[str], List[str]]:
#             - A list of BibTeX keys (as strings).
#             - A list of full BibTeX entries (as strings) suitable for inclusion in a .bib file.
#     """
#     bib_keys = []
#     bib_strs = []

#     for i, url in enumerate(citations):
#         # Convert URL to bibtex url (e.g., from /abs/ or /html/ to /bibtex/)
#         bib_url = re.sub(r'\b(abs|html|pdf)\b', 'bibtex', url)

#         # Fetch BibTeX entry
#         response = requests.get(bib_url)
#         if response.status_code != 200:
#             raise ValueError(f"Failed to fetch BibTeX for URL: {url}")

#         bib_str = response.text.strip()

#         # Extract BibTeX key using regex
#         match = re.match(r'@[\w]+\{([^,]+),', bib_str)
#         if not match:
#             raise ValueError(f"Could not extract BibTeX key from: {bib_str[:100]}")

#         bib_key = match.group(1)
#         bib_keys.append(bib_key)
#         bib_strs.append(bib_str)

#     return bib_keys, bib_strs

def _arxiv_url_to_bib(citations: List[str]) -> Tuple[List[str], List[str]]:
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

    for url in citations:

        try:
            # Convert URL to bibtex url (e.g., from /abs/ or /html/ to /bibtex/)
            bib_url = re.sub(r'\b(abs|html|pdf)\b', 'bibtex', url)
            response = requests.get(bib_url)

            # If fetching fails, try the fallback using the arXiv ID
            if response.status_code != 200:
                # Extract arXiv id from the URL (matches patterns like 2010.07487)
                match_id = re.search(r'(\d{4}\.\d+)', url)
                if match_id:
                    arxiv_id = match_id.group(1)
                    fallback_url = f"https://arxiv.org/bibtex/{arxiv_id}"
                    response = requests.get(fallback_url)
                    if response.status_code != 200:
                        # Fallback failed; mark this citation as failed.
                        bib_keys.append(None)
                        continue
                else:
                    # Could not extract arXiv id; mark as failed.
                    bib_keys.append(None)
                    continue

            bib_str = response.text.strip()

            # Extract BibTeX key using regex
            match = re.match(r'@[\w]+\{([^,]+),', bib_str)
            if not match:
                # Could not extract key; mark as failed.
                bib_keys.append(None)
                continue

            bib_key = match.group(1)
            bib_keys.append(bib_key)
            bib_strs.append(bib_str)

        except Exception:
            bib_keys.append(None)
            continue

    return bib_keys, bib_strs

def _replace_grouped_citations(content: str, bib_keys: List[str]) -> str:
    """
    Replaces runs like [1][2][3] with a single sorted `\\citep{key1,key2,key3}`, sorted by year.
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
        # numbers = re.findall(r'\[(\d+)\]', match.group())  # ['1', '2', '3']
        # keys = [bib_keys[int(n) - 1] for n in numbers]     # adjust for 1-indexed
        # sorted_keys = sorted(keys, key=extract_year)
        # return f" \\citep{{{','.join(sorted_keys)}}}"
        numbers = re.findall(r'\[(\d+)\]', match.group())  # e.g. ['1', '2', '3']
        # Only include keys that were successfully fetched.
        keys = [bib_keys[int(n) - 1] for n in numbers if bib_keys[int(n) - 1] is not None]
        if not keys:
            return ""  # Remove citation markers if no valid keys exist.
        sorted_keys = sorted(keys, key=extract_year)
        return f" \\citep{{{','.join(sorted_keys)}}}"


    # Match sequences like [1][2][3]
    pattern = r'(?:\[\d+\])+'
    return re.sub(pattern, replacer, content)

def _replace_references_with_cite(content: str, citations: List[str], bibtex_file_str: str) -> Tuple[str, str]:
    """
    Replaces numeric reference markers like [1] in the content with LaTeX-style `\\citep{...}`,
    and appends corresponding BibTeX entries to the bibtex string.

    Args:
        content (str): A paragraph of text containing references like [1], [2], etc. (1-indexed).
        citations (List[str]): A list of arXiv URLs corresponding to the reference numbers. (0-indexed).
        bibtex_file_str (str): A string representing the contents of a .bib file.

    Returns:
        Tuple[str, str]:
            - The updated content with [N] replaced by `\\citep{BibTeXKey}`.
            - The updated BibTeX string with new entries appended.
    """
    bib_keys, bib_strs = _arxiv_url_to_bib(citations)

    # Replace all references with \citep{bibkey}
    content = _replace_grouped_citations(content, bib_keys)

    # Append all BibTeX entries to the .bib string
    bibtex_file_str = bibtex_file_str.rstrip() + '\n\n' + '\n\n'.join(bib_strs)

    return content, bibtex_file_str

### END OF ARXIV AND REFERENCES ###
