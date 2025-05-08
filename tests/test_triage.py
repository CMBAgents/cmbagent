from typing import Annotated
from autogen import ConversableAgent, LLMConfig
from autogen.agentchat import initiate_group_chat
from autogen.agentchat.group.patterns import AutoPattern
from autogen.agentchat.group import ReplyResult, AgentNameTarget

# Define the query classification tool
def classify_query(
    query: Annotated[str, "The user query to classify"]
) -> ReplyResult:
    """Classify a user query as technical or general."""
    technical_keywords = ["error", "bug", "broken", "crash", "not working", "shutting down"]

    if any(keyword in query.lower() for keyword in technical_keywords):
        return ReplyResult(
            message="This appears to be a technical issue. Routing to technical support...",
            target=AgentNameTarget("tech_agent")
        )
    else:
        return ReplyResult(
            message="This appears to be a general question. Routing to general support...",
            target=AgentNameTarget("general_agent")
        )

# Create the agents
# llm_config = LLMConfig(api_type="openai", model="gpt-4o-mini")
# llm_config = LLMConfig(api_type="google", model="gemini-2.5-pro-exp-03-25")
llm_config = LLMConfig(api_type="google", model="gemini-2.0-flash")

with llm_config:
    triage_agent = ConversableAgent(
        name="triage_agent",
        system_message="""You are a triage agent. For each user query,
        identify whether it is a technical issue or a general question.
        Use the classify_query tool to categorize queries and route them appropriately.
        Do not provide suggestions or answers, only route the query.""",
        functions=[classify_query]
    )

    tech_agent = ConversableAgent(
        name="tech_agent",
        system_message="""You solve technical problems like software bugs
        and hardware issues. After analyzing the problem, use the provide_technical_solution
        tool to format your response consistently."""
    )

    general_agent = ConversableAgent(
        name="general_agent",
        system_message="You handle general, non-technical support questions."
    )

# User agent
user = ConversableAgent(
    name="user",
    human_input_mode="ALWAYS"
)

# Set up the conversation pattern
pattern = AutoPattern(
    initial_agent=triage_agent,
    agents=[triage_agent, tech_agent, general_agent],
    user_agent=user,
    group_manager_args={"llm_config": llm_config}
)

# Run the chat
result, context, last_agent = initiate_group_chat(
    pattern=pattern,
    messages="My laptop keeps shutting down randomly. Can you help?",
    max_rounds=10
)