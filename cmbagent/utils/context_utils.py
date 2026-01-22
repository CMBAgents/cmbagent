"""Context fetching and management utilities.

This module provides helper functions for fetching and managing context
documentation from remote URLs.
"""

import requests
from typing import Dict, Optional


def fetch_context_from_url(url: str, timeout: int = 30) -> str:
    """Fetch context documentation from a URL.

    Parameters
    ----------
    url : str
        URL to fetch the context from
    timeout : int, optional
        Request timeout in seconds, by default 30

    Returns
    -------
    str
        The fetched text content

    Raises
    ------
    requests.HTTPError
        If the HTTP request fails (non-200 status code)
    requests.Timeout
        If the request times out
    requests.RequestException
        For other request-related errors

    Examples
    --------
    >>> context = fetch_context_from_url("https://example.com/docs.md")
    >>> print(len(context))
    12345
    """
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()  # Raises HTTPError for non-200 codes
    return resp.text


def add_contexts_from_urls(
    shared_context: Dict,
    contexts_config: Dict[str, str],
    timeout: int = 30
) -> None:
    """Fetch multiple contexts from URLs and add them to shared_context.

    This is a convenience function that fetches multiple context documents
    and adds them to the shared context dictionary. It's particularly useful
    for loading documentation for tools like CAMB, CLASS, etc.

    Parameters
    ----------
    shared_context : dict
        The shared context dictionary to update
    contexts_config : dict
        Dictionary mapping context keys to URLs.
        Example: {'camb_context': 'https://example.com/camb_docs.md'}
    timeout : int, optional
        Request timeout in seconds, by default 30

    Raises
    ------
    requests.HTTPError
        If any HTTP request fails
    requests.Timeout
        If any request times out

    Examples
    --------
    >>> shared_context = {}
    >>> contexts = {
    ...     'camb_context': 'https://example.com/camb.md',
    ...     'classy_context': 'https://example.com/classy.md'
    ... }
    >>> add_contexts_from_urls(shared_context, contexts)
    >>> 'camb_context' in shared_context
    True
    """
    for context_key, url in contexts_config.items():
        shared_context[context_key] = fetch_context_from_url(url, timeout=timeout)


def get_context_for_agent(
    agent: str,
    context_urls: Optional[Dict[str, str]] = None
) -> Optional[Dict[str, str]]:
    """Get the context configuration for a specific agent.

    This helper determines which context documents need to be fetched
    based on the agent type.

    Parameters
    ----------
    agent : str
        The agent name (e.g., 'camb_context', 'classy_context')
    context_urls : dict, optional
        Dictionary mapping agent names to their context URLs.
        If None, returns None.

    Returns
    -------
    dict or None
        Dictionary with single key-value pair for the context to fetch,
        or None if no context is needed for this agent

    Examples
    --------
    >>> from cmbagent.utils import camb_context_url, classy_context_url
    >>> urls = {
    ...     'camb_context': camb_context_url,
    ...     'classy_context': classy_context_url
    ... }
    >>> config = get_context_for_agent('camb_context', urls)
    >>> config
    {'camb_context': 'https://...'}
    """
    if context_urls is None or agent not in context_urls:
        return None
    return {agent: context_urls[agent]}
