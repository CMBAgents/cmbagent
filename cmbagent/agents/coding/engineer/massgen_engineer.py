"""MassGen-powered engineer agent for multi-agent code generation.

This module provides an engineer agent that uses MassGen (multi-agent system)
for code generation instead of a single LLM. Multiple agents collaborate to
produce higher-quality code through planning, voting, and consensus.
"""

import os
import asyncio
import re
from pathlib import Path
from typing import Dict, Optional, Union, List, Callable, Any
from autogen.agentchat import ConversableAgent, UpdateSystemMessage
from cmbagent.base_agent import CmbAgentSwarmAgent

# Lazy import massgen to avoid dependency issues if not installed
_massgen = None

def get_massgen():
    """Lazy import of massgen."""
    global _massgen
    if _massgen is None:
        try:
            import massgen
            _massgen = massgen
        except ImportError:
            raise ImportError(
                "MassGen is required for engineer_backend='massgen'. "
                "Install it with: pip install massgen"
            )
    return _massgen


class MassGenEngineerAgent(CmbAgentSwarmAgent):
    """Hybrid engineer agent that uses MassGen for initial code generation,
    then falls back to standard LLM for debugging.

    Strategy:
    - First code generation: Use MassGen (multi-agent collaboration)
    - Debugging/retries (after code failures): Use single LLM (fast iteration)

    This provides quality initial code with efficient debugging cycles.

    Args:
        name: Agent name
        massgen_config: Path to MassGen YAML configuration file
        extract_code: Whether to extract code from MassGen response (default: True)
        use_massgen_for_retries: If True, use MassGen even for retries (default: False)
        **kwargs: Additional arguments passed to CmbAgentSwarmAgent
    """

    DEFAULT_MASSGEN_CONFIG = str(
        Path(__file__).parent.parent.parent.parent.parent /
        "massgen_configs" / "engineer_massgen.yaml"
    )

    def __init__(
        self,
        name: str,
        massgen_config: Optional[str] = None,
        extract_code: bool = True,
        verbose: bool = False,
        enable_logging: bool = True,
        use_massgen_for_retries: bool = False,
        **kwargs
    ):
        super().__init__(name=name, **kwargs)

        self.massgen_config = massgen_config or self.DEFAULT_MASSGEN_CONFIG
        self.extract_code = extract_code
        self.verbose = verbose
        self.enable_logging = enable_logging
        self.use_massgen_for_retries = use_massgen_for_retries

        # Track code generation attempts
        self._generation_count = 0

        # Verify config exists
        if not os.path.exists(self.massgen_config):
            raise FileNotFoundError(
                f"MassGen config not found: {self.massgen_config}\n"
                f"Expected at: {self.DEFAULT_MASSGEN_CONFIG}"
            )

        print(f"[MassGenEngineer] Initialized (hybrid mode)")
        print(f"[MassGenEngineer] Config: {self.massgen_config}")
        print(f"[MassGenEngineer] Strategy: MassGen for initial, single LLM for retries")

        # Register MassGen reply function with highest priority
        # This will be called before the default LLM reply function
        self.register_reply(
            trigger=ConversableAgent,
            reply_func=self._massgen_reply_func,
            position=0,  # Highest priority
        )

    def _extract_code_from_response(self, response: str) -> str:
        """Extract Python code from MassGen structured response.

        Expects format:
        **Code Explanation:**
        <explanation>

        **Python Code:**
        <code>
        """
        # Try to extract Python Code section
        match = re.search(
            r'\*\*Python Code:\*\*\s*```python\s*(.*?)```',
            response,
            re.DOTALL | re.IGNORECASE
        )
        if match:
            return match.group(1).strip()

        match = re.search(
            r'\*\*Python Code:\*\*\s*(.*?)(?=\*\*|$)',
            response,
            re.DOTALL | re.IGNORECASE
        )
        if match:
            code = match.group(1).strip()
            # Remove markdown code fences if present
            code = re.sub(r'^```python\s*', '', code)
            code = re.sub(r'```\s*$', '', code)
            return code.strip()

        # Fallback: try to find any code block
        match = re.search(r'```python\s*(.*?)```', response, re.DOTALL)
        if match:
            return match.group(1).strip()

        # Last resort: return the response as-is
        return response

    def _get_context_variables(self, messages: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """Extract context variables from AG2's ContextVariables.

        These are the variables that the engineer.yaml template expects.

        Args:
            messages: Message history (unused, but kept for compatibility)
        """
        context = {}

        # Try to get context from AG2's ContextVariables
        # The context is stored in the agent's state during UpdateSystemMessage
        if hasattr(self, 'context_variables') and self.context_variables:
            # AG2's ContextVariables has a `data` attribute
            if hasattr(self.context_variables, 'data'):
                context = dict(self.context_variables.data)
            elif isinstance(self.context_variables, dict):
                context = self.context_variables.copy()

        # Also check _agent_state (backup)
        if not context and hasattr(self, '_agent_state') and self._agent_state:
            context = self._agent_state.copy()

        # Set defaults for required template variables
        defaults = {
            'improved_main_task': '',
            'engineer_append_instructions': '',
            'final_plan': 'No formal plan - one-shot execution',
            'current_plan_step_number': '1',
            'current_status': 'Starting task execution',
            'current_sub_task': '',
            'current_instructions': 'Execute the task as described',
            'database_path': './data',
            'codebase_path': './codebase',
            'previous_steps_execution_summary': 'No previous steps - this is the first step.',
        }

        # Merge defaults with any existing context
        for key, value in defaults.items():
            if key not in context:
                context[key] = value

        return context

    def _format_engineer_prompt(self, task: str, messages: Optional[List[Dict]] = None) -> str:
        """Format the full engineer prompt with all context variables.

        This ensures MassGen receives the same detailed instructions as
        the standard engineer would.

        Args:
            task: The raw task from the conversation
            messages: Message history for context

        Returns:
            Fully formatted prompt with all context variables
        """
        from cmbagent.utils.yaml import yaml_load_file
        import os

        # Load engineer template
        engineer_yaml = os.path.join(
            os.path.dirname(__file__),
            "engineer.yaml"
        )
        engineer_info = yaml_load_file(engineer_yaml)
        template = engineer_info['instructions']

        # Get context variables from AG2's ContextVariables
        context = self._get_context_variables(messages)

        # Update task-specific context
        # If improved_main_task is not set, use the task
        if not context.get('improved_main_task') or context['improved_main_task'] == '':
            context['improved_main_task'] = task

        if not context.get('current_sub_task') or context['current_sub_task'] == '':
            context['current_sub_task'] = task

        if self.verbose:
            print(f"[MassGenEngineer] Context variables:")
            for key in ['improved_main_task', 'final_plan', 'current_plan_step_number',
                       'current_status', 'database_path', 'codebase_path']:
                print(f"  {key}: {context.get(key, 'NOT SET')}")

        # Format the template
        try:
            formatted_prompt = template.format(**context)
        except KeyError as e:
            # Missing variable - log warning and continue with defaults
            print(f"[MassGenEngineer] Warning: Missing context variable {e}")
            print(f"[MassGenEngineer] Using defaults and continuing...")
            # Try again with just the essential variables
            essential_context = {
                'improved_main_task': task,
                'engineer_append_instructions': '',
                'final_plan': 'No formal plan - one-shot execution',
                'current_plan_step_number': '1',
                'current_status': 'Starting task execution',
                'current_sub_task': task,
                'current_instructions': 'Execute the task as described',
                'database_path': context.get('database_path', './data'),
                'codebase_path': context.get('codebase_path', './codebase'),
                'previous_steps_execution_summary': 'No previous steps - this is the first step.',
            }
            try:
                formatted_prompt = template.format(**essential_context)
            except Exception as e2:
                print(f"[MassGenEngineer] Error formatting template: {e2}")
                print(f"[MassGenEngineer] Falling back to task-only prompt")
                formatted_prompt = task

        return formatted_prompt

    async def _call_massgen(self, prompt: str, messages: Optional[List[Dict]] = None) -> str:
        """Call MassGen asynchronously to generate code.

        Args:
            prompt: The raw task/prompt from the conversation
            messages: Message history for context

        Returns:
            Formatted response ready for AG2 (includes code block)
        """
        massgen = get_massgen()

        # Format the full engineer prompt with all context variables
        formatted_prompt = self._format_engineer_prompt(prompt, messages)

        if self.verbose:
            print(f"\n[MassGenEngineer] Calling MassGen...")
            print(f"[MassGenEngineer] Config: {self.massgen_config}")
            print(f"[MassGenEngineer] Raw prompt length: {len(prompt)} chars")
            print(f"[MassGenEngineer] Formatted prompt length: {len(formatted_prompt)} chars")
            print(f"[MassGenEngineer] Raw prompt: {prompt}")

        # Call MassGen with the FULL formatted prompt
        result = await massgen.run(
            query=formatted_prompt,
            config=self.massgen_config,
            verbose=self.verbose,
            enable_logging=self.enable_logging
        )

        if self.verbose:
            print(f"[MassGenEngineer] Selected agent: {result.get('selected_agent', 'N/A')}")
            if result.get('log_directory'):
                print(f"[MassGenEngineer] Logs: {result['log_directory']}")

        response = result['final_answer']

        # Format response for AG2 executor
        # The response should include the full explanation + code in a code block
        # This matches what the engineer normally returns
        formatted_response = self._format_response_for_ag2(response)

        if self.verbose:
            print(f"[MassGenEngineer] Formatted response length: {len(formatted_response)} chars")

        return formatted_response

    def _format_response_for_ag2(self, massgen_response: str) -> str:
        """Format MassGen response for AG2 executor.

        Ensures the response includes code in ```python``` blocks
        which the executor expects.
        """
        # Check if response already has code blocks
        if "```python" in massgen_response:
            # Already formatted properly
            return massgen_response

        # Try to extract code and wrap it properly
        code = self._extract_code_from_response(massgen_response)

        # Build formatted response matching engineer's expected format
        formatted = f"""**Code Explanation:**

Generated by MassGen multi-agent collaboration.

**Python Code:**

```python
{code}
```
"""
        return formatted

    def _is_retry_attempt(self, messages: List[Dict]) -> bool:
        """Detect if this is a retry/debugging attempt.

        Returns True if:
        - Code execution errors are present in history
        - Previous code blocks exist (indicating a retry)
        - Error-related keywords in recent messages
        """
        if len(messages) < 2:
            # First message - definitely not a retry
            return False

        # Check last few messages for error indicators
        recent_messages = messages[-5:] if len(messages) >= 5 else messages

        for msg in recent_messages:
            content = msg.get("content", "").lower()

            # Check for execution results or errors
            if any(keyword in content for keyword in [
                "exitcode",
                "traceback",
                "error:",
                "exception",
                "failed",
                "stderr:",
                "exit code",
                "returned non-zero",
            ]):
                return True

            # Check for code execution output
            if "```python" in content and any(err in content for err in [
                "error", "exception", "failed", "traceback"
            ]):
                return True

        return False

    def _massgen_reply_func(
        self,
        recipient: ConversableAgent,
        messages: Optional[List[Dict]] = None,
        sender: Optional[ConversableAgent] = None,
        config: Optional[Any] = None,
    ) -> tuple[bool, Union[str, Dict, None]]:
        """Custom reply function for MassGen hybrid approach.

        This is called by AG2's reply system. The config parameter may contain
        context_variables from the conversation.

        Strategy:
        - First code generation: Use MassGen (multi-agent collaboration)
        - Retry/debugging: Use standard LLM (fast, cheap iteration)

        Returns:
            tuple: (should_reply, reply_content)
                - should_reply: True if this function generated a reply
                - reply_content: The generated reply or None
        """
        if messages is None:
            print("[MassGenEngineer] Warning: messages is None, skipping")
            return False, None

        if not messages:
            print("[MassGenEngineer] Warning: messages is empty, skipping")
            return False, None

        # Store context_variables if available (passed through config or recipient)
        if config and isinstance(config, dict) and 'context_variables' in config:
            self.context_variables = config['context_variables']
        elif hasattr(recipient, 'context_variables'):
            self.context_variables = recipient.context_variables
        elif hasattr(sender, 'context_variables'):
            self.context_variables = sender.context_variables

        # Determine if this is a retry
        is_retry = self._is_retry_attempt(messages)

        self._generation_count += 1

        try:
            if is_retry and not self.use_massgen_for_retries:
                # RETRY MODE: Skip to next reply function (standard LLM)
                print(f"\n[MassGenEngineer] Attempt #{self._generation_count} - RETRY MODE (single LLM)")
                print(f"[MassGenEngineer] Delegating to standard LLM for fast debugging")

                # Return False to let next reply function (default LLM) handle it
                return False, None

            else:
                # INITIAL MODE: Use MassGen for quality code generation
                print(f"\n[MassGenEngineer] Attempt #{self._generation_count} - INITIAL MODE (MassGen multi-agent)")
                print(f"[MassGenEngineer] Using MassGen for high-quality code generation")

                last_message = messages[-1]
                prompt = last_message.get("content", "")

                if not prompt:
                    print("[MassGenEngineer] Warning: prompt is empty")
                    return False, None

                # Call MassGen synchronously (AG2 expects sync)
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # Already in async context, create new loop
                        import nest_asyncio
                        try:
                            nest_asyncio.apply()
                        except:
                            # nest_asyncio not available, try different approach
                            loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(loop)
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)

                print(f"[MassGenEngineer] Calling MassGen...")
                response = loop.run_until_complete(self._call_massgen(prompt, messages))

                if response:
                    print(f"[MassGenEngineer] MassGen generated response (length: {len(response)})")
                    return True, response  # We generated a reply
                else:
                    print(f"[MassGenEngineer] Warning: MassGen returned empty response")
                    return False, None  # Let next reply function try

        except Exception as e:
            print(f"[MassGenEngineer] Error in _massgen_reply_func: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            return False, None  # Let next reply function try


def create_massgen_engineer_agent(
    name: str,
    llm_config: Dict,
    instructions: str,
    description: str,
    massgen_config: Optional[str] = None,
    verbose: bool = False,
    enable_logging: bool = True,
    use_massgen_for_retries: bool = False,
    **kwargs
) -> MassGenEngineerAgent:
    """Factory function to create a hybrid MassGen engineer agent.

    Args:
        name: Agent name (typically "engineer")
        llm_config: LLM config (used for retry attempts with single LLM)
        instructions: Engineer instructions/system message
        description: Agent description
        massgen_config: Path to MassGen config (optional)
        verbose: Enable verbose output
        enable_logging: Enable MassGen logging
        use_massgen_for_retries: Use MassGen even for retries (default: False)
        **kwargs: Additional arguments

    Returns:
        Configured MassGenEngineerAgent with hybrid strategy
    """
    agent = MassGenEngineerAgent(
        name=name,
        massgen_config=massgen_config,
        extract_code=True,
        verbose=verbose,
        enable_logging=enable_logging,
        use_massgen_for_retries=use_massgen_for_retries,
        update_agent_state_before_reply=[UpdateSystemMessage(instructions)],
        description=description,
        llm_config=llm_config,  # Used for retry attempts
        **kwargs
    )

    return agent
