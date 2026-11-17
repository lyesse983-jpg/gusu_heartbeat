"""Small, stateless HTTP adapters for tool-capable vision models.

The engine uses OpenAI-style message history. Photo tools may return a JSON
object containing ``_image: {mime, data}``; adapters send those bytes as an
actual model image input, rather than expecting the model to read a local path.
No provider error body, URL, credentials, or request headers enter exceptions.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx


class ProviderError(RuntimeError):
    """A deliberately sanitized error safe to show in activity logs."""


def _json_object(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            raise ProviderError("模型返回了无法解析的工具参数。") from None
        if isinstance(parsed, dict):
            return parsed
    raise ProviderError("模型返回的工具参数必须是 JSON 对象。")


def _tool_content(value: Any) -> tuple[str, dict | None]:
    """Remove image bytes from textual metadata and return them separately."""
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return value, None
    else:
        parsed = value
    if not isinstance(parsed, dict) or "_image" not in parsed:
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), None
    metadata = dict(parsed)
    image = metadata.pop("_image")
    if not isinstance(image, dict) or not isinstance(image.get("data"), str):
        raise ProviderError("照片工具返回的图片数据无效。")
    if image.get("mime") not in {"image/jpeg", "image/png", "image/gif", "image/webp"}:
        raise ProviderError("照片工具返回了不支持的图片格式。")
    return json.dumps(metadata, ensure_ascii=False), image


def _endpoint(kind: str, base_url: str | None) -> str:
    base = (base_url or ("https://api.anthropic.com" if kind == "anthropic"
                         else "https://api.openai.com/v1")).rstrip("/")
    try:
        parts = urlsplit(base)
        if (parts.scheme not in {"http", "https"} or not parts.netloc or
                parts.username or parts.password or parts.query or parts.fragment):
            raise ValueError
        path = parts.path.rstrip("/")
        suffix = "/messages" if kind == "anthropic" else "/chat/completions"
        if path.endswith(suffix):
            return base
        # A non-root OpenAI-compatible base URL is an exact API prefix, like
        # the official SDK's base_url. Anthropic prefixes conventionally keep v1.
        if not path or (kind == "anthropic" and not path.endswith("/v1")):
            path += "/v1"
        return urlunsplit((parts.scheme, parts.netloc, path + suffix, "", ""))
    except (ValueError, TypeError):
        raise ProviderError("模型 API 地址无效，请使用不含凭据的 HTTP(S) 地址。") from None


def _anthropic_history(messages: list[dict]) -> list[dict]:
    converted: list[dict] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        blocks: list[dict] = []
        if role == "tool":
            text, image = _tool_content(content)
            result: list[dict] = [{"type": "text", "text": text}]
            if image:
                result.append({"type": "image", "source": {
                    "type": "base64", "media_type": image["mime"], "data": image["data"]}})
            blocks.append({"type": "tool_result", "tool_use_id": message["tool_call_id"],
                           "content": result})
            role = "user"
        elif role == "assistant":
            if content:
                blocks.append({"type": "text", "text": str(content)})
            for call in message.get("tool_calls", []):
                function = call["function"]
                blocks.append({"type": "tool_use", "id": call["id"],
                               "name": function["name"],
                               "input": _json_object(function.get("arguments", {}))})
        elif role == "user":
            if content:
                blocks.append({"type": "text", "text": str(content)})
        else:
            raise ProviderError("会话记录包含不支持的消息类型。")
        if not blocks:
            continue
        # Parallel tool calls produce several consecutive tool messages. Claude
        # requires their tool_result blocks in one immediately following message.
        if converted and converted[-1]["role"] == role:
            converted[-1]["content"].extend(blocks)
        else:
            converted.append({"role": role, "content": blocks})
    return converted


def _openai_history(system: str, messages: list[dict]) -> list[dict]:
    converted = [{"role": "system", "content": system}]
    pending_images: list[dict] = []

    def flush_images() -> None:
        if pending_images:
            converted.append({"role": "user", "content": [
                {"type": "text", "text": "以下图片由刚才的照片工具实际读取；请结合对应工具结果查看。"},
                *pending_images,
            ]})
            pending_images.clear()

    for message in messages:
        if message.get("role") == "tool":
            text, image = _tool_content(message.get("content"))
            converted.append({"role": "tool", "tool_call_id": message["tool_call_id"],
                              "content": text})
            if image:
                pending_images.append({"type": "image_url", "image_url": {
                    "url": f"data:{image['mime']};base64,{image['data']}"}})
        else:
            # Wait until every tool_call has a tool result before inserting a
            # user image message. Inserting between results breaks API ordering.
            flush_images()
            if message.get("role") not in {"user", "assistant"}:
                raise ProviderError("会话记录包含不支持的消息类型。")
            converted.append({key: value for key, value in message.items()
                              if key in {"role", "content", "tool_calls", "name"}})
    flush_images()
    return converted


class Provider:
    """HTTP provider. API keys stay in memory and are never returned or logged."""

    def __init__(self, kind: str, api_key: str, model: str,
                 base_url: str | None = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        if kind not in {"anthropic", "openai"}:
            raise ProviderError("模型接口类型必须是 anthropic 或 openai。")
        if not isinstance(api_key, str) or not api_key.strip():
            raise ProviderError("尚未配置模型 API 密钥。")
        if not isinstance(model, str) or not model.strip():
            raise ProviderError("尚未配置模型名称。")
        self.kind = kind
        self._api_key = api_key.strip()
        self.model = model.strip()
        self.url = _endpoint(kind, base_url)
        self.transport = transport

    async def complete(self, system: str, messages: list[dict], tools: list[dict]) -> dict:
        if self.kind == "anthropic":
            payload = {"model": self.model, "max_tokens": 2048, "system": system,
                       "messages": _anthropic_history(messages)}
            if tools:
                payload["tools"] = tools
            headers = {"x-api-key": self._api_key, "anthropic-version": "2023-06-01"}
        else:
            payload = {"model": self.model, "messages": _openai_history(system, messages)}
            token_field = ("max_completion_tokens" if self.model.startswith(
                ("gpt-5", "gpt-6", "o1", "o3", "o4")) else "max_tokens")
            payload[token_field] = 2048
            if tools:
                payload["tools"] = [{"type": "function", "function": {
                    "name": tool["name"], "description": tool.get("description", ""),
                    "parameters": tool["input_schema"]}} for tool in tools]
            headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            async with httpx.AsyncClient(timeout=60.0, transport=self.transport,
                                         follow_redirects=False) as client:
                response = await client.post(self.url, json=payload, headers=headers)
            if not response.is_success:
                raise ProviderError(f"模型服务请求失败（HTTP {response.status_code}）。")
            data = response.json()
        except ProviderError:
            raise
        except httpx.TimeoutException:
            raise ProviderError("模型服务请求超时，请稍后重试。") from None
        except (httpx.HTTPError, httpx.InvalidURL):
            raise ProviderError("无法连接模型服务，请检查网络与 API 地址。") from None
        except (ValueError, TypeError):
            raise ProviderError("模型服务返回了无效的 JSON 响应。") from None
        try:
            if self.kind == "anthropic":
                blocks = data["content"]
                if not isinstance(blocks, list):
                    raise TypeError
                text = "\n".join(block["text"] for block in blocks if block.get("type") == "text")
                calls = [{"id": block["id"], "name": block["name"],
                          "arguments": _json_object(block["input"])}
                         for block in blocks if block.get("type") == "tool_use"]
            else:
                message = data["choices"][0]["message"]
                text = message.get("content") or ""
                if not isinstance(text, str):
                    raise TypeError
                calls = [{"id": call["id"], "name": call["function"]["name"],
                          "arguments": _json_object(call["function"].get("arguments", "{}"))}
                         for call in message.get("tool_calls", [])]
            if any(not isinstance(call["id"], str) or not isinstance(call["name"], str)
                   for call in calls):
                raise TypeError
            return {"text": text, "calls": calls, "raw": data}
        except ProviderError:
            raise
        except (KeyError, IndexError, TypeError, AttributeError):
            raise ProviderError("模型服务返回了不完整的消息或工具调用。") from None
