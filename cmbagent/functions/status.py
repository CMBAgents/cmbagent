"""Functions for status tracking and workflow control."""

import os
from typing import Literal
from autogen.agentchat.group import ContextVariables
from autogen.agentchat.group import AgentTarget, ReplyResult
from autogen.cmbagent_utils import cmbagent_debug, IMG_WIDTH
import autogen

from IPython.display import Image as IPImage, display as ip_display

from .utils import load_docstrings, load_plots


cmbagent_disable_display = autogen.cmbagent_disable_display


def _get_status_icon(status: str) -> str:
    """Get emoji icon for status."""
    status_icons = {
        "completed": "✅",
        "failed": "❌",
        "in progress": "⏳"
    }
    return status_icons.get(status, "")


def _update_context_variables(
    context_variables: ContextVariables,
    current_status: str,
    current_plan_step_number: int,
    current_sub_task: str,
    current_instructions: str,
    agent_for_sub_task: str
) -> None:
    """Update context variables with current step information."""
    context_variables["current_plan_step_number"] = current_plan_step_number
    context_variables["current_sub_task"] = current_sub_task
    context_variables["agent_for_sub_task"] = agent_for_sub_task
    context_variables["current_instructions"] = current_instructions
    context_variables["current_status"] = current_status


def _load_codebase_info(cmbagent_instance, context_variables: ContextVariables) -> str:
    """Load and format docstrings from codebase."""
    codes = os.path.join(cmbagent_instance.work_dir, context_variables['codebase_path'])
    docstrings = load_docstrings(codes)

    output_str = ""
    for module, info in docstrings.items():
        output_str += "-----------\n"
        output_str += f"Filename: {module}.py\n"
        output_str += f"File path: {info['file_path']}\n\n"

        # Show parse errors (if any)
        if "error" in info:
            output_str += f"⚠️  Parse error: {info['error']}\n\n"

        output_str += "Available functions:\n"

        if info["functions"]:
            for func, doc in info["functions"].items():
                output_str += f"function name: {func}\n"
                output_str += "````\n"
                output_str += f"{doc or '(no docstring)'}\n"
                output_str += "````\n\n"
        else:
            output_str += "(none)\n\n"

    return output_str


def _display_new_images(cmbagent_instance, context_variables: ContextVariables) -> None:
    """Load and display new plot images from data directory."""
    data_directory = os.path.join(cmbagent_instance.work_dir, context_variables['database_path'])
    image_files = load_plots(data_directory)

    displayed_images = context_variables.get("displayed_images", [])
    new_images = [img for img in image_files if img not in displayed_images]

    for img_file in new_images:
        if not cmbagent_disable_display:
            ip_display(IPImage(filename=img_file, width=2 * IMG_WIDTH))
        else:
            print(f"\n- Saved {img_file}")

    context_variables["displayed_images"] = displayed_images + new_images


def _initialize_transfer_flags(context_variables: ContextVariables) -> None:
    """Initialize all agent transfer flags to False."""
    agent_transfer_map = {
        "engineer": "transfer_to_engineer",
        "researcher": "transfer_to_researcher",
        "idea_maker": "transfer_to_idea_maker",
        "idea_hater": "transfer_to_idea_hater",
        "camb_context": "transfer_to_camb_context",
    }

    for flag_name in agent_transfer_map.values():
        context_variables[flag_name] = False
    context_variables["transfer_to_classy_context"] = False


def _determine_next_agent_human_in_loop(cmbagent_instance, context_variables: ContextVariables):
    """Determine next agent for human-in-the-loop mode."""
    agent_transfer_map = {
        "engineer": "transfer_to_engineer",
        "researcher": "transfer_to_researcher",
        "idea_maker": "transfer_to_idea_maker",
        "idea_hater": "transfer_to_idea_hater",
        "camb_context": "transfer_to_camb_context",
    }

    agent_to_transfer_to = None

    if "in progress" in context_variables["current_status"]:
        agent_name = context_variables["agent_for_sub_task"]
        if agent_name in agent_transfer_map:
            transfer_flag = agent_transfer_map[agent_name]
            context_variables[transfer_flag] = True
            agent_to_transfer_to = cmbagent_instance.get_agent_from_name(agent_name)

    if "completed" in context_variables["current_status"]:
        agent_to_transfer_to = cmbagent_instance.get_agent_from_name('admin')
        context_variables["n_attempts"] = 0

    if "failed" in context_variables["current_status"]:
        if context_variables["agent_for_sub_task"] == "engineer":
            agent_to_transfer_to = cmbagent_instance.get_agent_from_name('engineer')
        elif context_variables["agent_for_sub_task"] == "researcher":
            agent_to_transfer_to = cmbagent_instance.get_agent_from_name('researcher_response_formatter')

    return agent_to_transfer_to


def _determine_next_agent_default(cmbagent_instance, context_variables: ContextVariables):
    """Determine next agent for default mode."""
    agent_transfer_map = {
        "engineer": "transfer_to_engineer",
        "researcher": "transfer_to_researcher",
        "idea_maker": "transfer_to_idea_maker",
        "idea_hater": "transfer_to_idea_hater",
        "camb_context": "transfer_to_camb_context",
    }

    agent_to_transfer_to = None

    if "in progress" in context_variables["current_status"]:
        agent_name = context_variables["agent_for_sub_task"]
        if agent_name in agent_transfer_map:
            transfer_flag = agent_transfer_map[agent_name]
            context_variables[transfer_flag] = True
            agent_to_transfer_to = cmbagent_instance.get_agent_from_name(agent_name)

        if cmbagent_instance.mode == "deep_research" and \
           context_variables["current_plan_step_number"] != cmbagent_instance.step:
            agent_to_transfer_to = cmbagent_instance.get_agent_from_name('terminator')

    if "completed" in context_variables["current_status"]:
        if context_variables["current_plan_step_number"] == context_variables["number_of_steps_in_plan"]:
            agent_to_transfer_to = cmbagent_instance.get_agent_from_name('terminator')
        else:
            agent_to_transfer_to = cmbagent_instance.get_agent_from_name('controller')
            if cmbagent_instance.mode != "deep_research":
                context_variables["n_attempts"] = 0

    if "failed" in context_variables["current_status"]:
        if context_variables["agent_for_sub_task"] == "engineer":
            agent_to_transfer_to = cmbagent_instance.get_agent_from_name('engineer')
        elif context_variables["agent_for_sub_task"] == "researcher":
            agent_to_transfer_to = cmbagent_instance.get_agent_from_name('researcher_response_formatter')

    return agent_to_transfer_to


def _format_status_message(context_variables: ContextVariables, icon: str) -> str:
    """Format the status message."""
    return f"""
**Step number:** {context_variables["current_plan_step_number"]} out of {context_variables["number_of_steps_in_plan"]}.

**Sub-task:** {context_variables["current_sub_task"]}

**Agent in charge of sub-task:** `{context_variables["agent_for_sub_task"]}`

**Instructions:**

{context_variables["current_instructions"]}

**Status:** {context_variables["current_status"]} {icon}
        """


def create_record_status(cmbagent_instance, controller):
    """Factory function to create record_status with cmbagent instance."""

    def record_status(
        current_status: Literal["in progress", "failed", "completed"],
        current_plan_step_number: int,
        current_sub_task: str,
        current_instructions: str,
        agent_for_sub_task: Literal["engineer", "researcher", "idea_maker", "idea_hater",
                                    "camb_context", "classy_context", "aas_keyword_finder"],
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
            agent_for_sub_task (str): The agent responsible for the sub-task in the current step.
            context_variables (dict): Execution context dictionary.

        Returns:
            ReplyResult: Contains a formatted status message and updated context.
        """
        # Get status icon
        icon = _get_status_icon(current_status)

        # Update context variables
        _update_context_variables(
            context_variables, current_status, current_plan_step_number,
            current_sub_task, current_instructions, agent_for_sub_task
        )

        print("previous_steps_execution_summary: ", context_variables["previous_steps_execution_summary"])

        # Load codebase information
        context_variables["current_codebase"] = _load_codebase_info(cmbagent_instance, context_variables)

        # Display new images
        _display_new_images(cmbagent_instance, context_variables)

        # Initialize transfer flags
        _initialize_transfer_flags(context_variables)

        # Determine next agent based on mode
        if cmbagent_instance.mode == "human_in_the_loop":
            agent_to_transfer_to = _determine_next_agent_human_in_loop(cmbagent_instance, context_variables)
        else:
            agent_to_transfer_to = _determine_next_agent_default(cmbagent_instance, context_variables)

        # Debug logging
        if cmbagent_debug:
            if agent_to_transfer_to is None:
                print("agent_to_transfer_to is None")
            else:
                print("agent_to_transfer_to: ", agent_to_transfer_to.name)

        # Format and return result
        message = _format_status_message(context_variables, icon)

        if agent_to_transfer_to is None:
            target = AgentTarget(controller)
        else:
            target = AgentTarget(agent_to_transfer_to)

        return ReplyResult(target=target, message=message, context_variables=context_variables)

    return record_status


def create_record_status_starter(cmbagent_instance):
    """Factory function to create record_status_starter with cmbagent instance."""

    def record_status_starter(context_variables: ContextVariables) -> ReplyResult:
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
            "in progress": "⏳"
        }

        icon = status_icons.get(current_status, "")

        # Map agent names to their transfer flag names
        agent_transfer_map = {
            "engineer": "transfer_to_engineer",
            "researcher": "transfer_to_researcher",
            "idea_maker": "transfer_to_idea_maker",
            "idea_hater": "transfer_to_idea_hater",
            "camb_context": "transfer_to_camb_context",
        }

        # Initialize all transfer flags to False
        for flag_name in agent_transfer_map.values():
            context_variables[flag_name] = False

        agent_to_transfer_to = None
        if "in progress" in context_variables["current_status"]:
            agent_name = context_variables["agent_for_sub_task"]
            if agent_name in agent_transfer_map:
                transfer_flag = agent_transfer_map[agent_name]
                context_variables[transfer_flag] = True
                agent_to_transfer_to = cmbagent_instance.get_agent_from_name(agent_name)

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
            context_variables=context_variables
        )

    return record_status_starter
