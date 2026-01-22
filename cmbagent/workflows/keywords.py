"""Keyword extraction workflows for CMBAgent.

This module provides various keyword extraction methods using different taxonomies
and classification systems including UNESCO, AAAI, and AAS (American Astronomical Society).
"""

import os
import json
import time
import datetime

from ..utils import (
    work_dir_default,
    get_api_keys_from_env,
    unesco_taxonomy_path,
    aaai_keywords_path,
    AAS_keywords_string
)
from ..utils.keywords_utils import UnescoKeywords, AaaiKeywords


def get_keywords(
    input_text: str,
    n_keywords: int = 5,
    work_dir=work_dir_default,
    api_keys=get_api_keys_from_env(),
    kw_type='unesco'
):
    """Get keywords from input text using various classification systems.

    This function extracts keywords from input text using different taxonomies.
    It supports UNESCO, AAAI, and AAS keyword systems.

    Parameters
    ----------
    input_text : str
        Text to extract keywords from
    n_keywords : int, optional
        Number of keywords to extract, by default 5
    work_dir : str, optional
        Working directory for outputs, by default work_dir_default
    api_keys : dict, optional
        API keys for model providers, by default fetched from environment
    kw_type : str, optional
        Type of keyword system to use ('unesco', 'aaai', 'aas'), by default 'unesco'

    Returns
    -------
    list
        List of extracted keywords

    Examples
    --------
    >>> from cmbagent.workflows import get_keywords
    >>> text = "This paper discusses machine learning algorithms for classification"
    >>> keywords = get_keywords(text, n_keywords=5, kw_type='unesco')
    >>> print(keywords)
    ['COMPUTER SCIENCE', 'ARTIFICIAL INTELLIGENCE', ...]
    """
    if kw_type == 'aas':
        return get_aas_keywords(input_text, n_keywords, work_dir, api_keys)
    elif kw_type == 'unesco':
        aggregated_keywords = []

        ukw = UnescoKeywords(unesco_taxonomy_path)
        keywords_string = ', '.join(ukw.get_unesco_level1_names())
        n_keywords_level1 = ukw.n_keywords_level1
        domains = get_keywords_from_string(input_text, keywords_string, n_keywords_level1, work_dir, api_keys)

        print('domains:')
        print(domains)
        domains.append('MATHEMATICS') if 'MATHEMATICS' not in domains else None
        aggregated_keywords.extend(domains)

        for domain in domains:
            print('inside domain: ', domain)
            if '&' in domain:
                domain = domain.replace('&', '\\&')
            keywords_string = ', '.join(ukw.get_unesco_level2_names(domain))
            n_keywords_level2 = ukw.n_keywords_level2
            sub_fields = get_keywords_from_string(input_text, keywords_string, n_keywords_level2, work_dir, api_keys)

            print('sub_fields:')
            print(sub_fields)
            aggregated_keywords.extend(sub_fields)

            for sub_field in sub_fields:
                print('inside sub_field: ', sub_field)
                keywords_string = ', '.join(ukw.get_unesco_level3_names(sub_field))
                n_keywords_level3 = ukw.n_keywords_level3
                specific_areas = get_keywords_from_string(input_text, keywords_string, n_keywords_level3, work_dir, api_keys)
                print('specific_areas:')
                print(specific_areas)
                aggregated_keywords.extend(specific_areas)

        aggregated_keywords = list(set(aggregated_keywords))
        keywords_string = ', '.join(aggregated_keywords)
        keywords = get_keywords_from_string(input_text, keywords_string, n_keywords, work_dir, api_keys)

        print('keywords in unesco:')
        print(keywords)
        return keywords
    elif kw_type == 'aaai':
        return get_keywords_from_aaai(input_text, n_keywords, work_dir, api_keys)


def get_keywords_from_aaai(
    input_text,
    n_keywords=6,
    work_dir=work_dir_default,
    api_keys=get_api_keys_from_env()
):
    """Extract keywords using AAAI taxonomy.

    Uses the AAAI (Association for the Advancement of Artificial Intelligence)
    keyword taxonomy to extract relevant keywords from input text.

    Parameters
    ----------
    input_text : str
        Text to extract keywords from
    n_keywords : int, optional
        Number of keywords to extract, by default 6
    work_dir : str, optional
        Working directory for outputs, by default work_dir_default
    api_keys : dict, optional
        API keys for model providers, by default fetched from environment

    Returns
    -------
    list
        List of AAAI keywords extracted from the text
    """
    # Import here to avoid circular dependency
    from ..cmbagent import CMBAgent

    start_time = time.time()
    cmbagent = CMBAgent(work_dir=work_dir, api_keys=api_keys)
    end_time = time.time()
    initialization_time = end_time - start_time

    PROMPT = f"""
    {input_text}
    """
    start_time = time.time()

    aaai_keywords = AaaiKeywords(aaai_keywords_path)
    keywords_string = aaai_keywords.aaai_keywords_string

    cmbagent.solve(
        task="Find the relevant keywords in the provided list",
        max_rounds=2,
        initial_agent='aaai_keywords_finder',
        mode="one_shot",
        shared_context={
            'text_input_for_AAS_keyword_finder': PROMPT,
            'AAS_keywords_string': keywords_string,
            'N_AAS_keywords': n_keywords,
        }
    )
    end_time = time.time()
    execution_time = end_time - start_time

    if not hasattr(cmbagent, 'groupchat'):
        Dummy = type('Dummy', (object,), {'new_conversable_agents': []})
        cmbagent.groupchat = Dummy()

    cmbagent.display_cost()

    # Save timing report as JSON
    timing_report = {
        'initialization_time': initialization_time,
        'execution_time': execution_time,
        'total_time': initialization_time + execution_time
    }

    # Add timestamp
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    # Save to JSON file in workdir
    timing_path = os.path.join(work_dir, f"timing_report_{timestamp}.json")
    with open(timing_path, 'w') as f:
        json.dump(timing_report, f, indent=2)

    # grab last user message with role "user"
    user_msg = next(
        (msg["content"] for msg in cmbagent.chat_result.chat_history if msg.get("role") == "user"),
        ""
    )

    # extract lines starting with a dash
    keywords = [line.lstrip("-").strip() for line in user_msg.splitlines() if line.startswith("-")]
    return keywords


def get_keywords_from_string(
    input_text,
    keywords_string,
    n_keywords,
    work_dir,
    api_keys
):
    """Extract keywords from a predefined list of keywords.

    Given a string of possible keywords, extracts the most relevant ones
    from the input text.

    Parameters
    ----------
    input_text : str
        Text to extract keywords from
    keywords_string : str
        Comma-separated string of possible keywords
    n_keywords : int
        Number of keywords to extract
    work_dir : str
        Working directory for outputs
    api_keys : dict
        API keys for model providers

    Returns
    -------
    list
        List of extracted keywords from the provided keywords_string
    """
    # Import here to avoid circular dependency
    from ..cmbagent import CMBAgent

    start_time = time.time()
    cmbagent = CMBAgent(work_dir=work_dir, api_keys=api_keys)
    end_time = time.time()
    initialization_time = end_time - start_time

    PROMPT = f"""
    {input_text}
    """
    start_time = time.time()

    cmbagent.solve(
        task="Find the relevant keywords in the provided list",
        max_rounds=2,
        initial_agent='list_keywords_finder',
        mode="one_shot",
        shared_context={
            'text_input_for_AAS_keyword_finder': PROMPT,
            'AAS_keywords_string': keywords_string,
            'N_AAS_keywords': n_keywords,
        }
    )
    end_time = time.time()
    execution_time = end_time - start_time

    if not hasattr(cmbagent, 'groupchat'):
        Dummy = type('Dummy', (object,), {'new_conversable_agents': []})
        cmbagent.groupchat = Dummy()

    cmbagent.display_cost()

    # Save timing report as JSON
    timing_report = {
        'initialization_time': initialization_time,
        'execution_time': execution_time,
        'total_time': initialization_time + execution_time
    }

    # Add timestamp
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    # Save to JSON file in workdir
    timing_path = os.path.join(work_dir, f"timing_report_{timestamp}.json")
    with open(timing_path, 'w') as f:
        json.dump(timing_report, f, indent=2)

    # grab last user message with role "user"
    user_msg = next(
        (msg["content"] for msg in cmbagent.chat_result.chat_history if msg.get("role") == "user"),
        ""
    )

    # extract lines starting with a dash
    keywords = [line.lstrip("-").strip() for line in user_msg.splitlines() if line.startswith("-")]
    return keywords


def get_aas_keywords(
    input_text: str,
    n_keywords: int = 5,
    work_dir=work_dir_default,
    api_keys=get_api_keys_from_env()
):
    """Extract keywords using AAS (American Astronomical Society) taxonomy.

    Uses the AAS keyword system to extract relevant astronomy and astrophysics
    keywords from input text.

    Parameters
    ----------
    input_text : str
        Text to extract keywords from
    n_keywords : int, optional
        Number of keywords to extract, by default 5
    work_dir : str, optional
        Working directory for outputs, by default work_dir_default
    api_keys : dict, optional
        API keys for model providers, by default fetched from environment

    Returns
    -------
    dict
        Dictionary of AAS keywords with URLs
    """
    # Import here to avoid circular dependency
    from ..cmbagent import CMBAgent

    start_time = time.time()
    cmbagent = CMBAgent(work_dir=work_dir, api_keys=api_keys)
    end_time = time.time()
    initialization_time = end_time - start_time

    PROMPT = f"""
    {input_text}
    """
    start_time = time.time()
    cmbagent.solve(
        task="Find the relevant AAS keywords",
        max_rounds=50,
        initial_agent='aas_keyword_finder',
        mode="one_shot",
        shared_context={
            'text_input_for_AAS_keyword_finder': PROMPT,
            'AAS_keywords_string': AAS_keywords_string,
            'N_AAS_keywords': n_keywords,
        }
    )
    end_time = time.time()
    execution_time = end_time - start_time
    aas_keywords = cmbagent.final_context['aas_keywords']  # here you get the dict with urls

    if not hasattr(cmbagent, 'groupchat'):
        Dummy = type('Dummy', (object,), {'new_conversable_agents': []})
        cmbagent.groupchat = Dummy()

    cmbagent.display_cost()

    # Save timing report as JSON
    timing_report = {
        'initialization_time': initialization_time,
        'execution_time': execution_time,
        'total_time': initialization_time + execution_time
    }

    # Add timestamp
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    # Save to JSON file in workdir
    timing_path = os.path.join(work_dir, f"timing_report_{timestamp}.json")
    with open(timing_path, 'w') as f:
        json.dump(timing_report, f, indent=2)

    print('aas_keywords: ', aas_keywords)

    return aas_keywords
