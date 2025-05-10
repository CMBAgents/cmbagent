from autogen.agentchat.group import NestedChatTarget, AgentTarget, TerminateTarget, OnCondition, StringLLMCondition
from autogen.cmbagent_utils import cmbagent_debug
from typing import Any, Dict, List
import autogen
import os
from autogen import GroupChatManager, GroupChat
from autogen.agentchat.contrib.capabilities.transform_messages import TransformMessages
from autogen.agentchat.contrib.capabilities.transforms import MessageHistoryLimiter, MessageTokenLimiter

cmbagent_debug = autogen.cmbagent_utils.cmbagent_debug

def register_all_hand_offs(cmbagent_instance):
    if cmbagent_debug:
        print('\nregistering all hand_offs...')

    # Get all agent instances
    task_improver = cmbagent_instance.get_agent_object_from_name('task_improver')
    task_recorder = cmbagent_instance.get_agent_object_from_name('task_recorder')   
    planner = cmbagent_instance.get_agent_object_from_name('planner')
    planner_response_formatter = cmbagent_instance.get_agent_object_from_name('planner_response_formatter')
    plan_recorder = cmbagent_instance.get_agent_object_from_name('plan_recorder')
    plan_reviewer = cmbagent_instance.get_agent_object_from_name('plan_reviewer')
    reviewer_response_formatter = cmbagent_instance.get_agent_object_from_name('reviewer_response_formatter')
    review_recorder = cmbagent_instance.get_agent_object_from_name('review_recorder')
    idea_maker = cmbagent_instance.get_agent_object_from_name('idea_maker')
    idea_maker_response_formatter = cmbagent_instance.get_agent_object_from_name('idea_maker_response_formatter')
    idea_hater = cmbagent_instance.get_agent_object_from_name('idea_hater')
    idea_hater_response_formatter = cmbagent_instance.get_agent_object_from_name('idea_hater_response_formatter')
    researcher = cmbagent_instance.get_agent_object_from_name('researcher')
    researcher_response_formatter = cmbagent_instance.get_agent_object_from_name('researcher_response_formatter')
    engineer = cmbagent_instance.get_agent_object_from_name('engineer')
    engineer_response_formatter = cmbagent_instance.get_agent_object_from_name('engineer_response_formatter')
    classy_sz = cmbagent_instance.get_agent_object_from_name('classy_sz_agent')
    classy_sz_response_formatter = cmbagent_instance.get_agent_object_from_name('classy_sz_response_formatter')
    camb = cmbagent_instance.get_agent_object_from_name('camb_agent')
    camb_response_formatter = cmbagent_instance.get_agent_object_from_name('camb_response_formatter')
    planck = cmbagent_instance.get_agent_object_from_name('planck_agent')
    cobaya = cmbagent_instance.get_agent_object_from_name('cobaya_agent')
    cobaya_response_formatter = cmbagent_instance.get_agent_object_from_name('cobaya_response_formatter')
    executor = cmbagent_instance.get_agent_object_from_name('executor')
    terminator = cmbagent_instance.get_agent_object_from_name('terminator')
    control = cmbagent_instance.get_agent_object_from_name('control')
    perplexity = cmbagent_instance.get_agent_object_from_name('perplexity')
    admin = cmbagent_instance.get_agent_object_from_name('admin')
    aas_keyword_finder = cmbagent_instance.get_agent_object_from_name('aas_keyword_finder')
    executor_response_formatter = cmbagent_instance.get_agent_object_from_name('executor_response_formatter')
    plan_setter = cmbagent_instance.get_agent_object_from_name('plan_setter')
    executor_bash = cmbagent_instance.get_agent_object_from_name('executor_bash')
    installer = cmbagent_instance.get_agent_object_from_name('installer')
    engineer_nest = cmbagent_instance.get_agent_object_from_name('engineer_nest')
    mode = cmbagent_instance.mode


    # camb handoffs
    camb.agent.handoffs.set_after_work(AgentTarget(camb_response_formatter.agent))

    # camb response formatter handoffs
    camb_response_formatter.agent.handoffs.set_after_work(AgentTarget(control.agent))

    # classy_sz handoffs
    classy_sz.agent.handoffs.set_after_work(AgentTarget(classy_sz_response_formatter.agent))

    # classy_sz response formatter handoffs
    classy_sz_response_formatter.agent.handoffs.set_after_work(AgentTarget(control.agent))
    
    # cobaya handoffs
    cobaya.agent.handoffs.set_after_work(AgentTarget(cobaya_response_formatter.agent))

    # cobaya response formatter handoffs
    cobaya_response_formatter.agent.handoffs.set_after_work(AgentTarget(control.agent))


    # planck handoffs   
    planck.agent.handoffs.set_after_work(AgentTarget(control.agent))

    # Task improver handoffs
    task_improver.agent.handoffs.set_after_work(AgentTarget(task_recorder.agent))
    
    # Plan setter handoffs
    plan_setter.agent.handoffs.set_after_work(AgentTarget(planner.agent))

    # Task recorder handoffs
    task_recorder.agent.handoffs.set_after_work(AgentTarget(planner.agent))
    
    # Planner handoffs
    planner.agent.handoffs.set_after_work(AgentTarget(planner_response_formatter.agent))
    
    # Planner response formatter handoffs
    planner_response_formatter.agent.handoffs.set_after_work(AgentTarget(plan_recorder.agent))
    
    # Plan recorder handoffs
    plan_recorder.agent.handoffs.set_after_work(AgentTarget(plan_reviewer.agent))
    
    # Plan reviewer handoffs
    plan_reviewer.agent.handoffs.set_after_work(AgentTarget(reviewer_response_formatter.agent))
    
    # Reviewer response formatter handoffs
    reviewer_response_formatter.agent.handoffs.set_after_work(AgentTarget(review_recorder.agent))
    
    # Review recorder handoffs
    review_recorder.agent.handoffs.set_after_work(AgentTarget(planner.agent))
    
    # Installer handoffs
    installer.agent.handoffs.set_after_work(AgentTarget(executor_bash.agent))
    
    # Executor bash handoffs
    executor_bash.agent.handoffs.set_after_work(AgentTarget(executor_response_formatter.agent))

    # AAS keyword finder handoffs
    aas_keyword_finder.agent.handoffs.set_after_work(AgentTarget(control.agent))

    # Researcher handoffs
    researcher.agent.handoffs.set_after_work(AgentTarget(researcher_response_formatter.agent))

    # Researcher response formatter handoffs    
    researcher_response_formatter.agent.handoffs.set_after_work(AgentTarget(executor.agent))

    # Engineer handoffs
    # engineer.agent.handoffs.set_after_work(AgentTarget(engineer_response_formatter.agent))


    # Engineer response formatter handoffs  
    # engineer_response_formatter.agent.handoffs.set_after_work(AgentTarget(executor.agent))

    # Executor handoffs
    # executor.agent.handoffs.set_after_work(AgentTarget(executor_response_formatter.agent))

    # executor.agent.register_nested_chats(
    #     trigger=engineer_response_formatter.agent,
    #     chat_queue=[
    #         {
    #             # The initial message is the one received by the player agent from
    #             # the other player agent.
    #             "sender": executor.agent,
    #             "recipient": executor_response_formatter.agent,
    #             # The final message is sent to the player agent.
    #             "summary_method": "last_msg",
    #         }
    #     ],
    # )
    # executor.agent.handoffs.set_after_work(AgentTarget(engineer_nest.agent))

    # Executor response formatter handoffs # handled by function call. 
    # executor_response_formatter.agent.handoffs.set_after_work(AgentTarget(control.agent))

    # Create the group chat for collaborative lesson planning
    executor_chat = GroupChat(
        agents=[
                engineer_response_formatter.agent, 
                executor.agent, 
                ],
        messages=[],
        max_round=3,
        # send_introductions=True,
        speaker_selection_method = 'round_robin',
    )

    executor_manager = GroupChatManager(
        groupchat=executor_chat,
        llm_config=cmbagent_instance.llm_config,
        name="executor_manager",

    )

    context_handling = TransformMessages(
            transforms=[
                MessageHistoryLimiter(max_messages=1),
        ]
    )
    # context_handling.add_to_agent(executor_manager)
    context_handling.add_to_agent(executor_response_formatter.agent)
    # context_handling.add_to_agent(executor.agent)
    # context_handling.add_to_agent(engineer_response_formatter.agent)
    # context_handling.add_to_agent(engineer.agent)

    # context_handling = TransformMessages(
    #         transforms=[
    #             MessageHistoryLimiter(max_messages=1),
    #     ]
    # )


    # Create nested chats configuration
    nested_chats = [
        # {
        #     # The first internal chat formats the engineer response
        #     # A round of revisions is supported with max_turns = 2
        #     "recipient": engineer.agent,
        #     "message": lambda recipient, messages, sender, config: f"{messages[-1]['content']}",
        #     "max_turns": 1,
        #     "summary_method": "last_msg",
        # },

        {
            # The first internal chat formats the engineer response
            # A round of revisions is supported with max_turns = 2
            # "carryover_config": {"summary_method": "last_msg"},
            "recipient": executor_manager,
            "message": lambda recipient, messages, sender, config: f"{messages[-1]['content']}",
            "max_turns": 1,
            "summary_method": "last_msg",
        }#,
        # {
        #     # A group chat follows, where the lesson plan is created
        #     "recipient": executor.agent,
        #     # "message": "execute the code",
        #     "max_turns": 1,
        #     # "summary_method": "last_msg",
        # },
        # {
        #     # Finally, a two-agent chat formats the lesson plan
        #     # The result of this will be the lead_teacher_agent's reply
        #     "recipient": executor_response_formatter.agent,
        #     # "message": lambda recipient, messages, sender, config: f"{messages[-1]['content']}",
        #     "max_turns": 1,
        #     "summary_method": "last_msg",
        # }
    ]
    # nested_chat_config = {"chat_queue": nested_chats, "use_async": True}


   # create a list of all egents except the engineer:
    other_agents = [agent for agent in cmbagent_instance.agents if agent != engineer.agent]
    # print(other_agents)
    # import sys
    # sys.exit()
    # register the nested chats for the other agents:

    # engineer.agent.register_nested_chats(
    #     chat_queue=nested_chats,
    #     trigger=lambda sender: sender not in other_agents
    # )

    engineer_nest.agent.register_nested_chats(
    trigger=lambda sender: sender not in other_agents,
    chat_queue=nested_chats
    )

    engineer_nest.agent.handoffs.set_after_work(AgentTarget(executor_response_formatter.agent))

    engineer.agent.handoffs.set_after_work(AgentTarget(engineer_nest.agent))
    # engineer.agent.handoffs.set_after_work(NestedChatTarget(nested_chat_config=nested_chat_config))



    # nested_chat_one = {
    #     "carryover_config": {"summary_method": "last_msg"},  # Bring the last message into the chat
    #     "recipient": order_retrieval_agent,
    #     "message": extract_order_summary,  # Retrieve the status details of the order using the order id
    #     "max_turns": 1,  # Only one turn is necessary
    # }

    # nested_chat_two = {
    #     "recipient": order_summariser_agent,
    #     "message": "Summarise the order details provided in a Markdown table format with headings: Detail, Information",
    #     "max_turns": 1,
    #     "summary_method": "last_msg",  # Return their message back as the nested chat output
    # }

    # target={
    #     "chat_queue": chat_queue,
    # },

 
    # engineer.agent.register_nested_chats(
    #     chat_queue=nested_chats,
    #     trigger=lambda sender: sender not in [curriculum_agent, planning_manager, lesson_reviewer_agent],
    # )


    # idea maker handoffs
    idea_maker.agent.handoffs.set_after_work(AgentTarget(idea_maker_response_formatter.agent))

    # idea maker response formatter handoffs
    idea_maker_response_formatter.agent.handoffs.set_after_work(AgentTarget(control.agent))

    # idea haters handoffs
    idea_hater.agent.handoffs.set_after_work(AgentTarget(idea_hater_response_formatter.agent))

    # idea haters response formatter handoffs
    idea_hater_response_formatter.agent.handoffs.set_after_work(AgentTarget(control.agent))
    
    

    # Terminator handoffs
    terminator.agent.handoffs.set_after_work(TerminateTarget())


    if mode == "chat":

        agent_on = cmbagent_instance.get_agent_object_from_name(cmbagent_instance.chat_agent)

        # Control handoffs
        control.agent.handoffs.set_after_work(AgentTarget(admin.agent))

        # Admin handoffs
        admin.agent.handoffs.set_after_work(AgentTarget(agent_on.agent))


        
    else:  
  

        # Control handoffs
        control.agent.handoffs.set_after_work(AgentTarget(terminator.agent))

        control.agent.handoffs.add_llm_conditions([

            OnCondition(
                target=AgentTarget(engineer.agent),
                condition=StringLLMCondition(prompt="Code execution failed."),
            ),
            OnCondition(
                target=AgentTarget(camb.agent),
                condition=StringLLMCondition(prompt="Need camb_agent to find information on how to use the cosmology package camb."),
            ),
            OnCondition(
                target=AgentTarget(classy_sz.agent),
                condition=StringLLMCondition(prompt="Need classy_sz_agent to find information on how to use the cosmology package classy_sz."),
            ),
            OnCondition(
                target=AgentTarget(researcher.agent),
                condition=StringLLMCondition(prompt="Researcher needed to generate reasoning, write report, or interpret results"),
            ),
            OnCondition(
                target=AgentTarget(engineer.agent),
                condition=StringLLMCondition(prompt="Engineer needed to write code"),
            ),
            OnCondition(
                target=AgentTarget(idea_maker.agent),
                condition=StringLLMCondition(prompt="idea_maker needed to make new ideas"),
            ),
            OnCondition(
                target=AgentTarget(idea_hater.agent),
                condition=StringLLMCondition(prompt="idea_hater needed to critique ideas"),
            ),
            OnCondition(
                target=AgentTarget(terminator.agent),
                condition=StringLLMCondition(prompt="The task is complete."),
            ),
        ])


    if cmbagent_debug:
        print('\nall hand_offs registered...')
