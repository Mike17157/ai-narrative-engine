"""OpenAI-compatible text provider — drives OpenRouter (and OpenAI, and any
OpenAI-compatible endpoint) via /chat/completions.

OpenRouter is the default target: one key, hundreds of models. The API key and
model usually come from a saved Connection (see loom/connections.py), passed in
through `options`.

Model `options`:
    base_url:   default "https://openrouter.ai/api/v1"
    api_key:    bearer key (falls back to OPENROUTER_API_KEY / OPENAI_API_KEY)
    model:      e.g. "anthropic/claude-3.5-sonnet", "openai/gpt-4o-mini"
    max_tokens: int (default 40000)

Sampling controls (any subset; omitted → the model/provider default): temperature,
top_p, top_k, frequency_penalty, presence_penalty, repetition_penalty, min_p. These
come from a config's `params` and are passed straight through to /chat/completions
(OpenRouter forwards model-specific ones like top_k/min_p where supported).
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Callable

# Parses OpenRouter's context-overflow 400 so we can shrink max_tokens to fit and retry. We need the
# TOTAL requested (text + tool-schema + output), not just text — the tool-calling path adds ~1k of
# tool input the text path doesn't. "maximum context length is 32768 tokens. However, you requested
# about 33474 tokens (169 of text input, 970 of tool input, 32335 in the output)."
_CTX_RE = re.compile(r"maximum context length is (\d+) tokens.*?requested about (\d+) tokens", re.I | re.S)
# Learned safe output cap per model, so a generous ceiling (40k) doesn't re-fail on every call to a
# smaller-context model (e.g. qwen-2.5's 32k). Populated the first time a model rejects an oversized cap.
_CTX_CAP: dict[str, int] = {}

import httpx

from .base import TextResult


class OpenAICompatProvider:
    def __init__(self, options: dict[str, Any]):
        self.base_url: str = options.get("base_url", "https://openrouter.ai/api/v1").rstrip("/")
        self.api_key: str = (
            options.get("api_key")
            or os.environ.get("OPENROUTER_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or ""
        )
        self.model: str = options.get("model", "openai/gpt-4o-mini")
        # Default ceiling on OUTPUT tokens. It's just a cap (the model stops when done), so a
        # generous default avoids silently truncating large structured outputs into invalid JSON.
        # ponytail: 40k is a ceiling, not a reservation; if a provider rejects a too-large cap for a
        # given model, set a smaller per-preset `params.max_tokens`.
        self.max_tokens: int = int(options.get("max_tokens", 40000))
        # Optional sampling controls — only those explicitly set are forwarded straight to
        # /chat/completions (OpenRouter forwards model-specific ones where supported).
        self.sampling: dict[str, Any] = {}
        for k in ("temperature", "top_p", "top_k", "top_a", "min_p",
                  "frequency_penalty", "presence_penalty", "repetition_penalty", "seed"):
            v = options.get(k)
            if v is not None and v != "":
                self.sampling[k] = v
        # Stop sequences (string or list) and reasoning effort (reasoning models).
        stop = options.get("stop")
        if isinstance(stop, str) and stop.strip():
            stop = [stop.strip()]
        if isinstance(stop, (list, tuple)):
            stop = [str(s) for s in stop if str(s).strip()]
            if stop:
                self.sampling["stop"] = stop
        effort = options.get("reasoning_effort")
        if effort in ("low", "medium", "high"):
            self.sampling["reasoning"] = {"effort": effort}
        elif effort in ("none", "minimal"):
            # OpenAI-style top-level field; Ollama uses this to DISABLE the thinking channel
            # on reasoning models (without it a thinking model returns empty `content` over /v1).
            self.sampling["reasoning_effort"] = effort

    def _err(self, resp) -> str:
        """Surface the provider's real error body, not a bare 'HTTP 404'."""
        try:
            j = resp.json()
            err = j.get("error")
            msg = err.get("message") if isinstance(err, dict) else (err or j.get("message"))
            msg = msg or resp.text
        except Exception:  # noqa: BLE001
            msg = resp.text or ""
        return f"{self.model} → HTTP {resp.status_code}: {str(msg).strip()[:300]}"

    def _headers(self) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        if "openrouter" in self.base_url:
            # OpenRouter likes these for attribution; harmless elsewhere.
            h["HTTP-Referer"] = "http://localhost"
            h["X-Title"] = "Loom"
        return h

    def generate_text(self, **kwargs) -> TextResult:
        """max_tokens is a desired CEILING (default 40k), not a reservation. Two realities a generous
        ceiling must survive on OpenRouter: (1) a model's context may be SMALLER than the ceiling and
        OpenRouter REJECTS (doesn't clamp) oversized requests — we learn the fit per model (`_CTX_CAP`)
        so it only fails once, ever; (2) busy/free models RATE-LIMIT (429) — we back off and retry."""
        import time
        cap = _CTX_CAP.get(self.model)
        if cap and cap < self.max_tokens:
            self.max_tokens = cap   # use the previously-learned fit; no wasted failed request
        last: Exception | None = None
        for attempt in range(6):
            try:
                return self._generate_once(**kwargs)
            except RuntimeError as exc:
                last = exc
                s = str(exc)
                m = _CTX_RE.search(s)
                if m:                                   # context overflow → shrink to fit, remember, retry
                    ctx_max, requested = int(m.group(1)), int(m.group(2))
                    overflow = requested - ctx_max      # how far over we were (covers text + tool input)
                    safe = max(256, self.max_tokens - overflow - 256) if overflow > 0 else self.max_tokens
                    if safe < self.max_tokens:
                        _CTX_CAP[self.model] = min(_CTX_CAP.get(self.model, safe), safe)
                        self.max_tokens = safe
                        continue
                    raise
                if "429" in s or "rate" in s.lower() or "temporarily" in s.lower() or "overloaded" in s.lower():
                    time.sleep(min(30, 3 * 2 ** attempt))   # transient throttle → exponential back off
                    continue
                raise                                   # anything else is a real error
        raise last  # exhausted retries

    def _generate_once(
        self,
        *,
        system: str | None,
        prompt: str,
        emits: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        on_delta: Callable[[str], None] | None = None,
        images: list[str] | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> TextResult:
        if not self.api_key:
            raise RuntimeError("no API key for this connection — connect first in the Connection tab")

        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        if images:
            # Multimodal: OpenRouter/OpenAI take content as text + image_url parts.
            # Each image is a data URI (data:image/png;base64,...).
            content: list[dict] = [{"type": "text", "text": prompt}]
            for uri in images:
                content.append({"type": "image_url", "image_url": {"url": uri}})
            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": "user", "content": prompt})
        body: dict[str, Any] = {"model": self.model, "messages": messages,
                                "max_tokens": self.max_tokens, **self.sampling}
        url = f"{self.base_url}/chat/completions"

        # Native tool-use path — OpenAI-style `tools` + `tool_calls`. Non-streaming
        # (tool calls aren't text deltas); returns the calls for the caller to apply.
        if tools:
            body["tools"] = [{"type": "function", "function": {
                "name": t["name"], "description": t.get("description", ""),
                "parameters": t["parameters"]}} for t in tools]
            resp = httpx.post(url, json=body, headers=self._headers(), timeout=120)
            if resp.status_code >= 400:
                raise RuntimeError(self._err(resp))
            msg = resp.json()["choices"][0]["message"]
            calls: list[dict] = []
            for tc in (msg.get("tool_calls") or []):
                fn = tc.get("function") or {}
                args = fn.get("arguments")
                if isinstance(args, str):
                    try: args = json.loads(args) if args.strip() else {}
                    except (ValueError, TypeError): args = {}
                calls.append({"fn": fn.get("name"), "params": args if isinstance(args, dict) else {}})
            return TextResult(text=msg.get("content") or "", tool_calls=calls)

        # Structured path. Prefer LABELED PROSE for non-streaming calls: reasoning models flake on strict
        # json_schema (the grammar fights the reasoning -> empty content) but write labeled text reliably.
        # Falls back to json_schema when the schema is too complex to round-trip, or when the labeled parse
        # comes back empty. Streaming structured keeps json_schema (below).
        if emits is not None:
            if on_delta is None:
                from .labeled_structured import parse_schema_labeled, schema_to_labeled
                conv = schema_to_labeled(emits)
                if conv is not None:
                    instr, plan = conv
                    lmsgs = list(messages)
                    if lmsgs and lmsgs[0]["role"] == "system" and isinstance(lmsgs[0]["content"], str):
                        lmsgs[0] = {"role": "system", "content": lmsgs[0]["content"] + "\n\n" + instr}
                    else:
                        lmsgs = [{"role": "system", "content": instr}] + lmsgs
                    lbody = {"model": self.model, "messages": lmsgs,
                             "max_tokens": self.max_tokens, **self.sampling}
                    lresp = httpx.post(url, json=lbody, headers=self._headers(), timeout=120)
                    if lresp.status_code < 400:
                        ltext = lresp.json()["choices"][0]["message"].get("content") or ""
                        data = parse_schema_labeled(ltext, plan)
                        if any(v not in ("", [], 0, 0.0, False, None) for v in data.values()):
                            return TextResult(text=data.get("reply") or data.get("text") or ltext, data=data)
                    # else: fall through to json_schema

            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "loom_out", "strict": True, "schema": emits},
            }
            # Non-streaming json_schema fallback — one blocking request.
            if on_delta is None:
                resp = httpx.post(url, json=body, headers=self._headers(), timeout=120)
                if resp.status_code >= 400:
                    raise RuntimeError(self._err(resp))
                content = resp.json()["choices"][0]["message"]["content"]
                data = json.loads(content) if (content and content.strip()) else {}
                return TextResult(text=data.get("reply") or data.get("text") or "", data=data)

            # Streaming structured: stream the JSON tokens (so a UI can watch it form), accumulate,
            # then parse the whole thing. The schema still constrains the final object.
            body["stream"] = True
            chunks: list[str] = []
            with httpx.stream("POST", url, json=body, headers=self._headers(), timeout=120) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    raise RuntimeError(self._err(resp))
                for line in resp.iter_lines():
                    if cancel and cancel():
                        break
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[len("data:"):].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        obj = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    delta = (obj.get("choices") or [{}])[0].get("delta", {}).get("content")
                    if delta:
                        chunks.append(delta)
                        on_delta(delta)
            content = "".join(chunks)
            data = None
            if content and content.strip():
                try:
                    data = json.loads(content)
                except json.JSONDecodeError:
                    data = None
            if data is None:
                # Streamed content was EMPTY or malformed. Empty happens with reasoning models that
                # emit their thinking on a separate channel and stream NO `content` deltas — the live
                # text never arrives, but the final message.content still holds the JSON. Fall back to
                # one clean blocking call. ponytail: this re-runs the model (2x cost) only on the empty
                # path; when content DOES stream we keep the live tokens and never hit this.
                body.pop("stream", None)
                resp = httpx.post(url, json=body, headers=self._headers(), timeout=120)
                if resp.status_code >= 400:
                    raise RuntimeError(self._err(resp))
                content = resp.json()["choices"][0]["message"]["content"]
                data = json.loads(content) if (content and content.strip()) else {}
            return TextResult(text=data.get("reply") or data.get("text") or "", data=data)

        # Streaming path.
        body["stream"] = True
        chunks: list[str] = []
        with httpx.stream("POST", url, json=body, headers=self._headers(), timeout=120) as resp:
            if resp.status_code >= 400:
                resp.read()
                raise RuntimeError(self._err(resp))
            for line in resp.iter_lines():
                if cancel and cancel():
                    break  # caller asked to stop — closing the stream halts upstream
                if not line or not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if payload == "[DONE]":
                    break
                try:
                    obj = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                delta = (obj.get("choices") or [{}])[0].get("delta", {}).get("content")
                if delta:
                    chunks.append(delta)
                    if on_delta:
                        on_delta(delta)
        return TextResult(text="".join(chunks))
