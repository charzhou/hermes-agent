"""Named custom fast tiers use the owning profile's configured endpoint without credentials."""

from copy import deepcopy

import pytest
import hermes_yaml as yaml

from hermes_cli.models import resolve_fast_mode_overrides
from hermes_cli.runtime_provider import frozen_runtime_config
from hermes_constants import reset_hermes_home_override, set_hermes_home_override


@pytest.mark.parametrize("style", ["providers", "custom_providers"])
def test_named_custom_fast_requires_the_profiles_configured_route(tmp_path, monkeypatch, style):
    home = tmp_path / "owner"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    endpoint = "https://relay.example/Tenant-A/v1"
    entry = {
        "name": "Tegical OpenAI", "base_url": endpoint,
        "key_env": "UNUSED_FAST_KEY", "key_cmd": "must-not-run",
    }
    config = {style: {"tegical-openai": entry} if style == "providers" else [entry]}
    (home / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")

    def no_credentials(*_args, **_kwargs):
        raise AssertionError("Fast eligibility must not resolve credentials")

    monkeypatch.setattr("hermes_cli.runtime_provider_custom.get_secret_str", no_credentials)
    monkeypatch.setattr("agent.command_token_source.build_command_token_provider", no_credentials)
    monkeypatch.setattr("agent.models_dev.fetch_models_dev", dict)

    def tier(provider="tegical-openai", base_url=endpoint, model="gpt-6-astra", **kwargs):
        return resolve_fast_mode_overrides(model, provider=provider, base_url=base_url, **kwargs)

    for provider in ("tegical-openai", "Tegical OpenAI"):
        assert tier(provider) == {"service_tier": "priority"}
        assert tier(provider, tier="ultrafast") == {"service_tier": "ultrafast"}
    assert tier(base_url="https://RELAY.example:443/Tenant-A/v1/") == {"service_tier": "priority"}
    assert tier(base_url=None) == {"service_tier": "priority"}  # A pending picker has no runtime URL yet.
    for wrong_endpoint in (
        "https://other.example/Tenant-A/v1", "https://relay.example/Tenant-B/v1",
        "https://relay.example/tenant-a/v1", endpoint + "?tenant=other",
    ):
        assert tier(base_url=wrong_endpoint) is None
    assert tier(provider="unknown-relay") is None
    for model in ("claude-opus-5", "grok-4.6", "gpt-5.3-codex"):
        assert tier(model=model) is None
    for provider in ("openrouter", "copilot", "nous"):
        assert tier(provider=provider) is None

    frozen = deepcopy(config)
    frozen_entry = frozen[style]["tegical-openai"] if style == "providers" else frozen[style][0]
    frozen_entry["base_url"] = "https://frozen.example/v1"
    with frozen_runtime_config(frozen):
        assert tier(base_url="https://frozen.example/v1") == {"service_tier": "priority"}
        assert tier() is None
    assert tier() == {"service_tier": "priority"}

    if style == "providers":
        for invalid_entry in ({**entry, "enabled": False}, {"name": entry["name"]}):
            with frozen_runtime_config({"providers": {"tegical-openai": invalid_entry}}):
                assert tier() is None
        with frozen_runtime_config({"providers": {"openrouter": entry}}):
            assert tier(provider="openrouter") is None

    other_home = tmp_path / "other-profile"
    other_home.mkdir()
    (other_home / "config.yaml").write_text("providers: {}\n", encoding="utf-8")
    token = set_hermes_home_override(other_home)
    try:
        assert tier() is None
    finally:
        reset_hermes_home_override(token)
    assert tier() == {"service_tier": "priority"}
