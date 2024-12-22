
# Copyright (c) 2023 - 2024, Owners of https://github.com/ag2ai
#
# SPDX-License-Identifier: Apache-2.0
# from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple, Union
from cmbagent.base_agent import CmbAgentGroupChat, CmbSwarmAgent


from autogen import ChatResult, GroupChatManager, UserProxyAgent, AfterWorkOption, AFTER_WORK, ON_CONDITION, SwarmResult
# Parameter name for context variables
# Use the value in functions and they will be substituted with the context variables:
# e.g. def my_function(context_variables: Dict[str, Any], my_other_parameters: Any) -> Any:
__CONTEXT_VARIABLES_PARAM_NAME__ = "context_variables"


# class AfterWorkOption(Enum):
#     TERMINATE = "TERMINATE"
#     REVERT_TO_USER = "REVERT_TO_USER"
#     STAY = "STAY"


# @dataclass
# class AFTER_WORK:
#     agent: Union[AfterWorkOption, "CmbSwarmAgent", str, Callable]

#     def __post_init__(self):
#         if isinstance(self.agent, str):
#             self.agent = AfterWorkOption(self.agent.upper())


# @dataclass
# class ON_CONDITION:
#     target: Union["CmbSwarmAgent", Dict[str, Any]] = None
#     condition: str = ""

#     def __post_init__(self):
#         # Ensure valid types
#         if self.target is not None:
#             assert isinstance(self.target, CmbSwarmAgent) or isinstance(
#                 self.target, Dict
#             ), "'target' must be a CmbSwarmAgent or a Dict"

#         # Ensure they have a condition
#         assert isinstance(self.condition, str) and self.condition.strip(), "'condition' must be a non-empty string"


def initiate_cmb_swarm_chat(
    initial_agent: "CmbSwarmAgent",
    messages: Union[List[Dict[str, Any]], str],
    agents: List["CmbSwarmAgent"],
    rag_agents: List["CmbSwarmAgent"],
    send_introductions: bool,
    admin_name: str,
    user_agent: Optional[UserProxyAgent] = None,
    max_rounds: int = 20,
    context_variables: Optional[Dict[str, Any]] = None,
    after_work: Optional[Union[AFTER_WORK, Callable]] = AFTER_WORK(AfterWorkOption.TERMINATE),
    cost: int = 0,
    verbose: bool = False,
) -> Tuple[ChatResult, Dict[str, Any], "CmbSwarmAgent"]:
    """Initialize and run a swarm chat

    Args:
        initial_agent: The first receiving agent of the conversation.
        messages: Initial message(s).
        agents: List of swarm agents.
        user_agent: Optional user proxy agent for falling back to.
        max_rounds: Maximum number of conversation rounds.
        context_variables: Starting context variables.
        after_work: Method to handle conversation continuation when an agent doesn't select the next agent. If no agent is selected and no tool calls are output, we will use this method to determine the next agent.
            Must be a AFTER_WORK instance (which is a dataclass accepting a CmbSwarmAgent, AfterWorkOption, A str (of the AfterWorkOption)) or a callable.
            AfterWorkOption:
                - TERMINATE (Default): Terminate the conversation.
                - REVERT_TO_USER : Revert to the user agent if a user agent is provided. If not provided, terminate the conversation.
                - STAY : Stay with the last speaker.

            Callable: A custom function that takes the current agent, messages, groupchat, and context_variables as arguments and returns the next agent. The function should return None to terminate.
                ```python
                def custom_afterwork_func(last_speaker: CmbSwarmAgent, messages: List[Dict[str, Any]], groupchat: GroupChat, context_variables: Optional[Dict[str, Any]]) -> Optional[CmbSwarmAgent]:
                ```
    Returns:
        ChatResult:     Conversations chat history.
        Dict[str, Any]: Updated Context variables.
        CmbSwarmAgent:     Last speaker.
    """
    assert isinstance(initial_agent, CmbSwarmAgent), "initial_agent must be a CmbSwarmAgent"
    assert all(isinstance(agent, CmbSwarmAgent) for agent in agents), "Agents must be a list of CmbSwarmAgents"
    # Ensure all agents in hand-off after-works are in the passed in agents list
    for agent in agents:
        if agent.after_work is not None:
            if isinstance(agent.after_work.agent, CmbSwarmAgent):
                assert agent.after_work.agent in agents, "Agent in hand-off must be in the agents list"

    context_variables = context_variables or {}
    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]

    tool_execution = CmbSwarmAgent(
        name="Tool_Execution",
        system_message="Tool Execution"
    )
    tool_execution._set_to_tool_execution(context_variables=context_variables)

    INIT_AGENT_USED = False

    def swarm_transition(last_speaker: CmbSwarmAgent, groupchat: CmbAgentGroupChat):
        """Swarm transition function to determine the next agent in the conversation"""
        nonlocal INIT_AGENT_USED
        if not INIT_AGENT_USED:
            INIT_AGENT_USED = True
            return initial_agent

        if "tool_calls" in groupchat.messages[-1]:
            return tool_execution
        if tool_execution._next_agent is not None:
            next_agent = tool_execution._next_agent
            tool_execution._next_agent = None

            # Check for string, access agent from group chat.

            if isinstance(next_agent, str):
                if next_agent in swarm_agent_names:
                    next_agent = groupchat.agent_by_name(name=next_agent)
                else:
                    raise ValueError(
                        f"No agent found with the name '{next_agent}'. Ensure the agent exists in the swarm."
                    )

            return next_agent

        # get the last swarm agent
        last_swarm_speaker = None
        for message in reversed(groupchat.messages):
            if "name" in message and message["name"] in swarm_agent_names:
                agent = groupchat.agent_by_name(name=message["name"])
                if isinstance(agent, CmbSwarmAgent):
                    last_swarm_speaker = agent
                    break
        if last_swarm_speaker is None:
            raise ValueError("No swarm agent found in the message history")

        # If the user last spoke, return to the agent prior
        if (user_agent and last_speaker == user_agent) or groupchat.messages[-1]["role"] == "tool":
            return last_swarm_speaker

        # No agent selected via hand-offs (tool calls)
        # Assume the work is Done
        # override if agent-level after_work is defined, else use the global after_work
        tmp_after_work = last_swarm_speaker.after_work if last_swarm_speaker.after_work is not None else after_work
        if isinstance(tmp_after_work, AFTER_WORK):
            tmp_after_work = tmp_after_work.agent

        if isinstance(tmp_after_work, CmbSwarmAgent):
            return tmp_after_work
        elif isinstance(tmp_after_work, AfterWorkOption):
            if tmp_after_work == AfterWorkOption.TERMINATE or (
                user_agent is None and tmp_after_work == AfterWorkOption.REVERT_TO_USER
            ):
                return None
            elif tmp_after_work == AfterWorkOption.REVERT_TO_USER:
                return user_agent
            elif tmp_after_work == AfterWorkOption.STAY:
                return last_speaker
        elif isinstance(tmp_after_work, Callable):
            return tmp_after_work(last_speaker, groupchat.messages, groupchat, context_variables)
        else:
            raise ValueError("Invalid After Work condition")

    def create_nested_chats(agent: CmbSwarmAgent, nested_chat_agents: List[CmbSwarmAgent]):
        """Create nested chat agents and register nested chats"""
        for i, nested_chat_handoff in enumerate(agent._nested_chat_handoffs):
            nested_chats: Dict[str, Any] = nested_chat_handoff["nested_chats"]
            condition = nested_chat_handoff["condition"]

            # Create a nested chat agent specifically for this nested chat
            nested_chat_agent = CmbSwarmAgent(name=f"nested_chat_{agent.name}_{i + 1}")

            nested_chat_agent.register_nested_chats(
                nested_chats["chat_queue"],
                reply_func_from_nested_chats=nested_chats.get("reply_func_from_nested_chats")
                or "summary_from_nested_chats",
                config=nested_chats.get("config", None),
                trigger=lambda sender: True,
                position=0,
                use_async=nested_chats.get("use_async", False),
            )

            # After the nested chat is complete, transfer back to the parent agent
            nested_chat_agent.register_hand_off(AFTER_WORK(agent=agent))

            nested_chat_agents.append(nested_chat_agent)

            # Nested chat is triggered through an agent transfer to this nested chat agent
            agent.register_hand_off(ON_CONDITION(nested_chat_agent, condition))

    nested_chat_agents = []
    for agent in agents:
        create_nested_chats(agent, nested_chat_agents)

    # Update tool execution agent with all the functions from all the agents
    for agent in agents + nested_chat_agents:
        tool_execution._function_map.update(agent._function_map)

    swarm_agent_names = [agent.name for agent in agents + nested_chat_agents]

    # If there's only one message and there's no identified swarm agent
    # Start with a user proxy agent, creating one if they haven't passed one in
    if len(messages) == 1 and "name" not in messages[0] and not user_agent:
        temp_user_proxy = [UserProxyAgent(name="_User")]
    else:
        temp_user_proxy = []

    groupchat = CmbAgentGroupChat(
        agents = [tool_execution]
        + agents
        + nested_chat_agents
        + ([user_agent] if user_agent is not None else temp_user_proxy),
        rag_agents = rag_agents,
        messages = messages,  # Set to empty. We will resume the conversation with the messages,
        max_round = max_rounds,
        speaker_selection_method = swarm_transition,
        send_introductions = send_introductions,
        admin_name = admin_name,
        cost = cost,
        verbose = verbose
    )
    manager = GroupChatManager(groupchat)
    clear_history = True

    if len(messages) > 1:
        last_agent, last_message = manager.resume(messages=messages)
        clear_history = False
    else:
        last_message = messages[0]

        if "name" in last_message:
            if last_message["name"] in swarm_agent_names:
                # If there's a name in the message and it's a swarm agent, use that
                last_agent = groupchat.agent_by_name(name=last_message["name"])
            elif user_agent and last_message["name"] == user_agent.name:
                # If the user agent is passed in and is the first message
                last_agent = user_agent
            else:
                raise ValueError(f"Invalid swarm agent name in last message: {last_message['name']}")
        else:
            # No name, so we're using the user proxy to start the conversation
            if user_agent:
                last_agent = user_agent
            else:
                # If no user agent passed in, use our temporary user proxy
                last_agent = temp_user_proxy[0]

    chat_result = last_agent.initiate_chat(
        manager,
        message=last_message,
        clear_history=clear_history,
    )

    # Clear the temporary user proxy's name from messages
    if len(temp_user_proxy) == 1:
        for message in chat_result.chat_history:
            if "name" in message and message["name"] == "_User":
                # delete the name key from the message
                del message["name"]

    return chat_result, context_variables, manager.last_speaker, groupchat





# Forward references for SwarmAgent in SwarmResult
SwarmResult.update_forward_refs()
