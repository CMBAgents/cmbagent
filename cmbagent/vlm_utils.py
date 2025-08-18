import base64
import json
import os
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


def create_vlm_correction_schema(context_variables: ContextVariables = None, has_code_context: bool = False):
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


def create_vlm_evaluation_schema(context_variables: ContextVariables = None) -> type:
    """
    Phase 3: VLM analyzes comparison plot and selects winner.
    Focuses purely on analysis and winner selection - not task creation.
    """
    base_fields = {
        "experiment_analysis": (str, Field(
            description="Analysis of each experiment shown in the comparison plot and their performance"
        )),
        "metric_comparison": (str, Field(
            description="Detailed comparison of the metric values - which experiments performed best and why"
        )),
        "winner_selection": (str, Field(
            description="Name of the winning experiment (including 'Original' as an option) that performed best overall"
        )),
        "winner_reasoning": (str, Field(
            description="Clear scientific reasoning explaining why this experiment was selected based on performance, validity, and interpretability"
        )),
        "performance_summary": (str, Field(
            description="Summary of how much better the winner performed compared to other approaches"
        ))
    }
            
    VLMEvaluationAnalysis = type(
        "VLMEvaluationAnalysis",
        (BaseModel,),
        {
            "__annotations__": {field_name: field_type for field_name, (field_type, _) in base_fields.items()},
            **{field_name: field_info for field_name, (_, field_info) in base_fields.items()},
            "__doc__": "Structured output schema for VLM experiment comparison evaluation (Phase 3)."
        }
    )
    
    return VLMEvaluationAnalysis


def create_vlm_discovery_schema(context_variables: ContextVariables = None, has_code_context: bool = False):
    """
    Construct structured output schema for scientific discovery mode.
    """
    domain_criteria = ""
    llm_completion = None
    
    if vlm_criteria_mode == "llm_generated" and context_variables:
        # Generate discovery criteria using LLM based on task context
        task = context_variables.get("improved_main_task", "scientific plot")
        plot_context = f"Task context: {task}. Focus on the scientific discovery aspects of this analysis."
        llm_completion = generate_llm_scientific_criteria(plot_context, mode="discovery")
        domain_criteria = llm_completion.choices[0].message.content
    
    # Build discovery description
    base_discovery_description = (
        "Assessment of scientific discovery opportunities: Look for patterns, anomalies, or features that suggest "
        "further scientific investigation is warranted."
    )
    
    if domain_criteria:
        discovery_description = f"{base_discovery_description}\n\nADDITIONAL DOMAIN-SPECIFIC DISCOVERY CRITERIA:\n{domain_criteria}"
    else:
        discovery_description = base_discovery_description
        
    # Define base field structure for scientific discovery
    base_fields = {
        "scientific_observations": (list[str], Field(
            description="List specific scientific patterns, anomalies, or features you observe that are genuinely interesting."
        )),
        "potential_causes": (list[str], Field(
            description="What might be driving these patterns - missing physics, systematic effects, model inadequacy, etc."
        )),
        "signals_to_investigate": (list[str], Field(
            description="What specific measurements, regions, or aspects warrant closer scientific examination?"
        )),
        "verdict": (str, Field(
            description="'continue' or 'explore' - only choose 'explore' if there are compelling scientific features that merit investigation"
        ))
    }
    
    if has_code_context:
        base_fields["code_analysis"] = (str, Field(
            description="Brief analysis of how the code relates to the scientific observations"
        ))
            
    VLMDiscoveryAnalysis = type(
        "VLMDiscoveryAnalysis",
        (BaseModel,),
        {
            "__annotations__": {field_name: field_type for field_name, (field_type, _) in base_fields.items()},
            **{field_name: field_info for field_name, (_, field_info) in base_fields.items()},
            "__doc__": "Structured output schema for VLM scientific discovery analysis."
        }
    )
    
    # Store LLM completion for cost tracking
    if context_variables and llm_completion:
        context_variables["llm_completion"] = llm_completion
    
    return VLMDiscoveryAnalysis


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


def send_image_to_vlm(base_64_img: str, vlm_prompt: str, inject_wrong_plot: bool | str = False, context_variables: ContextVariables = None, mode: str = "correction") -> tuple[str | OpenAICompletion, str | None]:
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
    
    if mode == "discovery":
        VLMAnalysis = create_vlm_discovery_schema(context_variables, has_code_context=has_code_context)
    elif mode == "evaluation":
        VLMAnalysis = create_vlm_evaluation_schema(context_variables)
    elif mode == "correction":
        VLMAnalysis = create_vlm_correction_schema(context_variables, has_code_context=has_code_context)
    else:
        # Default to correction mode for backward compatibility
        VLMAnalysis = create_vlm_correction_schema(context_variables, has_code_context=has_code_context)
    api_keys = get_api_keys_from_env()

    # Override model selection for discovery mode to use OpenAI SOTA vision model
    if mode == "discovery":
        selected_vlm_model = "gpt-4o"  # OpenAI's SOTA vision model for discovery
    else:
        selected_vlm_model = vlm_model  # Use global setting for other modes

    if selected_vlm_model in ["gpt-4o", "o3-2025-04-16"]:
        client = OpenAI(api_key=api_keys["OPENAI"])

        if cmbagent_debug:
            print(f"Using OpenAI VLM call with model {selected_vlm_model}")
            
        # External OpenAI API call with structured output
        try:
            # Build request parameters
            request_params = {
                "model": selected_vlm_model,
                "messages": [
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
                "response_format": {
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
            }
            
            # Add reasoning_effort only for o3 models
            if selected_vlm_model.startswith("o3-"):
                request_params["reasoning_effort"] = "medium"
                if cmbagent_debug:
                    print(f"Using reasoning effort: medium for {selected_vlm_model}")
            
            completion = client.chat.completions.create(**request_params)
            
            if cmbagent_debug:
                print(f"VLM prompt:\n{vlm_prompt}")
                
            return completion, injected_code
            
        except Exception as e:
            print(f"ERROR: VLM API call failed: {e}")
            # Return a graceful fallback completion
            fallback_response = _create_fallback_response(has_code_context, mode)
            return fallback_response, injected_code

    elif selected_vlm_model in ["gemini-2.5-flash", "gemini-2.5-pro"]:
        if cmbagent_debug:
            print(f"VLM model: {selected_vlm_model}")
        client = genai.Client(api_key=api_keys["GEMINI"])
        
        try:
            # Convert base64 image to bytes
            image_bytes = base64.b64decode(base_64_img)
            
            # External Gemini API call with structured output
            response = client.models.generate_content(
                model=selected_vlm_model,
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
            
            completion_obj = OpenAICompletion(response.text, 
                                  prompt_tokens, 
                                  completion_tokens,
                                  total_tokens)
            return completion_obj, injected_code
                                  
        except Exception as e:
            print(f"ERROR: VLM API call failed: {e}")
            # Return a graceful fallback completion
            fallback_response = _create_fallback_response(has_code_context, mode)
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


def create_vlm_evaluation_prompt(context_variables: ContextVariables) -> str:
    """Create VLM prompt for Phase 3 evaluation - analyzing comparison plot and selecting winner."""
    task_context = context_variables.get("improved_main_task", "No task context")
    comparison_metric = context_variables.get("comparison_metric", "unknown metric")
    experiment_results = context_variables.get("experiment_results", [])
    proposed_experiments = context_variables.get("proposed_experiments", [])
    
    # Format experiment information
    experiments_info = ""
    for i, exp in enumerate(proposed_experiments):
        experiments_info += f"\n{i+1}. **{exp.get('name', f'Experiment {i+1}')}**: {exp.get('description', 'No description')}"
        experiments_info += f"\n   Expected outcome: {exp.get('expected_outcome', 'No expected outcome specified')}"
    
    # Format results
    results_info = ""
    for result in experiment_results:
        results_info += f"\n- **{result['name']}**: {comparison_metric} = {result.get('metric_value', 'N/A')}"
    
    prompt = f"""You are a senior scientist evaluating experiment comparison results. Analyze the comparison plot showing multiple experimental approaches and select the winning method.

**ORIGINAL TASK**: {task_context}

**EXPERIMENTS TESTED**:{experiments_info}

**RESULTS SUMMARY**:{results_info}

**COMPARISON METRIC**: {comparison_metric}

**YOUR TASK**: 
1. Analyze the comparison plot showing all experimental approaches
2. Compare performance based on the {comparison_metric} values
3. Select the experiment that performed best overall (including "Original" as an option)
4. Provide clear scientific reasoning for your selection
5. Explain the performance differences between approaches

**EVALUATION CRITERIA**:
- Raw {comparison_metric} performance (consider whether higher or lower values are better)
- Magnitude of improvement over baseline
- Scientific validity and robustness
- Practical interpretability

**IMPORTANT**: The "Original" baseline approach is always a valid choice if the new experiments did not provide clear improvements.

Analyze the comparison plot carefully and make your selection based on scientific merit."""

    return prompt


def create_vlm_scientific_prompt(context_variables: ContextVariables, executed_code: str = None) -> str:
    """Create the scientific discovery VLM prompt focusing on opportunities rather than errors."""
    # Fetch relevant task context
    improved_main_task = context_variables.get("improved_main_task", "No improved main task provided.")
    
    # Create base scientific discovery prompt
    base_prompt = _create_base_scientific_vlm_prompt(improved_main_task)
    
    # Add code context if visibility is enabled and code is provided
    if show_code_to_plot_judge and executed_code:
        code_section = _create_code_context_section(executed_code)
        vlm_prompt = base_prompt + code_section
    else:
        vlm_prompt = base_prompt

    return vlm_prompt


def _create_base_scientific_vlm_prompt(improved_main_task: str) -> str:
    """Create the base scientific discovery VLM prompt focusing on scientific opportunities."""
    
    # Generate domain-specific discovery criteria
    domain_criteria = ""
    if vlm_criteria_mode == "llm_generated":
        # Generate discovery criteria using LLM based on task context
        plot_context = f"Task context: {improved_main_task}. Focus on the scientific discovery aspects of this analysis."
        llm_completion = generate_llm_scientific_criteria(plot_context, mode="discovery")
        domain_criteria = llm_completion.choices[0].message.content
    
    # Build discovery criteria description
    base_description = """You are an expert scientist focusing on discovery opportunities. 

Analyze this scientific plot for interesting patterns, anomalies, and opportunities for further investigation. Your goal is to identify scientifically meaningful features that merit deeper exploration.

IMPORTANT: Only flag features that are genuinely scientifically interesting. Most plots may not have significant anomalies or discovery opportunities - that's perfectly normal. Be selective and only highlight truly noteworthy patterns."""
    
    if domain_criteria:
        discovery_description = f"{base_description}\n\nADDITIONAL DOMAIN-SPECIFIC DISCOVERY CRITERIA:\n{domain_criteria}"
    else:
        discovery_description = base_description
    
    return f"""{discovery_description}

**TASK CONTEXT**: {improved_main_task}

Examine these aspects for discovery opportunities:

- Parameter constraints: Are some parameters much better/worse constrained? What does this tell us?
- Model agreement: How well does theory match data? Any systematic trends in residuals?
- Correlations and degeneracies: Strong parameter relationships that reveal physics insights?
- Statistical patterns: Outliers, anomalous regions, or unexpected scatter that might be significant?
- Prior dependence: How sensitive are results to assumptions?
- Data quality and precision: What drives the uncertainties? Are there regions of particularly high/low precision?
- Experimental design opportunities: What additional measurements or analyses could provide new insights?

Focus on interesting scientific patterns that suggest further investigation could reveal new physics or deeper understanding.

Identify compelling scientific features worthy of exploration.
"""


def _create_fallback_response(has_code_context: bool = False, mode: str = "correction"):
    """Create a fallback VLM response when API calls fail."""
    if mode == "discovery":
        fallback_dict = {
            "scientific_observations": ["VLM analysis failed - continuing without evaluation"],
            "potential_causes": ["VLM analysis failed - please check VLM configuration"], 
            "signals_to_investigate": ["VLM analysis failed - no signals identified"],
            "verdict": "continue"
        }
        if has_code_context:
            fallback_dict["code_analysis"] = "VLM analysis failed - code could not be analyzed"
    else:  # correction mode
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
    

def generate_llm_scientific_criteria(plot_description: str, mode: str = "correction"):
    """
    Generate domain-specific scientific criteria using LLM based on plot description.
    Returns an OpenAICompletion object with cost information.
    """
    try:
        api_keys = get_api_keys_from_env()
        client = OpenAI(api_key=api_keys["OPENAI"])
        
        if mode == "correction":
            prompt = f"""You are a scientific expert analyzing plots. Generate domain-specific scientific accuracy criteria for error detection.

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
        
        else:  # mode == "discovery"
            prompt = f"""You are a scientific expert analyzing plots. Generate domain-specific discovery criteria for identifying scientifically interesting patterns that warrant further investigation.

Context: {plot_description}

Your response will be used as criteria in a VLM prompt, so be direct and specific. Do not include conversational phrases.

Focus on patterns that suggest deeper scientific exploration is needed:
1. Parameter degeneracies and correlations that reveal physics
2. Model inadequacy signatures (systematic residuals, poor fits)  
3. Statistical significance patterns (outliers, anomalous scatter)
4. Convergence and sampling issues in inference
5. Prior sensitivity and robustness indicators
6. Unexpected trends that deviate from theoretical predictions

IMPORTANT: Focus on signs that suggest "there's more science here to explore" rather than "this is wrong."
Look for patterns that hint at interesting physics, parameter relationships, or experimental opportunities.

Examples of discovery-worthy patterns:
- Parameter degeneracy: Strong correlation between parameters suggesting need for additional constraints
- Model tension: Systematic residuals indicating missing physics or model components
- Statistical anomalies: Outliers or scatter patterns that exceed expected measurement uncertainties
- Prior sensitivity: Results that change significantly with different prior choices, indicating weak constraints
- Unexpected correlations: Parameter relationships that deviate from theoretical predictions
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
        
        # Fresh client setup for this call
        client = genai.Client(api_key=api_keys["GEMINI"])
        
        debugger_prompt = f"""You are a plot debugging expert. The VLM has identified problems with a plot, and you need to provide targeted code fixes.

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

        # External Gemini API call with structured output
        response = client.models.generate_content(
            model="gemini-2.5-pro",
            contents=[
                debugger_prompt
            ],
            config={
                "response_mime_type": "application/json",
                "response_schema": PlotDebuggerResponse.model_json_schema()
            }
        )
        
        # Parse structured response
        # Use response.text like the main VLM function instead of candidates chain
        debugger_response = response.text
        if not debugger_response:
            print("ERROR: Empty debugger response text")
            return []
            
        print(f"DEBUG: Debugger response text: {debugger_response}")
        parsed_response = PlotDebuggerResponse.model_validate_json(debugger_response)
        
        return parsed_response.fixes
            
    except Exception as e:
        print(f"ERROR: External Gemini debugger failed: {e}")
        return []


class ExperimentProposerResponse(BaseModel):
    """Structured response from experiment proposer."""
    experiment_suggestions: list[str] = Field(
        ...,
        description="List of targeted, actionable experimental suggestions based on scientific observations. Each suggestion should be specific and implementable in code."
    )


class ExperimentDetails(BaseModel):
    """Details for a single experiment in the 3-phase discovery workflow."""
    name: str = Field(..., description="Short descriptive name for the experiment")
    description: str = Field(..., description="Clear description of what this experiment does and why it's scientifically interesting")
    implementation_hints: str = Field(..., description="Specific guidance for the engineer on how to implement this experiment")
    expected_outcome: str = Field(..., description="What we expect to learn or discover from this experiment")


class Enhanced3PhaseProposalResponse(BaseModel):
    """Structured response for Phase 1: Discovery & Proposal."""
    experiments: list[ExperimentDetails] = Field(..., description="List of 3-5 structured experiments to implement")
    comparison_metric: str = Field(..., description="Single metric to use for comparing all experiments (e.g., 'R²', 'AIC', 'chi²', 'log-likelihood', etc.)")
    comparison_strategy: str = Field(..., description="Brief description of how to create the comparison visualization showing all experiments together")


class ExperimentResult(BaseModel):
    """Results from a single implemented experiment."""
    name: str = Field(..., description="Name of the experiment")
    metric_value: float = Field(..., description="Calculated value of the comparison metric")
    implementation_notes: str = Field(..., description="Any notes about the implementation")


class Enhanced3PhaseEvaluationResponse(BaseModel):
    """Structured response for Phase 3: Evaluation & Final Implementation."""
    winner_name: str = Field(..., description="Name of the winning experiment")
    reasoning: str = Field(..., description="Scientific reasoning for why this experiment was selected")
    final_task_description: str = Field(..., description="Clear description of how the winning approach should be implemented as the new primary task")


class FinalTaskCreationResponse(BaseModel):
    """Response from experiment proposer for creating final implementation task."""
    final_task_description: str = Field(..., description="Complete description of the new primary task replacing the original task")
    implementation_specifics: str = Field(..., description="Specific implementation guidance: parameters, methods, code changes needed")
    success_criteria: str = Field(..., description="How to know if the winning approach has been successfully implemented")
    differences_from_original: str = Field(..., description="Key differences between the winning approach and the original baseline")


def call_enhanced_experiment_proposer_phase1(context_variables: ContextVariables) -> dict:
    """
    Phase 1: Generate structured experiments for enhanced 3-phase discovery workflow.
    Reads scientific observations from context, returns structured experiments.
    """
    try:
        from openai import OpenAI
        
        api_keys = get_api_keys_from_env()
        if not api_keys.get("OPENAI"):
            print("WARNING: No OpenAI API key found, returning empty response")
            return {"experiments": [], "comparison_metric": None, "comparison_strategy": ""}
        
        client = OpenAI(api_key=api_keys["OPENAI"])
        
        # Pull data from shared context
        task_context = context_variables.get("improved_main_task", "No task context")
        vlm_analysis = context_variables.get("vlm_plot_analysis", "No VLM analysis")
        executed_code = context_variables.get("latest_executed_code", "No code available")
        observations = context_variables.get("scientific_observations", [])
        potential_causes = context_variables.get("potential_causes", [])
        signals_to_investigate = context_variables.get("signals_to_investigate", [])
        
        observations_str = "\n".join(f"- {obs}" for obs in observations) if observations else "No specific observations"
        causes_str = "\n".join(f"- {cause}" for cause in potential_causes) if potential_causes else "No specific causes identified"
        signals_str = "\n".join(f"- {signal}" for signal in signals_to_investigate) if signals_to_investigate else "No specific signals identified"
        
        proposer_prompt = f"""You are designing a comprehensive scientific experiment comparing multiple approaches. Based on VLM observations, propose 3-5 structured experiments that can be implemented and compared systematically.

TASK CONTEXT: {task_context}

VLM SCIENTIFIC OBSERVATIONS:
{observations_str}

POTENTIAL CAUSES TO INVESTIGATE:
{causes_str}

SIGNALS/REGIONS TO EXAMINE:
{signals_str}

CURRENT CODE (baseline approach):
```python
{executed_code}
```

FULL VLM ANALYSIS:
{vlm_analysis}

Design experiments that:
1. Include the original baseline approach as experiment #1 for comparison
2. Test alternative models, parameters, or analysis methods
3. Are clearly differentiated and scientifically motivated
4. Can be implemented by an engineer and compared using a single metric

Choose ONE quantitative metric that can be calculated for all experiments (R², AIC, χ², log-likelihood, RMSE, etc.).

Your comparison strategy should create a single visualization showing all experiments with their metric values - like a bar chart, comparison plot, or table.

Each experiment should have clear implementation guidance for the engineer."""

        print(f"DEBUG: Sending prompt to OpenAI Phase 1:")
        print(f"- Task context: {task_context}")
        print(f"- VLM analysis length: {len(vlm_analysis)} chars")
        print(f"- Observations: {len(observations)}")
        print(f"- Potential causes: {len(potential_causes)}")
        print(f"- Signals: {len(signals_to_investigate)}")

        # Generate OpenAI-compatible schema with additionalProperties: false at all levels
        def fix_openai_schema(schema_dict):
            """Recursively add additionalProperties: false to all object types for OpenAI strict mode."""
            if isinstance(schema_dict, dict):
                if schema_dict.get("type") == "object":
                    schema_dict["additionalProperties"] = False
                for key, value in schema_dict.items():
                    schema_dict[key] = fix_openai_schema(value)
            elif isinstance(schema_dict, list):
                return [fix_openai_schema(item) for item in schema_dict]
            return schema_dict

        base_schema = Enhanced3PhaseProposalResponse.model_json_schema()
        openai_schema = fix_openai_schema(base_schema)

        response = client.chat.completions.create(
            model="gpt-5",
            messages=[
                {"role": "user", "content": proposer_prompt}
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "enhanced_proposal_response",
                    "strict": True,
                    "schema": openai_schema
                }
            }
        )
        
        print(f"DEBUG: Phase 1 proposer response object: {response}")
        response_content = response.choices[0].message.content
        print(f"DEBUG: Phase 1 proposer response content: {repr(response_content)}")
        
        if not response_content:
            print("ERROR: Empty phase 1 proposer response content")
            return {"experiments": [], "comparison_metric": None, "comparison_strategy": ""}
            
        parsed_response = Enhanced3PhaseProposalResponse.model_validate_json(response_content)
        
        return {
            "experiments": [exp.model_dump() for exp in parsed_response.experiments],
            "comparison_metric": parsed_response.comparison_metric,
            "comparison_strategy": parsed_response.comparison_strategy
        }
            
    except Exception as e:
        print(f"ERROR: Enhanced experiment proposer phase 1 failed: {e}")
        return {"experiments": [], "comparison_metric": None, "comparison_strategy": ""}


def call_vlm_evaluation_analysis_phase3(context_variables: ContextVariables) -> dict:
    """
    Phase 3a: VLM analyzes comparison plot and selects winner.
    Returns winner selection and analysis, NOT final task creation.
    """
    try:
        from google import genai
        
        api_keys = get_api_keys_from_env()
        if not api_keys.get("GEMINI"):
            print("WARNING: No Gemini API key found for evaluation analysis")
            return {"winner_selection": "Original", "winner_reasoning": "VLM unavailable", "experiment_analysis": "", "metric_comparison": "", "performance_summary": ""}
        
        client = genai.Client(api_key=api_keys["GEMINI"])
        
        # Pull data from shared context
        task_context = context_variables.get("improved_main_task", "No task context")
        experiment_results = context_variables.get("experiment_results", [])
        comparison_plot_path = context_variables.get("comparison_plot_path")
        comparison_metric = context_variables.get("comparison_metric", "unknown metric")
        proposed_experiments = context_variables.get("proposed_experiments", [])
        
        # Format experiment results for analysis
        results_str = "\n".join([
            f"- {result['name']}: {comparison_metric} = {result.get('metric_value', 'N/A')}"
            for result in experiment_results
        ])
        
        # Include experiment descriptions
        experiments_str = "\n".join([
            f"- {exp.get('name', f'Experiment {i+1}')}: {exp.get('description', 'No description')}"
            for i, exp in enumerate(proposed_experiments)
        ])
        
        evaluator_prompt = f"""You are analyzing the results of a scientific experiment comparison. Based on the comparison plot and metric values, select the winning approach.

ORIGINAL TASK CONTEXT: {task_context}

EXPERIMENTS TESTED:
{experiments_str}

EXPERIMENT RESULTS:
{results_str}

COMPARISON METRIC USED: {comparison_metric}

Your job is to:
1. Analyze each experiment's performance based on the comparison plot
2. Compare the {comparison_metric} values across all experiments (including the original baseline)
3. Select the experiment that performed best overall
4. Provide clear scientific reasoning for your selection
5. Summarize how much better the winner performed

Consider:
- Raw metric performance (higher/lower is better depending on metric)
- Statistical significance of improvements
- Scientific validity and interpretability
- Practical implementation considerations

Remember: "Original" baseline approach is always an option if none of the new experiments clearly outperformed it."""

        # Include image analysis if comparison plot is available
        if comparison_plot_path and os.path.exists(comparison_plot_path):
            try:
                with open(comparison_plot_path, 'rb') as img_file:
                    base_64_img = base64.b64encode(img_file.read()).decode('utf-8')
                
                from google.genai import types
                response = client.models.generate_content(
                    model="gemini-2.5-pro",
                    contents=[
                        types.Part.from_bytes(
                            data=base64.b64decode(base_64_img),
                            mime_type='image/png',
                        ),
                        evaluator_prompt
                    ],
                    config={
                        "response_mime_type": "application/json",
                        "response_schema": create_vlm_evaluation_schema().model_json_schema()
                    }
                )
            except Exception:
                # Fallback to text-only analysis
                response = client.models.generate_content(
                    model="gemini-2.5-pro",
                    contents=[evaluator_prompt],
                    config={
                        "response_mime_type": "application/json",
                        "response_schema": create_vlm_evaluation_schema().model_json_schema()
                    }
                )
        else:
            # Text-only analysis
            response = client.models.generate_content(
                model="gemini-2.5-pro",
                contents=[evaluator_prompt],
                config={
                    "response_mime_type": "application/json",
                    "response_schema": create_vlm_evaluation_schema().model_json_schema()
                }
            )
        
        if not response.text:
            print("ERROR: Empty VLM evaluation response")
            return {"winner_selection": "Original", "winner_reasoning": "VLM analysis failed", "experiment_analysis": "", "metric_comparison": "", "performance_summary": ""}
            
        # Parse the structured VLM response
        vlm_analysis = json.loads(response.text)
        
        return {
            "winner_selection": vlm_analysis.get("winner_selection", "Original"),
            "winner_reasoning": vlm_analysis.get("winner_reasoning", "No reasoning provided"),
            "experiment_analysis": vlm_analysis.get("experiment_analysis", ""),
            "metric_comparison": vlm_analysis.get("metric_comparison", ""),
            "performance_summary": vlm_analysis.get("performance_summary", "")
        }
            
    except Exception as e:
        print(f"ERROR: VLM evaluation analysis failed: {e}")
        return {"winner_selection": "Original", "winner_reasoning": f"Analysis failed: {e}", "experiment_analysis": "", "metric_comparison": "", "performance_summary": ""}


def call_experiment_proposer_final_task_phase3(context_variables: ContextVariables) -> dict:
    """
    Phase 3b: Experiment proposer creates final task description based on VLM winner selection.
    Takes winner analysis and creates actionable task for engineer.
    """
    try:
        from openai import OpenAI
        
        api_keys = get_api_keys_from_env()
        if not api_keys.get("OPENAI"):
            print("WARNING: No OpenAI API key found for final task creation")
            return {"final_task_description": "Continue with original task", "implementation_specifics": "", "success_criteria": "", "differences_from_original": ""}
        
        client = OpenAI(api_key=api_keys["OPENAI"])
        
        # Pull data from shared context
        task_context = context_variables.get("improved_main_task", "No task context")
        original_code = context_variables.get("latest_executed_code", "")
        proposed_experiments = context_variables.get("proposed_experiments", [])
        winner_selection = context_variables.get("vlm_winner_selection", "Original")
        winner_reasoning = context_variables.get("vlm_winner_reasoning", "No reasoning")
        vlm_analysis = context_variables.get("vlm_evaluation_analysis", "No VLM analysis")
        
        # Find the winning experiment details
        winning_experiment = None
        if winner_selection != "Original":
            for exp in proposed_experiments:
                if exp.get("name") == winner_selection:
                    winning_experiment = exp
                    break
        
        proposer_prompt = f"""You are creating the final implementation task for an engineer based on experiment comparison results. Convert the winning approach into actionable engineering instructions.

ORIGINAL TASK: {task_context}

ORIGINAL CODE:
```python
{original_code}
```

VLM WINNER SELECTION: {winner_selection}
VLM REASONING: {winner_reasoning}

FULL VLM ANALYSIS:
{vlm_analysis}

WINNING EXPERIMENT DETAILS:
{json.dumps(winning_experiment, indent=2) if winning_experiment else "Original baseline approach"}

ALL EXPERIMENTS TESTED:
{json.dumps(proposed_experiments, indent=2)}

Your job is to create a complete final task description that:

1. **Final Task Description**: Clear, actionable description of what the engineer should implement using the winning approach
2. **Implementation Specifics**: Concrete guidance on parameters, methods, code modifications needed
3. **Success Criteria**: How to know if the implementation is successful
4. **Differences from Original**: Key changes from the baseline approach

If the winner is "Original", then the final task should be to continue with the original approach but with any improvements suggested by the analysis.

Make this actionable and specific enough for an engineer to implement immediately."""

        # Generate OpenAI-compatible schema with additionalProperties: false at all levels
        def fix_openai_schema(schema_dict):
            """Recursively add additionalProperties: false to all object types for OpenAI strict mode."""
            if isinstance(schema_dict, dict):
                if schema_dict.get("type") == "object":
                    schema_dict["additionalProperties"] = False
                for key, value in schema_dict.items():
                    schema_dict[key] = fix_openai_schema(value)
            elif isinstance(schema_dict, list):
                return [fix_openai_schema(item) for item in schema_dict]
            return schema_dict

        base_schema = FinalTaskCreationResponse.model_json_schema()
        openai_schema = fix_openai_schema(base_schema)

        response = client.chat.completions.create(
            model="gpt-5",
            messages=[
                {"role": "user", "content": proposer_prompt}
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "final_task_creation_response",
                    "strict": True,
                    "schema": openai_schema
                }
            }
        )
        
        response_content = response.choices[0].message.content
        if not response_content:
            print("ERROR: Empty final task creation response")
            return {"final_task_description": "Continue with original task", "implementation_specifics": "", "success_criteria": "", "differences_from_original": ""}
            
        parsed_response = FinalTaskCreationResponse.model_validate_json(response_content)
        
        return {
            "final_task_description": parsed_response.final_task_description,
            "implementation_specifics": parsed_response.implementation_specifics,
            "success_criteria": parsed_response.success_criteria,
            "differences_from_original": parsed_response.differences_from_original
        }
            
    except Exception as e:
        print(f"ERROR: Final task creation failed: {e}")
        return {"final_task_description": f"Continue with original task. Error: {e}", "implementation_specifics": "", "success_criteria": "", "differences_from_original": ""}


def call_enhanced_experiment_evaluator_phase3(context_variables: ContextVariables) -> dict:
    """
    Phase 3: Evaluate experiment results and select winner for enhanced 3-phase discovery workflow.
    Reads experiment results from context, returns winner selection.
    """
    try:
        from google import genai
        
        api_keys = get_api_keys_from_env()
        if not api_keys.get("GEMINI"):
            print("WARNING: No Gemini API key found, returning empty response")
            return {"winner_name": "", "reasoning": "", "final_task_description": ""}
        
        client = genai.Client(api_key=api_keys["GEMINI"])
        
        # Pull data from shared context
        task_context = context_variables.get("improved_main_task", "No task context")
        experiment_results = context_variables.get("experiment_results", [])
        comparison_plot_path = context_variables.get("comparison_plot_path")
        comparison_metric = context_variables.get("comparison_metric", "unknown metric")
        
        # Format experiment results for analysis
        results_str = "\n".join([
            f"- {result['name']}: {comparison_metric} = {result.get('metric_value', 'N/A')}"
            for result in experiment_results
        ])
        
        # Read comparison plot if available
        plot_analysis = ""
        if comparison_plot_path and os.path.exists(comparison_plot_path):
            try:
                with open(comparison_plot_path, 'rb') as img_file:
                    base_64_img = base64.b64encode(img_file.read()).decode('utf-8')
                    plot_analysis = "COMPARISON PLOT INCLUDED FOR ANALYSIS"
            except Exception:
                plot_analysis = "Could not read comparison plot"
        
        evaluator_prompt = f"""You are a senior scientist evaluating experimental results to select the best approach. Based on the comparison of multiple experiments, choose the winning approach and explain why.

ORIGINAL TASK CONTEXT: {task_context}

EXPERIMENT RESULTS:
{results_str}

COMPARISON METRIC USED: {comparison_metric}

Your job is to:
1. Select the experiment with the best scientific performance based on the {comparison_metric} values
2. Provide clear scientific reasoning for your choice
3. Describe how this winning approach should be implemented as the new primary task

Consider not just the raw metric values, but also:
- Scientific validity and robustness
- Interpretability and physical meaning
- Practical implementation considerations
- Statistical significance of improvements

The final task description should be specific enough for an engineer to implement the winning approach as their new primary objective."""

        # Include image analysis if plot is available
        if plot_analysis == "COMPARISON PLOT INCLUDED FOR ANALYSIS":
            from google.genai import types
            response = client.models.generate_content(
                model="gemini-2.5-pro",
                contents=[
                    types.Part.from_bytes(
                        data=base64.b64decode(base_64_img),
                        mime_type='image/png',
                    ),
                    evaluator_prompt
                ],
                config={
                    "response_mime_type": "application/json",
                    "response_schema": Enhanced3PhaseEvaluationResponse.model_json_schema()
                }
            )
        else:
            response = client.models.generate_content(
                model="gemini-2.5-pro",
                contents=[evaluator_prompt],
                config={
                    "response_mime_type": "application/json",
                    "response_schema": Enhanced3PhaseEvaluationResponse.model_json_schema()
                }
            )
        
        if not response.text:
            print("ERROR: Empty phase 3 evaluator response text")
            return {"winner_name": "", "reasoning": "", "final_task_description": ""}
            
        parsed_response = Enhanced3PhaseEvaluationResponse.model_validate_json(response.text)
        
        return {
            "winner_name": parsed_response.winner_name,
            "reasoning": parsed_response.reasoning,
            "final_task_description": parsed_response.final_task_description
        }
            
    except Exception as e:
        print(f"ERROR: Enhanced experiment evaluator phase 3 failed: {e}")
        return {"winner_name": "", "reasoning": "", "final_task_description": ""}


def call_external_experiment_proposer(task_context: str, vlm_analysis: str, observations: list[str], potential_causes: list[str], signals_to_investigate: list[str], executed_code: str) -> list[str]:
    """
    Call external Gemini 2.5 Pro to convert VLM scientific observations into actionable experiment suggestions.
    
    Args:
        task_context: The main task context
        vlm_analysis: Full VLM scientific analysis JSON
        observations: List of scientific observations from VLM
        potential_causes: List of potential causes identified by VLM
        signals_to_investigate: List of signals/regions to examine from VLM
        executed_code: The code that generated the plot
    
    Returns:
        List of targeted experiment suggestions
    """
    try:
        from google import genai
        
        api_keys = get_api_keys_from_env()
        if not api_keys.get("GEMINI"):
            print("WARNING: No Gemini API key found, returning empty suggestions")
            return []
        
        # Fresh client setup for this call
        client = genai.Client(api_key=api_keys["GEMINI"])
        
        observations_str = "\n".join(f"- {obs}" for obs in observations) if observations else "No specific observations"
        causes_str = "\n".join(f"- {cause}" for cause in potential_causes) if potential_causes else "No specific causes identified"
        signals_str = "\n".join(f"- {signal}" for signal in signals_to_investigate) if signals_to_investigate else "No specific signals identified"
        
        proposer_prompt = f"""You are a scientific experiment designer. Based on VLM observations of a plot, propose specific, actionable experiments or analyses that can be implemented in code.

TASK CONTEXT: {task_context}

VLM SCIENTIFIC OBSERVATIONS:
{observations_str}

POTENTIAL CAUSES TO INVESTIGATE:
{causes_str}

SIGNALS/REGIONS TO EXAMINE:
{signals_str}

CURRENT CODE:
```python
{executed_code}
```

FULL VLM ANALYSIS:
{vlm_analysis}

Based on these scientific observations, propose specific experimental suggestions that:
1. Are actionable and implementable in code
2. Would help investigate the observed patterns/anomalies
3. Could provide statistical validation or refutation of the observations
4. Might reveal the underlying causes of the patterns
5. Are grounded in the scientific context of the task

Focus on concrete experiments like: parameter sweeps, statistical tests, model comparisons, sensitivity analyses, data subsampling, etc.

Your suggestions should be specific enough for an engineer to implement directly."""

        # External Gemini API call with structured output
        response = client.models.generate_content(
            model="gemini-2.5-pro",
            contents=[
                proposer_prompt
            ],
            config={
                "response_mime_type": "application/json",
                "response_schema": ExperimentProposerResponse.model_json_schema()
            }
        )
        
        # Parse structured response
        # Use response.text like the main VLM function instead of candidates chain
        proposer_response = response.text
        if not proposer_response:
            print("ERROR: Empty proposer response text")
            return []
            
        parsed_response = ExperimentProposerResponse.model_validate_json(proposer_response)
        
        return parsed_response.experiment_suggestions
            
    except Exception as e:
        print(f"ERROR: External Gemini experiment proposer failed: {e}")
        return []