from pydantic import BaseModel

# engineer response

class EngineerResponse(BaseModel):
    # steps: list[Step]
    # step: Step
    code_explanation: str
    python_code: str
    current_status_and_next_step_suggestion: str
    next_agent_suggestion: str

    def format(self) -> str:
        # steps_output = "\n".join(
        #     f"Step {i + 1}: {step.code_explanation}\n  Code snippet: \n {step.python_code}" for i, step in enumerate(self.steps)
        # )
        return f"""
**Code Explanation:**

{self.code_explanation}

**Python Code:**

```python
{self.python_code}
```

**Current Status and Next Step Suggestion:**

{self.current_status_and_next_step_suggestion}

**Next Agent Suggestion:**

{self.next_agent_suggestion}
        """

# planner response

class Subtasks(BaseModel):
    sub_task: str
    sub_task_agent: str


class PlannerResponse(BaseModel):
    main_task: str
    sub_tasks: list[Subtasks]
    next_step_suggestion: str
    next_agent_suggestion: str

    def format(self) -> str:
        plant_output = "\n".join(
            f"- Step {i + 1}:\n\t * sub-task: {step.sub_task}\n\t * agent: {step.sub_task_agent}" for i, step in enumerate(self.sub_tasks)
        )
        return f"""
**PLAN:**

- Main task: {self.main_task}

{plant_output}

**Next Step Suggestion:**

{self.next_step_suggestion}

**Next Agent Suggestion:**

{self.next_agent_suggestion}
        """