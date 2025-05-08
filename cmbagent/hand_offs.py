from autogen.agentchat.group import AgentTarget, TerminateTarget, OnCondition, StringLLMCondition
from autogen.cmbagent_utils import cmbagent_debug
from typing import Any, Dict, List
import autogen
import os

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
    cmbagent_tool_executor = cmbagent_instance.get_agent_object_from_name('cmbagent_tool_executor')

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

    # Plan setter handoffs
    # plan_setter.agent.handoffs.set_after_work(AgentTarget(cmbagent_tool_executor.agent))
    
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
    engineer.agent.handoffs.set_after_work(AgentTarget(engineer_response_formatter.agent))

    # Engineer response formatter handoffs  
    engineer_response_formatter.agent.handoffs.set_after_work(AgentTarget(executor.agent))

    # Executor handoffs
    executor.agent.handoffs.set_after_work(AgentTarget(executor_response_formatter.agent))

    # Executor response formatter handoffs
    executor_response_formatter.agent.handoffs.set_after_work(AgentTarget(control.agent))

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
