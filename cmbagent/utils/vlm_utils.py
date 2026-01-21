import base64
import json
import autogen
from typing import Literal
from openai import OpenAI
from google import genai
from google.genai import types
from autogen.agentchat.group import ContextVariables
from pydantic import BaseModel, Field
from .utils import get_api_keys_from_env
from .vlm_injections import scientific_context, get_injection_by_name

cmbagent_debug = autogen.cmbagent_debug
_last_executed_code = None

# VLM model configuration
# TODO: when refactoring, make a one_shot dictionary argument for all of this
vlm_model: Literal["gpt-4o", "o3-2025-04-16", "gemini-2.5-flash", "gemini-2.5-pro"] = "gemini-2.5-pro"
vlm_criteria_mode: Literal["llm_generated", "cmb_power_spectra"] | None = "llm_generated"
executed_code_context: Literal["exact", "cmb_power_spectra_template", "mean_reversion_trading_template"] = "exact"
show_code_to_plot_judge: bool = False


def create_vlm_analysis_schema(context_variables: ContextVariables = None, has_code_context: bool = False):
    """
    Construct structured output schema. 
    Scientific accuracy field can be supplemented with domain-specific scientific criteria.
    When code context is available, adds a code_analysis field.
    """
    domain_criteria = ""
    llm_completion = None
    
    if vlm_criteria_mode == "llm_generated" and context_variables:
        # Generate criteria using LLM based on task context
        task = context_variables.get("improved_main_task", "scientific plot")
        plot_context = f"Task context: {task}. Focus on the plotting/visualization aspects of this task."
        llm_completion = generate_llm_scientific_criteria(plot_context)
        domain_criteria = llm_completion.choices[0].message.content
        
    elif vlm_criteria_mode == "cmb_power_spectra":
        # Use pre-defined CMB power spectra criteria
        domain_criteria = scientific_context["cmb_power_spectra"]
    
    # Build scientific accuracy description
    base_description = (
        "Assessment of scientific accuracy: Are the data points, calculations, and scientific principles accurate? "
        "Are the units, scales, and relationships correct? Are there any mathematical or scientific errors?"
    )
    
    if domain_criteria:
        scientific_accuracy_desc = f"{base_description}\n\nADDITIONAL DOMAIN-SPECIFIC CRITERIA:\n{domain_criteria}"
    else:
        scientific_accuracy_desc = base_description
        
    print(f"VLM scientific accuracy description:\n{scientific_accuracy_desc}")
    
    # Define base fields that are always present
    base_fields = {
        "scientific_accuracy": (str, Field(
            ...,
            description=scientific_accuracy_desc
        )),
        "visual_clarity": (str, Field(
            ...,
            description=(
                "Assessment of visual clarity: Can the plot be interpreted without confusion? "
                "Are the data points, axis scales, and lines clearly visible?"
            )
        )),
        "completeness": (str, Field(
            ...,
            description=(
                "Assessment of completeness: Does it have axis labels, a title, and units? "
                "Are all scientifically necessary elements included? "
                "The plot should be self-contained and informative without unnecessary elements."
                "Only comment on a missing legend if there are multiple data series. "
            )
        )),
        "professional_presentation": (str, Field(
            ...,
            description=(
                "Assessment of professional presentation: Are existing labels and titles clear and appropriate? "
                "Is the layout clean and uncluttered? Are fonts, colors, and styling professional? "
            )
        )),
    }
    
    # Add code analysis field if code context is available
    if has_code_context:
        base_fields["code_analysis"] = (str, Field(
            ...,
            description=(
                "Is the code implementation consistent with the plot?"
                "Is the code implementaiton consistent with the scientific concepts for the intended task?"
                "When confident about an issue's cause, refer to specific lines or parts of the code to pinpoint errors."
            )
        ))
    
    # Add final fields
    base_fields.update({
        "problems": (list[str], Field(
            ...,
            description=(
                "List of specific problems found in the plot. Each problem should be a clear, "
                "actionable issue that needs to be addressed. Be precise and descriptive since "
                "this will be passed to a debugger with code access but no image access. "
                "Only list actual problems - if a criterion is satisfied, don't include it."
            )
        )),
        "verdict": (Literal["continue", "retry"], Field(
            ...,
            description=(
                "Final verdict: 'continue' if plot meets standards, 'retry' if improvements needed. "
                "BE STRICT on scientific accuracy, visual clarity, and completeness - any errors should trigger retry. "
                "BE MODERATE on professional presentation - only retry for issues that significantly impact scientific communication, "
                "not minor aesthetic preferences like tweaks to existing legend placement, title specifity, axis labels, etc."
            )
        ))
    })
            
    # Create the VLMAnalysis class dynamically
    VLMAnalysis = type(
        "VLMAnalysis",
        (BaseModel,),
        {
            "__annotations__": {field_name: field_type for field_name, (field_type, _) in base_fields.items()},
            **{field_name: field_info for field_name, (_, field_info) in base_fields.items()},
            "__doc__": "Structured output schema for VLM plot analysis."
        }
    )
    
    # Store LLM completion for cost tracking
    if context_variables and llm_completion:
        context_variables["llm_completion"] = llm_completion
    
    return VLMAnalysis


class OpenAICompletion:
    """
    OpenAI-style response object to further structure Gemini (and other) outputs.
    Can also store cost information for LLM calls.
    """
    def __init__(self, text_response, prompt_tokens, completion_tokens, total_tokens, total_cost=0.0, model="unknown"):
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
        # Store cost information for external API tracking
        self.total_cost = total_cost
        self.model = model


def generate_wrong_plot_injection(plot_type: str = "wrong_scalar_amplitude"):
    """
    Generate wrong code and corresponding plot for VLM testing.
    Returns the wrong code as string and base64 encoded plot.
    """
    wrong_code, base64_image = get_injection_by_name(plot_type, executed_code_context)
    
    return wrong_code, base64_image


def send_image_to_vlm(base_64_img: str, vlm_prompt: str, inject_wrong_plot: bool | str = False, context_variables: ContextVariables = None) -> tuple[str | OpenAICompletion, str | None]:
    """
    Send the encoded image to a VLM model and return the completion.
    Returns (completion, injected_code) where injected_code is None if no injection occurred.
    """
    injected_code = None
    
    # Check if this is the first plot evaluation (n_plot_evals == 0) for injection
    n_plot_evals = context_variables.get("n_plot_evals", 0) if context_variables else 0
    
    # Inject wrong plot on first evaluation if requested
    if inject_wrong_plot and n_plot_evals == 0:
        print("Replacing plot with our own wrong plot")
        
        # Determine plot type from inject_wrong_plot parameter or global config
        if isinstance(inject_wrong_plot, str):
            plot_type = inject_wrong_plot
        else:
            from .vlm_injections import vlm_injection_plot_type
            plot_type = vlm_injection_plot_type  # Use global default
        
        # Generate wrong plot
        wrong_code, wrong_plot_base64 = generate_wrong_plot_injection(plot_type)
        base_64_img = wrong_plot_base64
        injected_code = wrong_code
        
        # Store the injected code in context variables for engineer feedback
        if context_variables:
            context_variables["latest_executed_code"] = wrong_code
    else:
        if cmbagent_debug:
            print(f"Using real plot (inject_wrong_plot={inject_wrong_plot}, n_plot_evals={n_plot_evals})")
    
    # Check if we have code context
    executed_code = context_variables.get("latest_executed_code") if context_variables else None
    has_code_context = show_code_to_plot_judge and executed_code is not None
    
    VLMAnalysis = create_vlm_analysis_schema(context_variables, has_code_context=has_code_context)
    api_keys = get_api_keys_from_env()

    if vlm_model in ["gpt-4o", "o3-2025-04-16"]:
        client = OpenAI(api_key=api_keys["OPENAI"])
        reasoning_effort = "medium"

        if cmbagent_debug:
            print(f"Using OpenAI VLM call with {reasoning_effort} reasoning effort")
            
        # External OpenAI API call with structured output
        try:
            completion = client.chat.completions.create(
                model=vlm_model,
                reasoning_effort=reasoning_effort,
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
            # Return a graceful fallback completion
            fallback_response = _create_fallback_response(has_code_context)
            return fallback_response, injected_code

    elif vlm_model in ["gemini-2.5-flash", "gemini-2.5-pro"]:
        if cmbagent_debug:
            print(f"VLM model: {vlm_model}")
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
            # Return a graceful fallback completion
            fallback_response = _create_fallback_response(has_code_context)
            return fallback_response, injected_code


def account_for_external_api_calls(agent, completion, call_type="VLM"):
    """
    Helper function to add external API call costs to agent's cost tracking.
    Handles both real OpenAI completion objects and custom OpenAICompletion objects.
    So, for LLM scientific criteria, create an OpenAICompletion object with the cost info.
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
    
    # Extract token counts from completion object (works for both real and custom OpenAI completions)
    prompt_tokens = completion.usage.prompt_tokens if completion.usage else 0
    completion_tokens = completion.usage.completion_tokens if completion.usage else 0
    total_tokens = completion.usage.total_tokens if completion.usage else 0
    
    # For VLM calls, calculate cost using the pricing table
    if call_type == "VLM":
        model = vlm_model
        # https://platform.openai.com/docs/pricing
        # https://ai.google.dev/gemini-api/docs/pricing
        pricing = {
            "gpt-4o":           {"input": 2.50, "output": 10.00},
            "o3-2025-04-16":    {"input": 2.00, "output":  8.00},
            "gemini-2.5-flash": {"input": 0.00, "output":  0.00},  # Free tier
            "gemini-2.5-pro":   {"input": 0.00, "output":  0.00},  # Free tier
        }
        
        input_cost = (prompt_tokens / 1_000_000) * pricing[model]["input"]
        output_cost = (completion_tokens / 1_000_000) * pricing[model]["output"]
        total_cost = input_cost + output_cost
    else:
        # For LLM calls, cost is already calculated and stored in the completion object
        total_cost = getattr(completion, 'total_cost', 0.0)
        model = getattr(completion, 'model', 'gpt-4o')
    
    # Skip if no cost to track
    if total_cost == 0:
        return
    
    # Add to agent's cost tracking
    agent_name = getattr(agent, 'name', 'plot_judge')
    agent.cost_dict["Agent"].append(agent_name)
    agent.cost_dict["Cost"].append(total_cost)
    agent.cost_dict["Prompt Tokens"].append(prompt_tokens)
    agent.cost_dict["Completion Tokens"].append(completion_tokens)
    agent.cost_dict["Total Tokens"].append(total_tokens)
    
    print(f"{call_type} tokens added to {agent_name}: {prompt_tokens} prompt, {completion_tokens} completion, {total_tokens} total")
    print(f"{call_type} cost ({model}): ${total_cost:.8f}")
    

def _create_base_vlm_prompt(improved_main_task: str) -> str:
    """Create the base VLM prompt without code context."""
    return f"""
You are a plot judge analyzing a scientific plot. Your task is to evaluate the plot's quality and provide structured feedback.
Analyze this plot across scientific accuracy, visual clarity, completeness, and professional presentation.
Context about the goal of the plot: {improved_main_task}

IMPORTANT: We do not use LaTeX rendering at all in plots, so do not ask for it or comment on unrendered TeX code.

Request plot elements that are scientifically beneficial for understanding the plot. 
Don't request them just for rubric completeness if the plot is clear without them. 
Don't request additional annotations.

Be thorough and critical - the plot will only be accepted if ALL criteria are met. 
If any criterion is not satisfied, list the specific problems found in the problems field.

Your verdict must be either "continue" (plot fully meets all criteria) or "retry" (plot needs improvements)."""


def _create_code_context_section(executed_code: str) -> str:
    """Create the code context section for VLM prompt."""
    return f"""

CODE CONTEXT:
The following is the Python code that generated this plot:
{executed_code}"""


def create_vlm_prompt(context_variables: ContextVariables, executed_code: str = None) -> str:
    """Create the complete VLM prompt including optional code context."""
    # Fetch relevant task context
    improved_main_task = context_variables.get("improved_main_task", "No improved main task provided.")
    
    # Create base prompt
    base_prompt = _create_base_vlm_prompt(improved_main_task)
    
    # Add code context if visibility is enabled and code is provided
    if show_code_to_plot_judge and executed_code:
        code_section = _create_code_context_section(executed_code)
        vlm_prompt = base_prompt + code_section
    else:
        vlm_prompt = base_prompt
 

    return vlm_prompt


def _create_fallback_response(has_code_context: bool = False):
    """Create a fallback VLM response when API calls fail."""
    fallback_dict = {
        "scientific_accuracy": "VLM analysis failed - continuing without evaluation",
        "visual_clarity": "VLM analysis failed",
        "completeness": "VLM analysis failed",
        "professional_presentation": "VLM analysis failed",
        "problems": ["VLM analysis failed - please check VLM configuration"],
        "verdict": "continue"
    }
    if has_code_context:
        fallback_dict["code_analysis"] = "VLM analysis failed - code could not be analyzed"
    
    return OpenAICompletion(
        json.dumps(fallback_dict),
        0, 0, 0
    )
    

def generate_llm_scientific_criteria(plot_description: str, plot_type: str = "scientific plot"):
    """
    Generate domain-specific scientific criteria using LLM based on plot description.
    Returns an OpenAICompletion object with cost information.
    """
    try:
        api_keys = get_api_keys_from_env()
        client = OpenAI(api_key=api_keys["OPENAI"])
        
        prompt = f"""You are a scientific expert analyzing plots. Generate domain-specific scientific accuracy criteria for evaluating a {plot_type}.

Context: {plot_description}

Your response will be used as criteria in a VLM prompt, so be direct and specific. Do not include conversational phrases.

First, identify key features that should have specific expected coordinates/values (x-axis, y-axis positions, ratios, etc.). For each feature, specify:
1. Expected x/y coordinates or values
2. What deviations indicate and why they're scientifically invalid
3. What physical processes cause these features

IMPORTANT: Only include features you're confident about. Skip any where the expected values can vary significantly or you're uncertain. 
It's better to have fewer, more reliable criteria than many uncertain ones.

Example format:
"Feature name: Expected at x ≈ [value], y ≈ [value]
- If shifted to x < [value]: indicates [physical cause] (invalid because [reason])
- If shifted to x > [value]: indicates [physical cause] (invalid because [reason])
- If shifted to y < [value]: indicates [physical cause] (invalid because [reason])

Example (stellar main sequence):
"Main sequence turnoff: Expected at B-V ≈ 0.6, M_V ≈ 4.0 for solar metallicity
- If shifted bluer (B-V < 0.4): indicates higher metallicity/younger age (invalid for old globular clusters)
- If shifted redder (B-V > 0.8): indicates lower metallicity/older age (invalid for young open clusters)"

Provide similar specific criteria for this plot type, focusing only on features with well-defined expected values.

There are cases where specific values are not known beforehand or not the focus of the plot.
When this is the case, provide other distinct and discrete features that are required to be present.

Example (exoplanet transit light curve):
Check the following features:
- Phase of transit dip: should be at phase = 0.0
    - If not at phase = 0.0 → indicates wrong timing or ephemeris (invalid for phased data)
- Shape of transit dip: should be symmetric and flat-bottomed
    - If not symmetric/flat-bottomed → indicates wrong planet/star size or reduction error (invalid for known system)
- Depth of transit dip: should reach normalized flux ≈ 0.99 (1% depth)
    - If depth is incorrect → indicates wrong planet/star size or reduction error (invalid for known system)
- Ingress and egress: should have similar duration
    - If durations differ → may indicate reduction error or systematics
- Baseline flux: should be flat at flux = 1.0 before and after transit
    - If baseline is not flat → indicates improper normalization or stellar variability (invalid for detrended light curves)
"""

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800,
        )
        
        # Extract cost information
        prompt_tokens = response.usage.prompt_tokens if response.usage else 0
        completion_tokens = response.usage.completion_tokens if response.usage else 0
        total_tokens = response.usage.total_tokens if response.usage else 0
        
        # Calculate cost (OpenAI GPT-4o pricing: $2.50 input, $10.00 output per 1M tokens)
        input_cost = (prompt_tokens / 1_000_000) * 2.50
        output_cost = (completion_tokens / 1_000_000) * 10.00
        total_cost = input_cost + output_cost
        
        # Return OpenAICompletion object with cost information
        return OpenAICompletion(
            text_response=response.choices[0].message.content.strip(),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            total_cost=total_cost,
            model="gpt-4o"
        )
        
    except Exception as e:
        print(f"ERROR: Failed to generate LLM scientific criteria: {e}")
        return OpenAICompletion("", 0, 0, 0, 0.0, "gpt-4o")


class PlotDebuggerResponse(BaseModel):
    """Structured response from plot debugger."""
    fixes: list[str] = Field(
        ...,
        description="List of targeted, actionable fixes for the code. Each fix should reference specific code lines when possible, or provide helpful considerations for broader issues."
    )


def call_external_plot_debugger(task_context: str, vlm_analysis: str, problems: list[str], executed_code: str) -> list[str]:
    """
    Call external Gemini 2.5 Pro to analyze problems and generate targeted fixes.
    
    Args:
        task_context: The main task context
        vlm_analysis: Full VLM analysis JSON
        problems: List of problems identified by VLM
        executed_code: The code that generated the plot
    
    Returns:
        List of targeted fixes, or empty list on failure
    """
    try:
        from google import genai
        from google.genai import types
        
        api_keys = get_api_keys_from_env()
        if not api_keys.get("GEMINI"):
            print("WARNING: No Gemini API key found, returning empty fixes")
            return []
            
        client = genai.Client(api_key=api_keys["GEMINI"])
        
        prompt = f"""You are a plot debugging expert. The VLM has identified problems with a plot, and you need to provide targeted code fixes.

TASK CONTEXT:
{task_context}

VLM ANALYSIS:
{vlm_analysis}

PROBLEMS IDENTIFIED:
{'\n'.join(f"- {p}" for p in problems)}

EXECUTED CODE:
{executed_code}

YOUR JOB:
Analyze the code and provide targeted fixes for each problem. Multiple problems can often be caused by a single underlying issue in the code, so focus on root causes rather than symptoms.

For each fix:
- If you can identify specific code lines/sections: Reference them directly (e.g., "Line 15: change plt.xlim() to...")  
- If the issue is broader: Provide helpful considerations for the engineer
- Be concrete and actionable, focusing on code changes needed to address the visual/scientific issues
- Group related problems into single fixes when appropriate"""

        
        response = client.models.generate_content(
            model="gemini-2.5-pro",
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "response_schema": PlotDebuggerResponse.model_json_schema(),
                "temperature": 0.1,
            }
        )
        
        # Parse structured response
        debugger_response = response.candidates[0].content.parts[0].text
        parsed_response = PlotDebuggerResponse.model_validate_json(debugger_response)
        
        return parsed_response.fixes
            
    except Exception as e:
        print(f"ERROR: External Gemini debugger failed: {e}")
        return []