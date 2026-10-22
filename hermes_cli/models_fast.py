"""Route eligibility shared by fast-mode commands and request builders."""

from typing import Optional
from urllib.parse import urlparse


def fast_mode_route_supported(
    model_id: Optional[str], provider: Optional[str], base_url: Optional[str],
) -> bool:
    """Allow fast-mode params on first-party routes and custom OpenAI-compatible routes."""
    from agent.model_metadata import is_grok_46_family
    from hermes_cli.models import _is_anthropic_fast_model, _is_openai_fast_model, normalize_provider

    provider_id = normalize_provider(provider)
    if provider_id == "custom" or provider_id.startswith("custom:"):
        # A custom endpoint is an explicit operator choice. Hermes cannot verify
        # its billing semantics, so only eligible OpenAI models opt in here.
        return _is_openai_fast_model(model_id)

    if _is_anthropic_fast_model(model_id):
        allowed = {"anthropic": "api.anthropic.com"}
    elif is_grok_46_family(str(model_id or "")):
        allowed = {"xai": "api.x.ai"}
    else:
        allowed = {
            "openai": "api.openai.com",
            "openai-api": "api.openai.com",
            "openai-codex": "chatgpt.com",
        }
    if provider and provider_id not in allowed:
        from hermes_cli.runtime_provider_custom import named_custom_provider_matches_endpoint

        return _is_openai_fast_model(model_id) and named_custom_provider_matches_endpoint(provider, base_url)
    host = (urlparse(str(base_url or "")).hostname or "").lower()
    return not host or host in allowed.values()
