"""Central configuration for the skill platform service."""

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def active_environment() -> str:
    """Resolve the active deployment environment from ``CONXA_ENV``.

    This is *the single switch* for the whole platform. It selects which
    ``.env.<env>`` file is loaded (see ``Settings.model_config`` below) and which
    isolated tree of paths / endpoints / update channel everything derives from.

    Defaults to ``"dev"`` when unset — fail *safe*, not fail *prod*: an
    unconfigured process must never silently target production. ``prod`` is opt-in
    only via an explicit ``CONXA_ENV=prod``. Values other than dev/prod (e.g. a
    future ``staging``) pass through unchanged.
    """
    raw = os.environ.get("CONXA_ENV", "").strip().lower()
    if raw in ("prod", "production"):
        return "prod"
    if raw in ("", "dev", "development", "local"):
        return "dev"
    return raw


def env_files() -> tuple[str, ...]:
    """Env files pydantic loads, lowest-priority first.

    Legacy ``.env`` is read first (backward compatibility), then the
    environment-specific ``.env.<env>`` overrides it — so an existing ``.env``
    acts as a shared base while ``.env.dev`` / ``.env.prod`` carry the values that
    differ between lanes and always win.
    """
    return (".env", f".env.{active_environment()}")


def _load_env_files_into_process() -> None:
    """Populate ``os.environ`` from ``env_files()``, not just this Settings instance.

    ``Settings.model_config``'s ``env_file`` only feeds pydantic-settings' own
    field resolution — it never touches the real process environment. Plenty of
    code (``updates_routes.py``'s ``CONXA_HOST_VERSION``/``CONXA_APP_VERSION``,
    Build Studio's ``backend.py``) reads non-``SKILL_``-prefixed vars straight off
    ``os.environ``, so a value that only exists in ``.env.dev``/``.env.prod`` was
    silently invisible to them. ``override=False`` so a real shell/launcher-set
    env var always wins over the file, matching every other explicit-override rule
    in this codebase.
    """
    from dotenv import load_dotenv  # hard pinned dependency (pyproject.toml) — ImportError
    # propagates rather than silently skipping .env loading, which would leave every setting
    # on its hardcoded default with no visible sign why (e.g. missing API keys/DB URL).
    for _f in env_files():
        load_dotenv(_f, override=False)


_load_env_files_into_process()


def state_base_dir() -> Path:
    """Writable base directory for generated runtime state (``data/``, ``output/``).

    Frozen builds are installed under a read-only location (e.g. ``Program Files``
    on Windows), so state cannot live next to the bundled package. Redirect it to
    ``~/.conxa-build-studio``, keeping all Build Studio state under one root alongside
    the deps cache. Development keeps the in-repo source default so ``pip install -e``
    and ``python backend.py`` workflows are unchanged.

    ``CONXA_STUDIO_HOME`` overrides the root entirely. The dev/prod launcher sets it
    (``~/.conxa-build-studio-dev`` vs ``~/.conxa-build-studio``) so the two lanes
    keep fully separate deps caches, sandboxes, and generated bundles on one machine.
    """
    override = os.environ.get("CONXA_STUDIO_HOME", "").strip()
    if override:
        return Path(os.path.expanduser(override))
    if getattr(sys, "frozen", False):
        return Path(os.path.expanduser("~/.conxa-build-studio"))
    return Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class ProviderConfig:
    """Single LLM provider configuration (one key per instance)."""
    provider: str
    endpoint: str
    api_key: str
    text_model: str
    vision_model: str
    pool: str = "free"  # "free" | "starter" | "pro" — see Settings.llm_starter_* / llm_pro_*
    # Model to retry on the same entry when the primary model fails. Empty = no fallback.
    fallback_text_model: str = ""
    fallback_vision_model: str = ""
    # Copilot's screenshot+conversation turns (BUILD-26) — separate from vision_model so a
    # dedicated multimodal-capable model can be configured without affecting the compiler's
    # other vision tasks (anchor_vision, region_selector, ...). Empty = falls back to
    # vision_model at call time (see router.py's _call_provider).
    multimodal_model: str = ""
    fallback_multimodal_model: str = ""


def _provider_env(name: str) -> AliasChoices:
    return AliasChoices(f"SKILL_{name}", name)


class Settings(BaseSettings):
    """Environment-driven settings; safe defaults for local MVP."""

    model_config = SettingsConfigDict(
        env_prefix="SKILL_",
        env_file=env_files(),
        extra="ignore",
        populate_by_name=True,
    )

    data_dir: Path = state_base_dir() / "data"
    host: str = "127.0.0.1"
    port: int = 8000
    default_action_timeout_ms: int = 5000
    screenshot_jpeg_quality: int = 78
    # LLM shared settings (no per-feature toggles; LLM is mandatory and routed via the multi-provider pool)
    llm_max_calls_per_step: int = 1
    llm_debug: bool = False
    # How many per-step anchor_vision_frameset calls (each step's own 5 time-offset
    # frames, one call) run concurrently in one wave. Capped at the cloud's own
    # llm_proxy_max_concurrent_per_workspace below — a higher client-side value
    # trips the server's workspace-concurrency 429 on nearly every wave instead of
    # only during genuine provider rate-limiting.
    llm_anchor_vision_max_concurrent_steps: int = 4
    # Waves beyond this many retries give up prefetching the remaining steps —
    # generate_anchors_for_step_or_raise's own per-step call (source of truth)
    # still gets a real attempt for whatever's left uncached.
    llm_anchor_vision_max_wave_retries: int = 3
    # When a vision anchor call exhausts every provider (after the router's cooldown wait),
    # this decides what compile does: False (default) = hard-stop the compile with
    # VisionAnchorGenerationError so a persistent provider outage is fixed, not hidden.
    # True = degrade to deterministic keyword anchors for that step and keep compiling —
    # useful if you'd rather finish a compile on flaky free-tier keys than block on them.
    #
    # In production this value is normally overwritten per-compile from the workspace's
    # cloud entitlement (capabilities.vision_fallback_on_exhaustion via
    # GET /api/v1/entitlements/current — see backend.py's
    # _apply_vision_fallback_entitlement, called from handlers/compile.py::cmd_compile).
    # This local default/env var only matters as the fallback when that fetch fails, and
    # for dev workflows with no cloud reachable. It is a Build-Studio-local setting either
    # way — this compiler runs entirely inside Build Studio's process, so setting
    # SKILL_VISION_ANCHOR_FALLBACK_ON_EXHAUSTION on the cloud/Render backend does nothing.
    vision_anchor_fallback_on_exhaustion: bool = False

    # Timeouts (no legacy single-endpoint config — endpoints come from per-provider settings below)
    llm_vision_timeout_ms: int = 120000
    # Studio -> cloud proxy -> provider is a double hop through a free-tier Render
    # instance; 2s (the old default, calibrated for a direct call) benched every
    # pool key on a single slow response and 502'd the whole compile. See
    # docs/TRD.md §13.3 and TODO.md CLOUD-11.
    llm_text_timeout_ms: int = 20000

    # Pack structuring + skill.md tuning (calls Text endpoint above)
    llm_pack_enabled: bool = True
    llm_pack_timeout_ms: int = 600000
    llm_pack_max_attempts: int = 1
    llm_pack_structure_temperature: float = 0.0
    llm_pack_structure_max_tokens: int | None = None
    llm_pack_markdown_temperature: float = 0.15
    llm_pack_markdown_max_tokens: int = 8000
    llm_pack_top_p: float | None = None
    pack_recovery_vision_enabled: bool = True

    # The compiler's second opinion (BUILD-25) — a second whole-workflow LLM
    # call, sited after the primary compile, whose findings are APPLIED to the
    # compiled steps (binding names, {{placeholder}} values, phase, try_dismiss
    # branches; never selectors). Off means the pass returns [] before any call
    # is made; on failure it degrades the same way, and either path falls back
    # to a rules-only compile that is byte-identical to one that never ran it.
    # The env var keeps its original name (SKILL_LLM_SEMANTIC_SUGGESTIONS_ENABLED)
    # so existing .env files keep working.
    llm_semantic_suggestions_enabled: bool = True

    # Selector compilation tuning (calls Text endpoint above)
    llm_selector_timeout_ms: int = 60000
    llm_selector_candidates: int = 8          # candidates to request per element

    # Selector cache (Phase 1)
    selector_cache_ttl_days: int = 30
    selector_cache_enabled: bool = True

    # DOM snapshot (Phase 2)
    snapshot_dedup_enabled: bool = True
    snapshot_surrounding_text_radius_px: int = 200
    snapshot_capture_a11y: bool = True
    snapshot_retention_days: int = 30
    # Record and sign in inside Build Studio's own browser panel instead of a separate Chromium
    # window, when Electron offers one (CONXA_HOST_BROWSER_CDP + CONXA_HOST_CONTROL_URL). On since
    # the side-by-side equivalence check passed (docs/archive/spikes/in-app-recording-2026-10.md);
    # SKILL_RECORD_IN_PANEL=false brings the separate window back.
    record_in_panel: bool = True
    # Interval for the background GC that prunes expired selector-cache entries
    # and old session-snapshot blobs. 0 disables the scheduler (e.g. tests, local).
    gc_interval_secs: int = 6 * 60 * 60
    # Directory name at project root for generated bundles (default skill_package). Overrides .skill_bundle_root after UI rename.
    package_bundle_root: str = "skill_package"
    # First-class deployment environment — the single switch. Sourced from CONXA_ENV
    # (or SKILL_ENVIRONMENT) and normalized to "dev"/"prod". Cross-checked against
    # auth_required below so a mislabeled config (prod env with auth off, or real
    # auth while labeled dev) refuses to boot instead of silently misbehaving.
    environment: str = Field(
        default_factory=active_environment,
        validation_alias=AliasChoices("SKILL_ENVIRONMENT", "CONXA_ENV"),
    )

    # Public API / browser boundary.
    cors_allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    cors_preview_origin_regex: str = r"https://.*\.vercel\.app"
    max_json_body_bytes: int = 1_000_000
    # A single batched vision anchor request (Stage 4 batching, up to 4 images per
    # call) can carry several base64 JPEGs at once — 1MB is too tight (one alone
    # can approach 200KB base64'd); this stays far under the build-artifact ceiling.
    llm_vision_proxy_max_bytes: int = 8 * 1024 * 1024
    build_artifact_upload_max_bytes: int = 250 * 1024 * 1024

    # Root logging level. Nothing configures Python's root logger otherwise, so the
    # effective level defaults to WARNING and every logger.info()/logger.warning()
    # call in the cloud backend (tracking.py, saas.py, cashfree_routes.py, ...) is
    # silently discarded. Cloud's main.py applies this via logging.basicConfig();
    # Build Studio does not currently read it.
    log_level: str = "INFO"

    # Clerk authentication. Local development leaves this disabled; production
    # deployments should set SKILL_AUTH_REQUIRED=true and the Clerk values below.
    auth_required: bool = False
    clerk_issuer: str = ""
    clerk_jwks_url: str = ""
    clerk_authorized_parties: str = ""
    clerk_audience: str = ""
    clerk_secret_key: str = Field(default="", validation_alias="CLERK_SECRET_KEY")
    api_proxy_shared_secret: str = ""
    api_proxy_signing_window: int = 60  # max age in seconds for HMAC proxy timestamps

    # Metered LLM proxy used by Build Studio. Quota is per org per calendar month
    # (input + output tokens). 0 disables enforcement. The proxy only accepts
    # requests carrying the X-Conxa-Client header below.
    llm_proxy_monthly_token_quota: int = 5_000_000
    llm_proxy_client_header: str = "build-studio"
    # Concurrent in-flight /llm/proxy/* calls a single workspace may hold at once.
    # Without this, one workspace's compile burst can drain the shared provider
    # pool (cooldowns, quarantines) into 502s for every other tenant sharing it.
    # Over the cap gets a 429 with Retry-After instead — the Studio proxy client
    # already backs off on that.
    llm_proxy_max_concurrent_per_workspace: int = 4
    # Plan-aware quota enforcement. Enabled by default so paid plans are honored
    # in production; workspaces on the `development` plan (unlimited limits) and
    # any plan whose limit resolves to None are never blocked, so local dev is
    # unaffected. Tests that need enforcement off flip these per-case.
    entitlements_enforce_compile: bool = True
    entitlements_enforce_human_edit: bool = True
    entitlements_enforce_distribution: bool = True
    entitlements_enforce_machines: bool = True
    entitlements_enforce_seats: bool = True
    entitlements_enforce_execute_seats: bool = True
    entitlements_reservation_ttl_secs: int = 30 * 60

    # Production backing services. The local MVP still has file-backed fallbacks.
    database_url: str = ""
    redis_url: str = ""
    blob_read_write_token: str = ""
    worker_queue_name: str = "ai-native-jobs"
    worker_dead_letter_queue_name: str = "ai-native-jobs-dlq"

    # Billing and app redirects.
    app_url: str = "http://localhost:5173"
    api_base_url: str = ""  # Set in production to prevent X-Forwarded-Host injection

    # Tracking HMAC secret for signing runtime telemetry tokens.
    # Set SKILL_TRACKING_HMAC_SECRET in production to enable company-scoped tracking.
    tracking_hmac_secret: str = ""
    tracking_max_events_per_batch: int = 200
    tracking_max_field_chars: int = 256

    # Installer download link signing (SG-07). Empty key preserves the legacy
    # public/unsigned download behavior for local dev.
    installer_signing_key: str = ""
    installer_signing_window: int = 600  # seconds; longer than the proxy window — human download clicks, not machine calls

    # conxa-execute's Execute Key HMAC secret. An Execute Key is derived from a
    # purchase's Cashfree order/subscription reference id via
    # HMAC-SHA256(this secret, order_ref) rather than randomly generated and
    # stored — nothing sensitive ever touches the database. Empty in dev.
    execute_key_hmac_secret: str = ""

    # Enterprise BYOK (Azure OpenAI) key-at-rest encryption. 32 raw bytes,
    # base64-encoded (e.g. `python -c "import base64,os;print(base64.b64encode(os.urandom(32)).decode())"`).
    # Empty in dev — BYOK storage refuses to encrypt/decrypt without a real key
    # rather than silently storing plaintext.
    byok_encryption_key: str = Field(default="", validation_alias="SKILL_BYOK_ENCRYPTION_KEY")

    # Bug report submissions (dashboard "Report a bug" page) — sent as an email via
    # Resend's HTTPS API rather than SMTP, since Render's free plan blocks outbound
    # SMTP ports. Empty resend_api_key/bug_report_to_email disables the endpoint (503).
    resend_api_key: str = ""
    bug_report_to_email: str = ""
    bug_report_from_email: str = "Conxa Bug Reports <onboarding@resend.dev>"
    bug_report_max_bytes: int = 25 * 1024 * 1024

    # LLM key pool: three symmetric, single-deployment tiers (Free/Starter/Pro — each
    # its own provider label, endpoint, keys, text/vision models, fallback models), no
    # separate multi-provider rotation block any more. The old Groq/Google AI Studio/
    # NVIDIA NIM/Cerebras/Together/Mistral/FreeLLMAPI pool was retired 2026-09-23 — see
    # docs/cost_model.md "LLM Provider Strategy". Free/Starter run on OpenRouter; Pro
    # routes direct to Anthropic (llm_pro_* below).
    #
    # Free's models: GLM 5.3 Flash text + Qwen3 VL 235B A22B Instruct vision, via
    # OpenRouter (+5.5% OpenRouter platform fee on top of list price). LLM_FREE_API_KEYS
    # stays empty until set: no key configured = no Free pool entries.
    llm_free_provider: str = Field(
        default="openrouter", validation_alias=_provider_env("LLM_FREE_PROVIDER")
    )
    llm_free_endpoint: str = Field(
        default="https://openrouter.ai/api/v1", validation_alias=_provider_env("LLM_FREE_ENDPOINT")
    )
    llm_free_api_keys: str = Field(default="", validation_alias=_provider_env("LLM_FREE_API_KEYS"))
    llm_free_text_model: str = Field(
        default="z-ai/glm-5.3-flash", validation_alias=_provider_env("LLM_FREE_TEXT_MODEL")
    )
    llm_free_vision_model: str = Field(
        default="qwen/qwen3-vl-235b-a22b-instruct",
        validation_alias=_provider_env("LLM_FREE_VISION_MODEL"),
    )
    llm_free_fallback_text_model: str = Field(
        default="", validation_alias=_provider_env("LLM_FREE_FALLBACK_TEXT_MODEL")
    )
    llm_free_fallback_vision_model: str = Field(
        default="", validation_alias=_provider_env("LLM_FREE_FALLBACK_VISION_MODEL")
    )
    llm_free_multimodal_model: str = Field(
        default="", validation_alias=_provider_env("LLM_FREE_MULTIMODAL_MODEL")
    )
    llm_free_fallback_multimodal_model: str = Field(
        default="", validation_alias=_provider_env("LLM_FREE_FALLBACK_MULTIMODAL_MODEL")
    )

    # Router behavior
    llm_router_cooldown_secs: int = 60
    llm_router_max_retries: int = 3
    # How long route_text/route_vision will block waiting for a cooled-down provider
    # to clear before giving up. Must be >= llm_router_cooldown_secs or every provider
    # hitting a flat (no Retry-After) 429 cooldown together will never be waited out.
    # Kept well under llm_router_total_budget_secs so it can't alone exhaust the budget.
    llm_router_wait_ceiling_secs: float = 20.0
    # Whole-request wall-clock ceiling across every attempt + wait in one route_text/
    # route_vision call. Deliberately under Render's free-tier proxy timeout (~100s) so
    # a degraded pool returns a real 502 with error_detail instead of Render's edge
    # dropping the connection first. See docs/TRD.md §13.3.
    llm_router_total_budget_secs: float = 75.0
    # Starter and Pro each get a fully independent, single-deployment provider
    # block — own endpoint, own keys, own text/vision models, own fallback
    # models — rather than pointing at one of the Free-pool providers above.
    # A tier with no llm_{tier}_endpoint/api_keys configured contributes no
    # pool entry, so that plan's compiles fall back to the Free pool.
    # Starter's models (docs/cost_model.md "LLM Provider Strategy") — Kimi K3 text +
    # Qwen3 VL vision, via OpenRouter. LLM_STARTER_API_KEYS stays empty until set: no
    # key configured = no Starter pool entries, so Starter compiles fall back to Free.
    llm_starter_provider: str = Field(
        default="openrouter", validation_alias=_provider_env("LLM_STARTER_PROVIDER")
    )
    llm_starter_endpoint: str = Field(
        default="https://openrouter.ai/api/v1", validation_alias=_provider_env("LLM_STARTER_ENDPOINT")
    )
    llm_starter_api_keys: str = Field(default="", validation_alias=_provider_env("LLM_STARTER_API_KEYS"))
    llm_starter_text_model: str = Field(
        default="moonshotai/kimi-k3", validation_alias=_provider_env("LLM_STARTER_TEXT_MODEL")
    )
    llm_starter_vision_model: str = Field(
        default="qwen/qwen3-vl-235b-a22b-instruct",
        validation_alias=_provider_env("LLM_STARTER_VISION_MODEL"),
    )
    llm_starter_fallback_text_model: str = Field(
        default="", validation_alias=_provider_env("LLM_STARTER_FALLBACK_TEXT_MODEL")
    )
    llm_starter_fallback_vision_model: str = Field(
        default="", validation_alias=_provider_env("LLM_STARTER_FALLBACK_VISION_MODEL")
    )
    llm_starter_multimodal_model: str = Field(
        default="", validation_alias=_provider_env("LLM_STARTER_MULTIMODAL_MODEL")
    )
    llm_starter_fallback_multimodal_model: str = Field(
        default="", validation_alias=_provider_env("LLM_STARTER_FALLBACK_MULTIMODAL_MODEL")
    )

    # Pro's models (docs/cost_model.md "LLM Provider Strategy") — Claude Sonnet 5 text +
    # Claude Opus 5.5 vision, direct to Anthropic (no OpenRouter fee). Routed through the
    # router's existing OpenAI-compatible path: Anthropic's own API exposes an
    # OpenAI-compatible endpoint at this base URL (Bearer auth, same request/response
    # shape as every other pooled provider) — no new auth style needed. Model-id strings
    # are placeholders; verify against Anthropic's live model catalog before deploy.
    # LLM_PRO_API_KEYS stays empty until set: no key = no Pro pool entries, falls back to Free.
    llm_pro_provider: str = Field(
        default="anthropic", validation_alias=_provider_env("LLM_PRO_PROVIDER")
    )
    llm_pro_endpoint: str = Field(
        default="https://api.anthropic.com/v1", validation_alias=_provider_env("LLM_PRO_ENDPOINT")
    )
    llm_pro_api_keys: str = Field(default="", validation_alias=_provider_env("LLM_PRO_API_KEYS"))
    llm_pro_text_model: str = Field(
        default="claude-sonnet-5", validation_alias=_provider_env("LLM_PRO_TEXT_MODEL")
    )
    llm_pro_vision_model: str = Field(
        default="claude-opus-5-5", validation_alias=_provider_env("LLM_PRO_VISION_MODEL")
    )
    llm_pro_fallback_text_model: str = Field(
        default="", validation_alias=_provider_env("LLM_PRO_FALLBACK_TEXT_MODEL")
    )
    llm_pro_fallback_vision_model: str = Field(
        default="", validation_alias=_provider_env("LLM_PRO_FALLBACK_VISION_MODEL")
    )
    llm_pro_multimodal_model: str = Field(
        default="", validation_alias=_provider_env("LLM_PRO_MULTIMODAL_MODEL")
    )
    llm_pro_fallback_multimodal_model: str = Field(
        default="", validation_alias=_provider_env("LLM_PRO_FALLBACK_MULTIMODAL_MODEL")
    )

    # Cashfree payment gateway. These intentionally do not use the SKILL_ prefix.
    cashfree_app_id: str = Field(default="", validation_alias="CASHFREE_APP_ID")
    cashfree_secret_key: str = Field(default="", validation_alias="CASHFREE_SECRET_KEY")
    cashfree_webhook_secret: str = Field(default="", validation_alias="CASHFREE_WEBHOOK_SECRET")
    cashfree_starter_plan_id: str = Field(default="", validation_alias="CASHFREE_STARTER_PLAN_ID")
    cashfree_pro_plan_id: str = Field(default="", validation_alias="CASHFREE_PRO_PLAN_ID")
    # Note: compile add-on packs are one-time purchases via Cashfree Payment
    # Links — they need no plan IDs, only the app id / secret key above.
    cashfree_env: str = Field(default="TEST", validation_alias="CASHFREE_ENV")  # TEST | PROD

    # conxa-execute's 6 monthly auto-refill token subscription tiers. One-time
    # token packs need no plan IDs (Payment Links are created ad hoc), only
    # recurring subscriptions require a pre-created Cashfree Plan object.
    cashfree_execute_sub_250k_plan_id: str = Field(default="", validation_alias="CASHFREE_SUB_250K_PLAN_ID")
    cashfree_execute_sub_500k_plan_id: str = Field(default="", validation_alias="CASHFREE_SUB_500K_PLAN_ID")
    cashfree_execute_sub_1m_plan_id: str = Field(default="", validation_alias="CASHFREE_SUB_1M_PLAN_ID")
    cashfree_execute_sub_2_5m_plan_id: str = Field(default="", validation_alias="CASHFREE_SUB_2_5M_PLAN_ID")
    cashfree_execute_sub_5m_plan_id: str = Field(default="", validation_alias="CASHFREE_SUB_5M_PLAN_ID")
    cashfree_execute_sub_10m_plan_id: str = Field(default="", validation_alias="CASHFREE_SUB_10M_PLAN_ID")

    @field_validator("environment", mode="before")
    @classmethod
    def _normalize_environment(cls, value: object) -> str:
        raw = str(value or "").strip().lower()
        if raw in ("prod", "production"):
            return "prod"
        if raw in ("", "dev", "development", "local"):
            return "dev"
        return raw

    @field_validator("package_bundle_root", mode="before")
    @classmethod
    def _strip_package_bundle_root(cls, value: object) -> str:
        return str(value or "").strip() or "skill_package"

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _strip_cors_allowed_origins(cls, value: object) -> str:
        return str(value or "").strip()

    @property
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.cors_allowed_origins.split(",") if item.strip()]

    # Legacy single-endpoint accessors — derived from the multi-provider pool so out-of-scope
    # callers (e.g. services/skill_pack/llm.py which does its own HTTP) keep working without
    # additional env vars. NOT user-facing BC; just a runtime adapter pointing at the first
    # enabled provider. Recorder+compile paths use the router directly and never read these.
    @property
    def llm_text_endpoint(self) -> str:
        providers = self.enabled_llm_providers()
        return providers[0].endpoint if providers else ""

    @property
    def llm_text_model(self) -> str:
        providers = self.enabled_llm_providers()
        return providers[0].text_model if providers else ""

    @property
    def llm_text_api_key(self) -> str:
        providers = self.enabled_llm_providers()
        return providers[0].api_key if providers else ""

    @property
    def llm_vision_endpoint(self) -> str:
        providers = [p for p in self.enabled_llm_providers() if p.vision_model]
        return providers[0].endpoint if providers else ""

    @property
    def llm_vision_model(self) -> str:
        providers = [p for p in self.enabled_llm_providers() if p.vision_model]
        return providers[0].vision_model if providers else ""

    @property
    def llm_vision_api_key(self) -> str:
        providers = [p for p in self.enabled_llm_providers() if p.vision_model]
        return providers[0].api_key if providers else ""

    @property
    def clerk_authorized_party_values(self) -> list[str]:
        return [item.strip() for item in self.clerk_authorized_parties.split(",") if item.strip()]

    def _split_api_keys(self, value: str) -> list[str]:
        """Parse comma-separated API keys, handling quotes and bearer prefixes."""
        keys: list[str] = []
        for item in str(value or "").split(","):
            key = item.strip().strip('"').strip("'").strip()
            if key.lower().startswith("bearer "):
                key = key[7:].strip()
            if key:
                keys.append(key)
        return keys

    def enabled_llm_providers(self) -> list[ProviderConfig]:
        """Load all enabled LLM providers with their API keys, returning a flat pool.

        Free, Starter, and Pro are three symmetric, single-deployment tiers (see
        _tier_provider_configs) — each its own provider label, endpoint, keys, and
        text/vision/fallback models, tagged pool="free"/"starter"/"pro". No endpoint/keys
        configured for a tier means that plan contributes no pool entries and its
        compiles fall back to the Free pool. (docs/PRD.md §11's compile_pool capability;
        docs/cost_model.md "LLM Provider Strategy" for the current per-tier models.)"""
        result: list[ProviderConfig] = []
        result.extend(self._tier_provider_configs(
            "free", self.llm_free_provider, self.llm_free_endpoint,
            self.llm_free_api_keys, self.llm_free_text_model, self.llm_free_vision_model,
            self.llm_free_fallback_text_model, self.llm_free_fallback_vision_model,
            self.llm_free_multimodal_model, self.llm_free_fallback_multimodal_model,
        ))
        result.extend(self._tier_provider_configs(
            "starter", self.llm_starter_provider, self.llm_starter_endpoint,
            self.llm_starter_api_keys, self.llm_starter_text_model, self.llm_starter_vision_model,
            self.llm_starter_fallback_text_model, self.llm_starter_fallback_vision_model,
            self.llm_starter_multimodal_model, self.llm_starter_fallback_multimodal_model,
        ))
        result.extend(self._tier_provider_configs(
            "pro", self.llm_pro_provider, self.llm_pro_endpoint,
            self.llm_pro_api_keys, self.llm_pro_text_model, self.llm_pro_vision_model,
            self.llm_pro_fallback_text_model, self.llm_pro_fallback_vision_model,
            self.llm_pro_multimodal_model, self.llm_pro_fallback_multimodal_model,
        ))
        return result

    def _tier_provider_configs(
        self,
        pool: str,
        provider: str,
        endpoint: str,
        api_keys_str: str,
        text_model: str,
        vision_model: str,
        fallback_text_model: str,
        fallback_vision_model: str,
        multimodal_model: str = "",
        fallback_multimodal_model: str = "",
    ) -> list[ProviderConfig]:
        """Build the pool entries for a single-deployment tier (Free/Starter/Pro): its own
        provider label, endpoint, keys, and text/vision/multimodal models, independent of
        every other tier. No endpoint/keys configured = no entries, so that tier's
        compiles fall back to the Free pool (or fail outright if Free itself is unconfigured)."""
        if not endpoint or not provider:
            return []
        return [
            ProviderConfig(
                provider=provider,
                endpoint=endpoint,
                api_key=key,
                text_model=text_model,
                vision_model=vision_model,
                pool=pool,
                fallback_text_model=fallback_text_model,
                fallback_vision_model=fallback_vision_model,
                multimodal_model=multimodal_model,
                fallback_multimodal_model=fallback_multimodal_model,
            )
            for key in self._split_api_keys(api_keys_str)
        ]

    @model_validator(mode="after")
    def _require_env_auth_consistency(self) -> "Settings":
        """Cross-check the environment label against the real auth posture.

        The old design inferred prod from a scatter of independent flags; nothing
        stopped ``environment=prod`` from running with auth off — a silent,
        dangerous misconfig (public cloud with no authentication). Enforce the one
        direction that matters: a prod-labeled process MUST have auth enabled.

        The reverse (auth on while labeled ``dev``) is intentionally allowed — a
        hosted dev *tier* runs real Clerk auth against its own dev instance. Bypass
        with ``SKILL_ALLOW_ENV_MISMATCH=1`` for the rare deliberate case.
        """
        if os.environ.get("SKILL_ALLOW_ENV_MISMATCH") == "1":
            return self
        if self.environment == "prod" and not self.auth_required:
            raise ValueError(
                "CONXA_ENV=prod but SKILL_AUTH_REQUIRED is false. Production must run "
                "with auth enabled. Set SKILL_AUTH_REQUIRED=true (and the Clerk/DB/"
                "Cashfree values main.py requires), or use CONXA_ENV=dev for local work."
            )
        return self

    @model_validator(mode="after")
    def _require_at_least_one_provider(self) -> "Settings":
        """Fail fast if no LLM providers are enabled with API keys.

        Tests and bootstrap scripts can bypass with SKILL_ALLOW_NO_PROVIDERS=1.
        """
        if os.environ.get("SKILL_ALLOW_NO_PROVIDERS") == "1":
            return self
        if not self.enabled_llm_providers():
            raise ValueError(
                "No LLM providers enabled. Set at least one LLM_{TIER}_API_KEYS "
                "(e.g. LLM_FREE_API_KEYS=sk-or-... for the Free/OpenRouter pool). "
                "See ROUTER_SETUP.md or .env.example for the full provider list."
            )
        return self

    @field_validator("llm_text_timeout_ms", mode="before")
    @classmethod
    def _enforce_min_text_timeout(cls, value: object) -> int:
        try:
            timeout = int(value)
        except (TypeError, ValueError):
            timeout = 20000
        return max(10000, timeout)

    @field_validator("llm_vision_timeout_ms", mode="before")
    @classmethod
    def _enforce_min_vision_timeout(cls, value: object) -> int:
        try:
            timeout = int(value)
        except (TypeError, ValueError):
            timeout = 120000
        return max(10000, timeout)

    @field_validator("llm_pack_timeout_ms", mode="before")
    @classmethod
    def _enforce_min_pack_timeout(cls, value: object) -> int:
        try:
            timeout = int(value)
        except (TypeError, ValueError):
            timeout = 600000
        return max(600000, timeout)

    @field_validator("llm_pack_max_attempts", mode="before")
    @classmethod
    def _normalize_llm_pack_max_attempts(cls, value: object) -> int:
        try:
            n = int(value)
        except (TypeError, ValueError):
            return 1
        return max(1, min(n, 10))

    @field_validator("llm_pack_structure_temperature", mode="before")
    @classmethod
    def _clamp_llm_pack_structure_temperature(cls, value: object) -> float:
        try:
            t = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(2.0, t))

    @field_validator("llm_pack_markdown_temperature", mode="before")
    @classmethod
    def _clamp_llm_pack_markdown_temperature(cls, value: object) -> float:
        try:
            t = float(value)
        except (TypeError, ValueError):
            return 0.15
        return max(0.0, min(2.0, t))

    @field_validator("llm_pack_top_p", mode="before")
    @classmethod
    def _normalize_llm_pack_top_p(cls, value: object) -> float | None:
        if value is None or value == "":
            return None
        try:
            t = float(value)
        except (TypeError, ValueError):
            return None
        if t <= 0.0 or t > 1.0:
            return None
        return t

    @field_validator("llm_pack_structure_max_tokens", mode="before")
    @classmethod
    def _normalize_llm_pack_structure_max_tokens(cls, value: object) -> int | None:
        if value is None or value == "":
            return None
        try:
            n = int(value)
        except (TypeError, ValueError):
            return None
        if n < 1:
            return None
        return min(n, 200_000)

    @field_validator("llm_pack_markdown_max_tokens", mode="before")
    @classmethod
    def _normalize_llm_pack_markdown_max_tokens(cls, value: object) -> int:
        try:
            n = int(value)
        except (TypeError, ValueError):
            return 4000
        return max(1, min(n, 200_000))


settings = Settings()
