"""Loop warnings must retain real tool results and stop at request boundaries."""
import unittest
from unittest.mock import patch

from google.genai import types
from agent.events import AgentEventType
from agent.loop import Agent


def calls(*ids):
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role='model', parts=[types.Part(function_call=types.FunctionCall(
            name='probe', args={}, id=value)) for value in ids])
    )])


def done():
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role='model', parts=[types.Part.from_text(text='Done')])
    )])


class LoopTests(unittest.TestCase):
    def setUp(self):
        with patch('llm.client.Config.API_KEY', 'offline-test-key'):
            self.agent = Agent(max_turns=4)
        self.addCleanup(self.agent.client.client.close)

    def test_warning_retains_result_and_call_id_through_real_sdk(self):
        with patch.object(self.agent.client.client.models, '_generate_content',
                          side_effect=[calls('one'), calls('two'), calls('three'), done()]) as request, \
                patch('agent.loop.TOOL_MAP', {'probe': lambda: 'actual output'}):
            events = list(self.agent.run('work'))
        result = request.call_args_list[3].kwargs['contents'][-1].parts[0].function_response
        self.assertEqual(result.name, 'probe')
        self.assertEqual(result.id, 'three')
        self.assertEqual(result.response['result'], 'actual output')
        self.assertIn('SYSTEM WARNING', result.response['warning'])
        self.assertFalse([event for event in events if event.type == AgentEventType.AGENT_ERROR])
        self.assertEqual(events[-1].data['response'], 'Done')

    def test_warning_preserves_every_result_in_batch(self):
        with patch.object(self.agent.client.client.models, '_generate_content',
                          side_effect=[calls('one', 'two', 'three'), done()]) as request, \
                patch('agent.loop.TOOL_MAP', {'probe': lambda: 'actual output'}):
            list(self.agent.run('work'))
        results = [part.function_response for part in request.call_args_list[1].kwargs['contents'][-1].parts]
        self.assertEqual([item.id for item in results], ['one', 'two', 'three'])
        self.assertTrue(all(item.response['result'] == 'actual output' for item in results))
        self.assertIn('warning', results[-1].response)

    def test_detector_resets_between_user_requests(self):
        with patch.object(self.agent.client.client.models, '_generate_content',
                          side_effect=[calls('one'), calls('two'), done(), calls('three'), done()]) as request, \
                patch('agent.loop.TOOL_MAP', {'probe': lambda: 'actual output'}):
            list(self.agent.run('first task'))
            list(self.agent.run('new task'))
        result = request.call_args_list[4].kwargs['contents'][-1].parts[0].function_response
        self.assertNotIn('warning', result.response)
        self.assertEqual(len(self.agent.loop_detector.history), 1)


if __name__ == '__main__':
    unittest.main()
