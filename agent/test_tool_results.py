"""Exercise tool-result round trips through the real SDK without network access."""
import unittest
from unittest.mock import patch

from google.genai import types

from agent.events import AgentEventType
from agent.loop import Agent


class ToolResultTests(unittest.TestCase):
    def check_round_trip(self, names, tool_map, expected_results):
        tool_calls = types.GenerateContentResponse(candidates=[types.Candidate(
            content=types.Content(role="model", parts=[
                types.Part(function_call=types.FunctionCall(
                    name=name, args={}, id=f"call-{i}"
                )) for i, name in enumerate(names)
            ])
        )])
        finished = types.GenerateContentResponse(candidates=[types.Candidate(
            content=types.Content(role="model", parts=[
                types.Part.from_text(text="Done")
            ])
        )])
        with patch("llm.client.Config.API_KEY", "offline-test-key"):
            agent = Agent()
        try:
            # Only the transport is mocked. Chat.send_message still validates
            # and converts the actual payload produced by Agent.run.
            with patch.object(agent.client.client.models, "_generate_content",
                              side_effect=[tool_calls, finished]) as request, \
                    patch("agent.loop.TOOL_MAP", tool_map), \
                    patch("agent.loop.time.sleep"):
                events = list(agent.run("Run the tools"))
            self.assertEqual(request.call_count, 2)
            self.assertFalse([e for e in events if e.type == AgentEventType.AGENT_ERROR])
            self.assertEqual(events[-1].data["response"], "Done")
            contents = request.call_args_list[1].kwargs["contents"]
            results = [p.function_response for p in contents[-1].parts]
            self.assertEqual([r.name for r in results], names)
            self.assertEqual([r.id for r in results],
                             [f"call-{i}" for i in range(len(names))])
            self.assertEqual([r.response["result"] for r in results], expected_results)
        finally:
            agent.client.client.close()

    def test_single_result(self):
        self.check_round_trip(["probe"], {"probe": lambda: "ok"}, ["ok"])

    def test_multiple_results(self):
        self.check_round_trip(
            ["first", "second"],
            {"first": lambda: "one", "second": lambda: "two"},
            ["one", "two"],
        )

    def test_tool_failure_is_returned_to_model(self):
        def failing_tool():
            raise RuntimeError("test failure")

        self.check_round_trip(["probe"], {"probe": failing_tool},
                             ["Tool execution failed: test failure"])

    def test_unknown_tool_is_returned_to_model(self):
        self.check_round_trip(["missing"], {},
                             ["Error: Tool missing does not exist."])


if __name__ == "__main__":
    unittest.main()
