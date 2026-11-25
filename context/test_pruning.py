"""Offline pruning tests using real SDK chat history and request validation."""
import unittest
from unittest.mock import patch

from google.genai import types

from agent.events import AgentEventType
from agent.loop import Agent
from context.manager import ContextManager, PRUNED_PLACEHOLDER


def history_with_outputs(count=8):
    history = [types.Content(role="user", parts=[types.Part.from_text(text="Original task")])]
    for i in range(count):
        history.append(types.Content(role="model", parts=[types.Part(
            function_call=types.FunctionCall(name="read_file", args={"path": f"{i}.txt"},
                                             id=f"call-{i}")
        )]))
        history.append(types.Content(role="user", parts=[types.Part(
            function_response=types.FunctionResponse(name="read_file", id=f"call-{i}",
                                                    response={"result": str(i) * 500})
        )]))
    return history


def response(tokens):
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=[types.Part.from_text(text="Done")])
    )], usage_metadata=types.GenerateContentResponseUsageMetadata(total_token_count=tokens))


class PruningTests(unittest.TestCase):
    def test_preserves_recent_outputs_call_ids_and_original_history(self):
        manager = ContextManager()
        original = history_with_outputs()
        pruned, count = manager.prune_history(original)
        self.assertEqual(count, 2)
        self.assertEqual(pruned[0], original[0])
        for i in range(8):
            self.assertEqual(pruned[1 + 2 * i], original[1 + 2 * i])
            result = pruned[2 + 2 * i].parts[0].function_response
            self.assertEqual(result.name, "read_file")
            self.assertEqual(result.id, f"call-{i}")
            self.assertEqual(result.response["result"],
                             PRUNED_PLACEHOLDER if i < 2 else str(i) * 500)
            self.assertEqual(original[2 + 2 * i].parts[0].function_response.response["result"],
                             str(i) * 500)
        _, repeated_count = manager.prune_history(pruned)
        self.assertEqual(repeated_count, 0)
        self.assertEqual(manager.pruned_total, 2)

    def test_recent_and_small_outputs_are_not_pruned(self):
        manager = ContextManager()
        _, count = manager.prune_history(history_with_outputs(6))
        self.assertEqual(count, 0)
        history = history_with_outputs()
        for entry in history:
            for part in entry.parts:
                if part.function_response:
                    part.function_response.response = {"result": "ok"}
        _, count = manager.prune_history(history)
        self.assertEqual(count, 0)

    def test_threshold_crossing_rebuilds_chat_emits_event_and_continues(self):
        with patch("llm.client.Config.API_KEY", "offline-test-key"):
            agent = Agent()
        client = agent.client
        try:
            client.chat_session = client.client.chats.create(
                model="offline-model", config=client.generation_config,
                history=history_with_outputs(),
            )
            old_chat = client.chat_session
            with patch.object(client.client.models, "_generate_content",
                              side_effect=[response(81000), response(1000)]) as request, \
                    patch("agent.loop.time.sleep"), patch("builtins.print") as raw_print:
                first = list(agent.run("Continue"))
                second = list(agent.run("Continue again"))
                raw_print.assert_not_called()
            self.assertIsNot(client.chat_session, old_chat)
            prunes = [e for e in first if e.type == AgentEventType.CONTEXT_PRUNED]
            self.assertEqual(len(prunes), 1)
            self.assertEqual(prunes[0].data, {"n_pruned": 2, "tokens_before": 81000})
            self.assertFalse([e for e in second if e.type == AgentEventType.CONTEXT_PRUNED])
            for events in (first, second):
                self.assertFalse([e for e in events if e.type == AgentEventType.AGENT_ERROR])
                self.assertEqual(events[-1].data["response"], "Done")
            sent_history = request.call_args_list[1].kwargs["contents"]
            self.assertEqual(sent_history[2].parts[0].function_response.response["result"],
                             PRUNED_PLACEHOLDER)
            self.assertTrue(client.generation_config.automatic_function_calling.disable)
        finally:
            client.client.close()


if __name__ == "__main__":
    unittest.main()
