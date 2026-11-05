# FreeModels Chat API — agent guide

Give this file (or the live `/openapi.json`) to any agentic AI (Codex,
Claude Code, ...) and it can call the API. No auth, no key.

## Base URL
`https://<your-deploy-host>/` (e.g. the SnapDeploy container URL — fill in
the live URL after deploy)

## Models (9)
- `claude-opus-5.5` (Claude Opus 5.5, Anthropic) — alias `opus`
- `claude-sonnet-5` (Claude Sonnet 5.5, Anthropic) — alias `sonnet`
- `claude-fable-5` (Claude Fable 5, Anthropic)
- `claude-fable-5.1` (Claude Fable 5.1, Anthropic) — alias `fable`
- `sol` (GPT 5.6 Sol, OpenAI)
- `terra` (GPT 5.6 Terra, OpenAI)
- `gpt-6-astra` (GPT 6 Astra, OpenAI) — alias `astra`
- `glm-5.2` (GLM 5.2, Z.AI) — alias `glm`
- `kimi-k3` (Kimi K3, Moonshot AI) — alias `kimi`

All models are text chat only — no image/video/audio generation.

## Endpoints
- `GET /health` — `{"status":"ok","models":9,"upstream":"ready"}`
- `GET /v1/models` — OpenAI-shaped model list (with aliases)
- `POST /v1/chat/completions` — OpenAI-compatible
- `GET /chat?prompt=...&model=astra` — simplest call
- `GET /docs` — Swagger UI; `GET /openapi.json` — machine-readable spec

## Examples

Non-streaming chat (Astra):

```bash
curl -X POST https://<base>/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"astra","messages":[{"role":"user","content":"What is 17 + 25?"}]}'
```

Streaming chat (Opus):

```bash
curl -N -X POST https://<base>/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-opus-5.5","messages":[{"role":"user","content":"Write a haiku about rain"}],"stream":true}'
```

Simple GET:

```bash
curl "https://<base>/chat?prompt=Hello%20who%20are%20you&model=astra"
```

## Notes
- Unknown model → HTTP 400 with the valid model + alias list.
- Upstream failure → HTTP 502 with the upstream error text.
- `temperature` / `max_tokens` are accepted but ignored (upstream has no
  such fields). Optional `thinking` / `deep_search` booleans are forwarded.
- Rate limit: 30 chat requests/min per IP.
- Free hosting sleeps after ~15 min idle — the first request after sleep
  takes ~1 min to wake (open the URL in a browser first if an agent gets
  a 503 wake page), then responses are normal.
- Honest limits: unofficial wrapper around freemodels.pro's internal chat
  endpoint; the free service can be slow, busy, limited or blocked at any
  time, with no SLA.
