"""AI providers (Gemini, OpenAI) over plain httpx, 15 s timeout.

Rules this module lives by:
* the API key only ever travels in a request header (never in the URL, so httpx's INFO request log can't leak it);
* nothing here logs a key, a request body or a provider response body (OpenAI error text can contain a masked key);
* provider failures become AIProviderError(code) with a code from errors.py; callers turn that into an AppError.

Endpoints / shapes were checked against the providers' official docs on 2026-09-29. Re-check them when models change:
  Gemini : POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent, header x-goog-api-key
  OpenAI : POST https://api.openai.com/v1/responses, header Authorization: Bearer <key>

Tool calling (voice assistant), checked against the same docs on 2026-09-29:
  Gemini : tools=[{"functionDeclarations":[{name, description, parameters}]}]; the reply has parts[].functionCall {id, name, args};
           send the model's content back unchanged (it carries thought signatures) followed by a role "user" content whose
           parts[].functionResponse {id, name, response} answer each call. toolConfig.functionCallingConfig.mode NONE = no more calls.
  OpenAI : tools=[{"type":"function", name, description, parameters, strict}] (flat, Responses shape); output[] items of type
           "function_call" {call_id, name, arguments (JSON string)}; send every output item back in `input` (reasoning items must
           travel with them) plus {"type":"function_call_output","call_id",output (string)}. tool_choice "none" = no more calls.
"""
import json
import logging
from dataclasses import dataclass, field
import httpx
from .errors import AppError

log = logging.getLogger("nira")

TIMEOUT = 15.0   # seconds, applied to connect / read / write separately by httpx

# Defaults (the user can type any other model name in Settings). Names churn: see README "Model names".
DEFAULT_MODELS = {"gemini": "gemini-3.5-flash-lite", "openai": "gpt-6-luna"}
PROVIDERS = tuple(DEFAULT_MODELS)
PING_PROMPT = "Reply with the single word: ok"


class AIProviderError(Exception):
    """`code` is a key of errors.MESSAGES (invalid_key, quota_exceeded, network_error, timeout, ...)."""
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _http_post(url: str, *, headers: dict, json: dict) -> httpx.Response:
    """The single place that touches the network (tests replace this)."""
    return httpx.post(url, headers=headers, json=json, timeout=TIMEOUT)


@dataclass(frozen=True)
class ToolCall:
    id: str            # Gemini: functionCall.id ("" if absent); OpenAI: call_id
    name: str
    args: dict         # {} when the model sent nothing; {"__invalid__": True} when OpenAI's argument string was not a JSON object


@dataclass
class ChatStep:
    """One model answer: visible text and/or tool calls. `raw` is the provider's own form of the answer,
    sent back unchanged on the next request (thought signatures / reasoning items must travel with it)."""
    text: str = ""
    calls: list = field(default_factory=list)
    raw: object = None


# The transcript handed to chat_step() is a provider-neutral list of dicts:
#   {"role": "user" | "assistant", "text": str}          plain turns
#   {"role": "assistant_step", "step": ChatStep}          a model answer that contained tool calls
#   {"role": "tool_results", "results": [{"call": ToolCall, "output": dict}, ...]}


class BaseProvider:
    name = ""

    def __init__(self, api_key: str, model: str):
        self._api_key = api_key
        self.model = model

    def __repr__(self) -> str:                      # never include the key
        return f"<{type(self).__name__} model={self.model!r}>"

    # --- provider-specific parts ---
    def _build(self, prompt: str, max_tokens: int) -> tuple[str, dict, dict]:
        raise NotImplementedError

    def _text(self, data: dict) -> str:
        raise NotImplementedError

    def _is_bad_key_400(self, response: httpx.Response) -> bool:
        return False

    # --- shared ---
    def _code_for(self, response: httpx.Response) -> str:
        sc = response.status_code
        if sc in (401, 403):
            return "invalid_key"
        if sc in (402, 429):
            return "quota_exceeded"
        if sc == 404:
            return "model_not_found"
        if sc == 400 and self._is_bad_key_400(response):
            return "invalid_key"
        return "provider_error"

    def _post(self, url: str, headers: dict, body: dict) -> dict:
        """The one request/response path (timeouts, status codes -> AIProviderError). Never logs a body."""
        try:
            r = _http_post(url, headers=headers, json=body)
        except httpx.TimeoutException:
            log.warning("AI provider %s timed out", self.name)
            raise AIProviderError("timeout")
        except httpx.HTTPError as e:
            log.warning("AI provider %s network error (%s)", self.name, type(e).__name__)
            raise AIProviderError("network_error")
        if not 200 <= r.status_code < 300:
            log.warning("AI provider %s answered HTTP %s", self.name, r.status_code)   # status only, never the body
            raise AIProviderError(self._code_for(r))
        try:
            data = r.json()
        except ValueError:
            raise AIProviderError("provider_error")
        if not isinstance(data, dict):
            raise AIProviderError("provider_error")
        return data

    def complete(self, prompt: str, max_tokens: int = 256) -> str:
        """One non-streaming text completion. Returns "" if the model produced no visible text
        (for example when a reasoning model spent the whole token budget thinking)."""
        url, headers, body = self._build(prompt, max_tokens)
        return self._text(self._post(url, headers, body))

    def chat_step(self, system: str, transcript: list, tools: list, max_tokens: int = 1024, allow_tools: bool = True) -> ChatStep:
        """One tool-calling request. `tools` = [{"name", "description", "parameters": JSON schema}].
        allow_tools=False keeps the definitions but forbids further calls (used for the final answer)."""
        url, headers, body = self._build_chat(system, transcript, tools, max_tokens, allow_tools)
        return self._parse_chat(self._post(url, headers, body))

    def _build_chat(self, system, transcript, tools, max_tokens, allow_tools) -> tuple[str, dict, dict]:
        raise NotImplementedError

    def _parse_chat(self, data: dict) -> ChatStep:
        raise NotImplementedError

    def ping(self) -> None:
        """The tiny real call behind POST /settings/ai/test. Any 2xx means key + model work."""
        self.complete(PING_PROMPT, max_tokens=64)


class GeminiProvider(BaseProvider):
    name = "gemini"

    def _build(self, prompt, max_tokens):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        headers = {"x-goog-api-key": self._api_key, "Content-Type": "application/json"}
        # temperature / top_p / top_k are deprecated for the current Gemini models, so they are not sent.
        body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"maxOutputTokens": max_tokens}}
        return url, headers, body

    def _is_bad_key_400(self, response):
        # generateContent has historically answered a bad key with 400 INVALID_ARGUMENT / API_KEY_INVALID.
        t = response.text.lower()
        return "api_key_invalid" in t or "api key not valid" in t

    def _text(self, data):
        cands = data.get("candidates") or [{}]
        content = (cands[0] or {}).get("content") or {}
        parts = content.get("parts") or []
        return "".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought"))

    # --- tool calling ---
    def _contents(self, transcript):
        out = []
        for item in transcript:
            role = item["role"]
            if role in ("user", "assistant"):
                if item["text"]:
                    out.append({"role": "user" if role == "user" else "model", "parts": [{"text": item["text"]}]})
            elif role == "assistant_step":
                out.append(item["step"].raw)                       # the model's own content, thought signatures included
            elif role == "tool_results":
                parts = []
                for r in item["results"]:
                    fr = {"name": r["call"].name, "response": r["output"]}
                    if r["call"].id:
                        fr["id"] = r["call"].id                    # Gemini 3 always sends an id; it must come back
                    parts.append({"functionResponse": fr})
                out.append({"role": "user", "parts": parts})
        return out

    def _build_chat(self, system, transcript, tools, max_tokens, allow_tools):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        headers = {"x-goog-api-key": self._api_key, "Content-Type": "application/json"}
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": self._contents(transcript),
            "tools": [{"functionDeclarations": tools}],
            "toolConfig": {"functionCallingConfig": {"mode": "AUTO" if allow_tools else "NONE"}},
            "generationConfig": {"maxOutputTokens": max_tokens},
        }
        return url, headers, body

    def _parse_chat(self, data):
        cands = data.get("candidates") or [{}]
        content = (cands[0] or {}).get("content") or {}
        calls = []
        for part in content.get("parts") or []:
            fc = part.get("functionCall") if isinstance(part, dict) else None
            if isinstance(fc, dict) and fc.get("name"):
                args = fc.get("args")
                calls.append(ToolCall(str(fc.get("id") or ""), str(fc["name"]), args if isinstance(args, dict) else {}))
        return ChatStep(text=self._text(data), calls=calls, raw=content if calls else None)


class OpenAIProvider(BaseProvider):
    name = "openai"

    def _build(self, prompt, max_tokens):
        url = "https://api.openai.com/v1/responses"
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        body = {"model": self.model, "input": prompt, "max_output_tokens": max_tokens, "store": False}
        return url, headers, body

    def _text(self, data):
        # Raw REST has no `output_text` shortcut (that is an SDK convenience): walk output[].content[].
        out = []
        for item in data.get("output") or []:
            if isinstance(item, dict) and item.get("type") == "message":
                for part in item.get("content") or []:
                    if isinstance(part, dict) and part.get("type") == "output_text":
                        out.append(part.get("text") or "")
        return "".join(out)

    # --- tool calling ---
    @staticmethod
    def _strict(schema: dict) -> dict:
        """Strict mode wants additionalProperties:false and every property required; optional ones become nullable."""
        if schema.get("type") != "object":
            return dict(schema)
        required = set(schema.get("required") or [])
        props = {}
        for name, sub in (schema.get("properties") or {}).items():
            sub = dict(sub)
            if name not in required:
                sub["type"] = [sub["type"], "null"]
                if "enum" in sub:
                    sub["enum"] = list(sub["enum"]) + [None]
            props[name] = sub
        return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}

    def _input_items(self, transcript):
        items = []
        for item in transcript:
            role = item["role"]
            if role in ("user", "assistant"):
                if item["text"]:
                    items.append({"role": role, "content": item["text"]})
            elif role == "assistant_step":
                items.extend(item["step"].raw)                     # every output item, reasoning items included
            elif role == "tool_results":
                for r in item["results"]:
                    items.append({"type": "function_call_output", "call_id": r["call"].id,
                                  "output": json.dumps(r["output"], ensure_ascii=False)})
        return items

    def _build_chat(self, system, transcript, tools, max_tokens, allow_tools):
        url = "https://api.openai.com/v1/responses"
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        body = {
            "model": self.model, "instructions": system, "input": self._input_items(transcript),
            "tools": [{"type": "function", "name": t["name"], "description": t["description"],
                       "parameters": self._strict(t["parameters"]), "strict": True} for t in tools],
            "tool_choice": "auto" if allow_tools else "none",
            "max_output_tokens": max_tokens, "store": False,
            "include": ["reasoning.encrypted_content"],            # store=false: reasoning items can only be replayed this way
        }
        return url, headers, body

    def _parse_chat(self, data):
        output = [i for i in (data.get("output") or []) if isinstance(i, dict)]
        calls = []
        for item in output:
            if item.get("type") == "function_call" and item.get("name"):
                try:
                    args = json.loads(item.get("arguments") or "{}")
                except ValueError:
                    args = None
                calls.append(ToolCall(str(item.get("call_id") or ""), str(item["name"]),
                                      args if isinstance(args, dict) else {"__invalid__": True}))
        return ChatStep(text=self._text(data), calls=calls, raw=output if calls else None)


PROVIDER_CLASSES = {"gemini": GeminiProvider, "openai": OpenAIProvider}


def make_provider(provider: str, model: str, api_key: str) -> BaseProvider:
    cls = PROVIDER_CLASSES.get(provider)
    if not cls:
        raise AppError("provider_error")
    return cls(api_key, model)
