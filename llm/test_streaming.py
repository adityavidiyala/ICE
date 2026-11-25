"""Real SDK streaming round trips with offline transport responses."""
import io
import unittest
from unittest.mock import patch

from google.genai import types
from rich.console import Console

from agent.events import AgentEvent, AgentEventType as Event
from agent.loop import Agent
from ui.terminal import TerminalUI


def chunk(parts, finished=False, tokens=None):
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=parts),
        finish_reason="STOP" if finished else None,
    )], usage_metadata=(types.GenerateContentResponseUsageMetadata(total_token_count=tokens)
                       if tokens else None))


class StreamingTests(unittest.TestCase):
    def test_text_arrives_before_request_finishes_and_tools_execute_once(self):
        calls = []
        def probe():
            calls.append("executed")
            return "ok"

        def first_response():
            yield chunk([types.Part.from_text(text="Checking ")])
            yield chunk([types.Part.from_text(text="now.")])
            yield chunk([types.Part(function_call=types.FunctionCall(
                name="probe", args={}, id="call-1"), thought_signature=b"signature")],
                finished=True, tokens=200)

        with patch("llm.client.Config.API_KEY", "offline-test-key"):
            agent = Agent()
        try:
            with patch.object(agent.client.client.models, "_generate_content_stream",
                              side_effect=[first_response(), iter([
                                  chunk([types.Part.from_text(text="Done.")], True, 300)
                              ])]) as transport, patch("agent.loop.TOOL_MAP", {"probe": probe}):
                events = agent.run("Run probe", stream=True)
                self.assertEqual(next(events).type, Event.AGENT_START)
                first_delta = next(events)
                self.assertEqual(first_delta.type, Event.TEXT_DELTA)
                self.assertEqual(first_delta.data["content"], "Checking ")
                self.assertEqual(calls, [])
                remaining = list(events)
            self.assertEqual(calls, ["executed"])
            self.assertFalse([e for e in remaining if e.type == Event.AGENT_ERROR])
            self.assertEqual(remaining[-1].data["response"], "Done.")
            self.assertEqual(agent.client.context.total_tokens, 300)
            history = transport.call_args_list[1].kwargs["contents"]
            result = history[-1].parts[0].function_response
            self.assertEqual(result.response, {"result": "ok"})
            self.assertEqual(result.id, "call-1")
            self.assertTrue(any(p.thought_signature == b"signature"
                                for entry in history for p in entry.parts))
        finally:
            agent.client.client.close()

    def test_nonterminal_transcript_prints_stream_once_and_expands_literal_output(self):
        output = io.StringIO()
        console = Console(file=output, width=100)
        ui = TerminalUI()
        with patch("ui.terminal.console", console), patch("ui.display.console", console):
            for event in [AgentEvent.agent_start("hello"),
                          AgentEvent.text_delta("Hello "), AgentEvent.text_delta("world"),
                          AgentEvent.text_complete("Hello world"),
                          AgentEvent.tool_call_start("probe", {}),
                          AgentEvent.tool_call_complete("probe", "[green] literal"),
                          AgentEvent.agent_end("Hello world")]:
                ui.handle(event)
            ui.expand()
            ui.close()
        self.assertEqual(output.getvalue().count("Hello world"), 1)
        self.assertIn("[green] literal", output.getvalue())
        self.assertIn("1 tools", output.getvalue())


if __name__ == "__main__":
    unittest.main()
