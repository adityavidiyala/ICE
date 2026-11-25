import json
import time
from collections import deque
from google.genai import types
from llm.client import LLMClient
from agent.events import AgentEvent, AgentEventType
from tools import ICE_TOOLS

# We map the Python functions so we can call them dynamically by name
TOOL_MAP = {tool.__name__: tool for tool in ICE_TOOLS}

class LoopDetector:
    """Detects if the agent is stuck in a loop repeating the same actions."""
    def __init__(self, max_history=10, loop_threshold=3):
        self.history = deque(maxlen=max_history)
        self.loop_threshold = loop_threshold

    def reset(self):
        self.history.clear()

    def record_action(self, action_type: str, tool_name: str = None, args: dict = None):
        """Records an action to check for repetition."""
        action_sig = f"{action_type}:{tool_name}:{json.dumps(args, sort_keys=True) if args else ''}"
        self.history.append(action_sig)

    def check_for_loop(self) -> str | None:
        """Returns an error message if a loop is detected, otherwise None."""
        if len(self.history) < self.loop_threshold:
            return None
        recent_actions = list(self.history)[-self.loop_threshold:]
        if all(action == recent_actions[0] for action in recent_actions):
            return "You are repeating the same tool and arguments. Use the tool results already provided and change your approach."
        return None

class Agent:
    def __init__(self, max_turns: int = 15, backend=None):
        self.backend = backend
        self.client = LLMClient(sandboxed=True) if backend is not None else LLMClient()
        self.loop_detector = LoopDetector()
        self.max_turns = max_turns

    def run(self, user_message: str, stream: bool = False):
        """The main agentic loop."""
        self.loop_detector.reset()
        yield AgentEvent.agent_start(user_message)

        current_message = user_message
        final_response = None

        for turn in range(self.max_turns):
            pruned_before = self.client.context.pruned_total
            try:
                # Send the message; LLMClient returns (response, token_count)
                if stream:
                    response, token_count = yield from self.client.send_stream(current_message, tools=ICE_TOOLS)
                else:
                    response, token_count = self.client.send(current_message, tools=ICE_TOOLS)
            except Exception as e:
                yield AgentEvent.agent_error(f"LLM API Error: {e}")
                break

            # Emit token usage so the UI can display it
            if token_count:
                yield AgentEvent.token_usage(token_count, turn + 1)

            # Emit pruning event if the client pruned this turn
            ctx = self.client.context
            n_pruned = ctx.pruned_total - pruned_before
            if n_pruned > 0:
                yield AgentEvent.context_pruned(n_pruned, token_count)

            tool_calls_made = False
            tool_results_payload = []
            response_text = ""

            # Parse Gemini's response parts
            if response.candidates and response.candidates[0].content.parts:
                for part in response.candidates[0].content.parts:

                    # 1. Handle Text Output
                    if part.text and not part.thought:
                        response_text += part.text

                    # 2. Handle Tool Calls
                    elif part.function_call:
                        if response_text:
                            yield AgentEvent.text_complete(response_text)
                            response_text = ""
                        tool_calls_made = True
                        func_name = part.function_call.name
                        func_args = dict(part.function_call.args)

                        yield AgentEvent.tool_call_start(func_name, func_args)

                        # Record for loop detection
                        self.loop_detector.record_action("tool_call", tool_name=func_name, args=func_args)

                        # Execute the tool
                        if func_name in TOOL_MAP:
                            try:
                                if self.backend is not None and func_name != 'plan':
                                    tool_output = self.backend.invoke(func_name, func_args)
                                else:
                                    tool_output = TOOL_MAP[func_name](**func_args)
                            except Exception as e:
                                tool_output = f"Tool execution failed: {e}"
                        else:
                            tool_output = f"Error: Tool {func_name} does not exist."

                        yield AgentEvent.tool_call_complete(func_name, tool_output)

                        # Append the result for the next turn
                        tool_results_payload.append(types.Part(
                            function_response=types.FunctionResponse(
                                name=func_name,
                                id=part.function_call.id,
                                response={"result": tool_output},
                            )
                        ))

            final_response = response_text
            if response_text:
                yield AgentEvent.text_complete(response_text)

            if tool_calls_made:
                # Attach guidance to the result instead of replacing the batch.
                # Every requested function response (including its ID) survives.
                loop_err = self.loop_detector.check_for_loop()
                if loop_err:
                    tool_results_payload[-1].function_response.response["warning"] = (
                        f"SYSTEM WARNING: {loop_err}"
                    )
                current_message = tool_results_payload
            else:
                break
        else:
            yield AgentEvent.agent_error("Max turns reached without completing the task.")

        yield AgentEvent.agent_end(final_response)
