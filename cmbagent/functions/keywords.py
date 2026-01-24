"""Functions for AAS keyword extraction and recording."""

from typing import List
from autogen.agentchat.group import ContextVariables
from autogen.agentchat.group import AgentTarget, ReplyResult
from ..utils import AAS_keywords_dict


def create_record_aas_keywords(aas_keyword_finder, controller):
    """Factory function to create record_aas_keywords with agent references."""

    def record_aas_keywords(aas_keywords: List[str], context_variables: ContextVariables) -> ReplyResult:
        """
        Extracts the relevant AAS keywords from the list, given the text input.

        Args:
            aas_keywords (list[str]): The list of AAS keywords to be recorded
            context_variables (dict): A dictionary maintaining execution context, including previous plans,
                feedback tracking, and finalized plans.
        """

        for keyword in aas_keywords:
            if keyword not in AAS_keywords_dict:
                return ReplyResult(
                    target=AgentTarget(aas_keyword_finder),
                    message=f"Proposed keyword {keyword} not found in the list of AAS keywords. Extract keywords from provided AAS list!",
                    context_variables=context_variables
                )

        context_variables["aas_keywords"] = {
            f'{aas_keyword}': AAS_keywords_dict[aas_keyword] for aas_keyword in aas_keywords
        }

        AAS_keyword_list = "\n".join(
            [f"- [{keyword}]({AAS_keywords_dict[keyword]})" for keyword in aas_keywords]
        )

        return ReplyResult(
            target=AgentTarget(controller),
            message=f"""
**AAS keywords**:\n
{AAS_keyword_list}
""",
            context_variables=context_variables
        )

    return record_aas_keywords
