"""
Unified LLM API caller — abstracts provider differences for chat and streaming calls.

Supports: openai, anthropic, azure, openrouter, deepseek, xai, mistral, makersuite.
AWS Bedrock and Vertex AI require their own SDKs and are not covered by this caller
(use a proxy/bedrock-compatible endpoint URL if needed).
"""

import aiohttp
import asyncio
import time
import json
from typing import AsyncGenerator, Optional


class LLMCaller:
    """Unified LLM API caller supporting multiple providers."""

    def __init__(self, provider: str, base_url: str, api_key: str):
        self.provider = provider.lower()
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    async def chat(
        self,
        model: str,
        messages: list[dict],
        max_tokens: int = 256,
        temperature: float = 0.0,
        **kwargs
    ) -> dict:
        """
        Non-streaming chat call. Returns standardized response dict.

        Returns:
            {
                "content": str,
                "finish_reason": str,
                "usage": {"prompt_tokens": int, "completion_tokens": int},
                "http_status": int,
                "latency_ms": float,
                "error": str | None,
            }
        """
        start = time.monotonic()

        try:
            async with aiohttp.ClientSession() as session:
                headers, url, body = self._build_request(model, messages, max_tokens,
                                                         temperature, stream=False, **kwargs)
                async with session.post(url, headers=headers, json=body,
                                         timeout=aiohttp.ClientTimeout(total=120)) as resp:
                    latency_ms = (time.monotonic() - start) * 1000
                    raw = await resp.json()
                    return self._parse_response(resp.status, raw, latency_ms)
        except asyncio.TimeoutError:
            return self._error(0, "Request timed out", (time.monotonic() - start) * 1000)
        except Exception as e:
            return self._error(0, str(e), (time.monotonic() - start) * 1000)

    async def stream_chat(
        self,
        model: str,
        messages: list[dict],
        max_tokens: int = 256,
        temperature: float = 0.0,
        **kwargs
    ) -> dict:
        """
        Streaming chat call. Records per-chunk timestamps for latency analysis.

        Returns:
            {
                "content": str,               # concatenated output
                "finish_reason": str,
                "usage": {"prompt_tokens": int, "completion_tokens": int},
                "http_status": int,
                "ttft_ms": float,             # time to first token
                "total_latency_ms": float,    # end-to-end
                "chunks": list[dict],         # [{text, timestamp_ms, index}]
                "token_count": int,
                "error": str | None,
            }
        """
        start = time.monotonic()
        chunks = []
        content_parts = []
        first_token_time = None
        finish_reason = "stop"
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        http_status = 0

        try:
            async with aiohttp.ClientSession() as session:
                headers, url, body = self._build_request(model, messages, max_tokens,
                                                         temperature, stream=True, **kwargs)
                async with session.post(url, headers=headers, json=body,
                                         timeout=aiohttp.ClientTimeout(total=120)) as resp:
                    http_status = resp.status
                    if resp.status != 200:
                        error_body = await resp.text()
                        return self._stream_error(resp.status, error_body,
                                                  (time.monotonic() - start) * 1000)

                    async for line in resp.content:
                        line_str = line.decode('utf-8', errors='replace').strip()
                        if not line_str:
                            continue
                        if line_str.startswith("data: "):
                            line_str = line_str[6:]
                        if line_str == "[DONE]":
                            break

                        try:
                            chunk_data = json.loads(line_str)
                        except json.JSONDecodeError:
                            continue

                        now_ms = (time.monotonic() - start) * 1000
                        text, fr, u = self._parse_stream_chunk(chunk_data)

                        if text:
                            if first_token_time is None:
                                first_token_time = now_ms
                            content_parts.append(text)
                            chunks.append({
                                "text": text,
                                "timestamp_ms": now_ms,
                                "index": len(chunks)
                            })
                        if fr:
                            finish_reason = fr
                        if u:
                            usage = u

        except asyncio.TimeoutError:
            return self._stream_error(0, "Stream timed out",
                                      (time.monotonic() - start) * 1000)
        except Exception as e:
            return self._stream_error(0, str(e),
                                      (time.monotonic() - start) * 1000)

        total_ms = (time.monotonic() - start) * 1000
        content = "".join(content_parts)
        token_count = usage.get("completion_tokens") or len(content_parts)

        return {
            "content": content,
            "finish_reason": finish_reason,
            "usage": usage,
            "http_status": http_status,
            "ttft_ms": first_token_time if first_token_time else total_ms,
            "total_latency_ms": total_ms,
            "chunks": chunks,
            "token_count": token_count,
            "error": None,
        }

    # -----------------------------------------------------------------------
    # Provider-specific request building
    # -----------------------------------------------------------------------

    def _build_request(self, model: str, messages: list[dict],
                       max_tokens: int, temperature: float,
                       stream: bool = False, **kwargs) -> tuple:
        """Build (headers, url, body) for the provider. Returns tuple."""

        p = self.provider

        if p in ("openai", "openrouter", "deepseek", "xai", "mistral"):
            # OpenAI-compatible API
            url = f"{self.base_url}/chat/completions"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            }
            body = {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": stream,
            }
            if p == "mistral" and stream:
                # Mistral streaming works the same way
                pass
            return headers, url, body

        elif p == "anthropic":
            url = f"{self.base_url}/messages"
            headers = {
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            }
            body = {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": stream,
            }
            return headers, url, body

        elif p == "azure":
            api_version = kwargs.get("api_version", "2024-02-01")
            url = (f"{self.base_url}/openai/deployments/{model}/chat/completions"
                   f"?api-version={api_version}")
            headers = {
                "Content-Type": "application/json",
                "api-key": self.api_key,
            }
            body = {
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": stream,
            }
            return headers, url, body

        elif p == "makersuite":
            url = f"{self.base_url}/models/{model}:generateContent?key={self.api_key}"
            headers = {"Content-Type": "application/json"}
            # Convert messages to Gemini format
            contents = []
            for msg in messages:
                role = "user" if msg["role"] == "user" else "model"
                contents.append({"role": role, "parts": [{"text": msg["content"]}]})
            body = {
                "contents": contents,
                "generationConfig": {
                    "maxOutputTokens": max_tokens,
                    "temperature": temperature,
                },
            }
            if stream:
                url = url.replace(":generateContent", ":streamGenerateContent")
            return headers, url, body

        else:
            raise ValueError(f"Unsupported provider: {p}")

    # -----------------------------------------------------------------------
    # Provider-specific response parsing (non-streaming)
    # -----------------------------------------------------------------------

    def _parse_response(self, status: int, raw: dict, latency_ms: float) -> dict:
        p = self.provider

        try:
            if p in ("openai", "openrouter", "deepseek", "xai", "mistral", "azure"):
                choice = raw.get("choices", [{}])[0]
                return {
                    "content": choice.get("message", {}).get("content", ""),
                    "finish_reason": choice.get("finish_reason", "stop"),
                    "usage": raw.get("usage", {"prompt_tokens": 0, "completion_tokens": 0}),
                    "http_status": status,
                    "latency_ms": latency_ms,
                    "error": None,
                }
            elif p == "anthropic":
                content_parts = []
                for block in raw.get("content", []):
                    if block.get("type") == "text":
                        content_parts.append(block.get("text", ""))
                return {
                    "content": "".join(content_parts),
                    "finish_reason": raw.get("stop_reason", "stop"),
                    "usage": {
                        "prompt_tokens": raw.get("usage", {}).get("input_tokens", 0),
                        "completion_tokens": raw.get("usage", {}).get("output_tokens", 0),
                    },
                    "http_status": status,
                    "latency_ms": latency_ms,
                    "error": None,
                }
            elif p == "makersuite":
                candidate = raw.get("candidates", [{}])[0]
                parts = candidate.get("content", {}).get("parts", [])
                text = "".join(part.get("text", "") for part in parts)
                usage_meta = raw.get("usageMetadata", {})
                return {
                    "content": text,
                    "finish_reason": candidate.get("finishReason", "STOP"),
                    "usage": {
                        "prompt_tokens": usage_meta.get("promptTokenCount", 0),
                        "completion_tokens": usage_meta.get("candidatesTokenCount", 0),
                    },
                    "http_status": status,
                    "latency_ms": latency_ms,
                    "error": None,
                }
        except Exception as e:
            return {
                "content": "",
                "finish_reason": "error",
                "usage": {"prompt_tokens": 0, "completion_tokens": 0},
                "http_status": status,
                "latency_ms": latency_ms,
                "error": f"Parse error: {e}",
            }

    # -----------------------------------------------------------------------
    # Provider-specific stream chunk parsing
    # -----------------------------------------------------------------------

    def _parse_stream_chunk(self, data: dict) -> tuple:
        """Parse a streaming chunk. Returns (text, finish_reason, usage)."""
        p = self.provider

        if p in ("openai", "openrouter", "deepseek", "xai", "mistral", "azure"):
            choice = data.get("choices", [{}])[0]
            delta = choice.get("delta", {})
            text = delta.get("content", "")
            fr = choice.get("finish_reason")
            usage = data.get("usage")
            return text, fr, usage

        elif p == "anthropic":
            event_type = data.get("type", "")
            if event_type == "content_block_delta":
                delta = data.get("delta", {})
                text = delta.get("text", "") if delta.get("type") == "text_delta" else ""
                return text, None, None
            elif event_type == "message_stop":
                return "", data.get("stop_reason", "stop"), None
            elif event_type == "message_delta":
                usage = data.get("usage")
                if usage:
                    return "", None, {
                        "prompt_tokens": usage.get("input_tokens", 0),
                        "completion_tokens": usage.get("output_tokens", 0),
                    }
                return "", None, None
            return "", None, None

        elif p == "makersuite":
            candidate = data.get("candidates", [{}])[0]
            parts = candidate.get("content", {}).get("parts", [])
            text = "".join(part.get("text", "") for part in parts)
            fr = candidate.get("finishReason")
            usage = data.get("usageMetadata")
            return text, fr, usage

        return "", None, None

    # -----------------------------------------------------------------------
    # Error helpers
    # -----------------------------------------------------------------------

    def _error(self, status: int, message: str, latency_ms: float) -> dict:
        return {
            "content": "",
            "finish_reason": "error",
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            "http_status": status,
            "latency_ms": latency_ms,
            "error": message,
        }

    def _stream_error(self, status: int, message: str, total_ms: float) -> dict:
        return {
            "content": "",
            "finish_reason": "error",
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            "http_status": status,
            "ttft_ms": total_ms,
            "total_latency_ms": total_ms,
            "chunks": [],
            "token_count": 0,
            "error": message,
        }
