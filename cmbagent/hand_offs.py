from autogen.agentchat.group import AgentTarget, TerminateTarget, OnCondition, StringLLMCondition
from autogen.cmbagent_utils import cmbagent_debug
import autogen
from autogen import GroupChatManager, GroupChat
from autogen.agentchat.contrib.capabilities.transform_messages import TransformMessages
from autogen.agentchat.contrib.capabilities.transforms import MessageHistoryLimiter

cmbagent_debug = autogen.cmbagent_utils.cmbagent_debug


def register_all_hand_offs(cmbagent_instance):
    """Register all agent handoffs in a data-driven, Pythonic way."""

    if cmbagent_debug:
        print('\nregistering all hand_offs...')

    mode = cmbagent_instance.mode

    # ============================================================================
    # 1. AGENT RETRIEVAL - Use dictionary comprehension for bulk retrieval
    # ============================================================================

    # Core agents (always needed)
    core_agent_names = [
        'planner', 'planner_response_formatter',
        'plan_recorder', 'plan_reviewer', 'reviewer_response_formatter', 'review_recorder',
        'idea_maker', 'idea_maker_response_formatter', 'idea_hater', 'idea_hater_response_formatter',
        'researcher', 'researcher_response_formatter', 'engineer', 'engineer_response_formatter',
        'summarizer', 'summarizer_response_formatter',
        'executor', 'researcher_executor', 'executor_bash', 'terminator', 'controller',
        'admin', 'aas_keyword_finder', 'executor_response_formatter',
        'plan_setter', 'installer', 'engineer_nest', 'idea_maker_nest', 'idea_saver',
        'camb_context',
        'camb_response_formatter'
    ]

    # Retrieve all core agents at once
    agents = {
        name: cmbagent_instance.get_agent_object_from_name(name)
        for name in core_agent_names
    }

    # ============================================================================
    # 2. SIMPLE HANDOFF CHAINS - Use data structure + loop
    # ============================================================================

    # Define simple A -> B handoff chains
    simple_handoffs = [
        # Planning flow
        ('plan_setter', 'planner'),
        ('planner', 'planner_response_formatter'),
        ('planner_response_formatter', 'plan_recorder'),
        ('plan_recorder', 'plan_reviewer'),
        ('plan_reviewer', 'reviewer_response_formatter'),
        ('reviewer_response_formatter', 'review_recorder'),
        ('review_recorder', 'planner'),

        # Coding and Execution flow
        ('engineer', 'engineer_nest'),
        ('engineer_nest', 'executor_response_formatter'),
        ('installer', 'executor_bash'),
        ('executor_bash', 'executor_response_formatter'),

        # Research flow
        ('researcher', 'researcher_response_formatter'),
        ('researcher_response_formatter', 'researcher_executor'),
        ('researcher_executor', 'controller'),

        # Summarizer flow
        ('summarizer', 'summarizer_response_formatter'),
        ('summarizer_response_formatter', 'terminator'),

        # Idea flow
        ('idea_hater', 'idea_hater_response_formatter'),
        ('idea_hater_response_formatter', 'controller'),
        ('idea_maker', 'idea_maker_nest'),
        ('idea_maker_nest', 'controller'),

        # Other flows
        ('aas_keyword_finder', 'controller'),


        # Context agents
        ('camb_context', 'camb_response_formatter'),
    ]

    # Apply simple handoffs
    for source, target in simple_handoffs:
        agents[source].agent.handoffs.set_after_work(AgentTarget(agents[target].agent))

    # ============================================================================
    # 3. CONDITIONAL HANDOFFS - Based on mode
    # ============================================================================

    # Response formatters that route differently based on mode
    mode_dependent_formatters = ['camb_response_formatter']
    target = 'engineer' if mode == "one_shot" else 'controller'

    for formatter in mode_dependent_formatters:
        agents[formatter].agent.handoffs.set_after_work(AgentTarget(agents[target].agent))

    # ============================================================================
    # 4. MESSAGE HISTORY LIMITING - Use list + loop
    # ============================================================================

    context_handling = TransformMessages(
        transforms=[MessageHistoryLimiter(max_messages=1)]
    )

    # Agents that need message history limiting
    limited_history_agents = [
        'executor_response_formatter', 'planner_response_formatter', 'plan_recorder',
        'reviewer_response_formatter', 'review_recorder', 'researcher_response_formatter',
        'researcher_executor', 'idea_maker_response_formatter', 'idea_hater_response_formatter',
        'summarizer_response_formatter'
    ]

    for agent_name in limited_history_agents:
        context_handling.add_to_agent(agents[agent_name].agent)

    # ============================================================================
    # 6. NESTED CHATS - Helper function to reduce duplication
    # ============================================================================

    def setup_nested_chat(trigger_agent, manager_name, chat_agents, max_round=3):
        """Helper to set up nested chat pattern."""
        group_chat = GroupChat(
            agents=[agents[name].agent for name in chat_agents],
            messages=[],
            max_round=max_round,
            speaker_selection_method='round_robin',
        )

        manager = GroupChatManager(
            groupchat=group_chat,
            llm_config=cmbagent_instance.llm_config,
            name=manager_name,
        )

        nested_chats = [{
            "recipient": manager,
            "message": lambda recipient, messages, sender, config: f"{messages[-1]['content']}" if messages else "",
            "max_turns": 1,
            "summary_method": "last_msg",
        }]

        # Create trigger: all agents except the trigger agent
        other_agents = [agent for agent in cmbagent_instance.agents if agent != agents[trigger_agent].agent]

        agents[f"{trigger_agent}_nest"].agent.register_nested_chats(
            trigger=lambda sender: sender not in other_agents,
            chat_queue=nested_chats
        )

    # Set up nested chats
    setup_nested_chat(
        trigger_agent='engineer',
        manager_name='engineer_nested_chat',
        chat_agents=['engineer_response_formatter', 'executor'],
        max_round=3
    )

    setup_nested_chat(
        trigger_agent='idea_maker',
        manager_name='idea_maker_manager',
        chat_agents=['idea_maker_response_formatter', 'idea_saver'],
        max_round=4
    )

    # ============================================================================
    # 7. TERMINATOR & CONTROLLER SETUP
    # ============================================================================

    # Terminator always terminates
    agents['terminator'].agent.handoffs.set_after_work(TerminateTarget())

    # Controller behavior depends on mode
    if mode == "human_in_the_loop":
        agent_on = cmbagent_instance.get_agent_object_from_name(cmbagent_instance.chat_agent)
        agents['controller'].agent.handoffs.set_after_work(AgentTarget(agents['admin'].agent))
        agents['admin'].agent.handoffs.set_after_work(AgentTarget(agent_on.agent))
    else:
        agents['controller'].agent.handoffs.set_after_work(AgentTarget(agents['terminator'].agent))

        # Controller LLM conditions - use data structure
        controller_conditions = [
            ('engineer', "Code execution failed."),
            ('researcher', "Researcher needed to generate reasoning, write report, or interpret results"),
            ('engineer', "Engineer needed to write code, make plots, do calculations."),
            ('idea_maker', "idea_maker needed to make new ideas"),
            ('idea_hater', "idea_hater needed to critique ideas"),
            ('terminator', "The task is completed."),
        ]

        agents['controller'].agent.handoffs.add_llm_conditions([
            OnCondition(
                target=AgentTarget(agents[target_agent].agent),
                condition=StringLLMCondition(prompt=prompt)
            )
            for target_agent, prompt in controller_conditions
        ])

    if cmbagent_debug:
        print('\nall hand_offs registered...')
