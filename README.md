# FreeModels Chat API

Self-hosted REST wrapper around freemodels.pro's free multi-model text
chat (https://freemodels.pro — no login, no sign-up, no key). It replays
the chat page's real request with plain HTTP:

- `POST https://freemodels-chat.freemodels.workers.dev`
- Headers: `Origin: https://freemodels.pro`, `Referer: https://freemodels.pro/`
  (without them the upstream answers 403)
- Body: `{"messages": [...], "modelId": "<id>", "thinking": false, "deepSearch": false, "stream": true|false}`

## Models (9)

| modelId | Model | Provider |
|---|---|---|
| `claude-opus-5.5` | Claude Opus 5.5 | Anthropic |
| `claude-sonnet-5` | Claude Sonnet 5.5 | Anthropic |
| `claude-fable-5` | Claude Fable 5 | Anthropic |
| `claude-fable-5.1` | Claude Fable 5.1 | Anthropic |
| `sol` | GPT 5.6 Sol | OpenAI |
| `terra` | GPT 5.6 Terra | OpenAI |
| `gpt-6-astra` | GPT 6 Astra | OpenAI |
| `glm-5.2` | GLM 5.2 | Z.AI |
| `kimi-k3` | Kimi K3 | Moonshot AI |

Friendly aliases also work: `opus` → claude-opus-5.5, `sonnet` →
claude-sonnet-5, `astra` → gpt-6-astra, `fable` → claude-fable-5.1,
`glm` → glm-5.2, `kimi` → kimi-k3.

All 9 models are text chat only. There is no image/video/audio
generation on freemodels.pro — a "make a video" request is answered with
written storyboard text and Python (Pillow + FFmpeg) code, not a rendered
video.

## Run

```bash
./run.sh            # http://localhost:8000
```

## Endpoints

| Endpoint | What it does |
|---|---|
| `GET /health` | Server status + model count |
| `GET /v1/models` | OpenAI-shaped list of all 9 models (+ aliases) |
| `POST /v1/chat/completions` | OpenAI-compatible chat; `{model, messages, stream, temperature, max_tokens}` (temperature/max_tokens are accepted but not forwarded — upstream has no such fields); optional `thinking` / `deep_search` are forwarded |
| `GET /chat?prompt=...&model=&thinking=&deep_search=` | Simple GET convenience → `{"model", "content"}` |
| `GET /docs` | Swagger UI; `GET /openapi.json` — machine-readable spec |

Unknown model → 400 with the valid list. Upstream non-200 → 502 with
the upstream error text. `stream: true` → SSE passthrough of the
upstream's OpenAI-style chunks, ending with `data: [DONE]`.

## Honest limits

- **Unofficial wrapper.** freemodels.pro publishes no public API; this
  wraps its internal chat endpoint. It can change, break, or be blocked
  at any time, with no notice and no SLA.
- freemodels.pro is a free service "subject to availability and service
  limits" — it can be slow, busy, or rate-limited. The site's Terms
  forbid bypassing limits or gaining unauthorised access; this wrapper
  only replays the page's own request shape with the headers the page
  itself sends, and sends no more than a normal browser session would.
- No key, no account, no storage: chats are not stored anywhere by this
  API; every request is stateless. Your messages go to freemodels.pro's
  servers (its Privacy policy says messages are processed to generate
  replies and not stored beyond the reply).
- Rate limit: 30 chat requests/minute per IP (in-memory).
- Proxy: the app reads a single custom env var, `PROXY_URL`, only. On
  this VM `run.sh` forwards the shell's existing proxy setting into it
  at runtime; on the deploy host it is unset and the app connects
  directly. The proxy URL is never written to any file and never logged.
