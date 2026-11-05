"""FreeModels chat API — self-hosted REST wrapper.

Wraps freemodels.pro's free multi-model text chat (no login, no key) so
any agent (Codex, Claude Code, ...) can call its 9 models over a plain
OpenAI-compatible HTTP API.

Upstream (verified working server-side from this VM):
  POST https://freemodels-chat.freemodels.workers.dev
  Headers: Content-Type: application/json,
           Origin: https://freemodels.pro,
           Referer: https://freemodels.pro/
  (without Origin/Referer it answers 403 "Forbidden: this API is only
  available on https://freemodels.pro")
  Body: {"messages":[{"role":"user","content":"..."}],
         "modelId":"<id>","thinking":false,"deepSearch":false,"stream":bool}
  stream=false -> {"content":"..."} (HTTP 200)
  stream=true  -> OpenAI-style SSE `data: {"choices":[{"delta":...}]}`,
                  ending with `data: [DONE]`

Proxy rule: the VM's egress needs an authenticated proxy, but the deploy
host must connect directly. This code reads ONE custom env var, PROXY_URL,
and never the shell's proxy env vars (trust_env=False below). On the VM,
run.sh forwards the shell's proxy setting into PROXY_URL at runtime.
"""

import json
import os
import time
import uuid
from collections import defaultdict
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

UPSTREAM = "https://freemodels-chat.freemodels.workers.dev"
UPSTREAM_HEADERS = {
    "Content-Type": "application/json",
    "Origin": "https://freemodels.pro",
    "Referer": "https://freemodels.pro/",
    "User-Agent": "Mozilla/5.0",
}

MODELS = [
    {"id": "claude-opus-5.5", "name": "Claude Opus 5.5", "provider": "Anthropic",
     "description": "Flagship — most capable for complex coding and agentic tasks.",
     "aliases": ["opus"]},
    {"id": "claude-sonnet-5", "name": "Claude Sonnet 5.5", "provider": "Anthropic",
     "description": "Thinking — reasoning with extended thinking, strong coding.",
     "aliases": ["sonnet"]},
    {"id": "claude-fable-5", "name": "Claude Fable 5", "provider": "Anthropic",
     "description": "Creative model — storytelling, novels and short stories.",
     "aliases": []},
    {"id": "claude-fable-5.1", "name": "Claude Fable 5.1", "provider": "Anthropic",
     "description": "New — improved creative model with coding and research support.",
     "aliases": ["fable"]},
    {"id": "sol", "name": "GPT 5.6 Sol", "provider": "OpenAI",
     "description": "Fast — high-speed model for quick, insightful replies.",
     "aliases": []},
    {"id": "terra", "name": "GPT 5.6 Terra", "provider": "OpenAI",
     "description": "Reasoning — deep reasoning, advanced problem solving.",
     "aliases": []},
    {"id": "gpt-6-astra", "name": "GPT 6 Astra", "provider": "OpenAI",
     "description": "Free — reasoning-first flagship for coding, math, science and creative work.",
     "aliases": ["astra"]},
    {"id": "glm-5.2", "name": "GLM 5.2", "provider": "Z.AI",
     "description": "Versatile — strong bilingual and tool-calling abilities.",
     "aliases": ["glm"]},
    {"id": "kimi-k3", "name": "Kimi K3", "provider": "Moonshot AI",
     "description": "Long Context — ultra-long context, 200K+ token window and recall.",
     "aliases": ["kimi"]},
]

MODEL_IDS = {m["id"] for m in MODELS}
ALIASES = {a: m["id"] for m in MODELS for a in m["aliases"]}
DEFAULT_MODEL = "gpt-6-astra"

app = FastAPI(title="FreeModels Chat API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def resolve_model(raw: str) -> str:
    """Accept an exact modelId or a friendly alias; None if unknown."""
    if not raw:
        return None
    key = raw.strip().lower()
    if key in MODEL_IDS:
        return key
    return ALIASES.get(key)


def build_proxy():
    """Proxy from PROXY_URL only (never the shell's proxy env vars, and
    never trust_env). Returns None for a direct connection, which is what
    the deploy host wants."""
    raw = os.environ.get("PROXY_URL", "").strip()
    if not raw:
        return None
    u = urlparse(raw)
    if not u.hostname:
        return None
    url = f"{u.scheme or 'http'}://{u.hostname}:{u.port or 3128}"
    if u.username:
        return httpx.Proxy(url, auth=(u.username, u.password or ""))
    return url


def build_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=120.0, trust_env=False, proxy=build_proxy())


# --- tiny per-IP rate limit (30/min on chat endpoints) ---

_hits = defaultdict(list)


def check_rate(ip: str, limit: int = 30, window: int = 60):
    now = time.time()
    lst = [t for t in _hits[ip] if now - t < window]
    if len(lst) >= limit:
        raise HTTPException(429, "rate limit: try again in a bit")
    lst.append(now)
    _hits[ip] = lst


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def validate_messages(messages):
    if not isinstance(messages, list) or not messages:
        raise HTTPException(400, "messages must be a non-empty list")
    for m in messages:
        if not isinstance(m, dict) or not isinstance(m.get("role"), str) \
                or not isinstance(m.get("content"), str):
            raise HTTPException(400, "each message needs a string role and content")
    return [{"role": m["role"], "content": m["content"]} for m in messages]


def upstream_payload(model: str, messages, thinking: bool, deep_search: bool, stream: bool):
    return {
        "messages": messages,
        "modelId": model,
        "thinking": bool(thinking),
        "deepSearch": bool(deep_search),
        "stream": bool(stream),
    }


def upstream_error(status: int, text: str) -> HTTPException:
    # Upstream errors come as {"error": "..."}; keep the message, cap length.
    try:
        data = json.loads(text)
        msg = data.get("error") or data.get("message") or text
    except Exception:
        msg = text
    return HTTPException(502, f"freemodels upstream error (HTTP {status}): {str(msg)[:300]}")


async def chat_non_stream(model: str, messages, thinking: bool, deep_search: bool) -> str:
    async with build_client() as client:
        try:
            r = await client.post(UPSTREAM, json=upstream_payload(model, messages, thinking, deep_search, False),
                                  headers=UPSTREAM_HEADERS)
        except httpx.HTTPError:
            raise HTTPException(502, "freemodels upstream unreachable (network/proxy error)")
        if r.status_code != 200:
            raise upstream_error(r.status_code, r.text)
        try:
            data = r.json()
        except Exception:
            raise HTTPException(502, f"freemodels upstream returned non-JSON (HTTP {r.status_code})")
        if isinstance(data, dict) and data.get("error"):
            raise HTTPException(502, f"freemodels upstream error: {str(data['error'])[:300]}")
        content = data.get("content") if isinstance(data, dict) else None
        if not isinstance(content, str):
            raise HTTPException(502, f"freemodels upstream gave an unexpected reply: {str(data)[:300]}")
        return content


# --- endpoints ---


@app.get("/")
def root():
    return {
        "name": "FreeModels Chat API",
        "description": "OpenAI-compatible wrapper for freemodels.pro's free multi-model chat (9 models, no key, no login).",
        "models": [m["id"] for m in MODELS],
        "endpoints": [
            "GET /health",
            "GET /v1/models",
            "POST /v1/chat/completions {model, messages, stream, temperature, max_tokens}  (OpenAI-compatible)",
            "POST /v1/messages {model, messages, system, stream, max_tokens}  (Anthropic-compatible, for Claude Code)",
            "GET /chat?prompt=...&model=&thinking=&deep_search=",
        ],
    }


@app.get("/health")
def health():
    return {"status": "ok", "models": len(MODELS), "upstream": "ready"}


@app.get("/v1/models")
def list_models():
    now = int(time.time())
    return {
        "object": "list",
        "data": [
            {"id": m["id"], "object": "model", "created": now,
             "owned_by": m["provider"], "display_name": m["name"],
             "description": m["description"], "aliases": m["aliases"]}
            for m in MODELS
        ],
    }


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatBody(BaseModel):
    model: str = DEFAULT_MODEL
    messages: list
    stream: bool = False
    temperature: float | None = None  # accepted, not forwarded (upstream has no such field)
    max_tokens: int | None = None     # accepted, not forwarded (upstream has no such field)
    thinking: bool = False
    deep_search: bool = False


@app.post("/v1/chat/completions")
async def chat_completions(request: Request, body: ChatBody):
    check_rate(client_ip(request))
    model = resolve_model(body.model)
    if model is None:
        raise HTTPException(
            400,
            f"unknown model '{body.model}'; valid models: {sorted(MODEL_IDS)}; "
            f"aliases: {sorted(ALIASES)}",
        )
    messages = validate_messages(body.messages)

    if body.stream:
        # Open the upstream stream first so a non-200 can still be raised
        # as a proper 502 before any SSE bytes go out.
        client = build_client()
        try:
            # client.stream() is a context manager in httpx 0.28, so build
            # and send the request directly to hold the response open.
            req = client.build_request(
                "POST", UPSTREAM,
                json=upstream_payload(model, messages, body.thinking, body.deep_search, True),
                headers=UPSTREAM_HEADERS,
            )
            resp = await client.send(req, stream=True)
        except httpx.HTTPError:
            await client.aclose()
            raise HTTPException(502, "freemodels upstream unreachable (network/proxy error)")
        if resp.status_code != 200:
            text = (await resp.aread()).decode("utf-8", "replace")
            await resp.aclose()
            await client.aclose()
            raise upstream_error(resp.status_code, text)

        async def gen():
            try:
                async for chunk in resp.aiter_bytes():
                    if chunk:
                        yield chunk
            finally:
                await resp.aclose()
                await client.aclose()

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    content = await chat_non_stream(model, messages, body.thinking, body.deep_search)
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {"index": 0,
             "message": {"role": "assistant", "content": content},
             "finish_reason": "stop"}
        ],
        "usage": None,
    }


@app.get("/chat")
async def chat_get(
    request: Request,
    prompt: str = Query(..., max_length=10000),
    model: str = DEFAULT_MODEL,
    thinking: bool = False,
    deep_search: bool = False,
):
    check_rate(client_ip(request))
    resolved = resolve_model(model)
    if resolved is None:
        raise HTTPException(
            400,
            f"unknown model '{model}'; valid models: {sorted(MODEL_IDS)}; "
            f"aliases: {sorted(ALIASES)}",
        )
    content = await chat_non_stream(
        resolved, [{"role": "user", "content": prompt}], thinking, deep_search)
    return {"model": resolved, "content": content}


# --- Anthropic-compatible /v1/messages (for Claude Code) ---

class MessagesBody(BaseModel):
    model: str = DEFAULT_MODEL
    messages: list
    system: str | list | None = None
    stream: bool = False
    max_tokens: int | None = None  # required by Anthropic clients; accepted, not forwarded
    thinking: bool = False
    deep_search: bool = False


def anthropic_error_response(status: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"type": "error", "error": {"type": "api_error", "message": message}},
    )


def flatten_anthropic_content(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            p.get("text", "")
            for p in content
            if isinstance(p, dict) and p.get("type") == "text" and isinstance(p.get("text"), str)
        )
    return ""


def to_upstream_messages(messages, system):
    out = []
    if system:
        out.append({"role": "system", "content": flatten_anthropic_content(system)})
    for m in messages:
        if not isinstance(m, dict) or not isinstance(m.get("role"), str):
            raise HTTPException(400, "each message needs a role and content")
        role = "assistant" if m.get("role") == "assistant" else "user"
        out.append({"role": role, "content": flatten_anthropic_content(m.get("content"))})
    return out


@app.post("/v1/messages")
async def messages_endpoint(request: Request, body: MessagesBody):
    check_rate(client_ip(request))
    model = resolve_model(body.model)
    if model is None:
        return anthropic_error_response(
            400,
            f"unknown model '{body.model}'; valid models: {sorted(MODEL_IDS)}; "
            f"aliases: {sorted(ALIASES)}",
        )
    if not isinstance(body.messages, list) or not body.messages:
        return anthropic_error_response(400, "messages must be a non-empty array")
    try:
        messages = to_upstream_messages(body.messages, body.system)
    except HTTPException as e:
        msg = e.detail if isinstance(e.detail, str) else str(e.detail)
        return anthropic_error_response(e.status_code, msg)

    if body.stream:
        client = build_client()
        try:
            req = client.build_request(
                "POST", UPSTREAM,
                json=upstream_payload(model, messages, body.thinking, body.deep_search, True),
                headers=UPSTREAM_HEADERS,
            )
            resp = await client.send(req, stream=True)
        except httpx.HTTPError:
            await client.aclose()
            return anthropic_error_response(502, "freemodels upstream unreachable (network/proxy error)")
        if resp.status_code != 200:
            text = (await resp.aread()).decode("utf-8", "replace")
            await resp.aclose()
            await client.aclose()
            return anthropic_error_response(resp.status_code, text[:500] or f"Upstream HTTP {resp.status_code}")

        msg_id = f"msg_{uuid.uuid4().hex[:24]}"

        async def gen():
            def ev(event):
                return f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"

            yield ev({
                "type": "message_start",
                "message": {
                    "id": msg_id, "type": "message", "role": "assistant",
                    "model": model, "content": [], "stop_reason": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            })
            yield ev({"type": "content_block_start", "index": 0,
                      "content_block": {"type": "text", "text": ""}})
            buf = b""
            try:
                async for chunk in resp.aiter_bytes():
                    if not chunk:
                        continue
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        t = line.decode("utf-8", "replace").strip()
                        if not t.startswith("data:"):
                            continue
                        payload = t[5:].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            j = json.loads(payload)
                        except Exception:
                            continue
                        choice = (j.get("choices") or [{}])[0]
                        text = (
                            choice.get("delta", {}).get("content")
                            or choice.get("message", {}).get("content")
                            or ""
                        )
                        if text:
                            yield ev({"type": "content_block_delta", "index": 0,
                                      "delta": {"type": "text_delta", "text": text}})
                    else:
                        continue
                    break
            finally:
                await resp.aclose()
                await client.aclose()
            yield ev({"type": "content_block_stop", "index": 0})
            yield ev({"type": "message_delta",
                      "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                      "usage": {"output_tokens": 0}})
            yield ev({"type": "message_stop"})

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    try:
        content = await chat_non_stream(model, messages, body.thinking, body.deep_search)
    except HTTPException as e:
        msg = e.detail if isinstance(e.detail, str) else str(e.detail)
        return anthropic_error_response(e.status_code, msg)
    return {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": content}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }
