from typing import Literal, List
import os
import re
import ast
import base64
from autogen.cmbagent_utils import cmbagent_debug
from IPython.display import Image as IPImage, display as ip_display
from IPython.display import Markdown
from autogen.cmbagent_utils import IMG_WIDTH
import autogen
from autogen.agentchat.group import ContextVariables
from autogen.agentchat.group import AgentTarget, ReplyResult, TerminateTarget
from autogen import register_function
from typing import Optional
import datetime
import json
from pathlib import Path
from .utils import AAS_keywords_dict
from .vlm_utils import account_for_external_api_calls, send_image_to_vlm, create_vlm_prompt, call_external_plot_debugger, vlm_model

cmbagent_debug = autogen.cmbagent_debug
cmbagent_disable_display = autogen.cmbagent_disable_display



## migration guide:
# # OLD
# def check_order_id(order_id: str, context_variables: dict[str, Any]) -> SwarmResult:
#     """Check if the order ID is valid"""
#     ...
#     return SwarmResult(
#         agent=order_triage_agent,
#         context_variables=context_variables,
#         values=f"Order ID {order_id} is valid.",
#     )

# # NEW
# # Now returning ReplyResult
# # (and we make sure to use the ContextVariables type for context_variables, if needed)
# def check_order_id(order_id: str, context_variables: ContextVariables) -> ReplyResult:
#     """Check if the order ID is valid"""
#     ...
#     return ReplyResult(
#         # Parameter now called target and we pass in a suitable TransitionTarget:
#         target=AgentTarget(order_triage_agent),
#         context_variables=context_variables,
#         # Parameter now called message:
#         message=f"Order ID {order_id} is valid.",
#     )


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
    idea_saver = cmbagent_instance.get_agent_from_name('idea_saver')
    control_starter = cmbagent_instance.get_agent_from_name('control_starter')
    camb_context = cmbagent_instance.get_agent_from_name('camb_context')
    classy_context = cmbagent_instance.get_agent_from_name('classy_context')
    plot_judge = cmbagent_instance.get_agent_from_name('plot_judge')
    plot_debugger = cmbagent_instance.get_agent_from_name('plot_debugger')
    
    if not cmbagent_instance.skip_rag_agents:
        classy_sz = cmbagent_instance.get_agent_from_name('classy_sz_agent')
        classy_sz_response_formatter = cmbagent_instance.get_agent_from_name('classy_sz_response_formatter')
        camb = cmbagent_instance.get_agent_from_name('camb_agent')
        camb_response_formatter = cmbagent_instance.get_agent_from_name('camb_response_formatter')
        planck = cmbagent_instance.get_agent_from_name('planck_agent')

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
                                                               "camb_context",
                                                               "classy_context",
                                                               "plot_judge",
                                                            #    "planck_agent", no need for paper agents
                                                               "control"], 
                                context_variables: ContextVariables,
                                execution_status: Literal["success", "failure"],
                                fix_suggestion: Optional[str] = None
                                ) -> ReplyResult:
        
        # Transfer executed code from global variable to shared context
        try:
            import cmbagent.vlm_utils
            if getattr(cmbagent.vlm_utils, "_last_executed_code", None):
                context_variables["latest_executed_code"] = cmbagent.vlm_utils._last_executed_code
                cmbagent.vlm_utils._last_executed_code = None  # Prevent reuse
            else:
                context_variables["latest_executed_code"] = None
        except Exception:
            context_variables["latest_executed_code"] = None

                
        
        workflow_status_str = rf"""
xxxxxxxxxxxxxxxxxxxxxxxxxx

Workflow status:

Plan step number: {context_variables["current_plan_step_number"]}

Agent for sub-task (might be different from the next agent suggestion for debugging): {context_variables["agent_for_sub_task"]}

Current status (before execution): {context_variables["current_status"]}

xxxxxxxxxxxxxxxxxxxxxxxxxx
"""
        # print(f"in functions.py post_execution_transfer: context_variables: {context_variables}")
        # print(f"in functions.py post_execution_transfer: next agent suggestion: {next_agent_suggestion}")
        # print(f"in functions.py post_execution_transfer: workflow_status_str: {workflow_status_str}")
        # print(f"in functions.py post_execution_transfer: context_variables['n_attempts']: {context_variables['n_attempts']}")
        # print(f"in functions.py post_execution_transfer: context_variables['max_n_attempts']: {context_variables['max_n_attempts']}")

        # import sys; sys.exit()
        
        if context_variables["agent_for_sub_task"] == "engineer" or context_variables["agent_for_sub_task"] == "camb_agent" or context_variables["agent_for_sub_task"] == "camb_context" or context_variables["agent_for_sub_task"] == "classy_context":
            
            if context_variables["n_attempts"] >= context_variables["max_n_attempts"]:
                return ReplyResult(target=AgentTarget(terminator),
                                message=f"Max number of code execution attempts ({context_variables['max_n_attempts']}) reached. Exiting.",
                                context_variables=context_variables)
            
            if execution_status == "success":
                # Check if plot evaluation is enabled
                evaluate_plots = context_variables.get("evaluate_plots", False)
                
                if evaluate_plots:
                    # Check if there are new images that need plot_judge review
                    data_directory = os.path.join(cmbagent_instance.work_dir, context_variables['database_path'])
                    image_files = load_plots(data_directory)
                    displayed_images = context_variables.get("displayed_images", [])
                    new_images = [img for img in image_files if img not in displayed_images]
                    
                    if new_images:
                        # Call VLM to evaluate the latest plot
                        most_recent_image = new_images[-1]
                        context_variables["latest_plot_path"] = most_recent_image
                        if most_recent_image not in context_variables["displayed_images"]:
                            context_variables["displayed_images"].append(most_recent_image)
                        # Handoff to plot_judge
                        return ReplyResult(target=AgentTarget(plot_judge),
                                        message=f"Plot created: {most_recent_image}. Please analyze this plot using a VLM.",
                                        context_variables=context_variables)
                    else:
                        # No new images needing approval, so VLM feedback history is cleared
                        context_variables["vlm_plot_structured_feedback"] = None
                        context_variables["latest_executed_code"] = None
                        
                        # No new plots to evaluate, continue to control
                        return ReplyResult(target=AgentTarget(control),
                                        message="Execution status: " + execution_status + ". Transfer to control.\n" + f"{workflow_status_str}\n",
                                        context_variables=context_variables)
                else:
                    # Plot evaluation disabled, so skip VLM and go straight to control
                    context_variables["vlm_plot_structured_feedback"] = None
                    context_variables["latest_executed_code"] = None
                    
                    return ReplyResult(target=AgentTarget(control),
                                    message="Execution status: " + execution_status + ". Transfer to control.\n" + f"{workflow_status_str}\n",
                                    context_variables=context_variables)

            if next_agent_suggestion == "engineer":
                context_variables["n_attempts"] += 1
                return ReplyResult(target=AgentTarget(engineer),
                                message="Execution status: " + execution_status 
                                + ". Transfer to engineer.\n" 
                                + f"{workflow_status_str}\n" 
                                + f"Fix suggestion: {fix_suggestion}\n",
                                context_variables=context_variables)    
            
            elif next_agent_suggestion == "classy_sz_agent":
                context_variables["n_attempts"] += 1
                return ReplyResult(target=AgentTarget(classy_sz),
                                message="Execution status: " + execution_status + ". Transfer to classy_sz_agent.\n" + f"{workflow_status_str}\n",
                                context_variables=context_variables)

            elif next_agent_suggestion == "camb_agent":
                context_variables["n_attempts"] += 1
                return ReplyResult(target=AgentTarget(camb),
                                message="Execution status: " + execution_status + ". Transfer to camb_agent.\n" + f"{workflow_status_str}\n",
                                context_variables=context_variables)
            
            elif next_agent_suggestion == "camb_context":
                context_variables["n_attempts"] += 1
                return ReplyResult(target=AgentTarget(camb_context),
                                message="Execution status: " + execution_status + ". Transfer to camb_context.\n" + f"{workflow_status_str}\n"
                                + f"Fix suggestion: {fix_suggestion}\n",
                                context_variables=context_variables)
            
            elif next_agent_suggestion == "classy_context":
                context_variables["n_attempts"] += 1
                return ReplyResult(target=AgentTarget(classy_context),
                                message="Execution status: " + execution_status + ". Transfer to classy_context.\n" + f"{workflow_status_str}\n"
                                + f"Fix suggestion: {fix_suggestion}\n",
                                context_variables=context_variables)            
            

            elif next_agent_suggestion == "control":
                context_variables["n_attempts"] += 1
                return ReplyResult(target=AgentTarget(control),
                                message="Execution status: " + execution_status + ". Transfer to control.\n" + f"{workflow_status_str}\n",
                                context_variables=context_variables)
            
            elif next_agent_suggestion == "installer":
                context_variables["n_attempts"] += 1
                return ReplyResult(target=AgentTarget(installer),
                                message="Execution status: " + execution_status + ". Transfer to installer.\n" + f"{workflow_status_str}\n",
                                context_variables=context_variables)
        else:
                return ReplyResult(target=AgentTarget(control),
                                message="Transfer to control.\n" + workflow_status_str,
                                context_variables=context_variables)
        
    # executor_response_formatter._add_single_function(post_execution_transfer)

    register_function(
        post_execution_transfer,
        caller=executor_response_formatter,
        executor=executor_response_formatter,
        description=r"""
Transfer to the next agent based on the execution status.
For the next agent suggestion, follow these rules:

    - Suggest the installer agent if error related to missing Python modules (i.e., ModuleNotFoundError).
    - Suggest the classy_sz_agent if error is an internal classy_sz error.
    - Suggest the camb_context agent if CAMB documentation should be consulted, e.g., if the Python error is related to the camb code.
    - Suggest the classy_context agent if classy documentation should be consulted, e.g., if the Python error is related to the classy code, e.g., classy.CosmoSevereError.
    - Suggest camb_context to fix Python errors related to the camb code.
    - Suggest classy_context to fix Python errors related to the classy code, e.g., classy.CosmoSevereError.
    - Suggest the engineer agent if error related to generic Python code. Don't prioritize the engineer agent if the error is related to the camb or classy code, in this case suggest camb_context or classy_context instead.
    - Suggest the cobaya_agent if error related to internal cobaya code.
    - Suggest the control agent only if execution was successful. 
""",
    )

    def call_vlm_judge(context_variables: ContextVariables) -> ReplyResult:
        """
        Analyze latest_plot_path (set by post_execution_transfer) using VLM and store the analysis in context.
        """
        # Check if we've already reached the maximum number of plot evaluations before calling VLM
        current_evals = context_variables.get("n_plot_evals", 0)
        max_evals = context_variables.get("max_n_plot_evals", 1)
        
        if current_evals >= max_evals:
            # Clear VLM feedback and executed code
            context_variables["vlm_plot_structured_feedback"] = None
            context_variables["latest_executed_code"] = None
            context_variables["n_plot_evals"] = 0
            return ReplyResult(target=AgentTarget(control),
                             message=f"Plot evaluation retry limit ({max_evals}) reached. Accepting current plot and continuing to control.",
                             context_variables=context_variables)
        
        print(f"Plot evaluation {current_evals + 1}/{max_evals}")
        
        img_path = context_variables.get("latest_plot_path")
        if not img_path:
            return ReplyResult(
                target=AgentTarget(plot_debugger),
                message="No plot path found in context",
                context_variables=context_variables
            )
        
        # Check if file exists
        if not os.path.exists(img_path):
            return ReplyResult(
                target=AgentTarget(plot_debugger),
                message=f"Plot file not found at {img_path}",
                context_variables=context_variables
            )
        
        try:
            print(f"Reading plot file: {img_path}")
            with open(img_path, 'rb') as img_file:
                base_64_img = base64.b64encode(img_file.read()).decode('utf-8')
                
        except Exception as e:
            return ReplyResult(
                target=AgentTarget(plot_debugger),
                message=f"Error reading image file: {str(e)}",
                context_variables=context_variables
            )
        
        try:
            # Send the image to the VLM model and get the analysis (injection checks n_plot_evals before increment)
            executed_code = context_variables.get("latest_executed_code")
            vlm_prompt = create_vlm_prompt(context_variables, executed_code)
            inject_wrong_plot = context_variables.get("inject_wrong_plot", False)
            completion, injected_code = send_image_to_vlm(base_64_img, vlm_prompt, inject_wrong_plot=inject_wrong_plot, context_variables=context_variables)
            
            # Increment plot evaluation counter after VLM call
            context_variables["n_plot_evals"] = current_evals + 1
            vlm_analysis_json = completion.choices[0].message.content
            print(f"\n\nVLM analysis: \n\n{vlm_analysis_json}\n\n")
            
            if injected_code:
                print(f"Injected code:\n{injected_code}\n")
            
            # Parse the structured JSON response
            try:
                vlm_analysis_data = json.loads(vlm_analysis_json)
                vlm_verdict = vlm_analysis_data.get("verdict", "continue")
                vlm_problems = vlm_analysis_data.get("problems", [])
                
                # Store verdict and problems in shared context
                context_variables["vlm_plot_analysis"] = vlm_analysis_json
                context_variables["vlm_verdict"] = vlm_verdict
                context_variables["plot_problems"] = vlm_problems
                
            except json.JSONDecodeError as e:
                print(f"Warning: Could not parse VLM JSON response: {e}")
                # Fall back to text parsing for verdict
                vlm_verdict = "continue"
                if "VERDICT: continue" in vlm_analysis_json:
                    vlm_verdict = "continue"
                elif "VERDICT: retry" in vlm_analysis_json:
                    vlm_verdict = "retry"
                
                context_variables["vlm_plot_analysis"] = vlm_analysis_json
                context_variables["vlm_verdict"] = vlm_verdict
                context_variables["plot_problems"] = ["VLM parsing failed - analysis may be incomplete"]
                print(f"VLM VERDICT (fallback): {vlm_verdict}")
            
            account_for_external_api_calls(plot_judge, completion)
            
            # Track LLM scientific criteria costs if they exist
            llm_completion = context_variables.get("llm_completion")
            if llm_completion:
                account_for_external_api_calls(plot_judge, llm_completion, call_type="LLM")
                        
            return ReplyResult(
                target=AgentTarget(plot_debugger),
                message=f"VLM analysis completed with verdict: {vlm_verdict}.",
                context_variables=context_variables
            )
            
        except Exception as e:
            error_msg = f"Error calling VLM API: {str(e)}"
            context_variables["vlm_plot_analysis"] = f"ERROR: {error_msg}"
            return ReplyResult(
                target=AgentTarget(plot_debugger),
                message=f"VLM analysis failed: {error_msg}. Please handle this error.",
                context_variables=context_variables
            )
    
    register_function(
        call_vlm_judge,
        caller=plot_judge,
        executor=plot_judge,
        description=r"""
        Call a VLM to judge the plot.
        """,
    )
    
    def route_plot_judge_verdict(context_variables: ContextVariables) -> ReplyResult:
        """
        Route based on plot_judge verdict stored in context: continue to control, retry to engineer.
        Handles all debugging logic internally for retry cases.
        """
        # Get verdict and problems from shared context
        verdict = context_variables.get("vlm_verdict", "continue")
        vlm_problems = context_variables.get("plot_problems", [])
        
        # Get current evaluation count (already incremented in call_vlm_judge)
        current_evals = context_variables.get("n_plot_evals", 0)
        max_evals = context_variables.get("max_n_plot_evals", 1)

        # Update displayed_images list
        if "latest_plot_path" in context_variables and "displayed_images" in context_variables:
            if context_variables["latest_plot_path"] not in context_variables["displayed_images"]:
                context_variables["displayed_images"].append(context_variables["latest_plot_path"])

        if verdict == "continue":
            # Clear VLM feedback, problems, and fixes when plot is approved
            context_variables["vlm_plot_structured_feedback"] = None
            context_variables["latest_executed_code"] = None
            context_variables["plot_problems"] = []
            context_variables["plot_fixes"] = []
            return ReplyResult(target=AgentTarget(control),
                             message="Plot approved. Continuing to control.",
                             context_variables=context_variables)
        else:  # verdict == "retry"
            # Check if we've reached the maximum number of plot evaluations (retries)
            if current_evals > max_evals:
                print(f"Maximum plot evaluation retries ({max_evals}) reached. Accepting current plot and continuing to control.")
                # Clear VLM feedback and executed code when accepting due to limit
                context_variables["vlm_plot_structured_feedback"] = None
                context_variables["latest_executed_code"] = None
                context_variables["plot_problems"] = []
                context_variables["plot_fixes"] = []
                return ReplyResult(target=AgentTarget(control),
                                 message=f"Plot evaluation retry limit ({max_evals}) reached. Accepting current plot and continuing to control.",
                                 context_variables=context_variables)
            
            # Call external debugger to generate fixes if we have problems
            fixes = []
            if vlm_problems:                
                task_context = context_variables.get("improved_main_task", "No task context")
                vlm_analysis = context_variables.get("vlm_plot_analysis", "No VLM analysis")
                executed_code = context_variables.get("latest_executed_code", "No code available")
                
                fixes = call_external_plot_debugger(
                    task_context=task_context,
                    vlm_analysis=vlm_analysis, 
                    problems=vlm_problems,
                    executed_code=executed_code
                )
                
                # Store fixes in shared context
                context_variables["plot_fixes"] = fixes
            
            # Construct comprehensive feedback with problems from VLM and fixes from debugger
            engineer_feedback = ""
            if vlm_problems or fixes:
                engineer_feedback = "The plot has been analyzed and needs improvements:\n\n"
                
                if vlm_problems:
                    engineer_feedback += "Problems identified by plot judge:\n" + "\n".join(f"- {p}" for p in vlm_problems) + "\n\n"
                
                if fixes:
                    engineer_feedback += "Targeted fixes from code debugger:\n" + "\n".join(f"- {f}" for f in fixes) + "\n\n"
                
                # Include code corresponding to the problematic plot as context
                code_context = context_variables.get("latest_executed_code")
                if code_context and len(code_context.strip()) > 0:
                    engineer_feedback += "Code that generated this plot:\n```python\n" + code_context + "\n```\n"
            
            # Store structured feedback in context for engineer prompt injection
            context_variables["vlm_plot_structured_feedback"] = engineer_feedback if engineer_feedback else None
                        
            print(f"\n=== ENGINEER FEEDBACK ===")
            if vlm_problems:
                print("Problems identified by plot judge:")
                for i, problem in enumerate(vlm_problems, 1):
                    print(f"  {i}. {problem}")
                print()
            
            if fixes:
                print("Targeted fixes from plot debugger:")
                for i, fix in enumerate(fixes, 1):
                    print(f"  {i}. {fix}")
                print()
            
            if context_variables.get("latest_executed_code"):
                print("Code that generated this plot:")
                print("```python")
                print(context_variables.get("latest_executed_code"))
                print("```")
            
            print("=== END ENGINEER FEEDBACK ===\n")
            
            return ReplyResult(target=AgentTarget(engineer),
                            message="Plot needs fixes. Returning to engineer.",
                            context_variables=context_variables)

    register_function(
        route_plot_judge_verdict,
        caller=plot_debugger,
        executor=plot_debugger,
        description=r"""
        Route based on plot_judge verdict stored in context: continue to control, retry to engineer.
        Handles external debugging calls internally for retry cases.
        """,
    )

    def terminate_session(context_variables: ContextVariables) -> ReplyResult:
        """
        Terminate the session.
        """

        ## do things to context_variables
        # context_variables["improved_main_task"] = improved_main_task


        return ReplyResult(target=TerminateTarget(), ## terminate
                            message="Session terminated.",
                            context_variables=context_variables)


    terminator._add_single_function(terminate_session)
    # terminator.functions = [terminate_session]


    def record_improved_task(improved_main_task: str,  context_variables: ContextVariables) -> ReplyResult:
        """
        Records the improved main task.
        """


        context_variables["improved_main_task"] = improved_main_task

        if not cmbagent_disable_display:
            display(Markdown(improved_main_task))
        else:
            print(improved_main_task)


        return ReplyResult(target=AgentTarget(planner), ## transfer to planner
                            message="Improved main task has been logged. Now, suggest a plan, planner!",
                            context_variables=context_variables)


    task_recorder._add_single_function(record_improved_task)


    def record_aas_keywords(aas_keywords: list[str], context_variables: ContextVariables) -> ReplyResult:
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
                return ReplyResult(target=AgentTarget(aas_keyword_finder), ## loop-back 
                                message=f"Proposed keyword {keyword} not found in the list of AAS keywords. Extract keywords from provided AAS list!",
                                context_variables=context_variables)
            
        context_variables["aas_keywords"] = {f'{aas_keyword}': AAS_keywords_dict[aas_keyword] for aas_keyword in aas_keywords}
            
        AAS_keyword_list = "\n".join(
                            [f"- [{keyword}]({AAS_keywords_dict[keyword]})" for keyword in aas_keywords]
                        )

        return ReplyResult(target=AgentTarget(control), ## print and finish
                        message=f"""
**AAS keywords**:\n
{AAS_keyword_list}
""",
                        context_variables=context_variables)


    # aas_keyword_finder._add_single_function(record_aas_keywords)  
    # aas_keyword_finder.functions = [record_aas_keywords]
    register_function(
        record_aas_keywords,
        caller=aas_keyword_finder,
        executor=aas_keyword_finder,
        description=r"""
        Extracts the relevant AAS keywords from the list, given the text input.
        Args:
            aas_keywords (list[str]): The list of AAS keywords to be recorded
            context_variables (dict): A dictionary maintaining execution context, including previous plans, 
                feedback tracking, and finalized plans.
        """,
    )


    def record_plan(plan_suggestion: str, number_of_steps_in_plan: int, context_variables: ContextVariables) -> ReplyResult:
        """
        Records a suggested plan and updates relevant execution context.

        This function logs a full plan suggestion into the `context_variables` dictionary. If no feedback 
        remains to be given (i.e., `context_variables["feedback_left"] == 0`), the most recent plan 
        suggestion is marked as the final plan. The function also updates the total number of steps in 
        the plan.

        The function ensures that the plan is properly stored and transferred to the `plan_reviewer` agent 
        for further evaluation.

        Args:
            plan_suggestion (str): The complete plan suggestion to be recorded. Unaltered, as it is, preserve capitalization and ponctuation.
            number_of_steps_in_plan (int): The total number of **Steps** in the suggested plan.
            context_variables (dict): A dictionary maintaining execution context, including previous plans, 
                feedback tracking, and finalized plans.
        """
        context_variables["plans"].append(plan_suggestion)

        context_variables["proposed_plan"] = plan_suggestion

        context_variables["number_of_steps_in_plan"] = number_of_steps_in_plan

        if context_variables["feedback_left"]==0:
            context_variables["final_plan"] = context_variables["plans"][-1]
            return ReplyResult(target=AgentTarget(terminator), ## transfer to control
                            message="Planning stage complete. Exiting.",
                            context_variables=context_variables)
        else:
            return ReplyResult(target=AgentTarget(plan_reviewer), ## transfer to plan reviewer
                            message="Plan has been logged.",
                            context_variables=context_variables)


    # plan_recorder._add_single_function(record_plan)
    # plan_recorder.functions = [record_plan]
    register_function(
        record_plan,
        caller=plan_recorder,
        executor=plan_recorder,
        description=r"""
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
        """,
    )

    def record_plan_constraints(needed_agents: list[Literal["engineer", 
                                                       "researcher", 
                                                       "idea_maker", 
                                                       "idea_hater", 
                                                       "camb_agent",
                                                       "camb_context",
                                                       "classy_context",
                                                       "classy_sz_agent", 
                                                       "planck_agent",
                                                       "aas_keyword_finder"]],
                                context_variables: ContextVariables) -> ReplyResult:
        """
        Records the constraints on the plan.
        """

        context_variables["needed_agents"] = needed_agents

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

        return ReplyResult(target=AgentTarget(planner),
                           message="Plan constraints have been logged.",
                           context_variables=context_variables)

    plan_setter._add_single_function(record_plan_constraints)


    def record_review(plan_review: str, context_variables: ContextVariables) -> ReplyResult:
        """ Record reviews of the plan."""
        context_variables["reviews"].append(plan_review)
        context_variables["feedback_left"] -= 1

        context_variables["recommendations"] = plan_review


        return ReplyResult(target=AgentTarget(planner),  ## transfer back to planner
                        message=f"""
Recommendations have been logged.  
Number of feedback rounds left: {context_variables["feedback_left"]}. 
Now, update the plan accordingly, planner!""",
                        
                        context_variables=context_variables)

    # review_recorder._add_single_function(record_review)
    # review_recorder.functions = [record_review]
    register_function(
        record_review,
        caller=review_recorder,
        executor=review_recorder,
        description=r"""
        Records the reviews of the plan.
        """,
    )


    def record_ideas(ideas: list):
        """ Record ideas. You must record the entire list of ideas and their descriptions. You must not alter the list."""
        # print('ideas: ', ideas)
        # print(f"saving to work directory....{cmbagent_instance.work_dir}")
        # save in json file
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath =  os.path.join(cmbagent_instance.work_dir, f'ideas_{timestamp}.json')
        with open(filepath, 'w') as f:
            json.dump(ideas, f)

        return f"\nIdeas saved in {filepath}\n"



    register_function(
        record_ideas,
        caller=idea_saver,
        executor=idea_saver,
        description=r"""
        Records the ideas. You must record the entire list of ideas and their descriptions. You must not alter the list.
        """,
    )


    def record_status(
        current_status: Literal["in progress", "failed", "completed"],
        current_plan_step_number: int,
        current_sub_task: str,
        current_instructions: str,
        agent_for_sub_task: Literal["engineer", 
                                    "researcher", #"perplexity", 
                                    "idea_maker", 
                                    "idea_hater", 
                                    # "classy_sz_agent", 
                                    # "camb_agent", 
                                    "camb_context",
                                    "classy_context",
                                    "aas_keyword_finder", #"planck_agent"
                                    ],
        context_variables: ContextVariables
    ) -> ReplyResult:
        """
        Updates the execution context and returns the current progress.
        Must be called **before calling the agent in charge of the next sub-task**.
        Must be called **after** each action taken.

        Args:
            current_status (str): The current status ("in progress", "failed", or "completed").
            current_plan_step_number (int): The current step number in the plan.
            current_sub_task (str): Description of the current sub-task.
            current_instructions (str): Instructions for the sub-task.
            agent_for_sub_task (str): The agent responsible for the sub-task in the current step. Stays the same for the whole step.
            context_variables (dict): Execution context dictionary.

        Returns:
            ReplyResult: Contains a formatted status message and updated context.
        """

        # print(f"in functions.py: record_status: context_variables:")
        # print(f"max_n_attempts: {context_variables['max_n_attempts']}")


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
            # print("loading docstrings...")
            docstrings = load_docstrings(codes)
            # print("docstrings loaded!")
            # print("="*70)
            # print("\n\n")
            output_str = ""
            for module, info in docstrings.items():
                output_str += "-----------\n"
                output_str += f"Filename: {module}.py\n"
                output_str += f"File path: {info['file_path']}\n\n"

                # Show parse errors (if any) ✨
                if "error" in info:
                    output_str += f"⚠️  Parse error: {info['error']}\n\n"

                output_str += "Available functions:\n"

                if info["functions"]:                          # non-empty dict
                    for func, doc in info["functions"].items():
                        output_str += f"function name: {func}\n"
                        output_str += "````\n"
                        output_str += f"{doc or '(no docstring)'}\n"
                        output_str += "````\n\n"
                else:
                    output_str += "(none)\n\n"

            # Store the full output string in your context variable.
            context_variables["current_codebase"] = output_str

            # print("current_codebase: ", context_variables["current_codebase"])
            # print("="*70)
            # print("\n\n")

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
                    print(f"\n- Saved {img_file}")

            # Update the context to include the newly displayed images.
            context_variables["displayed_images"] = displayed_images + new_images

            



            context_variables["transfer_to_engineer"] = False
            context_variables["transfer_to_researcher"] = False
            context_variables["transfer_to_camb_agent"] = False
            context_variables["transfer_to_camb_context"] = False
            context_variables["transfer_to_classy_context"] = False
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
                elif context_variables["agent_for_sub_task"] == "camb_context":
                    context_variables["transfer_to_camb_context"] = True
                elif context_variables["agent_for_sub_task"] == "classy_context":
                    context_variables["transfer_to_classy_context"] = True
            
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
                elif context_variables["transfer_to_camb_context"]:
                    agent_to_transfer_to = cmbagent_instance.get_agent_from_name('camb_context')
                elif context_variables["transfer_to_classy_context"]:
                    agent_to_transfer_to = cmbagent_instance.get_agent_from_name('classy_context')


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
                return ReplyResult(
                    target=AgentTarget(control),
                    message=f"""
**Step number:** {context_variables["current_plan_step_number"]} out of {context_variables["number_of_steps_in_plan"]}.\n 
**Sub-task:** {context_variables["current_sub_task"]}\n 
**Agent in charge of sub-task:** `{context_variables["agent_for_sub_task"]}`\n 
**Instructions:**\n 
{context_variables["current_instructions"]}\n 
**Status:** {context_variables["current_status"]} {icon}
            """,
                    context_variables=context_variables)
            else:
                return ReplyResult(
                    target=AgentTarget(agent_to_transfer_to),
                    message=f"""
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
            # print(f"loading docstrings from {codes}...")
            docstrings = load_docstrings(codes)
            # print("docstrings loaded!")
            # print("="*70)
            # print("\n\n")
            # output_str = ""
            # for module, info in docstrings.items():
            #     output_str += "-----------\n"
            #     output_str += f"Filename: {module}.py\n"
            #     output_str += f"File path: {info['file_path']}\n\n"
            #     output_str += f"Available functions:\n"
            #     for func, doc in info['functions'].items():
            #         output_str += f"function name: {func}\n"
            #         output_str += "````\n"
            #         output_str += f"{doc}\n"
            #         output_str += "````\n\n"
            output_str = ""
            for module, info in docstrings.items():
                output_str += "-----------\n"
                output_str += f"Filename: {module}.py\n"
                output_str += f"File path: {info['file_path']}\n\n"

                # Show parse errors (if any) ✨
                if "error" in info:
                    output_str += f"⚠️  Parse error: {info['error']}\n\n"

                output_str += "Available functions:\n"

                if info["functions"]:                          # non-empty dict
                    for func, doc in info["functions"].items():
                        output_str += f"function name: {func}\n"
                        output_str += "````\n"
                        output_str += f"{doc or '(no docstring)'}\n"
                        output_str += "````\n\n"
                else:
                    output_str += "(none)\n\n"



            # Store the full output string in your context variable.
            context_variables["current_codebase"] = output_str

            # print("current_codebase: ", context_variables["current_codebase"])
            # print("="*70)
            # import sys
            # sys.exit()

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
                    print(f"\n- Saved {img_file}")

            # Update the context to include the newly displayed images.
            context_variables["displayed_images"] = displayed_images + new_images

            
            # if cmbagent_debug:
            # print("\n\n in functions.py record_status: context_variables: ", context_variables)
            # import pprint
            # print('--'*70)
            # pprint.pprint(context_variables["current_status"])
            # pprint.pprint(context_variables["agent_for_sub_task"])
            # print('--'*70)


            context_variables["transfer_to_engineer"] = False
            context_variables["transfer_to_researcher"] = False
            context_variables["transfer_to_camb_agent"] = False
            context_variables["transfer_to_cobaya_agent"] = False
            context_variables["transfer_to_perplexity"] = False
            context_variables["transfer_to_idea_maker"] = False
            context_variables["transfer_to_idea_hater"] = False
            context_variables["transfer_to_classy_sz_agent"] = False
            context_variables["transfer_to_camb_context"] = False
            context_variables["transfer_to_classy_context"] = False
            context_variables["transfer_to_planck_agent"] = False
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
                elif context_variables["agent_for_sub_task"] == "camb_context":
                    context_variables["transfer_to_camb_context"] = True
                elif context_variables["agent_for_sub_task"] == "classy_context":
                    context_variables["transfer_to_classy_context"] = True
            
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
                elif context_variables["transfer_to_camb_context"]:
                    agent_to_transfer_to = cmbagent_instance.get_agent_from_name('camb_context')
                elif context_variables["transfer_to_classy_context"]:
                    agent_to_transfer_to = cmbagent_instance.get_agent_from_name('classy_context')

                if cmbagent_instance.mode == "planning_and_control_context_carryover" and context_variables["current_plan_step_number"] != cmbagent_instance.step:
                    agent_to_transfer_to = cmbagent_instance.get_agent_from_name('terminator')


            if "completed" in context_variables["current_status"]:
                

                if context_variables["current_plan_step_number"] == context_variables["number_of_steps_in_plan"]:
                    agent_to_transfer_to = cmbagent_instance.get_agent_from_name('terminator')

                else:

                    agent_to_transfer_to = cmbagent_instance.get_agent_from_name('control')
                    ## reset the number of code execution attempts for the next step
                    ## (the markdown execution always works)
                    if cmbagent_instance.mode != "planning_and_control_context_carryover": ## this is to keep memory of the number of attempts, to save later if needed... currently not used
                        context_variables["n_attempts"] = 0

                # if context_variables["agent_for_sub_task"] == "engineer":
                #     print("\n successfully ran the code after ", context_variables["n_attempts"], " attempts!")
                

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
                return ReplyResult(
                    target=AgentTarget(control),
                    message=f"""
**Step number:** {context_variables["current_plan_step_number"]} out of {context_variables["number_of_steps_in_plan"]}.\n 
**Sub-task:** {context_variables["current_sub_task"]}\n 
**Agent in charge of sub-task:** `{context_variables["agent_for_sub_task"]}`\n 
**Instructions:**\n 
{context_variables["current_instructions"]}\n 
**Status:** {context_variables["current_status"]} {icon}
            """,
                    context_variables=context_variables)
            else:
                return ReplyResult(
                    target=AgentTarget(agent_to_transfer_to),
                    message=f"""
**Step number:** {context_variables["current_plan_step_number"]} out of {context_variables["number_of_steps_in_plan"]}.\n 
**Sub-task:** {context_variables["current_sub_task"]}\n 
**Agent in charge of sub-task:** `{context_variables["agent_for_sub_task"]}`\n 
**Instructions:**\n 
{context_variables["current_instructions"]}\n 
**Status:** {context_variables["current_status"]} {icon}
        """,
                context_variables=context_variables)
        


    # control._add_single_function(record_status)
    register_function(
        record_status,
        caller=control,
        executor=control,
        description=r"""
        Updates the context and returns the current progress.
        Must be called **before calling the agent in charge of the next sub-task**.
        Must be called **after** each action taken.

        Args:
            current_status (str): The current status ("in progress", "failed", or "completed").
            current_plan_step_number (int): The current step number in the plan.
            current_sub_task (str): Description of the current sub-task.
            current_instructions (str): Instructions for the sub-task.
            agent_for_sub_task (str): The agent responsible for the sub-task.
            context_variables (dict): context dictionary.

        Returns:
            ReplyResult: Contains a formatted status message and updated context.
        """,
    )
    # control.functions = [record_status]



    def record_status_starter(
        # current_status: Literal["in progress", "failed", "completed"],
        # current_plan_step_number: int,
        # current_sub_task: str,
        # current_instructions: str,
        # agent_for_sub_task: Literal["engineer", "researcher", #"perplexity", 
        #                             "idea_maker", "idea_hater", "classy_sz_agent", "camb_agent", "aas_keyword_finder", "planck_agent"],
        context_variables: ContextVariables
    ) -> ReplyResult:
        """
        Updates the execution context and returns the current progress.
        Must be called **before calling the agent in charge of the next sub-task**.
        Must be called **after** each action taken.

        Args:
            context_variables (dict): Execution context dictionary.

        Returns:
            ReplyResult: Contains a formatted status message and updated context.
        """

        current_status = "in progress"

        # Map statuses to icons
        status_icons = {
            "completed": "✅",
            "failed": "❌",
            "in progress": "⏳"  # or any other icon you prefer
        }
        
        icon = status_icons.get(current_status, "")
        



        
        # if cmbagent_debug:
        # print("\n\n in functions.py record_status: context_variables: ", context_variables)
        # import pprint
        # print('--'*70)
        # pprint.pprint(context_variables.get("current_status"))
        # pprint.pprint(context_variables.get("agent_for_sub_task"))
        # print(context_variables['current_status'])
        # print(context_variables['agent_for_sub_task'])
        # print('--'*70)

        # import sys
        # sys.exit()

        context_variables["transfer_to_engineer"] = False
        context_variables["transfer_to_researcher"] = False
        context_variables["transfer_to_camb_agent"] = False
        context_variables["transfer_to_cobaya_agent"] = False
        context_variables["transfer_to_perplexity"] = False
        context_variables["transfer_to_idea_maker"] = False
        context_variables["transfer_to_idea_hater"] = False
        context_variables["transfer_to_classy_sz_agent"] = False
        context_variables["transfer_to_camb_context"] = False
        context_variables["transfer_to_classy_context"] = False
        context_variables["transfer_to_planck_agent"] = False

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
            elif context_variables["agent_for_sub_task"] == "camb_context":
                context_variables["transfer_to_camb_context"] = True
            elif context_variables["agent_for_sub_task"] == "classy_context":
                context_variables["transfer_to_classy_context"] = True
        
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
            elif context_variables["transfer_to_camb_context"]:
                agent_to_transfer_to = cmbagent_instance.get_agent_from_name('camb_context')
            elif context_variables["transfer_to_classy_context"]:
                agent_to_transfer_to = cmbagent_instance.get_agent_from_name('classy_context')



        return ReplyResult(
            target=AgentTarget(agent_to_transfer_to),
            message=f"""
**Step number:** {context_variables["current_plan_step_number"]} out of {context_variables["number_of_steps_in_plan"]}.\n 
**Sub-task:** {context_variables["current_sub_task"]}\n 
**Agent in charge of sub-task:** `{context_variables["agent_for_sub_task"]}`\n 
**Instructions:**\n 
{context_variables["current_instructions"]}\n 
**Status:** {context_variables["current_status"]} {icon}
""",
        context_variables=context_variables)
    


    # control._add_single_function(record_status)
    register_function(
        record_status_starter,
        # record_status,
        caller=control_starter,
        executor=control_starter,
        description=r"""
        Updates the context and returns the current progress.
        Must be called **before calling the agent in charge of the next sub-task**.
        Must be called **after** each action taken.

        Args:

            context_variables (dict): context dictionary.

        Returns:
            ReplyResult: Contains a formatted status message and updated context.
        """,
    )
    # control.functions = [record_status]



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

# def load_docstrings(directory="codebase"):
#     """
#     Loads all top-level function docstrings from Python files in the specified directory
#     without executing any code, and extracts the file path from the top comment of each file.
    
#     Parameters:
#         directory (str): Path to the directory containing Python files.
    
#     Returns:
#         dict: A dictionary where each key is a module name and each value is a dictionary
#               containing the file path and another dictionary mapping function names to their docstrings.
#     """
#     all_docstrings = {}
    
#     for file in os.listdir(directory):
#         if file.endswith(".py") and not file.startswith("__"):
#             module_name = file[:-3]  # Remove the .py extension
#             file_path = os.path.join(directory, file)
#             doc_info = extract_functions_docstrings_from_file(file_path)
#             all_docstrings[module_name] = doc_info
#     return all_docstrings
def load_docstrings(directory: str = "codebase"):
    """
    Loads all top-level function docstrings from Python files in *directory*
    without executing any code.  If a file can’t be parsed, its error is
    stored in an `"error"` field.
    """
    all_docstrings = {}

    for file in os.listdir(directory):
        if file.endswith(".py") and not file.startswith("__"):
            module     = file[:-3]                  # drop '.py'
            file_path  = os.path.join(directory, file)
            try:
                all_docstrings[module] = extract_functions_docstrings_from_file(file_path)
            except Exception as err:
                all_docstrings[module] = {
                    "file_path": file_path,
                    "functions": {},               # ALWAYS a dict
                    "error": f"{err.__class__.__name__}: {err}",
                }
    return all_docstrings


def load_plots(directory: str) -> list:
    """
    Recursively searches for image files (png, jpg, jpeg, gif) in directory and all subdirectories.
    Excludes checkpoint files from Jupyter notebooks.
    Returns plots sorted by modification time (oldest first).
    """
    image_extensions = ('.png', '.jpg', '.jpeg', '.gif')
    directory = Path(directory)
    image_files = [path for path in directory.rglob('*') 
                   if path.suffix.lower() in image_extensions 
                   and '.ipynb_checkpoints' not in str(path)]
    
    # Sort by modification time (oldest first)
    image_files.sort(key=lambda x: x.stat().st_mtime)
    
    return [str(path) for path in image_files]