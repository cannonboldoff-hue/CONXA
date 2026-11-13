# Multi-Provider LLM Router Setup

## Summary

The router manages a pool of (provider, endpoint, key, model) tuples with automatic failover
and per-key cool-down. As of 2026-09-23 (docs/cost_model.md "LLM Provider Strategy"), every
plan routes through a real, billed provider pool — there is no free-tier key-rotation pool
any more. Three tiered pools:

| Plan | Text model | Vision model | Provider |
|------|-----------|-------------|----------|
| Free | GLM 5.3 Flash | Qwen3 VL 235B A22B Instruct | OpenRouter |
| Starter | Kimi K3 | Qwen3 VL 235B A22B Instruct | OpenRouter |
| Pro | Claude Sonnet 5 | Claude Opus 5.5 | Direct (Anthropic) |
| Enterprise | Whatever the company requires | Same | BYOK or negotiated |

## What's New

### 1. Multi-Provider LLM Router (`app/llm/router.py`)
- Manages a pool of (provider, endpoint, key, model) tuples from enabled providers, each
  tagged `pool="free"|"starter"|"pro"` (see `PoolEntry.pool`)
- Routes text/vision calls via LRU + per-key cool-down on 429 errors, filtered to the
  requesting workspace's `compile_pool` (falls back to the Free pool if that tier's pool
  is empty or unconfigured)
- Handles 401/403 (5-minute quarantine, then retried), 429 (cooldown honoring `Retry-After`),
  other errors (retry)
- Tracks per-entry metrics: `requests_sent`, `requests_429`, `cooled_until`, `last_used_at`
- Skips text-only providers for vision tasks

### 2. Configuration (`packages/conxa-core/conxa_core/config.py`)
Three symmetric, single-deployment tiers — each its own provider label, endpoint, keys,
text/vision models, and fallback models, independent of every other tier:
- **Free pool** (`llm_free_*`): GLM 5.3 Flash text + Qwen3 VL 235B A22B Instruct vision, via
  OpenRouter. +5.5% OpenRouter platform fee on top of list price.
- **Starter pool** (`llm_starter_*`): Kimi K3 text + Qwen3 VL 235B A22B Instruct vision, also
  via OpenRouter — its own independent endpoint/key block so it can be pointed at a different
  key or provider without touching Free.
- **Pro pool** (`llm_pro_*`): Claude Sonnet 5 text + Claude Opus 5.5 vision, direct to
  Anthropic — see "Pro routes through Anthropic's OpenAI-compatible endpoint" below.
- **Enterprise**: no fixed pool — BYOK (`app/services/byok.py`) or a negotiated deployment,
  priced per contract (see `docs/cost_model.md` "Planning Enterprise to the margin target").
- Each tier supports comma-separated API keys: `LLM_{TIER}_API_KEYS=key1,key2,key3`
- Router behavior knobs: `LLM_ROUTER_COOLDOWN_SECS`, `MAX_RETRIES`, `REQUEST_TIMEOUT_MS`, `PREFER_FAST_FOR_TEXT`
- Fails fast at startup if no tier is configured with keys (`_require_at_least_one_provider`);
  bypass in tests/scripts with `SKILL_ALLOW_NO_PROVIDERS=1`

### 3. Router Integration
- The cloud exposes the pool behind `POST /api/v1/llm/proxy/{text,vision}`
  (`app/api/llm_proxy_routes.py`). Build Studio's compile pipeline calls the proxy
  via the `conxa_core.llm` router protocol (`conxa_core/llm/client.py`).
- The proxy meters usage per org and enforces the monthly token quota before
  dispatching to the router pool. `compile_pool_for(principal)` resolves a workspace's
  billing tier to `"free"`/`"starter"`/`"pro"`/`"premium"` before the call.

> **Note:** LLM-native selector generation was removed. Selectors are produced
> deterministically by `IdentityBundle` + `selector_grammar.py` in the Build
> Studio compiler; the LLM never writes selector strings. See the invariants in
> the root `CLAUDE.md`.

### Pro routes through Anthropic's OpenAI-compatible endpoint

Pro's `llm_pro_endpoint` defaults to `https://api.anthropic.com/v1` — Anthropic's own
OpenAI-compatible endpoint (Bearer auth, same `/v1/chat/completions` request/response shape
every other pooled provider uses). This means Pro needs no new router code: the existing
`_is_openai_compatible_endpoint`/`_chat_completions_url` machinery already recognizes any
endpoint whose path ends in `/v1`.

**Known gap to verify before relying on this in production:** Anthropic's OpenAI-compat layer
has historically had narrower support for `response_format: {"type": "json_object"}` than
OpenAI's own API, and every non-streaming router call sends that field
(`_openai_body_dict`). Run one live `route_text`/`route_vision` call through the Pro pool with
a real key before shipping Pro traffic against it, and note the outcome here. If it doesn't
behave, the router's existing `_DeterministicRejection` path (400s) will surface it loudly
rather than silently misbehaving.

## Usage

### 1. Configure API Keys

Copy `.env.example` to `.env` and fill in API keys:

```bash
# Free pool — OpenRouter (GLM 5.3 Flash + Qwen3 VL)
LLM_FREE_API_KEYS=sk-or-your-key

# Starter pool — usually the same OpenRouter key, on its own var
LLM_STARTER_API_KEYS=sk-or-your-key

# Pro pool — direct Anthropic key
LLM_PRO_API_KEYS=your-anthropic-key
```

### 2. Configure Each Tier

```env
# LLM_{TIER}_PROVIDER/_ENDPOINT already default to the right values for each
# tier (Free/Starter → "openrouter", Pro → "anthropic") — no *_ENABLED flag
# to set. An empty LLM_{TIER}_API_KEYS is what keeps that tier's pool empty
# (falls back to Free).
```

### Copilot's multimodal model (optional)

The Human Review Copilot's turns (`copilot_diagnose`/`copilot_reply`) route to `text_model`
when the turn has no screenshot, and to a separate `*_MULTIMODAL_MODEL` slot — not
`*_VISION_MODEL` — when it does, so Copilot's screenshot+conversation turns can use a different
model than the compiler's other vision tasks (`anchor_vision`, ...) on the
same tier. Every tier has its own `LLM_{TIER}_MULTIMODAL_MODEL`, e.g.:

```env
LLM_FREE_MULTIMODAL_MODEL=            # optional — falls back to LLM_FREE_VISION_MODEL when unset
LLM_STARTER_MULTIMODAL_MODEL=
LLM_PRO_MULTIMODAL_MODEL=
```

### Conxa Execute's model

Conxa Execute has no dedicated LLM deployment of its own. Its chat calls (`usage_class:
"execute_chat"`) always draw from the caller's own workspace tier pool — GLM 5.3 Flash on
Free, Kimi K3 on Starter, Claude Opus 5.5 on Pro — via each tier's `*_MULTIMODAL_MODEL` slot
(falls back to `*_VISION_MODEL` when unset). Execute chat always takes the multimodal path,
screenshot or not (`_copilot_modality("execute_chat", ...)` always returns `"multimodal"`).

### 3. Router Behavior (Optional Tuning)

```env
# Cool a key for 60s after hitting rate limit
LLM_ROUTER_COOLDOWN_SECS=60

# Retry up to 3 times before giving up
LLM_ROUTER_MAX_RETRIES=3

# Per-request timeout (30s)
LLM_ROUTER_REQUEST_TIMEOUT_MS=30000

# Prefer the fastest available text-only provider
LLM_ROUTER_PREFER_FAST_FOR_TEXT=true
```

## Backward Compatibility

- If **no providers enabled** in .env, the router pool is empty → it falls back to
  the legacy single-endpoint config (`SKILL_LLM_TEXT_ENDPOINT`, `SKILL_LLM_VISION_ENDPOINT`).
- `.env.example` includes both the multi-provider block and the legacy single-endpoint
  section.
