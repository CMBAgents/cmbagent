from autogen import SwarmResult, AfterWorkOption
from typing import Literal
from pydantic import Field
import os
import re
import ast
from autogen.cmbagent_utils import cmbagent_debug
from IPython.display import Image as IPImage, display as ip_display
from IPython.display import Markdown
from autogen.cmbagent_utils import IMG_WIDTH
import autogen
from autogen.tools.experimental import PerplexitySearchTool
from .utils import AAS_keywords_dict

cmbagent_debug = autogen.cmbagent_debug
cmbagent_disable_display = autogen.cmbagent_disable_display


def register_functions_to_agents(cmbagent_instance):
    '''
    This function registers the functions to the agents.
    '''
    task_recorder = cmbagent_instance.get_agent_from_name('task_recorder')
    task_improver = cmbagent_instance.get_agent_from_name('task_improver')
    planner = cmbagent_instance.get_agent_from_name('planner')
    planner_response_formatter = cmbagent_instance.get_agent_from_name('planner_response_formatter')
    plan_recorder = cmbagent_instance.get_agent_from_name('plan_recorder')
    plan_reviewer = cmbagent_instance.get_agent_from_name('plan_reviewer')
    reviewer_response_formatter = cmbagent_instance.get_agent_from_name('reviewer_response_formatter')
    review_recorder = cmbagent_instance.get_agent_from_name('review_recorder')
    researcher = cmbagent_instance.get_agent_from_name('researcher')
    researcher_response_formatter = cmbagent_instance.get_agent_from_name('researcher_response_formatter')
    engineer = cmbagent_instance.get_agent_from_name('engineer')
    engineer_response_formatter = cmbagent_instance.get_agent_from_name('engineer_response_formatter')
    classy_sz = cmbagent_instance.get_agent_from_name('classy_sz_agent')
    classy_sz_response_formatter = cmbagent_instance.get_agent_from_name('classy_sz_response_formatter')
    camb = cmbagent_instance.get_agent_from_name('camb_agent')
    camb_response_formatter = cmbagent_instance.get_agent_from_name('camb_response_formatter')
    planck = cmbagent_instance.get_agent_from_name('planck_agent')
    executor = cmbagent_instance.get_agent_from_name('executor')
    executor_response_formatter = cmbagent_instance.get_agent_from_name('executor_response_formatter')
    terminator = cmbagent_instance.get_agent_from_name('terminator')
    control = cmbagent_instance.get_agent_from_name('control')
    admin = cmbagent_instance.get_agent_from_name('admin')
    perplexity = cmbagent_instance.get_agent_from_name('perplexity')
    aas_keyword_finder = cmbagent_instance.get_agent_from_name('aas_keyword_finder')
    plan_setter = cmbagent_instance.get_agent_from_name('plan_setter')
    idea_maker = cmbagent_instance.get_agent_from_name('idea_maker')
    installer = cmbagent_instance.get_agent_from_name('installer')
    # print("Perplexity API key: ", os.getenv("PERPLEXITY_API_KEY"))
    # perplexity_search_tool = PerplexitySearchTool(
    #                     model="sonar-reasoning-pro",
    #                     api_key=os.getenv("PERPLEXITY_API_KEY"),
    #                     max_tokens=100000,
    #                     search_domain_filter=["arxiv.org"]
    #                 )
    
    # perplexity_search_tool.register_for_llm(perplexity)
    # perplexity_search_tool.register_for_execution(perplexity)

    # perplexity._add_single_function(perplexity_search_tool)

    def post_execution_transfer(next_agent_suggestion: Literal["engineer", 
                                                               "classy_sz_agent", 
                                                               "installer",
                                                               "camb_agent", 
                                                               "cobaya_agent",
                                                            #    "planck_agent", no need for paper agents
                                                               "control"], 
                                context_variables: dict,
                                execution_status: Literal["success", "failure"] 
                                ) -> SwarmResult:
        """
        Transfer to the next agent based on the execution status.
        For the next agent suggestion, follow these rules:
            - Suggest the engineer agent if error related to generic Python code.
            - Suggest the installer agent if error related to missing Python modules (i.e., ModuleNotFoundError).
            - Suggest the classy_sz_agent if error is an internal classy_sz error.
            - Suggest the camb_agent if error related to internal camb code.
            - Suggest the cobaya_agent if error related to internal cobaya code.
            - Suggest the control agent only if execution was successful.
        """

        workflow_status_str = rf"""
xxxxxxxxxxxxxxxxxxxxxxxxxx

Workflow status:

Plan step number: {context_variables["current_plan_step_number"]}

Agent for sub-task (might be different from the next agent suggestion for debugging): {context_variables["agent_for_sub_task"]}

Current status (before execution): {context_variables["current_status"]}

xxxxxxxxxxxxxxxxxxxxxxxxxx
"""
        
        if context_variables["agent_for_sub_task"] == "engineer":
            
            if context_variables["n_attempts"] >= context_variables["max_n_attempts"]:
                return SwarmResult(agent=AfterWorkOption.TERMINATE,
                                values=f"Max number of code execution attempts ({context_variables['max_n_attempts']}) reached. Exiting.",
                                context_variables=context_variables)
            
            if execution_status == "success":
                return SwarmResult(agent=control,
                                values="Execution status: " + execution_status + ". Transfer to control.\n" + f"{workflow_status_str}\n",
                                context_variables=context_variables)

            if next_agent_suggestion == "engineer":
                context_variables["n_attempts"] += 1
                return SwarmResult(agent=engineer,
                                values="Execution status: " + execution_status + ". Transfer to engineer.\n" + f"{workflow_status_str}\n",
                                context_variables=context_variables)    
            elif next_agent_suggestion == "classy_sz_agent":
                context_variables["n_attempts"] += 1
                return SwarmResult(agent=classy_sz,
                                values="Execution status: " + execution_status + ". Transfer to classy_sz_agent.\n" + f"{workflow_status_str}\n",
                                context_variables=context_variables)
            
            elif next_agent_suggestion == "control":
                context_variables["n_attempts"] += 1
                return SwarmResult(agent=control,
                                values="Execution status: " + execution_status + ". Transfer to control.\n" + f"{workflow_status_str}\n",
                                context_variables=context_variables)
            
            elif next_agent_suggestion == "installer":
                context_variables["n_attempts"] += 1
                return SwarmResult(agent=installer,
                                values="Execution status: " + execution_status + ". Transfer to installer.\n" + f"{workflow_status_str}\n",
                                context_variables=context_variables)
        else:
                return SwarmResult(agent=control,
                                values="Transfer to control.\n" + workflow_status_str,
                                context_variables=context_variables)
        
    executor_response_formatter._add_single_function(post_execution_transfer)




    def terminate_session(context_variables: dict) -> SwarmResult:
        """
        Terminate the session.
        """

        ## do things to context_variables
        # context_variables["improved_main_task"] = improved_main_task


        return SwarmResult(agent=AfterWorkOption.TERMINATE, ## transfer to planner
                            values="Session terminated.",
                            context_variables=context_variables)


    terminator._add_single_function(terminate_session)


    def record_improved_task(improved_main_task: str,  context_variables: dict) -> SwarmResult:
        """
        Records the improved main task.
        """


        context_variables["improved_main_task"] = improved_main_task

        if not cmbagent_disable_display:
            display(Markdown(improved_main_task))
        else:
            print(improved_main_task)


        return SwarmResult(agent=planner, ## transfer to planner
                            values="Improved main task has been logged. Now, suggest a plan, planner!",
                            context_variables=context_variables)


    task_recorder._add_single_function(record_improved_task)


    def record_aas_keywords(aas_keywords: list[str], context_variables: dict) -> SwarmResult:
        """
        Extracts the relevant AAS keywords from the list, given the text input.
        Args:
            aas_keywords (list[str]): The list of AAS keywords to be recorded
            context_variables (dict): A dictionary maintaining execution context, including previous plans, 
                feedback tracking, and finalized plans.
        """
        
        # print('aas_keywords: ', aas_keywords)

        for keyword in aas_keywords:
            if keyword not in AAS_keywords_dict:
                return SwarmResult(agent=aas_keyword_finder, ## transfer to control
                                values=f"Proposed keyword {keyword} not found in the list of AAS keywords. Extract keywords from provided AAS list!",
                                context_variables=context_variables)
            
        context_variables["aas_keywords"] = {f'{aas_keyword}': AAS_keywords_dict[aas_keyword] for aas_keyword in aas_keywords}
            
        AAS_keyword_list = "\n".join(
                            [f"- [{keyword}]({AAS_keywords_dict[keyword]})" for keyword in aas_keywords]
                        )

        return SwarmResult(agent=AfterWorkOption.TERMINATE, ## transfer to control
                        values=f"""
**AAS keywords**:\n
{AAS_keyword_list}
""",
                        context_variables=context_variables)

        # import sys
        # sys.exit()

        # context_variables["proposed_plan"] = plan_suggestion

        # context_variables["number_of_steps_in_plan"] = number_of_steps_in_plan

        # if context_variables["feedback_left"]==0:
        #     context_variables["final_plan"] = context_variables["plans"][-1]
        #     return SwarmResult(agent=AfterWorkOption.TERMINATE, ## transfer to control
        #                     values="Planning stage complete. Exiting.",
        #                     context_variables=context_variables)
        # else:
        #     return SwarmResult(agent=plan_reviewer, ## transfer to plan reviewer
        #                     values="Plan has been logged.",
        #                     context_variables=context_variables)


    aas_keyword_finder._add_single_function(record_aas_keywords)



    def record_plan(plan_suggestion: str, number_of_steps_in_plan: int, context_variables: dict) -> SwarmResult:
        """
        Records a suggested plan and updates relevant execution context.

        This function logs a full plan suggestion into the `context_variables` dictionary. If no feedback 
        remains to be given (i.e., `context_variables["feedback_left"] == 0`), the most recent plan 
        suggestion is marked as the final plan. The function also updates the total number of steps in 
        the plan.

        The function ensures that the plan is properly stored and transferred to the `plan_reviewer` agent 
        for further evaluation.

        Args:
            plan_suggestion (str): The complete plan suggestion to be recorded.
            number_of_steps_in_plan (int): The total number of **Steps** in the suggested plan.
            context_variables (dict): A dictionary maintaining execution context, including previous plans, 
                feedback tracking, and finalized plans.
        """
        context_variables["plans"].append(plan_suggestion)

        context_variables["proposed_plan"] = plan_suggestion

        context_variables["number_of_steps_in_plan"] = number_of_steps_in_plan

        if context_variables["feedback_left"]==0:
            context_variables["final_plan"] = context_variables["plans"][-1]
            return SwarmResult(agent=AfterWorkOption.TERMINATE, ## transfer to control
                            values="Planning stage complete. Exiting.",
                            context_variables=context_variables)
        else:
            return SwarmResult(agent=plan_reviewer, ## transfer to plan reviewer
                            values="Plan has been logged.",
                            context_variables=context_variables)


    plan_recorder._add_single_function(record_plan)


    def record_plan_constraints(needed_agents: list[Literal["engineer", 
                                                       "researcher", 
                                                    #    "perplexity", 
                                                       "idea_maker", 
                                                       "idea_hater", 
                                                       "camb_agent",
                                                       "classy_sz_agent", 
                                                       "planck_agent",
                                                       "aas_keyword_finder"]],
                                context_variables: dict) -> SwarmResult:
        """
        Records the constraints on the plan.
        """
        # print('needed_agents: ', needed_agents)
        context_variables["needed_agents"] = needed_agents
        # print('planner_append_instructions before update: ', context_variables["planner_append_instructions"])
        # print('plan_reviewer_append_instructions before update: ', context_variables["plan_reviewer_append_instructions"])
        # print(dir(idea_maker))

        str_to_append = f"The plan must strictly involve only the following agents: {', '.join(needed_agents)}\n"
        
        str_to_append += r"""
**AGENT ROLES**
Here are the descriptions of the agents that are needed to carry out the plan:
"""
        for agent in set(needed_agents):
            agent_object = cmbagent_instance.get_agent_from_name(agent)

            str_to_append += f'- {agent}: {agent_object.description}'

        str_to_append += "\n"

        str_to_append += r"""
You must not invoke any other agent than the ones listed above.
"""
        context_variables["planner_append_instructions"] += str_to_append
        context_variables["plan_reviewer_append_instructions"] += str_to_append

        # print('planner_append_instructions after update: ', context_variables["planner_append_instructions"])
        # print('plan_reviewer_append_instructions after update: ', context_variables["plan_reviewer_append_instructions"])
        return SwarmResult(agent=planner,
                           values="Plan constraints have been logged.",
                           context_variables=context_variables)

    plan_setter._add_single_function(record_plan_constraints)


    def record_review(plan_review: str, context_variables: dict) -> SwarmResult:
        """ Record reviews of the plan."""
        context_variables["reviews"].append(plan_review)
        context_variables["feedback_left"] -= 1

        context_variables["recommendations"] = plan_review

        # if context_variables["feedback_left"]


        # Controlling the flow to the next agent from a tool call
        # if context_variables["reviews_left"] < 0:
        #     context_variables["plan_recorded"] = True
        #     return SwarmResult(agent=plan_manager,
        #                        values="No further recommendations to be made on the plan. Update plan and proceed",
        #                        context_variables=context_variables)
        # else:
        return SwarmResult(agent=planner,  ## transfer back to planner
                        values=f"""
Recommendations have been logged.  
Number of feedback rounds left: {context_variables["feedback_left"]}. 
Now, update the plan accordingly, planner!""",
                        
                        context_variables=context_variables)


    review_recorder._add_single_function(record_review)


    def record_status(
        current_status: Literal["in progress", "failed", "completed"],
        current_plan_step_number: int,
        current_sub_task: str,
        current_instructions: str,
        agent_for_sub_task: Literal["engineer", "researcher", #"perplexity", 
                                    "idea_maker", "idea_hater", "classy_sz_agent", "camb_agent", "aas_keyword_finder", "planck_agent"],
        context_variables: dict
    ) -> SwarmResult:
        """
        Updates the execution context and returns the current progress.
        Must be called **before calling the agent in charge of the next sub-task**.
        Must be called **after** each action taken.

        Args:
            current_status (str): The current status ("in progress", "failed", or "completed").
            current_plan_step_number (int): The current step number in the plan.
            current_sub_task (str): Description of the current sub-task.
            current_instructions (str): Instructions for the sub-task.
            agent_for_sub_task (str): The agent responsible for the sub-task.
            context_variables (dict): Execution context dictionary.

        Returns:
            SwarmResult: Contains a formatted status message and updated context.
        """

        if cmbagent_instance.mode == "chat":

            # Map statuses to icons
            status_icons = {
                "completed": "✅",
                "failed": "❌",
                "in progress": "⏳"  # or any other icon you prefer
            }
            
            icon = status_icons.get(current_status, "")
            
            context_variables["current_plan_step_number"] = current_plan_step_number
            context_variables["current_sub_task"] = current_sub_task
            context_variables["agent_for_sub_task"] = agent_for_sub_task
            context_variables["current_instructions"] = current_instructions
            context_variables["current_status"] = current_status

            codes = os.path.join(cmbagent_instance.work_dir, context_variables['codebase_path'])
            docstrings = load_docstrings(codes)
            output_str = ""
            for module, info in docstrings.items():
                output_str += "-----------\n"
                output_str += f"Filename: {module}.py\n"
                output_str += f"File path: {info['file_path']}\n\n"
                output_str += f"Available functions:\n"
                for func, doc in info['functions'].items():
                    output_str += f"function name: {func}\n"
                    output_str += "````\n"
                    output_str += f"{doc}\n"
                    output_str += "````\n\n"

            # Store the full output string in your context variable.
            context_variables["current_codebase"] = output_str

            # Load image plots from the "data" directory.
            data_directory = os.path.join(cmbagent_instance.work_dir, context_variables['database_path'])
            image_files = load_plots(data_directory)
    
            # Retrieve the list of images that have been displayed so far.
            displayed_images = context_variables.get("displayed_images", [])

            # Identify new images that haven't been displayed before.
            new_images = [img for img in image_files if img not in displayed_images]

            # Display only the new images.
            for img_file in new_images:
                if not cmbagent_disable_display:
                    ip_display(IPImage(filename=img_file, width=2 * IMG_WIDTH))
                else:
                    print(img_file)

            # Update the context to include the newly displayed images.
            context_variables["displayed_images"] = displayed_images + new_images

            
            if cmbagent_debug:
                print("\n\n in functions.py record_status: context_variables: ", context_variables)
                import pprint
                print('--'*70)
                pprint.pprint(context_variables["current_status"])
                pprint.pprint(context_variables["agent_for_sub_task"])
                print('--'*70)


            context_variables["transfer_to_engineer"] = False
            context_variables["transfer_to_researcher"] = False
            context_variables["transfer_to_camb_agent"] = False
            context_variables["transfer_to_planck_agent"] = False
            context_variables["transfer_to_cobaya_agent"] = False
            context_variables["transfer_to_perplexity"] = False
            context_variables["transfer_to_idea_maker"] = False
            context_variables["transfer_to_idea_hater"] = False
            context_variables["transfer_to_classy_sz_agent"] = False

            agent_to_transfer_to = None
            if "in progress" in context_variables["current_status"]:
                if context_variables["agent_for_sub_task"] == "engineer":
                    context_variables["transfer_to_engineer"] = True
                elif context_variables["agent_for_sub_task"] == "researcher":
                    context_variables["transfer_to_researcher"] = True
                elif context_variables["agent_for_sub_task"] == "camb_agent":
                    context_variables["transfer_to_camb_agent"] = True
                elif context_variables["agent_for_sub_task"] == "cobaya_agent":
                    context_variables["transfer_to_cobaya_agent"] = True
                elif context_variables["agent_for_sub_task"] == "perplexity":
                    context_variables["transfer_to_perplexity"] = True
                elif context_variables["agent_for_sub_task"] == "idea_maker":
                    context_variables["transfer_to_idea_maker"] = True
                elif context_variables["agent_for_sub_task"] == "idea_hater":
                    context_variables["transfer_to_idea_hater"] = True
                elif context_variables["agent_for_sub_task"] == "classy_sz_agent":
                    context_variables["transfer_to_classy_sz_agent"] = True
                elif context_variables["agent_for_sub_task"] == "planck_agent":
                    context_variables["transfer_to_planck_agent"] = True

            
                if context_variables["transfer_to_engineer"]:
                    agent_to_transfer_to = cmbagent_instance.get_agent_from_name('engineer')
                elif context_variables["transfer_to_researcher"]:
                    agent_to_transfer_to = cmbagent_instance.get_agent_from_name('researcher')
                elif context_variables["transfer_to_camb_agent"]:
                    agent_to_transfer_to = cmbagent_instance.get_agent_from_name('camb_agent')
                elif context_variables["transfer_to_cobaya_agent"]:
                    agent_to_transfer_to = cmbagent_instance.get_agent_from_name('cobaya_agent')
                elif context_variables["transfer_to_perplexity"]:
                    agent_to_transfer_to = cmbagent_instance.get_agent_from_name('perplexity')
                elif context_variables["transfer_to_idea_maker"]:
                    agent_to_transfer_to = cmbagent_instance.get_agent_from_name('idea_maker')
                elif context_variables["transfer_to_idea_hater"]:
                    agent_to_transfer_to = cmbagent_instance.get_agent_from_name('idea_hater')
                elif context_variables["transfer_to_classy_sz_agent"]:
                    agent_to_transfer_to = cmbagent_instance.get_agent_from_name('classy_sz_agent')
                elif context_variables["transfer_to_planck_agent"]:
                    agent_to_transfer_to = cmbagent_instance.get_agent_from_name('planck_agent')


            if "completed" in context_variables["current_status"]:

                if context_variables["current_plan_step_number"] == context_variables["number_of_steps_in_plan"]:
                    agent_to_transfer_to = cmbagent_instance.get_agent_from_name('admin')
                else:
                    agent_to_transfer_to = cmbagent_instance.get_agent_from_name('admin')


                # if context_variables["agent_for_sub_task"] == "engineer":
                #     print("\n successfully ran the code after ", context_variables["n_attempts"], " attempts!")
                
                ## reset the number of code execution attempts
                ## (the markdown execution always works)
                context_variables["n_attempts"] = 0
            if "failed" in context_variables["current_status"]:
                if context_variables["agent_for_sub_task"] == "engineer":
                    agent_to_transfer_to = cmbagent_instance.get_agent_from_name('engineer')
                elif context_variables["agent_for_sub_task"] == "researcher":
                    agent_to_transfer_to = cmbagent_instance.get_agent_from_name('researcher_response_formatter')


            if cmbagent_debug:
                if agent_to_transfer_to is None:
                    print("agent_to_transfer_to is None")
                else:   
                    print("agent_to_transfer_to: ", agent_to_transfer_to.name)
                

            if agent_to_transfer_to is None:
                return SwarmResult(
                    
                    values=f"""
**Step number:** {context_variables["current_plan_step_number"]} out of {context_variables["number_of_steps_in_plan"]}.\n 
**Sub-task:** {context_variables["current_sub_task"]}\n 
**Agent in charge of sub-task:** `{context_variables["agent_for_sub_task"]}`\n 
**Instructions:**\n 
{context_variables["current_instructions"]}\n 
**Status:** {context_variables["current_status"]} {icon}
            """,
                    context_variables=context_variables)
            else:
                return SwarmResult(
                    agent=agent_to_transfer_to,
                    values=f"""
**Step number:** {context_variables["current_plan_step_number"]} out of {context_variables["number_of_steps_in_plan"]}.\n 
**Sub-task:** {context_variables["current_sub_task"]}\n 
**Agent in charge of sub-task:** `{context_variables["agent_for_sub_task"]}`\n 
**Instructions:**\n 
{context_variables["current_instructions"]}\n 
**Status:** {context_variables["current_status"]} {icon}
        """,
                context_variables=context_variables)


        else:

            # Map statuses to icons
            status_icons = {
                "completed": "✅",
                "failed": "❌",
                "in progress": "⏳"  # or any other icon you prefer
            }
            
            icon = status_icons.get(current_status, "")
            
            context_variables["current_plan_step_number"] = current_plan_step_number
            context_variables["current_sub_task"] = current_sub_task
            context_variables["agent_for_sub_task"] = agent_for_sub_task
            context_variables["current_instructions"] = current_instructions
            context_variables["current_status"] = current_status

            codes = os.path.join(cmbagent_instance.work_dir, context_variables['codebase_path'])
            docstrings = load_docstrings(codes)
            output_str = ""
            for module, info in docstrings.items():
                output_str += "-----------\n"
                output_str += f"Filename: {module}.py\n"
                output_str += f"File path: {info['file_path']}\n\n"
                output_str += f"Available functions:\n"
                for func, doc in info['functions'].items():
                    output_str += f"function name: {func}\n"
                    output_str += "````\n"
                    output_str += f"{doc}\n"
                    output_str += "````\n\n"

            # Store the full output string in your context variable.
            context_variables["current_codebase"] = output_str

            # Load image plots from the "data" directory.
            data_directory = os.path.join(cmbagent_instance.work_dir, context_variables['database_path'])
            image_files = load_plots(data_directory)
    
            # Retrieve the list of images that have been displayed so far.
            displayed_images = context_variables.get("displayed_images", [])

            # Identify new images that haven't been displayed before.
            new_images = [img for img in image_files if img not in displayed_images]

            # Display only the new images.
            for img_file in new_images:
                if not cmbagent_disable_display:
                    ip_display(IPImage(filename=img_file, width=2 * IMG_WIDTH))
                else:
                    print(img_file)

            # Update the context to include the newly displayed images.
            context_variables["displayed_images"] = displayed_images + new_images

            
            if cmbagent_debug:
                print("\n\n in functions.py record_status: context_variables: ", context_variables)
                import pprint
                print('--'*70)
                pprint.pprint(context_variables["current_status"])
                pprint.pprint(context_variables["agent_for_sub_task"])
                print('--'*70)


            context_variables["transfer_to_engineer"] = False
            context_variables["transfer_to_researcher"] = False
            context_variables["transfer_to_camb_agent"] = False
            context_variables["transfer_to_cobaya_agent"] = False
            context_variables["transfer_to_perplexity"] = False
            context_variables["transfer_to_idea_maker"] = False
            context_variables["transfer_to_idea_hater"] = False
            context_variables["transfer_to_classy_sz_agent"] = False

            agent_to_transfer_to = None
            if "in progress" in context_variables["current_status"]:
                if context_variables["agent_for_sub_task"] == "engineer":
                    context_variables["transfer_to_engineer"] = True
                elif context_variables["agent_for_sub_task"] == "researcher":
                    context_variables["transfer_to_researcher"] = True
                elif context_variables["agent_for_sub_task"] == "camb_agent":
                    context_variables["transfer_to_camb_agent"] = True
                elif context_variables["agent_for_sub_task"] == "cobaya_agent":
                    context_variables["transfer_to_cobaya_agent"] = True
                elif context_variables["agent_for_sub_task"] == "perplexity":
                    context_variables["transfer_to_perplexity"] = True
                elif context_variables["agent_for_sub_task"] == "idea_maker":
                    context_variables["transfer_to_idea_maker"] = True
                elif context_variables["agent_for_sub_task"] == "idea_hater":
                    context_variables["transfer_to_idea_hater"] = True
                elif context_variables["agent_for_sub_task"] == "classy_sz_agent":
                    context_variables["transfer_to_classy_sz_agent"] = True
                elif context_variables["agent_for_sub_task"] == "planck_agent":
                    context_variables["transfer_to_planck_agent"] = True

            
                if context_variables["transfer_to_engineer"]:
                    agent_to_transfer_to = cmbagent_instance.get_agent_from_name('engineer')
                elif context_variables["transfer_to_researcher"]:
                    agent_to_transfer_to = cmbagent_instance.get_agent_from_name('researcher')
                elif context_variables["transfer_to_camb_agent"]:
                    agent_to_transfer_to = cmbagent_instance.get_agent_from_name('camb_agent')
                elif context_variables["transfer_to_cobaya_agent"]:
                    agent_to_transfer_to = cmbagent_instance.get_agent_from_name('cobaya_agent')
                elif context_variables["transfer_to_perplexity"]:
                    agent_to_transfer_to = cmbagent_instance.get_agent_from_name('perplexity')
                elif context_variables["transfer_to_idea_maker"]:
                    agent_to_transfer_to = cmbagent_instance.get_agent_from_name('idea_maker')
                elif context_variables["transfer_to_idea_hater"]:
                    agent_to_transfer_to = cmbagent_instance.get_agent_from_name('idea_hater')
                elif context_variables["transfer_to_classy_sz_agent"]:
                    agent_to_transfer_to = cmbagent_instance.get_agent_from_name('classy_sz_agent')
                elif context_variables["transfer_to_planck_agent"]:
                    agent_to_transfer_to = cmbagent_instance.get_agent_from_name('planck_agent')

            if "completed" in context_variables["current_status"]:

                if context_variables["current_plan_step_number"] == context_variables["number_of_steps_in_plan"]:
                    agent_to_transfer_to = cmbagent_instance.get_agent_from_name('terminator')
                else:
                    agent_to_transfer_to = cmbagent_instance.get_agent_from_name('control')


                # if context_variables["agent_for_sub_task"] == "engineer":
                #     print("\n successfully ran the code after ", context_variables["n_attempts"], " attempts!")
                
                ## reset the number of code execution attempts
                ## (the markdown execution always works)
                context_variables["n_attempts"] = 0
            if "failed" in context_variables["current_status"]:
                if context_variables["agent_for_sub_task"] == "engineer":
                    agent_to_transfer_to = cmbagent_instance.get_agent_from_name('engineer')
                elif context_variables["agent_for_sub_task"] == "researcher":
                    agent_to_transfer_to = cmbagent_instance.get_agent_from_name('researcher_response_formatter')


            if cmbagent_debug:
                if agent_to_transfer_to is None:
                    print("agent_to_transfer_to is None")
                else:   
                    print("agent_to_transfer_to: ", agent_to_transfer_to.name)
                

            if agent_to_transfer_to is None:
                return SwarmResult(
                    
                    values=f"""
**Step number:** {context_variables["current_plan_step_number"]} out of {context_variables["number_of_steps_in_plan"]}.\n 
**Sub-task:** {context_variables["current_sub_task"]}\n 
**Agent in charge of sub-task:** `{context_variables["agent_for_sub_task"]}`\n 
**Instructions:**\n 
{context_variables["current_instructions"]}\n 
**Status:** {context_variables["current_status"]} {icon}
            """,
                    context_variables=context_variables)
            else:
                return SwarmResult(
                    agent=agent_to_transfer_to,
                    values=f"""
**Step number:** {context_variables["current_plan_step_number"]} out of {context_variables["number_of_steps_in_plan"]}.\n 
**Sub-task:** {context_variables["current_sub_task"]}\n 
**Agent in charge of sub-task:** `{context_variables["agent_for_sub_task"]}`\n 
**Instructions:**\n 
{context_variables["current_instructions"]}\n 
**Status:** {context_variables["current_status"]} {icon}
        """,
                context_variables=context_variables)
        


    control._add_single_function(record_status)



def extract_file_path_from_source(source):
    """
    Extracts the file path from the top comment in the source code.
    Expects a line like: "# filename: codebase/module.py"
    
    Parameters:
        source (str): The source code of the file.
        
    Returns:
        str or None: The file path if found, else None.
    """
    match = re.search(r'^#\s*filename:\s*(.+)$', source, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return None

def extract_functions_docstrings_from_file(file_path):
    """
    Parses the given Python file and extracts docstrings from all top-level function
    definitions (including methods in classes) without capturing nested (internal) functions.
    Also extracts the file path from the file's top comment.
    
    Parameters:
        file_path (str): Path to the Python file.
    
    Returns:
        dict: A dictionary with two keys:
              - "file_path": the file path extracted from the comment.
              - "functions": a dictionary mapping function names to their docstrings.
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        source = f.read()
        
    # Extract the file path from the comment at the top of the file
    file_path_from_comment = extract_file_path_from_source(source)
    
    # Parse the AST
    tree = ast.parse(source, filename=file_path)
    functions = {}
    
    # Process only top-level statements
    for node in tree.body:
        # Capture top-level function definitions.
        if isinstance(node, ast.FunctionDef):
            functions[node.name] = ast.get_docstring(node)
        # Optionally, capture methods inside classes.
        elif isinstance(node, ast.ClassDef):
            for subnode in node.body:
                if isinstance(subnode, ast.FunctionDef):
                    qualified_name = f"{node.name}.{subnode.name}"
                    functions[qualified_name] = ast.get_docstring(subnode)
                    
    return {"file_path": file_path_from_comment, "functions": functions}

def load_docstrings(directory="codebase"):
    """
    Loads all top-level function docstrings from Python files in the specified directory
    without executing any code, and extracts the file path from the top comment of each file.
    
    Parameters:
        directory (str): Path to the directory containing Python files.
    
    Returns:
        dict: A dictionary where each key is a module name and each value is a dictionary
              containing the file path and another dictionary mapping function names to their docstrings.
    """
    all_docstrings = {}
    
    for file in os.listdir(directory):
        if file.endswith(".py") and not file.startswith("__"):
            module_name = file[:-3]  # Remove the .py extension
            file_path = os.path.join(directory, file)
            doc_info = extract_functions_docstrings_from_file(file_path)
            all_docstrings[module_name] = doc_info
    return all_docstrings



def load_plots(directory: str) -> list:
    """
    Searches the given directory for image files with extensions
    png, jpg, jpeg, or gif and returns a list of their file paths.
    
    Args:
        directory (str): The directory to search.
        
    Returns:
        list: List of image file paths.
    """
    image_extensions = ('.png', '.jpg', '.jpeg', '.gif')
    image_files = []

    # List of directories to search: base directory + specific subdirectories.
    dirs_to_search = [directory,
                      os.path.join(directory, "plots"),
                      os.path.join(directory, "images"),
                      os.path.join(directory, "figures")]

    for dir_path in dirs_to_search:
        if os.path.exists(dir_path) and os.path.isdir(dir_path):
            for file in os.listdir(dir_path):
                if file.lower().endswith(image_extensions):
                    image_files.append(os.path.join(dir_path, file))
    return image_files