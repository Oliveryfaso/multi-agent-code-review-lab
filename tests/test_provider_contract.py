import unittest
from pathlib import Path
from unittest.mock import patch
from macr.providers import base
from macr.providers.mock import MockLLMProvider
from macr.providers.deepseek import DeepSeekProvider


class FakeTransport:
    def __init__(self, payload=None, error=None):
        self.payload, self.error, self.calls = payload, error, []
    def post(self, endpoint, body, timeout_seconds, max_response_bytes):
        self.calls.append((endpoint, body, timeout_seconds, max_response_bytes))
        if self.error:
            raise self.error
        return self.payload


class ProviderContractTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(hasattr(base, 'ModelRequest'), 'synchronous model contract missing')
        self.request = base.ModelRequest([{'role': 'user', 'content': 'fixture'}], timeout_seconds=2)

    def test_all_providers_share_sync_contract(self):
        response = base.ModelResponse('fixture output', provider='mock')
        self.assertEqual(MockLLMProvider([response]).complete(self.request), response)
        transport = FakeTransport({'choices': [{'message': {'content': 'answer'}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 3, 'completion_tokens': 2}})
        with patch.object(Path, 'read_text', side_effect=AssertionError('must not scan env')):
            provider = DeepSeekProvider(api_key='fixture', transport=transport)
            result = provider.complete(self.request)
        self.assertIsInstance(result, base.ModelResponse)
        self.assertEqual(result.usage['input_tokens'], 3)
        self.assertEqual(result.usage_kind, 'measured')
        self.assertEqual(transport.calls[0][2], 2)

    def test_timeout_unknown_usage_and_no_remote_default(self):
        with self.assertRaises(base.ProviderError) as error:
            DeepSeekProvider(api_key='fixture').complete(self.request)
        self.assertEqual(error.exception.code, 'remote_disabled')
        transport = FakeTransport(error=TimeoutError('fixture'))
        with self.assertRaises(base.ProviderError) as error:
            DeepSeekProvider(api_key='fixture', transport=transport).complete(self.request)
        self.assertEqual(error.exception.code, 'model_timeout')
        result = DeepSeekProvider(api_key='fixture', transport=FakeTransport({'choices': [{'message': {'content': 'x'}}]})).complete(self.request)
        self.assertEqual((result.usage_kind, result.usage), ('unknown', {}))

    def test_cli_mock_and_rules_are_distinct(self):
        from macr.cli import _provider
        self.assertIsNone(_provider('rules'))
        self.assertIsInstance(_provider('mock'), MockLLMProvider)
        with self.assertRaises(base.ProviderError):
            _provider('deepseek')

    def test_consumers_call_complete_and_reject_coroutines(self):
        from macr.agents.planner import LlmPlanner
        provider = MockLLMProvider([base.ModelResponse('{"intent":"code_qa","steps":[]}')])
        plan, response = LlmPlanner(provider).plan('send')
        self.assertIsNotNone(response)
        class AsyncProvider:
            async def complete(self, request):
                return base.ModelResponse('unexpected')
        planner = LlmPlanner(AsyncProvider())
        _, response = planner.plan('send')
        self.assertIsNone(response)
        self.assertEqual(planner.last_model_error, 'provider_contract')

    def test_invalid_request_and_response_are_explicit(self):
        with self.assertRaises(base.ProviderError):
            MockLLMProvider().complete(base.ModelRequest([], timeout_seconds=0))
        with self.assertRaises(base.ProviderError):
            DeepSeekProvider(api_key='fixture', transport=FakeTransport({'choices': []})).complete(self.request)
