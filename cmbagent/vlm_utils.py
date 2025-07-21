import base64
import autogen
from typing import Literal
from openai import OpenAI
from google import genai
from google.genai import types
from autogen.agentchat.group import ContextVariables
from pydantic import BaseModel, Field
from .utils import get_api_keys_from_env
from .vlm_injections import get_scientific_context
import json

cmbagent_debug = autogen.cmbagent_debug

# TODO: add this to config
# NOTE: injecting with max_n_plot_evals = 1 will be useless
vlm_model: Literal["gpt-4o", "o3-2025-04-16", "gemini-2.5-flash", "gemini-2.5-pro"] = "gemini-2.5-pro"
_last_executed_code = None


def create_vlm_analysis_schema(context_variables: ContextVariables = None):
    """
    Construct structured output schema with optional domain-specific scientific context.
    """
    scientific_criteria = ""
    if context_variables:
        injection_config = context_variables.get("injection_config", False)
        if injection_config and isinstance(injection_config, dict):
            scientific_context_type = injection_config.get("scientific_criteria", None)
            
            # Get plot description for LLM-generated criteria
            plot_description = None
            if scientific_context_type == "llm_generated":
                full_task = context_variables.get("improved_main_task", "scientific plot")
                # Extract plotting-specific context from the broader task
                plot_description = f"Task context: {full_task}. Focus on the plotting/visualization aspects of this task."
            
            scientific_criteria = get_scientific_context(scientific_context_type, plot_description)
    
    # Base scientific accuracy description
    scientific_accuracy_desc = (
        "Assessment of scientific accuracy: Are the data points, calculations, and scientific principles accurate? "
        "Are the units, scales, and relationships correct? Are there any mathematical or scientific errors?"
    )
    
    # Add scientific context if available
    if scientific_criteria:
        scientific_accuracy_desc += f"\n\nADDITIONAL DOMAIN-SPECIFIC CRITERIA:\n{scientific_criteria}"
    print(f"DEBUG: Scientific accuracy description:\n{scientific_accuracy_desc}")
            
    class VLMAnalysis(BaseModel):
        """
        Structured output schema for VLM plot analysis.
        """
        scientific_accuracy: str = Field(
            ...,
            description=scientific_accuracy_desc
        )
        visual_clarity: str = Field(
            ...,
            description=(
                "Assessment of visual clarity: Can the plot be interpreted without confusion? "
                "Are the data points, axis scales, and lines clearly visible?"
            )
        )
        completeness: str = Field(
            ...,
            description=(
                "Assessment of completeness: Does it have axis labels, a title, and units? "
                "Are all scientifically necessary elements included? "
                "The plot should be self-contained and informative without unnecessary elements."
                "Only comment on a missing legend if there are multiple data series. "
            )
        )
        professional_presentation: str = Field(
            ...,
            description=(
                "Assessment of professional presentation: Are existing labels and titles clear and appropriate? "
                "Is the layout clean and uncluttered? Are fonts, colors, and styling professional? "
                "We do not use LaTeX rendering at all in plots, so do not ask for it or comment on unrendered TeX code."
            )
        )
        recommendations: str = Field(
            ...,
            description=(
                "Specific recommendations for improvement if any criteria are not met. "
                "Provide actionable fixes that can be implemented."
            )
        )
        verdict: Literal["continue", "retry"] = Field(
            ...,
            description=(
                "Final verdict: 'continue' if plot meets standards, 'retry' if improvements needed. "
                "BE STRICT on scientific accuracy, visual clarity, and completeness - any errors should trigger retry. "
                "BE MODERATE on professional presentation - only retry for issues that significantly impact scientific communication, "
                "not minor aesthetic preferences like tweaks to existing legend placement, title specifity, axis labels, etc."
            )
        )
    
    return VLMAnalysis


class OpenAICompletion:
    """
    OpenAI-style response object to further structure Gemini (and other) outputs.
    """
    def __init__(self, text_response, prompt_tokens, completion_tokens, total_tokens):
        self.choices = [type('obj', (object,), {
            'message': type('obj', (object,), {
                'content': text_response
            })()
        })()]
        self.usage = type('obj', (object,), {
            'prompt_tokens': prompt_tokens,
            'completion_tokens': completion_tokens,
            'total_tokens': total_tokens
        })()


def generate_wrong_plot_injection(injection_config):
    """
    Generate wrong code and corresponding plot for VLM testing.
    Returns the wrong code as string and base64 encoded plot.
    """
    from .vlm_injections import get_injection
    
    # Get the injection using the new system
    wrong_code, base64_image = get_injection(injection_config)
    
    return wrong_code, base64_image


def send_image_to_vlm(base_64_img: str, vlm_prompt: str, inject_wrong_plot: bool = False, context_variables: ContextVariables = None) -> tuple[str | OpenAICompletion, str | None]:
    """
    Send the encoded image to a VLM model and return the completion.
    Returns (completion, injected_code) where injected_code is None if no injection occurred.
    """
    injected_code = None
    
    # Check if this is the first plot evaluation (n_plot_evals == 0) for injection
    n_plot_evals = context_variables.get("n_plot_evals", 0) if context_variables else 0
    
    # Inject wrong plot on first evaluation if requested
    if inject_wrong_plot and n_plot_evals == 0:
        print("DEBUG: [INJECTION] Replacing plot with our own wrong plot")
        
        # Get injection config from context variables
        injection_config = context_variables.get("injection_config", False) if context_variables else False
        
        # Generate wrong plot
        wrong_code, wrong_plot_base64 = generate_wrong_plot_injection(injection_config)
        base_64_img = wrong_plot_base64
        injected_code = wrong_code
        
        # Store the injected code in context variables for engineer feedback
        if context_variables:
            context_variables["latest_executed_code"] = wrong_code
    else:
        print(f"DEBUG: [NO INJECTION] Using real plot (inject_wrong_plot={inject_wrong_plot}, n_plot_evals={n_plot_evals})")
    
    # Create dynamic VLMAnalysis schema with scientific context
    VLMAnalysis = create_vlm_analysis_schema(context_variables)
    
    api_keys = get_api_keys_from_env()

    # Check if model in OpenAI family
    if vlm_model in ["gpt-4o", "o3-2025-04-16"]:
        client = OpenAI(api_key=api_keys["OPENAI"])

        # External OpenAI API call with structured output
        print("DEBUG: [VLM] Using OpenAI API call with reasoning_effort=medium")
        
        try:
            completion = client.chat.completions.create(
                model=vlm_model,
                reasoning_effort="medium",
                messages=[
                    {
                        'role': 'user',
                        'content': [
                            {'type': 'text', 'text': vlm_prompt},
                            {
                                'type': 'image_url',
                                'image_url': {
                                    'url': f'data:image/png;base64,{base_64_img}',
                                    'detail': 'auto'
                                }
                            }
                        ]
                    }
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "vlm_analysis",
                        "strict": True,
                        "schema": {
                            **VLMAnalysis.model_json_schema(),
                            "additionalProperties": False
                        }
                    }
                }
            )
            
            if cmbagent_debug:
                print(f"VLM prompt:\n{vlm_prompt}")
                
            return completion, injected_code
            
        except Exception as e:
            print(f"ERROR: VLM API call failed: {e}")
            # Return a fallback completion that allows the process to continue
            fallback_response = OpenAICompletion(
                '{"scientific_accuracy": "VLM analysis failed - continuing without evaluation", "visual_clarity": "VLM analysis failed", "completeness": "VLM analysis failed", "professional_presentation": "VLM analysis failed", "recommendations": "VLM analysis failed - please check VLM configuration", "verdict": "continue"}',
                0, 0, 0
            )
            return fallback_response, injected_code

    # Check if model in Gemini family
    elif vlm_model in ["gemini-2.5-flash", "gemini-2.5-pro"]:
        print(f"DEBUG: VLM model: {vlm_model}")
        client = genai.Client(api_key=api_keys["GEMINI"])
        
        try:
            # Convert base64 image to bytes
            image_bytes = base64.b64decode(base_64_img)
            
            # External Gemini API call with structured output
            response = client.models.generate_content(
                model=vlm_model,
                contents=[
                    types.Part.from_bytes(
                        data=image_bytes,
                        mime_type='image/png',
                    ),
                    vlm_prompt
                ],
                config={
                    "response_mime_type": "application/json",
                    "response_schema": VLMAnalysis.model_json_schema()
                }
            )
            
            if cmbagent_debug:
                print(f"VLM prompt:\n{vlm_prompt}")
                print(f"VLM response:\n{response.text}")
            
            # UsageMetadata JSON representation at https://ai.google.dev/api/generate-content
            prompt_tokens = response.usage_metadata.prompt_token_count
            total_tokens = response.usage_metadata.total_token_count
            completion_tokens = total_tokens - prompt_tokens  # candidates_token_count doesn't include all outputs
            
            return OpenAICompletion(response.text, 
                                  prompt_tokens, 
                                  completion_tokens,
                                  total_tokens), injected_code
                                  
        except Exception as e:
            print(f"ERROR: VLM API call failed: {e}")
            # Return a fallback completion that allows the process to continue
            fallback_response = OpenAICompletion(
                '{"scientific_accuracy": "VLM analysis failed - continuing without evaluation", "visual_clarity": "VLM analysis failed", "completeness": "VLM analysis failed", "professional_presentation": "VLM analysis failed", "recommendations": "VLM analysis failed - please check VLM configuration", "verdict": "continue"}',
                0, 0, 0
            )
            return fallback_response, injected_code


def account_for_external_api_calls(agent, completion):
    """
    Helper function to add external VLM API call tokens to agent's cost tracking.
    """
    # Initialize cost_dict if it doesn't exist (same pattern as AG2)
    if not hasattr(agent, "cost_dict"):
        agent.cost_dict = {
            "Agent": [],
            "Cost": [],
            "Prompt Tokens": [],
            "Completion Tokens": [],
            "Total Tokens": []
        }
    
    # Extract token counts
    prompt_tokens     = completion.usage.prompt_tokens if completion.usage else 0
    completion_tokens = completion.usage.completion_tokens if completion.usage else 0
    total_tokens      = completion.usage.total_tokens if completion.usage else 0
    
    # https://platform.openai.com/docs/pricing
    # https://ai.google.dev/gemini-api/docs/pricing
    pricing = {
        "gpt-4o":           {"input": 2.50, "output": 10.00},
        "o3-2025-04-16":    {"input": 2.00, "output":  8.00},
        "gemini-2.5-flash": {"input": 0.00, "output":  0.00},  # Free tier
        "gemini-2.5-pro":   {"input": 0.00, "output":  0.00},  # Free tier
    }
    
    # Calculate cost based on model
    input_cost  = (prompt_tokens / 1_000_000) * pricing[vlm_model]["input"]
    output_cost = (completion_tokens / 1_000_000) * pricing[vlm_model]["output"]
    total_cost  = input_cost + output_cost
    
    # Add to agent's cost tracking (display cost expects defaultdict(list), so append for later sum calc)
    agent_name = getattr(agent, 'name', 'plot_judge')
    agent.cost_dict["Agent"].append(agent_name)
    agent.cost_dict["Cost"].append(total_cost)
    agent.cost_dict["Prompt Tokens"].append(prompt_tokens)
    agent.cost_dict["Completion Tokens"].append(completion_tokens)
    agent.cost_dict["Total Tokens"].append(total_tokens)
    
    print(f"VLM tokens added to {agent_name}: {prompt_tokens} prompt, {completion_tokens} completion, {total_tokens} total")
    print(f"VLM cost: ${total_cost:.8f}")
    

def create_vlm_prompt(context_variables: ContextVariables) -> str:
        # Fetch relevant task context
        # current_task = context_variables.get("current_sub_task", "No specific task provided")
        # current_instructions = context_variables.get("current_instructions", "No specific instructions provided")
        improved_main_task = context_variables.get("improved_main_task", "No improved main task provided.")
        # NOTE: if using the above context, one can add a criterion for relevance

        vlm_prompt = f"""
You are a plot judge analyzing a scientific plot. Your task is to evaluate the plot's quality and provide structured feedback.
Analyze this plot across scientific accuracy, visual clarity, completeness, and professional presentation.
Context about the goal of the plot: {improved_main_task}

Request plot elements that are scientifically beneficial for understanding the plot. 
Don't request them just for rubric completeness if the plot is clear without them. 
Don't request additional annotations.

Be thorough and critical - the plot will only be accepted if ALL criteria are met. 
If any criterion is not satisfied, provide specific, actionable recommendations for improvement.

Your verdict must be either "continue" (plot fully meets all criteria) or "retry" (plot needs improvements).
"""
 
        # print(f"DEBUG: VLM prompt:\n{vlm_prompt}")

        return vlm_prompt