from autogen import  register_hand_off, AfterWork, OnCondition, AfterWorkOption, ConversableAgent
from autogen.cmbagent_utils import cmbagent_debug
from typing import Any, Dict, List
def register_all_hand_offs(cmbagent_instance):

    if cmbagent_debug:
        print('\nregistering all hand_offs...')

    planner = cmbagent_instance.get_agent_object_from_name('planner')
    planner_response_formatter = cmbagent_instance.get_agent_object_from_name('planner_response_formatter')
    plan_recorder = cmbagent_instance.get_agent_object_from_name('plan_recorder')
    plan_reviewer = cmbagent_instance.get_agent_object_from_name('plan_reviewer')
    reviewer_response_formatter = cmbagent_instance.get_agent_object_from_name('reviewer_response_formatter')
    review_recorder = cmbagent_instance.get_agent_object_from_name('review_recorder')
    researcher = cmbagent_instance.get_agent_object_from_name('researcher')
    researcher_response_formatter = cmbagent_instance.get_agent_object_from_name('researcher_response_formatter')
    engineer = cmbagent_instance.get_agent_object_from_name('engineer')
    engineer_response_formatter = cmbagent_instance.get_agent_object_from_name('engineer_response_formatter')
    classy_sz_response_formatter = cmbagent_instance.get_agent_object_from_name('classy_sz_response_formatter')
    executor = cmbagent_instance.get_agent_object_from_name('executor')
    control = cmbagent_instance.get_agent_object_from_name('control')
    admin = cmbagent_instance.get_agent_object_from_name('admin')
    paper_response_formatter = cmbagent_instance.get_agent_object_from_name('paper_response_formatter')

    # New rag agents (make sure these names match those in your agent_list/YAML)
    planck = cmbagent_instance.get_agent_object_from_name('planck_agent')
    tszpower = cmbagent_instance.get_agent_object_from_name('tszpower_agent')


    if cmbagent_debug:
        print('\nplanner: ', planner)
        print('\nplanner_response_formatter: ', planner_response_formatter)
        print('\nplan_recorder: ', plan_recorder)
        print('\nplan_reviewer: ', plan_reviewer)
        print('\nreviewer_response_formatter: ', reviewer_response_formatter)
        print('\nreview_recorder: ', review_recorder)
        print('\nresearcher: ', researcher)
        print('\nresearcher_response_formatter: ', researcher_response_formatter)
        print('\nengineer: ', engineer)
        print('\nengineer_response_formatter: ', engineer_response_formatter)
        print('\nclassy_sz_response_formatter: ', classy_sz_response_formatter)
        print('\npaper_response_formatter: ', paper_response_formatter)
        print('\nexecutor: ', executor)
        print('\ncontrol: ', control)
        print('\nadmin: ', admin)
        print('\nplanck: ', planck)
        print('\ntszpower: ', tszpower)


    #planner agent
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
            OnCondition(
                target=control.agent,
                condition="TRUE", ### keep this here as an example, will not actially be used because we bypass this now. 
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


    # control agent
    register_hand_off(
        agent = control.agent,
        hand_to = [
    
            OnCondition( 
                # condition (str): 
                # The condition for transitioning to the target agent, 
                # evaluated by the LLM to determine whether to call the underlying function/tool which does the transition.
                target=engineer.agent, 
                condition="Failed code execution that looks like a generic Python error. Fix this!",
                # available="review_recorded"
            ),
            
            OnCondition( 
                # condition (str): 
                # The condition for transitioning to the target agent, 
                # evaluated by the LLM to determine whether to call the underlying function/tool which does the transition.
                target=admin.agent, 
                condition="All steps in the plan have been fully implemented. Revert to user.",
                # available="code_approved"
            ),

            OnCondition( 
                # condition (str): 
                # The condition for transitioning to the target agent, 
                # evaluated by the LLM to determine whether to call the underlying function/tool which does the transition.
                target=researcher.agent, 
                condition="Generate proofs, reasoning, etc. Anything that does not require a code to be executed",
                # available="code_approved"
            ),

            
            OnCondition( 
                # condition (str): 
                # The condition for transitioning to the target agent, 
                # evaluated by the LLM to determine whether to call the underlying function/tool which does the transition.
                target=engineer.agent, 
                condition="Write code",
                # available="code_approved"
            ),
            OnCondition( 
                # condition (str): 
                # The condition for transitioning to the target agent, 
                # evaluated by the LLM to determine whether to call the underlying function/tool which does the transition.
                target=executor.agent, 
                condition="Execute code",
                # available="code_approved"
            ),

            OnCondition( 
                # condition (str): 
                # The condition for transitioning to the target agent, 
                # evaluated by the LLM to determine whether to call the underlying function/tool which does the transition.
                target=planck.agent, 
                condition="Code execution failed and error message that seems to involve Planck data not retrieved specifically, rather than a generic Python error.",
                # available="review_recorded"
            ),

            OnCondition(
                target=tszpower.agent,
                condition="Code execution failed and error message that seems to involve tszpower not retrieved specifically, rather than a generic Python error.",
            ),

            
            AfterWork(AfterWorkOption.STAY), # Handles the next step in the conversation when an agent doesn't suggest a tool call or a handoff
            # AFTER_WORK(plan_manager)
        ]
    )

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
    

    #classy_sz_response_formatter
    register_hand_off(
        agent = classy_sz_response_formatter.agent,
        hand_to = 
        [AfterWork(control.agent)])

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
            AfterWork(control.agent),
        ]
    )

    # New registrations for rag agents
    # For Planck agent: after Planck completes, hand off to classy_sz_response_formatter
    register_hand_off(
        agent = planck.agent,
        hand_to = [AfterWork(classy_sz_response_formatter.agent)]
    )

    # planck -> paper_response_formatter
    register_hand_off(
        agent = planck.agent,
        hand_to = 
        [AfterWork(paper_response_formatter.agent)])
    
    # paper_response_formatter -> control
    register_hand_off(
        agent = paper_response_formatter.agent,
        hand_to = 
        [AfterWork(control.agent)])

    # For tszpower agent: after tszpower completes, hand off to classy_sz_response_formatter
    register_hand_off(
        agent = tszpower.agent,
        hand_to = [AfterWork(classy_sz_response_formatter.agent)]
    )



    # Put admin transition just in case TODO: Should be removed in future
    register_hand_off(
        agent = admin.agent,
        hand_to = [AfterWork(planner.agent)]
    )
