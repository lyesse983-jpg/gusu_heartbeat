import json
import unittest

import httpx

from companion.provider import Provider, ProviderError


TOOLS = [{"name": "read_photo", "description": "Read an uploaded photo.", "input_schema": {
    "type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}}]


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_anthropic_tools_and_actual_image_input(self):
        requests = []

        def handler(request):
            payload = json.loads(request.content)
            requests.append(payload)
            self.assertEqual(str(request.url), "https://example.test/proxy/v1/messages")
            self.assertEqual(request.headers["x-api-key"], "secret-test-key")
            self.assertEqual(request.headers["anthropic-version"], "2023-06-01")
            self.assertEqual(payload["tools"], TOOLS)
            return httpx.Response(200, json={"content": [
                {"type": "text", "text": "看过这张照片了。"},
                {"type": "tool_use", "id": "next", "name": "save_note",
                 "input": {"text": "Actual observation"}}]})

        model = Provider("anthropic", "secret-test-key", "test-model",
                         "https://example.test/proxy/v1", httpx.MockTransport(handler))
        result = await model.complete("System", [
            {"role": "user", "content": "看照片"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "photo1", "type": "function", "function": {
                    "name": "read_photo", "arguments": '{"id":1}'}},
                {"id": "photo2", "type": "function", "function": {
                    "name": "read_photo", "arguments": '{"id":2}'}}]},
            {"role": "tool", "tool_call_id": "photo1", "content": json.dumps({
                "id": 1, "title": "Real uploaded photo", "_image": {
                    "mime": "image/jpeg", "data": "YWJj"}})},
            {"role": "tool", "tool_call_id": "photo2", "content": '{"id":2}'},
        ], TOOLS)
        blocks = requests[0]["messages"][-1]["content"]
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0]["tool_use_id"], "photo1")
        self.assertEqual(blocks[0]["content"][1], {"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg", "data": "YWJj"}})
        self.assertNotIn("_image", blocks[0]["content"][0]["text"])
        self.assertEqual(result["calls"][0]["arguments"], {"text": "Actual observation"})
        self.assertEqual(result["text"], "看过这张照片了。")

    async def test_openai_image_inserted_after_all_tool_results(self):
        captured = []

        def handler(request):
            payload = json.loads(request.content)
            captured.append(payload)
            self.assertEqual(str(request.url), "https://example.test/compatible/chat/completions")
            self.assertEqual(request.headers["authorization"], "Bearer test-key")
            self.assertEqual(payload["tools"][0]["function"]["parameters"], TOOLS[0]["input_schema"])
            return httpx.Response(200, json={"choices": [{"message": {
                "content": None, "tool_calls": [{"id": "next", "type": "function",
                    "function": {"name": "save_note", "arguments": '{"text":"ok"}'}}]}}]})

        provider = Provider("openai", "test-key", "gpt-5-test",
                            "https://example.test/compatible", httpx.MockTransport(handler))
        result = await provider.complete("System", [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "a", "type": "function", "function": {
                    "name": "read_photo", "arguments": '{"id":1}'}},
                {"id": "b", "type": "function", "function": {
                    "name": "read_photo", "arguments": '{"id":2}'}}]},
            {"role": "tool", "tool_call_id": "a", "content": json.dumps({
                "id": 1, "_image": {"mime": "image/png", "data": "YWJj"}})},
            {"role": "tool", "tool_call_id": "b", "content": '{"id":2}'},
        ], TOOLS)
        messages = captured[0]["messages"]
        self.assertEqual([m["role"] for m in messages], ["system", "assistant", "tool", "tool", "user"])
        self.assertEqual(messages[-1]["content"][1]["image_url"]["url"], "data:image/png;base64,YWJj")
        self.assertNotIn("max_tokens", captured[0])
        self.assertEqual(captured[0]["max_completion_tokens"], 2048)
        self.assertEqual(result["calls"], [{"id": "next", "name": "save_note", "arguments": {"text": "ok"}}])

    async def test_default_endpoints_and_plain_text(self):
        for kind, expected in [("anthropic", "https://api.anthropic.com/v1/messages"),
                               ("openai", "https://api.openai.com/v1/chat/completions")]:
            with self.subTest(kind=kind):
                def handler(request):
                    self.assertEqual(str(request.url), expected)
                    data = ({"content": [{"type": "text", "text": "hello"}]} if kind == "anthropic"
                            else {"choices": [{"message": {"content": "hello"}}]})
                    return httpx.Response(200, json=data)
                p = Provider(kind, "key", "model", transport=httpx.MockTransport(handler))
                self.assertEqual((await p.complete("sys", [{"role": "user", "content": "hi"}], []))["text"], "hello")

    async def test_errors_do_not_leak_response_or_key(self):
        for status in [401, 429, 500, 302]:
            def handler(request):
                return httpx.Response(status, text="server echoed secret-test-key / private content")
            p = Provider("openai", "secret-test-key", "test-model", transport=httpx.MockTransport(handler))
            with self.assertRaises(ProviderError) as caught:
                await p.complete("sys", [{"role": "user", "content": "hi"}], [])
            self.assertIn(str(status), str(caught.exception))
            self.assertNotIn("secret-test-key", str(caught.exception))
            self.assertNotIn("private content", str(caught.exception))

    async def test_malformed_tool_arguments_are_rejected(self):
        for args in ["not-json", "[]", "null"]:
            def handler(request):
                return httpx.Response(200, json={"choices": [{"message": {
                    "content": "", "tool_calls": [{"id": "x", "function": {
                        "name": "read_photo", "arguments": args}}]}}]})
            p = Provider("openai", "key", "model", transport=httpx.MockTransport(handler))
            with self.assertRaises(ProviderError):
                await p.complete("sys", [{"role": "user", "content": "hi"}], TOOLS)

    async def test_transport_errors_are_sanitized(self):
        for exception in [httpx.ConnectError("secret-test-key"), httpx.ReadTimeout("private content")]:
            def handler(request):
                raise exception
            p = Provider("openai", "key", "model", transport=httpx.MockTransport(handler))
            with self.assertRaises(ProviderError) as caught:
                await p.complete("sys", [{"role": "user", "content": "hi"}], [])
            self.assertNotIn("secret-test-key", str(caught.exception))
            self.assertNotIn("private content", str(caught.exception))

    async def test_invalid_json_and_missing_response_fields(self):
        for response in [httpx.Response(200, text="not-json"), httpx.Response(200, json={}),
                         httpx.Response(200, json={"choices": []})]:
            p = Provider("openai", "key", "model", transport=httpx.MockTransport(lambda req: response))
            with self.assertRaises(ProviderError):
                await p.complete("sys", [{"role": "user", "content": "hi"}], [])

    def test_missing_config_and_unsafe_urls(self):
        for args in [("bad", "key", "model"), ("openai", "", "model"), ("anthropic", "key", "")]:
            with self.assertRaises(ProviderError):
                Provider(*args)
        for url in ["file:///etc/passwd", "https://secret@host.test", "https://host.test?key=secret"]:
            with self.assertRaises(ProviderError) as caught:
                Provider("openai", "key", "model", base_url=url)
            self.assertNotIn("secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
