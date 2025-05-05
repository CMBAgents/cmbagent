from autogen import  (register_hand_off, 
                      AfterWork, OnCondition, 
                      AfterWorkOption, ConversableAgent,
                      OnContextCondition, 
                      ContextExpression)
from autogen.cmbagent_utils import cmbagent_debug
from typing import Any, Dict, List
import autogen
from autogen import AssistantAgent, UserProxyAgent, LLMConfig
from autogen.tools.experimental import PerplexitySearchTool
import os
cmbagent_debug = autogen.cmbagent_debug

def register_all_hand_offs(cmbagent_instance):




    if cmbagent_debug:
        print('\nregistering all hand_offs...')

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
    if cmbagent_debug:
        print('\nplanner: ', planner)
        print('\nplanner_response_formatter: ', planner_response_formatter)
        print('\nplan_recorder: ', plan_recorder)
        print('\nplan_reviewer: ', plan_reviewer)
        print('\nreviewer_response_formatter: ', reviewer_response_formatter)
        print('\nreview_recorder: ', review_recorder)
        print('\nidea_maker: ', idea_maker)
        print('\nidea_maker_response_formatter: ', idea_maker_response_formatter)
        print('\nidea_hater: ', idea_hater)
        print('\nidea_hater_response_formatter: ', idea_hater_response_formatter)
        print('\nresearcher: ', researcher)
        print('\nresearcher_response_formatter: ', researcher_response_formatter)
        print('\nengineer: ', engineer)
        print('\nengineer_response_formatter: ', engineer_response_formatter)
        print('\nclassy_sz: ', classy_sz)
        print('\nclassy_sz_response_formatter: ', classy_sz_response_formatter)
        print('\ncamb: ', camb)
        print('\ncamb_response_formatter: ', camb_response_formatter)
        print('\ncobaya: ', cobaya)
        print('\ncobaya_response_formatter: ', cobaya_response_formatter)
        print('\nexecutor: ', executor)
        print('\nterminator: ', terminator)
        print('\ncontrol: ', control)
        print('\nadmin: ', admin)
        print('\nperplexity: ', perplexity)
        print('\ntask_improver: ', task_improver)
        print('\naas_keyword_finder: ', aas_keyword_finder)
        print('\nexecutor_response_formatter: ', executor_response_formatter)
        print('\nplan_setter: ', plan_setter)


    mode = cmbagent_instance.mode
    if mode == "chat":
        # print(f"Chat mode is on. Chat agent is {cmbagent_instance.chat_agent}")
        agent_on = cmbagent_instance.get_agent_object_from_name(cmbagent_instance.chat_agent)
        
        #task_improver agent
        register_hand_off(
            agent = task_improver.agent,
            hand_to = [
                AfterWork(task_recorder.agent),
            ]
        )

        #plan_setter agent
        register_hand_off(
            agent = plan_setter.agent,
            hand_to = [
                AfterWork(planner.agent),
            ]
        )

        #task_recorder agent
        register_hand_off(
            agent = task_recorder.agent,
            hand_to = [
                AfterWork(planner.agent),
            ]
        )
        register_hand_off(
            agent = planner.agent,
            hand_to = [
                AfterWork(planner_response_formatter.agent),
            ]
        )

        #planner_response_formatter agent
        register_hand_off(
            agent = planner_response_formatter.agent,
            hand_to = 
            [AfterWork(plan_recorder.agent)])

        #plan_recorder agent

        register_hand_off(
            agent = plan_recorder.agent,
            hand_to = [
                AfterWork(plan_reviewer.agent),
            ]
        )


        #plan_reviewer agent
        def no_feedback_left(agent: ConversableAgent, messages: List[Dict[str, Any]]) -> bool:
            feedback_left = agent.get_context("feedback_left")
            # print(f"evaluating condition....{feedback_left}")
            return feedback_left <= 0

        register_hand_off(
            agent = plan_reviewer.agent,
            hand_to = [
                ## example to force a function call 
                ## see get_function_name_if_description_true in ag2 client.py
                OnCondition(
                    target=control.agent,
                    condition="TRUE", ### keep this here as an example, will not actially be used because we bypass this now, as we transit to control via a different handoff. 
                    available=no_feedback_left,
                ),
                AfterWork(reviewer_response_formatter.agent)
            ]
        )

        #reviewer_response_formatter agent
        register_hand_off(
            agent = reviewer_response_formatter.agent,
            hand_to = [
                AfterWork(review_recorder.agent)
            ]
        )

        #review_recorder agent
        register_hand_off(
            agent = review_recorder.agent,
            hand_to = [
                AfterWork(planner.agent),
            ]
        )

        register_hand_off(
            agent = installer.agent,
            hand_to = [
                AfterWork(executor_bash.agent),
            ]
        )

        register_hand_off(
            agent = executor_bash.agent,
            hand_to = [
                AfterWork(executor_response_formatter.agent),
            ]
        )

        #perplexity agent
        # config_list = LLMConfig(check_every_ms = None,api_type="openai", model="gpt-4o-mini")
        # perplexity_search_agent = AssistantAgent(
        # name="perplexity_search_agent",
        # llm_config=config_list,
        # )

        # perplexity_search_tool = PerplexitySearchTool(
        #                     api_key=os.getenv("PERPLEXITY_API_KEY"),
        #                     max_tokens=1000,
        #                     # search_domain_filter=["arxiv.org", "towardsdatascience.com"],
        #                 )

        # perplexity_search_tool.register_for_execution(perplexity.agent)
        # perplexity_search_tool.register_for_llm(perplexity.agent)

        # print(cmbagent_instance.agents)

        # nested_chat_one = {
        # "carryover_config": {"summary_method": "last_msg"},  # Bring the last message into the chat
        # "recipient": perplexity.agent,
        # "message": "perform the search",  # Retrieve the status details of the order using the order id
        # "max_turns": 1,  # Only one turn is necessary
        # }
        # chat_queue = [nested_chat_one]
        register_hand_off(
            agent = aas_keyword_finder.agent,
            hand_to = [
                AfterWork(control.agent),
            ]
        )

        # register_hand_off(
        #     agent = perplexity.agent,
        #     hand_to = [
        #         AfterWork(control.agent),
        #         #  OnCondition(
        #         #             target={
        #         #                     "chat_queue": chat_queue,
        #         #                     },
        #         #             condition="TRUE"
        #         #             )
        #         OnCondition(
        #             target=control.agent,
        #             condition="Literature search completed.", ### keep this here as an example, will not actially be used because we bypass this now, as we transit to control via a different handoff. 
        #         ),
        #     ]
        # )

        # control agent
        register_hand_off(
            agent = control.agent,
            hand_to = [
                # AfterWork(AfterWorkOption.TERMINATE), # Handles the next step in the conversation when an agent doesn't suggest a tool call or a handoff
                # AfterWork(AfterWorkOption.REVERT_TO_USER),
                AfterWork(admin.agent)
            ]
        )
            # # If the user is not logged in, transfer to the authentication agent
            # OnCondition(
            #     target=authentication_agent,
            #     condition="The customer is not logged in, authenticate the customer.",
            #     available=ContextExpression("!(${logged_in})"),
            # ),

            # OnContextCondition(
            #     target=order_triage_agent,
            #     condition="logged_in",
            # ),

        #engineer
        register_hand_off(
            agent = engineer.agent,
            hand_to = [
                AfterWork(engineer_response_formatter.agent),
            ]
        )

        #engineer_response_formatter
        register_hand_off(
            agent = engineer_response_formatter.agent,
            hand_to = 
            [AfterWork(executor.agent)])


        #classy_sz
        register_hand_off(
            agent = classy_sz.agent,
            hand_to = [
                AfterWork(classy_sz_response_formatter.agent),
            ]
        )

        #classy_sz_response_formatter
        register_hand_off(
            agent = classy_sz_response_formatter.agent,
            hand_to = 
            [AfterWork(control.agent)])


        #cobaya
        register_hand_off(
            agent = cobaya.agent,
            hand_to = [
                AfterWork(cobaya_response_formatter.agent),
            ]
        )

        #camb_response_formatter
        register_hand_off(
            agent = cobaya_response_formatter.agent,
            hand_to = 
            [AfterWork(control.agent)])

        #camb
        register_hand_off(
            agent = camb.agent,
            hand_to = [
                AfterWork(camb_response_formatter.agent),
            ]
        )

        #camb_response_formatter
        register_hand_off(
            agent = camb_response_formatter.agent,
            hand_to = 
            [AfterWork(control.agent)])

        #planck
        register_hand_off(
            agent = planck.agent,
            hand_to = [
                AfterWork(control.agent),
            ]
        )
        #researcher 
        register_hand_off(
            agent = researcher.agent,
            hand_to = [
                AfterWork(researcher_response_formatter.agent),
            ]
        )

        #researcher_response_formatter
        register_hand_off(
            agent = researcher_response_formatter.agent,
            hand_to = 
            [AfterWork(executor.agent)])

        #executor
        register_hand_off(
            agent = executor.agent,
            hand_to = [
                AfterWork(executor_response_formatter.agent),
            ]
        )

        #executor_response_formatter
        register_hand_off(
            agent = executor_response_formatter.agent,
            hand_to = [
                AfterWork(control.agent),
            ]
        )

        #idea maker 
        register_hand_off(
            agent = idea_maker.agent,
            hand_to = [
                AfterWork(idea_maker_response_formatter.agent),
            ]
        )

        #idea maker response formatter
        register_hand_off(
            agent = idea_maker_response_formatter.agent,
            hand_to = [
                AfterWork(control.agent),     # control?
            ]
        )

        #idea hater 
        register_hand_off(
            agent = idea_hater.agent,
            hand_to = [
                AfterWork(idea_hater_response_formatter.agent),
            ]
        )

        #idea hater response formatter
        register_hand_off(
            agent = idea_hater_response_formatter.agent,
            hand_to = [
                AfterWork(control.agent),     # control?
            ]
        )

        register_hand_off(
            agent = admin.agent,
            hand_to = [
                AfterWork(agent_on.agent)
            ]
        )



    else:
        #task_improver agent
        register_hand_off(
            agent = task_improver.agent,
            hand_to = [
                AfterWork(task_recorder.agent),
            ]
        )

        #plan_setter agent
        register_hand_off(
            agent = plan_setter.agent,
            hand_to = [
                AfterWork(planner.agent),
            ]
        )

        #task_recorder agent
        register_hand_off(
            agent = task_recorder.agent,
            hand_to = [
                AfterWork(planner.agent),
            ]
        )
        register_hand_off(
            agent = planner.agent,
            hand_to = [
                AfterWork(planner_response_formatter.agent),
            ]
        )

        #planner_response_formatter agent
        register_hand_off(
            agent = planner_response_formatter.agent,
            hand_to = 
            [AfterWork(plan_recorder.agent)])

        #plan_recorder agent

        register_hand_off(
            agent = plan_recorder.agent,
            hand_to = [
                AfterWork(plan_reviewer.agent),
            ]
        )


        #plan_reviewer agent
        def no_feedback_left(agent: ConversableAgent, messages: List[Dict[str, Any]]) -> bool:
            feedback_left = agent.get_context("feedback_left")
            # print(f"evaluating condition....{feedback_left}")
            return feedback_left <= 0

        register_hand_off(
            agent = plan_reviewer.agent,
            hand_to = [
                ## example to force a function call 
                ## see get_function_name_if_description_true in ag2 client.py
                OnCondition(
                    target=control.agent,
                    condition="TRUE", ### keep this here as an example, will not actially be used because we bypass this now, as we transit to control via a different handoff. 
                    available=no_feedback_left,
                ),
                AfterWork(reviewer_response_formatter.agent)
            ]
        )

        #reviewer_response_formatter agent
        register_hand_off(
            agent = reviewer_response_formatter.agent,
            hand_to = [
                AfterWork(review_recorder.agent)
            ]
        )

        #review_recorder agent
        register_hand_off(
            agent = review_recorder.agent,
            hand_to = [
                AfterWork(planner.agent),
            ]
        )

        register_hand_off(
            agent = installer.agent,
            hand_to = [
                AfterWork(executor_bash.agent),
            ]
        )

        register_hand_off(
            agent = executor_bash.agent,
            hand_to = [
                AfterWork(executor_response_formatter.agent),
            ]
        )

        #perplexity agent
        # config_list = LLMConfig(check_every_ms = None,api_type="openai", model="gpt-4o-mini")
        # perplexity_search_agent = AssistantAgent(
        # name="perplexity_search_agent",
        # llm_config=config_list,
        # )

        # perplexity_search_tool = PerplexitySearchTool(
        #                     api_key=os.getenv("PERPLEXITY_API_KEY"),
        #                     max_tokens=1000,
        #                     # search_domain_filter=["arxiv.org", "towardsdatascience.com"],
        #                 )

        # perplexity_search_tool.register_for_execution(perplexity.agent)
        # perplexity_search_tool.register_for_llm(perplexity.agent)

        # print(cmbagent_instance.agents)

        # nested_chat_one = {
        # "carryover_config": {"summary_method": "last_msg"},  # Bring the last message into the chat
        # "recipient": perplexity.agent,
        # "message": "perform the search",  # Retrieve the status details of the order using the order id
        # "max_turns": 1,  # Only one turn is necessary
        # }
        # chat_queue = [nested_chat_one]
        register_hand_off(
            agent = aas_keyword_finder.agent,
            hand_to = [
                AfterWork(control.agent),
            ]
        )

        # register_hand_off(
        #     agent = perplexity.agent,
        #     hand_to = [
        #         AfterWork(control.agent),
        #         #  OnCondition(
        #         #             target={
        #         #                     "chat_queue": chat_queue,
        #         #                     },
        #         #             condition="TRUE"
        #         #             )
        #         OnCondition(
        #             target=control.agent,
        #             condition="Literature search completed.", ### keep this here as an example, will not actially be used because we bypass this now, as we transit to control via a different handoff. 
        #         ),
        #     ]
        # )

        # control agent
        register_hand_off(
            agent = control.agent,
            hand_to = [

                OnCondition( 
                    # condition (str): 
                    # The condition for transitioning to the target agent, 
                    # evaluated by the LLM to determine whether to call the underlying function/tool which does the transition.
                    target=perplexity.agent, 
                    condition="Need perplexity agent to find information on existing scientific literature.",
                    # available="review_recorded"
                ),

                OnCondition( 
                    # condition (str): 
                    # The condition for transitioning to the target agent, 
                    # evaluated by the LLM to determine whether to call the underlying function/tool which does the transition.
                    target=engineer.agent, 
                    condition="Execution failed.",

                    # condition="Failed code execution that looks like a generic Python error. Fix this!",
                    # available="review_recorded"
                ),

                # OnCondition( 
                #     # condition (str): 
                #     # The condition for transitioning to the target agent, 
                #     # evaluated by the LLM to determine whether to call the underlying function/tool which does the transition.
                #     target=engineer.agent, 
                #     condition="Failed code execution.",

                #     # condition="Failed code execution that looks like a generic Python error. Fix this!",
                #     # available="review_recorded"
                # ),
                
                # OnCondition( 
                #     # condition (str): 
                #     # The condition for transitioning to the target agent, 
                #     # evaluated by the LLM to determine whether to call the underlying function/tool which does the transition.
                #     target=cobaya.agent, 
                #     condition="Need information on the cosmolology code cobaya.",
                #     # available="review_recorded"
                # ),
                
                # OnCondition( 
                #     # condition (str): 
                #     # The condition for transitioning to the target agent, 
                #     # evaluated by the LLM to determine whether to call the underlying function/tool which does the transition.
                #     target=cobaya.agent, 
                #     condition="Code execution failed and error message that seems to involve the cosmolology code cobaya specifically, rather than a generic Python error.",
                #     # available="review_recorded"
                # ),



                OnCondition( 
                    # condition (str): 
                    # The condition for transitioning to the target agent, 
                    # evaluated by the LLM to determine whether to call the underlying function/tool which does the transition.
                    target=camb.agent, 
                    condition="Need camb_agent to find information on how to use the cosmology package camb.",
                    # available="review_recorded"
                ),

                # OnCondition( 
                #     # condition (str): 
                #     # The condition for transitioning to the target agent, 
                #     # evaluated by the LLM to determine whether to call the underlying function/tool which does the transition.
                #     target=camb.agent, 
                #     condition="Code execution failed and error message from COBAYA but which refers to the cosmolology code CAMB specifically.",
                #     # available="review_recorded"
                # ),



                # OnCondition( 
                #     # condition (str): 
                #     # The condition for transitioning to the target agent, 
                #     # evaluated by the LLM to determine whether to call the underlying function/tool which does the transition.
                #     target=camb.agent, 
                #     condition="Code execution failed and error message that seems to involve the cosmolology code CAMB specifically (like CAMBError), rather than a generic Python error.",
                #     # available="review_recorded"
                # ),

                OnCondition( 
                    # condition (str): 
                    # The condition for transitioning to the target agent, 
                    # evaluated by the LLM to determine whether to call the underlying function/tool which does the transition.
                    target=classy_sz.agent, 
                    condition="Need classy_sz_agent to find information on how to use the cosmology package classy_sz.",
                    # available="review_recorded"
                ),
                
                # OnCondition( 
                #     # condition (str): 
                #     # The condition for transitioning to the target agent, 
                #     # evaluated by the LLM to determine whether to call the underlying function/tool which does the transition.
                #     target=classy_sz.agent, 
                #     condition="Code execution failed and error message that seems to involve the cosmolology code classy_sz specifically, rather than a generic Python error.",
                #     # available="review_recorded"
                # ),

                
                # OnCondition( 
                #     # condition (str): 
                #     # The condition for transitioning to the target agent, 
                #     # evaluated by the LLM to determine whether to call the underlying function/tool which does the transition.
                #     target=terminator.agent, 
                #     condition="**ALL** steps in the plan have been fully and successfully implemented. Terminate.",
                #     # available="code_approved"
                # ),

                OnCondition( 
                    # condition (str): 
                    # The condition for transitioning to the target agent, 
                    # evaluated by the LLM to determine whether to call the underlying function/tool which does the transition.
                    target=researcher.agent, 
                    condition="Researcher needed to generate reasoning, write report, or interpret results",
                    # available="code_approved"
                ),

                
                OnCondition( 
                    # condition (str): 
                    # The condition for transitioning to the target agent, 
                    # evaluated by the LLM to determine whether to call the underlying function/tool which does the transition.
                    target=engineer.agent, 
                    condition="Engineer needed to write code",
                    # available="code_approved"
                ),
                # OnCondition( 
                #     # condition (str): 
                #     # The condition for transitioning to the target agent, 
                #     # evaluated by the LLM to determine whether to call the underlying function/tool which does the transition.
                #     target=executor.agent, 
                #     condition="Executor needed to execute code",
                #     # available="code_approved"
                # ),

                OnCondition( 
                    # condition (str): 
                    # The condition for transitioning to the target agent, 
                    # evaluated by the LLM to determine whether to call the underlying function/tool which does the transition.
                    target=idea_maker.agent, 
                    condition="idea_maker needed to make new ideas",
                    # available="code_approved"
                ),

                OnCondition( 
                    # condition (str): 
                    # The condition for transitioning to the target agent, 
                    # evaluated by the LLM to determine whether to call the underlying function/tool which does the transition.
                    target=idea_hater.agent, 
                    condition="idea_hater needed to critique ideas",
                    # available="code_approved"
                ),
                
                # AfterWork(AfterWorkOption.TERMINATE), # Handles the next step in the conversation when an agent doesn't suggest a tool call or a handoff
                AfterWork(terminator.agent)
            ]
        )
            # # If the user is not logged in, transfer to the authentication agent
            # OnCondition(
            #     target=authentication_agent,
            #     condition="The customer is not logged in, authenticate the customer.",
            #     available=ContextExpression("!(${logged_in})"),
            # ),

            # OnContextCondition(
            #     target=order_triage_agent,
            #     condition="logged_in",
            # ),

        #engineer
        register_hand_off(
            agent = engineer.agent,
            hand_to = [
                AfterWork(engineer_response_formatter.agent),
            ]
        )

        #engineer_response_formatter
        register_hand_off(
            agent = engineer_response_formatter.agent,
            hand_to = 
            [AfterWork(executor.agent)])


        #classy_sz
        register_hand_off(
            agent = classy_sz.agent,
            hand_to = [
                AfterWork(classy_sz_response_formatter.agent),
            ]
        )

        #classy_sz_response_formatter
        register_hand_off(
            agent = classy_sz_response_formatter.agent,
            hand_to = 
            [AfterWork(control.agent)])


        #cobaya
        register_hand_off(
            agent = cobaya.agent,
            hand_to = [
                AfterWork(cobaya_response_formatter.agent),
            ]
        )

        #camb_response_formatter
        register_hand_off(
            agent = cobaya_response_formatter.agent,
            hand_to = 
            [AfterWork(control.agent)])

        #camb
        register_hand_off(
            agent = camb.agent,
            hand_to = [
                AfterWork(camb_response_formatter.agent),
            ]
        )

        #camb_response_formatter
        register_hand_off(
            agent = camb_response_formatter.agent,
            hand_to = 
            [AfterWork(control.agent)])
        
        #planck
        register_hand_off(
            agent = planck.agent,
            hand_to = [
                AfterWork(control.agent),
            ]
        )

        #researcher 
        register_hand_off(
            agent = researcher.agent,
            hand_to = [
                AfterWork(researcher_response_formatter.agent),
            ]
        )

        #researcher_response_formatter
        register_hand_off(
            agent = researcher_response_formatter.agent,
            hand_to = 
            [AfterWork(executor.agent)])

        #executor
        register_hand_off(
            agent = executor.agent,
            hand_to = [
                AfterWork(executor_response_formatter.agent),
            ]
        )

        #executor_response_formatter
        register_hand_off(
            agent = executor_response_formatter.agent,
            hand_to = [
                AfterWork(control.agent),
            ]
        )

        #idea maker 
        register_hand_off(
            agent = idea_maker.agent,
            hand_to = [
                AfterWork(idea_maker_response_formatter.agent),
            ]
        )

        #idea maker response formatter
        register_hand_off(
            agent = idea_maker_response_formatter.agent,
            hand_to = [
                AfterWork(control.agent),     # control?
            ]
        )

        #idea hater 
        register_hand_off(
            agent = idea_hater.agent,
            hand_to = [
                AfterWork(idea_hater_response_formatter.agent),
            ]
        )

        #idea hater response formatter
        register_hand_off(
            agent = idea_hater_response_formatter.agent,
            hand_to = [
                AfterWork(control.agent),     # control?
            ]
        )
