import base64
import autogen
from typing import Literal
from openai import OpenAI
from google import genai
from google.genai import types
from autogen.agentchat.group import ContextVariables
from .utils import get_api_keys_from_env

cmbagent_debug = autogen.cmbagent_debug

# TODO: add this to config
vlm_model: Literal["gpt-4o", "gemini-2.5-flash"] = "gemini-2.5-flash"
_last_executed_code = None


class OpenAICompletion:
    """
    OpenAI-style response object to structure Gemini (and other) outputs.
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


def send_image_to_vlm(base_64_img: str, vlm_prompt: str) -> str | OpenAICompletion:
    """
    Send the encoded image to a VLM model and return the completion.
    """
    api_keys = get_api_keys_from_env()

    # Check if model in OpenAI family
    if vlm_model in ["gpt-4o"]:
        client = OpenAI(api_key=api_keys["OPENAI"])

        # External OpenAI API call
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
        )
        
        if cmbagent_debug:
            print(f"VLM prompt:\n{vlm_prompt}")
            
        return completion

    # Check if model in Gemini family
    elif vlm_model in ["gemini-2.5-flash"]:
        client = genai.Client(api_key=api_keys["GEMINI"])
        
        # Convert base64 image to bytes
        image_bytes = base64.b64decode(base_64_img)
        
        # External Gemini API call
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[
                types.Part.from_bytes(
                    data=image_bytes,
                    mime_type='image/png',
                ),
                vlm_prompt
            ]
        )
        
        if cmbagent_debug:
            print(f"VLM prompt:\n{vlm_prompt}")
            print(f"VLM response:\n{response.text}")
        
        # UsageMetadata JSON representation at https://ai.google.dev/api/generate-content
        return OpenAICompletion(response.text, 
                              response.usage_metadata.prompt_token_count, 
                              response.usage_metadata.total_token_count - response.usage_metadata.prompt_token_count,
                              response.usage_metadata.total_token_count)


# TODO: explicitly test routing when plotting fails 2+ times (via injection, to be safe)

def account_for_external_api_calls(agent, completion):
    """
    Helper function to add external VLM API call tokens to agent's cost tracking.
    """
    # Initialize cost_dict if it doesn't exist (same pattern as display_cost)
    if not hasattr(agent, "cost_dict") or not agent.cost_dict.get("Agent"):
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
    pricing = {
        "gpt-4o":           {"input": 2.50, "output": 10.00},
        "gemini-2.5-flash": {"input": 0.00, "output": 0.00},
    }
    
    # Calculate cost based on model
    input_cost  = (prompt_tokens / 1_000_000) * pricing[vlm_model]["input"]
    output_cost = (completion_tokens / 1_000_000) * pricing[vlm_model]["output"]
    total_cost  = input_cost + output_cost
    
    # Add to agent's cost tracking (display cost expects defaultdict(list), so append for later sum calc)
    agent.cost_dict["Agent"].append("plot_judge")
    agent.cost_dict["Cost"].append(total_cost)
    agent.cost_dict["Prompt Tokens"].append(prompt_tokens)
    agent.cost_dict["Completion Tokens"].append(completion_tokens)
    agent.cost_dict["Total Tokens"].append(total_tokens)
    
    print(f"VLM tokens added to plot_judge: {prompt_tokens} prompt, {completion_tokens} completion, {total_tokens} total")
    print(f"VLM cost: ${total_cost:.8f}")
    

def create_vlm_prompt(context_variables: ContextVariables) -> str:
        # Fetch relevant task context
        # current_task = context_variables.get("current_sub_task", "No specific task provided")
        # current_instructions = context_variables.get("current_instructions", "No specific instructions provided")
        improved_main_task = context_variables.get("improved_main_task", "No improved main task provided.")
        # NOTE: if using the above context, add a criterion for relevance

        vlm_prompt = f"""
You are a plot judge analyzing a scientific plot. Your task is to evaluate the plot's quality and provide feedback.

Please analyze this plot and provide your assessment. You must evaluate ALL of the following criteria:

## EVALUATION CRITERIA:

1. **Scientific Accuracy** - Is the plot scientifically correct?
   - Are the data points, calculations, and scientific principles accurate?
   - Are the units, scales, and relationships correct?
   - Are there any mathematical or scientific errors?

2. **Visual Clarity** - Is the plot easy to read and understand?
   - Can the plot be interpreted without confusion?
   - Are the data points, lines, and trends clearly visible?
   - Is the color scheme and contrast appropriate?

3. **Completeness** - Does the plot show all necessary information?
   - Does it have a clear title, axis labels, and legend?
   - Are units shown where appropriate?
   - Are all necessary data series included?
   - Is the plot self-contained and informative?

4. **Professional Presentation** - Is the plot professionally formatted?
   - Are labels, legends, and titles clear and appropriate?
   - Is the layout clean and uncluttered?
   - Are fonts, colors, and styling professional?

## YOUR RESPONSE:
Provide your analysis in a clear, structured format with specific observations and recommendations. Be thorough and critical - the plot will only be accepted if ALL criteria are met. If any criterion is not satisfied, provide specific, actionable feedback for improvement.
"""
 
        if cmbagent_debug:
            print(f"VLM prompt:\n{vlm_prompt}")

        return vlm_prompt