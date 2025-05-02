import os
from cmbagent.base_agent import BaseAgent
from pydantic import BaseModel, Field
from typing import List, Optional, Literal, Dict, Any
import json
from pathlib import Path


class Subtasks(BaseModel):
    sub_task: str = Field(..., description="The sub-task to be performed")
    sub_task_agent: Literal["engineer", "researcher", "perplexity", "idea_maker", "idea_hater", "classy_sz_agent", "camb_agent"] =  Field(..., description="The name of the agent in charge of the sub-task")
    bullet_points: List[str] = Field(
        ..., description="A list of bullet points explaining what the sub-task should do"
    )

class PlannerResponse(BaseModel):
    # main_task: str = Field(..., description="The exact main task to solve.")
    sub_tasks: List[Subtasks]

    def format(self) -> str:
        plan_output = ""
        for i, step in enumerate(self.sub_tasks):
            plan_output += f"\n- Step {i + 1}:\n\t* sub-task: {step.sub_task}\n\t* agent in charge: {step.sub_task_agent}\n"
            if step.bullet_points:
                plan_output += f"\n\t* instructions:\n"
                for bullet in step.bullet_points:
                    plan_output += f"\t\t- {bullet}\n"
        message = f"""
**PLAN**
{plan_output}
        """
        return message
        

class PlannerResponseFormatterAgent(BaseAgent):
    
    def __init__(self, llm_config=None, **kwargs):

        agent_id = os.path.splitext(os.path.abspath(__file__))[0]

        llm_config['config_list'][0]['response_format'] = PlannerResponse

        super().__init__(llm_config=llm_config, agent_id=agent_id, **kwargs)


    def set_agent(self,**kwargs):

        super().set_assistant_agent(**kwargs)








def _parse_plan_string(plan_str: str) -> List[Dict[str, Any]]:
    """
    Convert the markdown‐style plan string produced by PlannerResponse.format()
    back into a list[dict] matching the Subtasks model.
    """
    lines = [ln.rstrip() for ln in plan_str.splitlines()]
    subtasks: List[Dict[str, Any]] = []
    current: Dict[str, Any] | None = None
    in_instr = False

    for ln in lines:
        ln_stripped = ln.lstrip()

        # --- step header ----------------------------------------------------
        if ln_stripped.startswith("- Step"):
            if current:
                subtasks.append(current)
            current = {"bullet_points": []}
            in_instr = False
            continue

        # --- sub‑task -------------------------------------------------------
        if ln_stripped.startswith("* sub-task:"):
            if current is None:    # defensive
                current = {"bullet_points": []}
            current["sub_task"] = ln_stripped.removeprefix("* sub-task:").strip()
            continue

        # --- agent in charge -----------------------------------------------
        if ln_stripped.startswith("* agent in charge:"):
            current["sub_task_agent"] = (
                ln_stripped.removeprefix("* agent in charge:").strip()
            )
            continue

        # --- instructions block start --------------------------------------
        if ln_stripped.startswith("* instructions:"):
            in_instr = True
            continue

        # --- bullet points --------------------------------------------------
        if in_instr and ln_stripped.startswith("-"):
            current["bullet_points"].append(ln_stripped.removeprefix("-").strip())

    # add last task if any
    if current:
        subtasks.append(current)

    return subtasks


def save_final_plan(final_context: Dict[str, Any], work_dir: str) -> Path:
    """
    Save `final_context["final_plan"]` as structured JSON at
    <work_dir>/planning/final_plan.json.

    The JSON structure complies with:
        {
            "sub_tasks": [
                {
                    "sub_task": "...",
                    "sub_task_agent": "...",
                    "bullet_points": [...]
                },
                ...
            ]
        }
    """
    planning_dir = work_dir


    if "final_plan" not in final_context:
        raise KeyError('"final_plan" key missing from final_context')

    plan_obj = final_context["final_plan"]

    # ---- Case 1: a Pydantic object ----------------------------------------
    if hasattr(plan_obj, "model_dump"):          # Pydantic v2
        plan_dict = plan_obj.model_dump()
    elif hasattr(plan_obj, "dict"):              # Pydantic v1
        plan_dict = plan_obj.dict()

    # ---- Case 2: already a dict / list ------------------------------------
    elif isinstance(plan_obj, (dict, list)):
        plan_dict = {"sub_tasks": plan_obj} if isinstance(plan_obj, list) else plan_obj

    # ---- Case 3: formatted string -----------------------------------------
    elif isinstance(plan_obj, str):
        plan_dict = {"sub_tasks": _parse_plan_string(plan_obj)}
    else:
        raise TypeError(
            '"final_plan" must be a PlannerResponse, dict/list, or formatted string'
        )

    # ---- Write the JSON ----------------------------------------------------
    json_path = planning_dir / "final_plan.json"
    with json_path.open("w", encoding="utf-8") as fp:
        json.dump(plan_dict, fp, ensure_ascii=False, indent=4)

    return json_path

