from typing import Annotated
from autogen import ConversableAgent, LLMConfig
from autogen.agentchat import initiate_group_chat
from autogen.agentchat.group.patterns import AutoPattern
from autogen.agentchat.group import (
    ContextVariables, ReplyResult, AgentTarget,
    OnCondition, StringLLMCondition,
    OnContextCondition, ExpressionContextCondition, ContextExpression,
    RevertToUserTarget
)

# Initialize context variables for our support system
support_context = ContextVariables(data={
    "query_count": 0,
    "repeat_issue": False,
    "previous_solutions": [],
    "issue_type": "",
    "issue_subtype": "",
})
