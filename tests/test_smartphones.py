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

# Configure the LLM
llm_config = LLMConfig(api_type="openai", model="gpt-4o-mini")
# llm_config = LLMConfig(api_type="google", model="gemini-2.5-pro-exp-03-25")


# Create all our support agents
with llm_config:
    # Main triage agent - the starting point for all user queries
    triage_agent = ConversableAgent(
        name="triage_agent",
        system_message="""You are a support triage agent. Your role is to:
        1. Determine if a query is technical or general
        2. Use the classify_query function to route appropriately

        Do not attempt to solve issues yourself - your job is proper routing."""
    )

    # General support for non-technical questions
    general_agent = ConversableAgent(
        name="general_agent",
        system_message="""You are a general support agent who handles non-technical questions.
        If the user is a premium customer (check account_tier context variable),
        you should transfer them directly to the premium support agent.
        Otherwise, provide helpful responses to general inquiries."""
    )

    # Tech agent for initial technical assessment
    tech_agent = ConversableAgent(
        name="tech_agent",
        system_message="""You are a technical support agent who handles the initial assessment
        of all technical issues.

        If the user is a premium customer (check account_tier context variable),
        you should transfer them directly to the premium support agent.

        Otherwise, determine if the issue is related to:
        - Computer issues (laptops, desktops, PCs, Macs)
        - Smartphone issues (iPhones, Android phones, tablets)

        And route to the appropriate specialist."""
    )

    # Device-specific agents
    computer_agent = ConversableAgent(
        name="computer_agent",
        system_message="""You are a computer specialist who handles issues with laptops, desktops,
        PCs, and Macs. You provide troubleshooting for hardware and software issues specific to
        computers. You're knowledgeable about Windows, macOS, Linux, and common computer peripherals.

        For first-time issues, provide a solution directly.

        If a user returns and says they tried your solution but are still having the issue,
        use the check_repeat_issue function to escalate to advanced troubleshooting. Do not provide a solution yourself for returning users, simply route it to advanced troubleshooting."""
    )

    smartphone_agent = ConversableAgent(
        name="smartphone_agent",
        system_message="""You are a smartphone specialist who handles issues with mobile devices
        including iPhones, Android phones, and tablets. You're knowledgeable about iOS, Android,
        mobile apps, battery issues, screen problems, and connectivity troubleshooting.

        For first-time issues, provide a solution directly.

        If a user returns and says they tried your solution but are still having the issue,
        use the check_repeat_issue function to escalate to advanced troubleshooting. Do not provide a solution yourself for returning users, simply route it to advanced troubleshooting"""
    )

    # Advanced troubleshooting for complex issues
    advanced_troubleshooting_agent = ConversableAgent(
        name="advanced_troubleshooting_agent",
        system_message="""You are an advanced troubleshooting specialist who handles complex,
        persistent issues that weren't resolved by initial solutions. You provide deeper
        diagnostic approaches and more comprehensive solutions for difficult technical problems."""
    )

# Define tool functions
def classify_query(
    query: Annotated[str, "The user query to classify"],
    context_variables: ContextVariables
) -> ReplyResult:
    """Classify a user query and route to the appropriate agent."""
    # Update query count
    context_variables["query_count"] += 1

    # Simple classification logic
    technical_keywords = ["error", "bug", "broken", "crash", "not working", "shutting down",
                        "frozen", "blue screen", "won't start", "slow", "virus"]

    if any(keyword in query.lower() for keyword in technical_keywords):
        return ReplyResult(
            message="This appears to be a technical issue. Let me route you to our tech support team.",
            target=AgentTarget(tech_agent),
            context_variables=context_variables
        )
    else:
        return ReplyResult(
            message="This appears to be a general question. Let me connect you with our general support team.",
            target=AgentTarget(general_agent),
            context_variables=context_variables
        )

def check_repeat_issue(
    description: Annotated[str, "User's description of the continuing issue"],
    context_variables: ContextVariables
) -> ReplyResult:
    """Check if this is a repeat of an issue that wasn't resolved."""
    # Mark this as a repeat issue in the context
    context_variables["repeat_issue"] = True
    context_variables["continuing_issue"] = description

    return ReplyResult(
        message="I understand that your issue wasn't resolved. Let me connect you with our advanced troubleshooting specialist.",
        target=AgentTarget(advanced_troubleshooting_agent),
        context_variables=context_variables
    )

# Add tool functions to the appropriate agents
triage_agent.functions = [classify_query]
computer_agent.functions = [check_repeat_issue]
smartphone_agent.functions = [check_repeat_issue]

# Route based on device type
tech_agent.handoffs.add_llm_conditions([
    OnCondition(
        target=AgentTarget(computer_agent),
        condition=StringLLMCondition(prompt="Route to computer specialist when the issue involves laptops, desktops, PCs, or Macs."),
    ),
    OnCondition(
        target=AgentTarget(smartphone_agent),
        condition=StringLLMCondition(prompt="Route to smartphone specialist when the issue involves phones, mobile devices, iOS, or Android."),
    )
])

# For other tech issues, revert to user
tech_agent.handoffs.set_after_work(RevertToUserTarget())

# Configure handoffs for computer agent - for repeat issues
computer_agent.handoffs.add_context_conditions([
    OnContextCondition(
        target=AgentTarget(advanced_troubleshooting_agent),
        condition=ExpressionContextCondition(
            expression=ContextExpression("${repeat_issue} == True")
        )
    )
])

# For first-time issues, revert to user
# computer_agent.handoffs.set_after_work(RevertToUserTarget())

# Similarly for smartphone agent
smartphone_agent.handoffs.add_context_conditions([
    OnContextCondition(
        target=AgentTarget(advanced_troubleshooting_agent),
        condition=ExpressionContextCondition(
            expression=ContextExpression("${repeat_issue} == True")
        )
    )
])
# smartphone_agent.handoffs.set_after_work(RevertToUserTarget())

# Configure handoffs for advanced troubleshooting agent
advanced_troubleshooting_agent.handoffs.set_after_work(RevertToUserTarget())

general_agent.handoffs.set_after_work(RevertToUserTarget())

# Create the user agent
user = ConversableAgent(name="user", human_input_mode="ALWAYS")

# Set up the conversation pattern
pattern = AutoPattern(
    initial_agent=triage_agent,
    agents=[
        triage_agent,
        tech_agent,
        computer_agent,
        smartphone_agent,
        advanced_troubleshooting_agent,
        general_agent
    ],
    user_agent=user,
    context_variables=support_context,
    group_manager_args = {"llm_config": llm_config},
)

# Run the chat
result, final_context, last_agent = initiate_group_chat(
    pattern=pattern,
    messages="My laptop keeps shutting down randomly. Can you help?",
    max_rounds=15
)