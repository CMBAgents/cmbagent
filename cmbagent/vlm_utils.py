import base64
import autogen
from typing import Literal
from openai import OpenAI
from google import genai
from google.genai import types
from autogen.agentchat.group import ContextVariables
from .utils import get_api_keys_from_env
from pydantic import BaseModel, Field
import json

# For injecting wrong plots
import os
import tempfile
import matplotlib.pyplot as plt
import numpy as np

cmbagent_debug = autogen.cmbagent_debug

# TODO: add this to config
vlm_model: Literal["gpt-4o", "gemini-2.5-flash"] = "gpt-4o"
_last_executed_code = None
_vlm_first_call = True    # Flag first VLM call for injection
INJECT_WRONG_PLOT = True  # Enable/disable plot injection for testing


class VLMAnalysis(BaseModel):
    """
    Structured output schema for VLM plot analysis.
    """
    scientific_accuracy: str = Field(description="Assessment of scientific accuracy: Are the data points, calculations, and scientific principles accurate? Are the units, scales, and relationships correct? Are there any mathematical or scientific errors?")
    visual_clarity: str = Field(description="Assessment of visual clarity: Can the plot be interpreted without confusion? Are the data points, lines, and trends clearly visible? Is the color scheme and contrast appropriate?")
    completeness: str = Field(description="Assessment of completeness: Does it have a clear title, axis labels, and legend? Are units shown where appropriate? Are all necessary data series included? Is the plot self-contained and informative?")
    professional_presentation: str = Field(description="Assessment of professional presentation: Are labels, legends, and titles clear and appropriate? Is the layout clean and uncluttered? Are fonts, colors, and styling professional?")
    recommendations: str = Field(description="Specific recommendations for improvement if any criteria are not met. Provide actionable fixes that can be implemented.")
    verdict: Literal["continue", "retry"] = Field(description="Final verdict: 'continue' if plot meets ALL criteria and is fully accepted, 'retry' if ANY improvements are needed and plot must be fixed and resubmitted")


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


def generate_wrong_plot_injection():
    """
    Generate wrong code and corresponding plot for VLM testing.
    Returns the wrong code as string and base64 encoded plot.
    Saves a copy to synthetic_output for debugging but uses temp file for VLM.
    """
    # Wrong code
    wrong_code = """
import numpy as np
import matplotlib.pyplot as plt
x = np.linspace(-5, 5, 100)
y = -x**2
plt.figure(figsize=(6, 6))
plt.plot(x, y)
plt.title("y = x^2")
plt.xlabel("x")
plt.ylabel("y")
plt.grid(True)
plt.savefig("plot_6.png")
"""
    
    # Generate the actual wrong plot in memory
    x = np.linspace(-5, 5, 100)
    y = -x**2
    plt.figure(figsize=(6, 6))
    plt.plot(x, y)
    plt.title("y = x^2")
    plt.xlabel("x")
    plt.ylabel("y")
    plt.grid(True)
    
    # Save to temporary file for VLM processing
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp_file:
        plt.savefig(tmp_file.name, dpi=100, bbox_inches='tight')
        
        # Also save a copy to synthetic_output for debugging
        try:
            synthetic_dir = "/Users/kahaan/Downloads/cmbagent/synthetic_output"
            os.makedirs(synthetic_dir, exist_ok=True)
            debug_path = os.path.join(synthetic_dir, "injected_wrong_plot.png")
            plt.savefig(debug_path, dpi=100, bbox_inches='tight')
            print(f"DEBUG: Injected plot saved to {debug_path} for reference")
        except Exception as e:
            print(f"DEBUG: Could not save injected plot to synthetic_output: {e}")
        
        plt.close()
        
        # Read and encode as base64
        with open(tmp_file.name, 'rb') as f:
            image_data = f.read()
            base64_image = base64.b64encode(image_data).decode('utf-8')
        
        # Clean up temporary file
        os.unlink(tmp_file.name)
    
    return wrong_code, base64_image


def send_image_to_vlm(base_64_img: str, vlm_prompt: str, inject_wrong_plot: bool = False, context_variables: ContextVariables = None) -> tuple[str | OpenAICompletion, str | None]:
    """
    Send the encoded image to a VLM model and return the completion.
    Returns (completion, injected_code) where injected_code is None if no injection occurred.
    """
    global _vlm_first_call
    injected_code = None
    
    # Inject wrong plot on first call if requested
    if inject_wrong_plot and _vlm_first_call:
        print("DEBUG: [INJECTION] Replacing plot with our own wrong plot")
        
        # Generate wrong plot in memory (no disk persistence)
        wrong_code, wrong_plot_base64 = generate_wrong_plot_injection()
        base_64_img = wrong_plot_base64
        injected_code = wrong_code
        _vlm_first_call = False  # Disable injection for subsequent calls
        
        # Store the injected code in context variables for engineer feedback
        if context_variables:
            context_variables["latest_executed_code"] = wrong_code
    else:
        print(f"DEBUG: [NO INJECTION] Using real plot (inject_wrong_plot={inject_wrong_plot}, _vlm_first_call={_vlm_first_call})")
    
    api_keys = get_api_keys_from_env()

    # Check if model in OpenAI family
    if vlm_model in ["gpt-4o"]:
        client = OpenAI(api_key=api_keys["OPENAI"])

        # External OpenAI API call with structured output
        completion = client.chat.completions.create(
            model='gpt-4o',
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

    # Check if model in Gemini family
    elif vlm_model in ["gemini-2.5-flash"]:
        client = genai.Client(api_key=api_keys["GEMINI"])
        
        # Convert base64 image to bytes
        image_bytes = base64.b64decode(base_64_img)
        
        # External Gemini API call with structured output
        response = client.models.generate_content(
            model='gemini-2.5-flash',
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


# TODO: explicitly test routing when plotting fails 2+ times (via injection, to be safe)

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
        "gemini-2.5-flash": {"input": 0.00, "output": 0.00},  # Free tier
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
        # improved_main_task = context_variables.get("improved_main_task", "No improved main task provided.")
        # NOTE: if using the above context, add a criterion for relevance

        vlm_prompt = f"""
You are a plot judge analyzing a scientific plot. Your task is to evaluate the plot's quality and provide structured feedback.

Analyze this plot across four key criteria: scientific accuracy, visual clarity, completeness, and professional presentation.

You will respond with a structured JSON object. Be thorough and critical - the plot will only be accepted if ALL criteria are met. 

If any criterion is not satisfied, provide specific, actionable recommendations for improvement.

Your verdict must be either "continue" (plot fully meets all criteria) or "retry" (plot needs improvements).
"""
 
        if cmbagent_debug:
            print(f"VLM prompt:\n{vlm_prompt}")

        return vlm_prompt