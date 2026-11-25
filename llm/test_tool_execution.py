"""Offline regression tests for ownership of tool execution."""
import unittest
from unittest.mock import patch

from google.genai import types

from agent.events import AgentEventType
from agent.loop import Agent
from llm.client import LLMClient


def tool_response():
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=[
            types.Part(function_call=types.FunctionCall(name="probe", args={}))
        ])
    )])


class ToolExecutionTests(unittest.TestCase):
    def test_sdk_returns_tool_call_without_executing_callable(self):
        calls = []

        def probe() -> str:
            """Record execution of a harmless test tool."""
            calls.append("executed")
            return "ok"

        with patch("llm.client.Config.API_KEY", "offline-test-key"):
            client = LLMClient()
        try:
            # Keep the real SDK chat and automatic-function-calling machinery;
            # replace only the network request with a model tool-call response.
            with patch.object(client.client.models, "_generate_content",
                              return_value=tool_response()) as request:
                response, _ = client.send("Run probe", tools=[probe])
            self.assertEqual(calls, [])
            self.assertEqual(request.call_count, 1)
            self.assertEqual(response.function_calls[0].name, "probe")
        finally:
            client.client.close()

    def test_agent_executes_once_after_start_event(self):
        calls = []

        def probe():
            calls.append("executed")
            return "ok"

        with patch("agent.loop.LLMClient") as client_type:
            client_type.return_value.context.pruned_total = 0
            client_type.return_value.send.side_effect = [
                (tool_response(), 0),
                (types.GenerateContentResponse(candidates=[types.Candidate(
                    content=types.Content(role="model", parts=[
                        types.Part.from_text(text="Done")
                    ])
                )]), 0),
            ]
            agent = Agent()
        with patch("agent.loop.TOOL_MAP", {"probe": probe}), \
                patch("agent.loop.time.sleep"):
            events = agent.run("Run probe")
            self.assertEqual(next(events).type, AgentEventType.AGENT_START)
            self.assertEqual(next(events).type, AgentEventType.TOOL_CALL_START)
            self.assertEqual(calls, [])
            self.assertEqual(next(events).type, AgentEventType.TOOL_CALL_COMPLETE)
            self.assertEqual(calls, ["executed"])
            remaining = list(events)
            self.assertEqual(calls, ["executed"])
            self.assertEqual(remaining[-1].type, AgentEventType.AGENT_END)


if __name__ == "__main__":
    unittest.main()
