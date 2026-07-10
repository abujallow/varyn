from __future__ import annotations

import json
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

import providers
from audit import AuditLogger


def openrouter_payload(content="hello", model="model-a", tool_calls=None):
    message = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {"choices": [{"message": message}], "model": model}


class FakeHTTPResponse:
    """Mimics the context-manager object urllib.request.urlopen() returns."""

    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeStreamResponse:
    """Mimics urlopen()'s response object when iterated line-by-line for SSE."""

    def __init__(self, lines: list[bytes]):
        self._lines = lines

    def __iter__(self):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def sse_lines(*chunks: dict) -> list[bytes]:
    lines = [f"data: {json.dumps(chunk)}".encode("utf-8") for chunk in chunks]
    lines.append(b"data: [DONE]")
    return lines


# ---------------------------------------------------------------------------
# post_json / call_openrouter -- the raw HTTP layer
# ---------------------------------------------------------------------------


class PostJsonTests(unittest.TestCase):
    def test_success_returns_parsed_json(self):
        body = json.dumps({"ok": True}).encode("utf-8")
        with patch("providers.urllib.request.urlopen", return_value=FakeHTTPResponse(body)):
            result = providers.post_json("https://example.test/api", {"a": 1}, timeout=5)
        self.assertEqual(result, {"ok": True})

    def test_http_error_raises_provider_request_error_with_status_code(self):
        exc = urllib.error.HTTPError(
            "https://example.test/api", 500, "Server Error", hdrs=None, fp=None
        )
        with patch("providers.urllib.request.urlopen", side_effect=exc):
            with self.assertRaises(providers.ProviderRequestError) as ctx:
                providers.post_json("https://example.test/api", {}, timeout=5)
        self.assertEqual(ctx.exception.status_code, 500)

    def test_url_error_raises_provider_request_error_without_status_code(self):
        exc = urllib.error.URLError("connection refused")
        with patch("providers.urllib.request.urlopen", side_effect=exc):
            with self.assertRaises(providers.ProviderRequestError) as ctx:
                providers.post_json("https://example.test/api", {}, timeout=5)
        self.assertIsNone(ctx.exception.status_code)

    def test_timeout_raises_provider_request_error_with_408(self):
        with patch("providers.urllib.request.urlopen", side_effect=TimeoutError()):
            with self.assertRaises(providers.ProviderRequestError) as ctx:
                providers.post_json("https://example.test/api", {}, timeout=5)
        self.assertEqual(ctx.exception.status_code, 408)


class CallOpenRouterTests(unittest.TestCase):
    def test_success_returns_response_dict(self):
        body = json.dumps(openrouter_payload()).encode("utf-8")
        with patch.object(providers, "OPENROUTER_API_KEY", "sk-test-not-real"), patch(
            "providers.urllib.request.urlopen", return_value=FakeHTTPResponse(body)
        ):
            data = providers.call_openrouter(
                [{"role": "user", "content": "hi"}], "model-a", None, timeout=5
            )
        self.assertEqual(data["model"], "model-a")

    def test_includes_tools_payload_when_tools_provided(self):
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            captured["headers"] = dict(request.headers)
            return FakeHTTPResponse(json.dumps(openrouter_payload()).encode("utf-8"))

        tools = [{"type": "function", "function": {"name": "lookup"}}]
        with patch.object(providers, "OPENROUTER_API_KEY", "sk-test-not-real"), patch(
            "providers.urllib.request.urlopen", side_effect=fake_urlopen
        ):
            providers.call_openrouter([{"role": "user", "content": "hi"}], "model-a", tools, timeout=5)
        self.assertEqual(captured["payload"]["tools"], tools)
        self.assertEqual(captured["payload"]["tool_choice"], "auto")
        # Header keys are Title-Cased by urllib.request.Request.
        self.assertEqual(captured["headers"]["Authorization"], "Bearer sk-test-not-real")

    def test_http_error_propagates_as_provider_request_error(self):
        exc = urllib.error.HTTPError(
            "https://openrouter.ai/api/v1/chat/completions", 429, "rate limited", hdrs=None, fp=None
        )
        with patch.object(providers, "OPENROUTER_API_KEY", "sk-test-not-real"), patch(
            "providers.urllib.request.urlopen", side_effect=exc
        ):
            with self.assertRaises(providers.ProviderRequestError) as ctx:
                providers.call_openrouter([{"role": "user", "content": "hi"}], "model-a", None, timeout=5)
        self.assertEqual(ctx.exception.status_code, 429)


# ---------------------------------------------------------------------------
# call_openrouter_stream -- SSE parsing
# ---------------------------------------------------------------------------


class CallOpenRouterStreamTests(unittest.TestCase):
    def _run(self, lines: list[bytes], tools=None):
        with patch.object(providers, "OPENROUTER_API_KEY", "sk-test-not-real"), patch(
            "providers.urllib.request.urlopen", return_value=FakeStreamResponse(lines)
        ):
            return list(
                providers.call_openrouter_stream(
                    [{"role": "user", "content": "hi"}], "model-a", tools, timeout=5
                )
            )

    def test_emits_tokens_and_final_complete_event(self):
        lines = sse_lines(
            {"model": "model-a", "choices": [{"delta": {"content": "Hel"}}]},
            {"model": "model-a", "choices": [{"delta": {"content": "lo"}}]},
        )
        events = self._run(lines)
        token_events = [event for event in events if event["type"] == "token"]
        complete_events = [event for event in events if event["type"] == "complete"]
        self.assertEqual([event["text"] for event in token_events], ["Hel", "lo"])
        self.assertEqual(len(complete_events), 1)
        result = complete_events[0]["result"]
        self.assertEqual(result.reply, "Hello")
        self.assertEqual(result.model, "model-a")
        self.assertEqual(result.provider, "openrouter")

    def test_list_form_content_deltas_are_joined(self):
        lines = sse_lines(
            {
                "model": "model-a",
                "choices": [{"delta": {"content": [{"text": "a"}, {"text": "b"}]}}],
            }
        )
        events = self._run(lines)
        complete_result = [e for e in events if e["type"] == "complete"][0]["result"]
        self.assertEqual(complete_result.reply, "ab")

    def test_non_data_lines_are_ignored(self):
        lines = [b": keep-alive", b""] + sse_lines(
            {"model": "model-a", "choices": [{"delta": {"content": "hi"}}]}
        )
        events = self._run(lines)
        complete_result = [e for e in events if e["type"] == "complete"][0]["result"]
        self.assertEqual(complete_result.reply, "hi")

    def test_malformed_json_line_is_skipped_not_fatal(self):
        lines = [b"data: {not valid json"] + sse_lines(
            {"model": "model-a", "choices": [{"delta": {"content": "hi"}}]}
        )
        events = self._run(lines)
        complete_result = [e for e in events if e["type"] == "complete"][0]["result"]
        self.assertEqual(complete_result.reply, "hi")

    def test_tool_call_deltas_accumulate_across_chunks(self):
        lines = sse_lines(
            {
                "model": "model-a",
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "id": "call-1", "function": {"name": "look", "arguments": "{\"a\""}}
                            ]
                        }
                    }
                ],
            },
            {
                "model": "model-a",
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "function": {"name": "up", "arguments": ": 1}"}}
                            ]
                        }
                    }
                ],
            },
        )
        events = self._run(lines)
        complete_result = [e for e in events if e["type"] == "complete"][0]["result"]
        self.assertEqual(len(complete_result.tool_calls), 1)
        call = complete_result.tool_calls[0]
        self.assertEqual(call.name, "lookup")
        self.assertEqual(call.arguments, {"a": 1})
        self.assertTrue(call.native)
        self.assertEqual(call.id, "call-1")

    def test_empty_content_and_no_tool_calls_raises(self):
        lines = sse_lines({"model": "model-a", "choices": [{"delta": {}}]})
        with self.assertRaises(providers.ProviderRequestError):
            self._run(lines)

    def test_http_error_mid_stream_raises_provider_request_error(self):
        exc = urllib.error.HTTPError(
            "https://openrouter.ai/api/v1/chat/completions", 503, "unavailable", hdrs=None, fp=None
        )
        with patch.object(providers, "OPENROUTER_API_KEY", "sk-test-not-real"), patch(
            "providers.urllib.request.urlopen", side_effect=exc
        ):
            with self.assertRaises(providers.ProviderRequestError) as ctx:
                list(
                    providers.call_openrouter_stream(
                        [{"role": "user", "content": "hi"}], "model-a", None, timeout=5
                    )
                )
        self.assertEqual(ctx.exception.status_code, 503)

    def test_timeout_raises_provider_request_error_with_408(self):
        with patch.object(providers, "OPENROUTER_API_KEY", "sk-test-not-real"), patch(
            "providers.urllib.request.urlopen", side_effect=TimeoutError()
        ):
            with self.assertRaises(providers.ProviderRequestError) as ctx:
                list(
                    providers.call_openrouter_stream(
                        [{"role": "user", "content": "hi"}], "model-a", None, timeout=5
                    )
                )
        self.assertEqual(ctx.exception.status_code, 408)


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


class ParseOpenRouterResponseTests(unittest.TestCase):
    def test_normal_text_response(self):
        result = providers.parse_openrouter_response(openrouter_payload(content="hi there"), "model-a")
        self.assertEqual(result.reply, "hi there")
        self.assertEqual(result.model, "model-a")
        self.assertEqual(result.tool_calls, [])

    def test_falls_back_to_requested_model_when_response_omits_model(self):
        data = openrouter_payload(content="hi")
        del data["model"]
        result = providers.parse_openrouter_response(data, "requested-model")
        self.assertEqual(result.model, "requested-model")

    def test_native_tool_calls_present(self):
        tool_calls = [
            {"id": "call-1", "function": {"name": "lookup", "arguments": '{"ticker": "AAPL"}'}}
        ]
        data = openrouter_payload(content="", tool_calls=tool_calls)
        result = providers.parse_openrouter_response(data, "model-a")
        self.assertEqual(len(result.tool_calls), 1)
        self.assertEqual(result.tool_calls[0].name, "lookup")
        self.assertTrue(result.tool_calls[0].native)

    def test_empty_content_and_no_tool_calls_raises(self):
        data = openrouter_payload(content="")
        with self.assertRaises(providers.ProviderRequestError):
            providers.parse_openrouter_response(data, "model-a")

    def test_empty_choices_raises(self):
        with self.assertRaises(providers.ProviderRequestError):
            providers.parse_openrouter_response({"choices": []}, "model-a")


class ParseNativeToolCallsTests(unittest.TestCase):
    def test_valid_arguments_parsed(self):
        calls = providers.parse_native_tool_calls(
            [{"id": "c1", "function": {"name": "lookup", "arguments": '{"a": 1}'}}]
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].arguments, {"a": 1})

    def test_malformed_json_arguments_become_empty_dict(self):
        calls = providers.parse_native_tool_calls(
            [{"id": "c1", "function": {"name": "lookup", "arguments": "{not json"}}]
        )
        self.assertEqual(calls[0].arguments, {})

    def test_missing_name_is_skipped(self):
        calls = providers.parse_native_tool_calls([{"id": "c1", "function": {"arguments": "{}"}}])
        self.assertEqual(calls, [])

    def test_missing_id_generates_fallback_id(self):
        calls = providers.parse_native_tool_calls(
            [{"function": {"name": "lookup", "arguments": "{}"}}]
        )
        self.assertEqual(calls[0].id, "native-0")

    def test_dict_arguments_passed_through(self):
        calls = providers.parse_native_tool_calls(
            [{"id": "c1", "function": {"name": "lookup", "arguments": {"a": 1}}}]
        )
        self.assertEqual(calls[0].arguments, {"a": 1})


class ParseStructuredActionsTests(unittest.TestCase):
    def test_fenced_json_block_parsed(self):
        content = 'Sure, here:\n```json\n{"action": "tool", "tool": "lookup", "arguments": {"a": 1}}\n```'
        calls = providers.parse_structured_actions(content)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "lookup")
        self.assertEqual(calls[0].arguments, {"a": 1})
        self.assertFalse(calls[0].native)

    def test_bare_json_object_parsed(self):
        content = '{"action": "tool", "tool": "lookup", "args": {"a": 1}}'
        calls = providers.parse_structured_actions(content)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].arguments, {"a": 1})

    def test_actions_list_parsed(self):
        content = json.dumps(
            {
                "actions": [
                    {"action": "tool", "tool": "lookup_a", "arguments": {}},
                    {"action": "tool", "tool": "lookup_b", "arguments": {}},
                ]
            }
        )
        calls = providers.parse_structured_actions(content)
        self.assertEqual([call.name for call in calls], ["lookup_a", "lookup_b"])

    def test_malformed_json_returns_empty_list(self):
        self.assertEqual(providers.parse_structured_actions("```json\n{not valid\n```"), [])

    def test_plain_prose_returns_empty_list(self):
        self.assertEqual(providers.parse_structured_actions("Just a normal reply, no tools needed."), [])

    def test_empty_content_returns_empty_list(self):
        self.assertEqual(providers.parse_structured_actions(""), [])

    def test_tagged_call_takes_priority_over_json(self):
        content = (
            "<tool_call>lookup<arg_key>ticker</arg_key><arg_value>AAPL</arg_value></tool_call>"
            '\n```json\n{"action": "tool", "tool": "other", "arguments": {}}\n```'
        )
        calls = providers.parse_structured_actions(content)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "lookup")


class ParseTaggedToolCallsTests(unittest.TestCase):
    def test_single_call_with_arguments(self):
        content = "<tool_call>lookup<arg_key>ticker</arg_key><arg_value>AAPL</arg_value></tool_call>"
        calls = providers.parse_tagged_tool_calls(content)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "lookup")
        self.assertEqual(calls[0].arguments, {"ticker": "AAPL"})
        self.assertFalse(calls[0].native)

    def test_multiple_calls_parsed_in_order(self):
        content = (
            "<tool_call>lookup_a<arg_key>x</arg_key><arg_value>1</arg_value></tool_call>"
            "<tool_call>lookup_b<arg_key>y</arg_key><arg_value>2</arg_value></tool_call>"
        )
        calls = providers.parse_tagged_tool_calls(content)
        self.assertEqual([call.name for call in calls], ["lookup_a", "lookup_b"])
        self.assertEqual(calls[1].arguments, {"y": "2"})

    def test_no_match_returns_empty_list(self):
        self.assertEqual(providers.parse_tagged_tool_calls("no tags here"), [])

    def test_call_without_arguments(self):
        content = "<tool_call>ping</tool_call>"
        calls = providers.parse_tagged_tool_calls(content)
        self.assertEqual(calls[0].name, "ping")
        self.assertEqual(calls[0].arguments, {})


# ---------------------------------------------------------------------------
# validated_model_chain / catalog filtering
# ---------------------------------------------------------------------------


class ValidatedModelChainTests(unittest.TestCase):
    def test_non_free_model_skipped_regardless_of_catalog(self):
        with patch.object(providers, "OPENROUTER_MODEL_CHAIN", ("openai/gpt-4", "openai/gpt-oss-20b:free")), \
                patch.object(providers, "load_model_catalog", return_value=None):
            validated, skipped = providers.validated_model_chain(require_tools=False)
        self.assertEqual(validated, ["openai/gpt-oss-20b:free"])
        self.assertEqual(skipped, [("openai/gpt-4", "not_explicitly_free")])

    def test_free_models_pass_when_catalog_unavailable(self):
        with patch.object(
            providers, "OPENROUTER_MODEL_CHAIN", ("openai/gpt-oss-20b:free", "openrouter/free")
        ), patch.object(providers, "load_model_catalog", return_value=None):
            validated, skipped = providers.validated_model_chain(require_tools=False)
        self.assertEqual(validated, ["openai/gpt-oss-20b:free", "openrouter/free"])
        self.assertEqual(skipped, [])

    def test_model_not_in_current_free_catalog_is_skipped(self):
        catalog = {"free_models": ["openrouter/free"], "tool_models": []}
        with patch.object(
            providers, "OPENROUTER_MODEL_CHAIN", ("openai/gpt-oss-20b:free", "openrouter/free")
        ), patch.object(providers, "load_model_catalog", return_value=catalog):
            validated, skipped = providers.validated_model_chain(require_tools=False)
        self.assertEqual(validated, ["openrouter/free"])
        self.assertEqual(skipped, [("openai/gpt-oss-20b:free", "not_in_current_free_catalog")])

    def test_require_tools_uses_tool_models_list(self):
        catalog = {"free_models": ["openai/gpt-oss-20b:free"], "tool_models": ["openrouter/free"]}
        with patch.object(
            providers, "OPENROUTER_MODEL_CHAIN", ("openai/gpt-oss-20b:free", "openrouter/free")
        ), patch.object(providers, "load_model_catalog", return_value=catalog):
            validated, skipped = providers.validated_model_chain(require_tools=True)
        self.assertEqual(validated, ["openrouter/free"])
        self.assertEqual(skipped, [("openai/gpt-oss-20b:free", "not_in_current_free_catalog")])

    def test_duplicate_configured_models_deduplicated(self):
        with patch.object(
            providers, "OPENROUTER_MODEL_CHAIN", ("openai/gpt-oss-20b:free", "openai/gpt-oss-20b:free")
        ), patch.object(providers, "load_model_catalog", return_value=None):
            validated, skipped = providers.validated_model_chain(require_tools=False)
        self.assertEqual(validated, ["openai/gpt-oss-20b:free"])


# ---------------------------------------------------------------------------
# call_gemini
# ---------------------------------------------------------------------------


class CallGeminiTests(unittest.TestCase):
    def test_success_returns_text(self):
        response = {"candidates": [{"content": {"parts": [{"text": "gemini says hi"}]}}]}
        with patch.object(providers, "GEMINI_API_KEY", "sk-gemini-not-real"), patch.object(
            providers, "post_json", return_value=response
        ) as mock_post:
            text = providers.call_gemini([{"role": "user", "content": "hi"}])
        self.assertEqual(text, "gemini says hi")
        # The API key travels in the URL for Gemini -- confirm it is not also
        # leaked into the payload body that would be easier to accidentally log.
        payload = mock_post.call_args.args[1]
        self.assertNotIn("sk-gemini-not-real", json.dumps(payload))

    def test_empty_text_raises_provider_request_error(self):
        response = {"candidates": [{"content": {"parts": [{"text": ""}]}}]}
        with patch.object(providers, "GEMINI_API_KEY", "sk-gemini-not-real"), patch.object(
            providers, "post_json", return_value=response
        ):
            with self.assertRaises(providers.ProviderRequestError):
                providers.call_gemini([{"role": "user", "content": "hi"}])

    def test_no_candidates_raises_provider_request_error(self):
        with patch.object(providers, "GEMINI_API_KEY", "sk-gemini-not-real"), patch.object(
            providers, "post_json", return_value={"candidates": []}
        ):
            with self.assertRaises(providers.ProviderRequestError):
                providers.call_gemini([{"role": "user", "content": "hi"}])


# ---------------------------------------------------------------------------
# complete() -- retry + fallback orchestration
# ---------------------------------------------------------------------------


class CompleteRetryFallbackTests(unittest.TestCase):
    def _patch_common(self, models, retries=1, max_attempts=6, backoff=0.0, max_total=42.0):
        return [
            patch.object(providers, "OPENROUTER_API_KEY", "sk-test-not-real"),
            patch.object(providers, "validated_model_chain", return_value=(models, [])),
            patch.object(providers, "PROVIDER_RETRIES", retries),
            patch.object(providers, "PROVIDER_MAX_ATTEMPTS", max_attempts),
            patch.object(providers, "PROVIDER_BACKOFF_SECONDS", backoff),
            patch.object(providers, "PROVIDER_MAX_TOTAL_SECONDS", max_total),
            patch.object(providers, "get_audit_logger", return_value=MagicMock()),
        ]

    def test_first_model_succeeds_immediately(self):
        patches = self._patch_common(["model-a", "model-b"])
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], \
                patch.object(providers, "call_openrouter", return_value=openrouter_payload(model="model-a")) as mock_call:
            result = providers.complete([{"role": "user", "content": "hi"}])
        self.assertEqual(result.provider, "openrouter")
        self.assertEqual(result.model, "model-a")
        self.assertEqual(mock_call.call_count, 1)

    def test_transient_failure_then_success_retries_same_model(self):
        patches = self._patch_common(["model-a"], retries=1)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], \
                patch.object(
                    providers,
                    "call_openrouter",
                    side_effect=[
                        providers.ProviderRequestError("HTTP 500 from provider.", status_code=500),
                        openrouter_payload(model="model-a"),
                    ],
                ) as mock_call:
            result = providers.complete([{"role": "user", "content": "hi"}])
        self.assertEqual(result.provider, "openrouter")
        self.assertEqual(mock_call.call_count, 2)

    def test_non_transient_failure_advances_to_next_model_without_retry(self):
        patches = self._patch_common(["model-a", "model-b"], retries=3)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], \
                patch.object(
                    providers,
                    "call_openrouter",
                    side_effect=[
                        providers.ProviderRequestError("HTTP 401 from provider.", status_code=401),
                        openrouter_payload(model="model-b"),
                    ],
                ) as mock_call:
            result = providers.complete([{"role": "user", "content": "hi"}])
        self.assertEqual(result.model, "model-b")
        # Non-transient (401) must not be retried on model-a -- exactly one call per model.
        self.assertEqual(mock_call.call_count, 2)

    def test_all_models_exhausted_falls_back_to_gemini(self):
        patches = self._patch_common(["model-a"], retries=0)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], \
                patch.object(
                    providers,
                    "call_openrouter",
                    side_effect=providers.ProviderRequestError("HTTP 500 from provider.", status_code=500),
                ), \
                patch.object(providers, "GEMINI_API_KEY", "sk-gemini-not-real"), \
                patch.object(providers, "GEMINI_MODEL", "gemini-test-model"), \
                patch.object(providers, "call_gemini", return_value="gemini fallback reply"):
            result = providers.complete([{"role": "user", "content": "hi"}])
        self.assertEqual(result.provider, "gemini")
        self.assertEqual(result.model, "gemini-test-model")
        self.assertEqual(result.reply, "gemini fallback reply")

    def test_all_models_and_gemini_exhausted_falls_back_to_local_offline(self):
        patches = self._patch_common(["model-a"], retries=0)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], \
                patch.object(
                    providers,
                    "call_openrouter",
                    side_effect=providers.ProviderRequestError("HTTP 500 from provider.", status_code=500),
                ), \
                patch.object(providers, "GEMINI_API_KEY", ""):
            result = providers.complete([{"role": "user", "content": "hi"}])
        self.assertEqual(result.provider, "local")
        self.assertEqual(result.status, "Local offline mode")
        self.assertIsNone(result.model)
        self.assertIn("model-a", result.error)
        self.assertIn(
            "Local tools remain available",
            result.reply,
        )

    def test_gemini_also_failing_reports_combined_errors(self):
        patches = self._patch_common(["model-a"], retries=0)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], \
                patch.object(
                    providers,
                    "call_openrouter",
                    side_effect=providers.ProviderRequestError("HTTP 500 from provider.", status_code=500),
                ), \
                patch.object(providers, "GEMINI_API_KEY", "sk-gemini-not-real"), \
                patch.object(
                    providers,
                    "call_gemini",
                    side_effect=providers.ProviderRequestError("Gemini quota exceeded."),
                ):
            result = providers.complete([{"role": "user", "content": "hi"}])
        self.assertEqual(result.provider, "local")
        self.assertIn("Gemini quota exceeded.", result.error)
        self.assertIn("model-a", result.error)

    def test_retry_budget_exhaustion_stops_further_attempts(self):
        patches = self._patch_common(["model-a", "model-b"], retries=5, max_attempts=1)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], \
                patch.object(
                    providers,
                    "call_openrouter",
                    side_effect=providers.ProviderRequestError("HTTP 500 from provider.", status_code=500),
                ) as mock_call, \
                patch.object(providers, "GEMINI_API_KEY", ""):
            result = providers.complete([{"role": "user", "content": "hi"}])
        # Global attempt budget is 1 -- only the very first call should ever fire.
        self.assertEqual(mock_call.call_count, 1)
        self.assertEqual(result.provider, "local")

    def test_no_openrouter_key_skips_straight_to_gemini(self):
        with patch.object(providers, "OPENROUTER_API_KEY", ""), patch.object(
            providers, "GEMINI_API_KEY", "sk-gemini-not-real"
        ), patch.object(providers, "GEMINI_MODEL", "gemini-test-model"), patch.object(
            providers, "call_gemini", return_value="gemini only reply"
        ), patch.object(providers, "get_audit_logger", return_value=MagicMock()):
            result = providers.complete([{"role": "user", "content": "hi"}])
        self.assertEqual(result.provider, "gemini")
        self.assertEqual(result.reply, "gemini only reply")


# ---------------------------------------------------------------------------
# stream_complete() -- retry + fallback orchestration, streaming edition
# ---------------------------------------------------------------------------


def make_stream(tokens, model="model-a", tool_calls=None, complete_status="OpenRouter local agent"):
    def _gen():
        for token in tokens:
            yield {"type": "token", "text": token}
        yield {
            "type": "complete",
            "result": providers.ProviderResult(
                reply="".join(tokens),
                provider="openrouter",
                model=model,
                status=complete_status,
                tool_calls=tool_calls or [],
            ),
        }

    return _gen()


def make_erroring_stream(tokens, status_code=500, message="boom"):
    def _gen():
        for token in tokens:
            yield {"type": "token", "text": token}
        raise providers.ProviderRequestError(message, status_code=status_code)

    return _gen()


class StreamCompleteRetryFallbackTests(unittest.TestCase):
    def _patch_common(self, models, retries=1, max_attempts=6, backoff=0.0, max_total=42.0):
        return [
            patch.object(providers, "OPENROUTER_API_KEY", "sk-test-not-real"),
            patch.object(providers, "validated_model_chain", return_value=(models, [])),
            patch.object(providers, "PROVIDER_RETRIES", retries),
            patch.object(providers, "PROVIDER_MAX_ATTEMPTS", max_attempts),
            patch.object(providers, "PROVIDER_BACKOFF_SECONDS", backoff),
            patch.object(providers, "PROVIDER_MAX_TOTAL_SECONDS", max_total),
            patch.object(providers, "get_audit_logger", return_value=MagicMock()),
        ]

    def test_first_model_streams_tokens_then_completes(self):
        patches = self._patch_common(["model-a"])
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], \
                patch.object(providers, "call_openrouter_stream", side_effect=[make_stream(["He", "llo"])]):
            events = list(providers.stream_complete([{"role": "user", "content": "hi"}]))
        token_events = [e for e in events if e["type"] == "token"]
        complete_events = [e for e in events if e["type"] == "complete"]
        self.assertEqual([e["text"] for e in token_events], ["He", "llo"])
        self.assertEqual(len(complete_events), 1)
        self.assertEqual(complete_events[0]["result"].provider, "openrouter")

    def test_error_before_any_tokens_retries_same_model(self):
        patches = self._patch_common(["model-a"], retries=1)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], \
                patch.object(
                    providers,
                    "call_openrouter_stream",
                    side_effect=[
                        make_erroring_stream([], status_code=500),
                        make_stream(["ok"]),
                    ],
                ):
            events = list(providers.stream_complete([{"role": "user", "content": "hi"}]))
        complete_events = [e for e in events if e["type"] == "complete"]
        self.assertEqual(len(complete_events), 1)
        self.assertEqual(complete_events[0]["result"].reply, "ok")

    def test_error_after_tokens_emitted_yields_interrupted_result_without_retry(self):
        patches = self._patch_common(["model-a", "model-b"], retries=3)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], \
                patch.object(
                    providers,
                    "call_openrouter_stream",
                    side_effect=[make_erroring_stream(["partial "], status_code=500)],
                ) as mock_stream:
            events = list(providers.stream_complete([{"role": "user", "content": "hi"}]))
        token_events = [e for e in events if e["type"] == "token"]
        complete_events = [e for e in events if e["type"] == "complete"]
        self.assertEqual([e["text"] for e in token_events], ["partial "])
        self.assertEqual(len(complete_events), 1)
        self.assertEqual(complete_events[0]["result"].status, "OpenRouter stream interrupted")
        # Must not retry or fail over once content has already been streamed to the client.
        self.assertEqual(mock_stream.call_count, 1)

    def test_all_models_exhausted_falls_back_to_local_offline_stream(self):
        patches = self._patch_common(["model-a"], retries=0)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], \
                patch.object(
                    providers,
                    "call_openrouter_stream",
                    side_effect=[make_erroring_stream([], status_code=500)],
                ), \
                patch.object(providers, "GEMINI_API_KEY", ""):
            events = list(providers.stream_complete([{"role": "user", "content": "hi"}]))
        complete_events = [e for e in events if e["type"] == "complete"]
        self.assertEqual(complete_events[0]["result"].provider, "local")
        self.assertIn("model-a", complete_events[0]["result"].error)

    def test_all_models_exhausted_falls_back_to_gemini_stream(self):
        patches = self._patch_common(["model-a"], retries=0)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], \
                patch.object(
                    providers,
                    "call_openrouter_stream",
                    side_effect=[make_erroring_stream([], status_code=500)],
                ), \
                patch.object(providers, "GEMINI_API_KEY", "sk-gemini-not-real"), \
                patch.object(providers, "GEMINI_MODEL", "gemini-test-model"), \
                patch.object(providers, "call_gemini", return_value="gemini stream reply"):
            events = list(providers.stream_complete([{"role": "user", "content": "hi"}]))
        token_events = [e for e in events if e["type"] == "token"]
        complete_events = [e for e in events if e["type"] == "complete"]
        self.assertEqual(token_events[-1]["text"], "gemini stream reply")
        self.assertEqual(complete_events[0]["result"].provider, "gemini")


# ---------------------------------------------------------------------------
# Secrets must never leak into errors, results, or audit log entries
# ---------------------------------------------------------------------------


class SecretRedactionTests(unittest.TestCase):
    def test_api_key_never_appears_in_provider_request_error_message(self):
        secret = "sk-super-secret-value-should-not-leak"
        exc = urllib.error.HTTPError(
            "https://openrouter.ai/api/v1/chat/completions", 401, "unauthorized", hdrs=None, fp=None
        )
        with patch.object(providers, "OPENROUTER_API_KEY", secret), patch(
            "providers.urllib.request.urlopen", side_effect=exc
        ):
            with self.assertRaises(providers.ProviderRequestError) as ctx:
                providers.call_openrouter([{"role": "user", "content": "hi"}], "model-a", None, timeout=5)
        self.assertNotIn(secret, str(ctx.exception))

    def test_api_key_never_appears_in_complete_result_error(self):
        import tempfile
        from pathlib import Path

        secret = "sk-super-secret-value-should-not-leak"
        exc = urllib.error.HTTPError(
            "https://openrouter.ai/api/v1/chat/completions", 401, "unauthorized", hdrs=None, fp=None
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            real_logger = AuditLogger(path=Path(tmpdir) / "audit" / "varyn-audit.jsonl")
            with patch.object(providers, "OPENROUTER_API_KEY", secret), patch.object(
                providers, "validated_model_chain", return_value=(["model-a"], [])
            ), patch.object(providers, "PROVIDER_RETRIES", 0), patch.object(
                providers, "GEMINI_API_KEY", ""
            ), patch.object(providers, "get_audit_logger", return_value=real_logger), patch(
                "providers.urllib.request.urlopen", side_effect=exc
            ):
                # Runs the real call_openrouter/post_json error-message-building code,
                # rather than a hand-crafted message, so this proves the production
                # exception path never interpolates the key -- not just that this test
                # avoided doing so itself.
                result = providers.complete([{"role": "user", "content": "hi"}])

            self.assertNotIn(secret, result.error or "")
            self.assertNotIn(secret, result.reply)
            audit_raw = (Path(tmpdir) / "audit" / "varyn-audit.jsonl").read_text(encoding="utf-8")
            self.assertNotIn(secret, audit_raw)

    def test_authorization_header_value_not_logged_by_audit(self):
        import tempfile
        from pathlib import Path

        secret = "sk-another-secret-value"
        with tempfile.TemporaryDirectory() as tmpdir:
            real_logger = AuditLogger(path=Path(tmpdir) / "audit" / "varyn-audit.jsonl")
            with patch.object(providers, "OPENROUTER_API_KEY", secret), patch.object(
                providers, "validated_model_chain", return_value=(["model-a"], [])
            ), patch.object(providers, "PROVIDER_RETRIES", 0), patch.object(
                providers, "get_audit_logger", return_value=real_logger
            ), patch.object(
                providers, "call_openrouter", return_value=openrouter_payload(model="model-a")
            ):
                providers.complete([{"role": "user", "content": "hi"}])

            audit_raw = (Path(tmpdir) / "audit" / "varyn-audit.jsonl").read_text(encoding="utf-8")
            self.assertNotIn(secret, audit_raw)
            self.assertNotIn("Authorization", audit_raw)
            self.assertNotIn("Bearer", audit_raw)


if __name__ == "__main__":
    unittest.main()
