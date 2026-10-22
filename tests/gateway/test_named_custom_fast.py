"""Discord /fast must follow named custom providers selected through /model."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
import hermes_yaml as yaml

import gateway.run as gateway_run
from agent.fast_mode import STATIC_TIERS, begin_turn, effective_request_overrides
from gateway.config import GatewayConfig, Platform
from gateway.platforms.event import MessageEvent
from gateway.session import SessionSource, SessionStore


@pytest.fixture
def named_custom_session(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("TEST_NAMED_RELAY_KEY", "test-relay-key")
    monkeypatch.setattr(gateway_run, "_hermes_home", home)
    config = {
        "model": {"default": "gpt-5.4", "provider": "tegical-openai"},
        "providers": {
            "tegical-openai": {
                "name": "Tegical OpenAI",
                "api": "https://relay.example/v1",
                "key_env": "TEST_NAMED_RELAY_KEY",
                "default_model": "gpt-6-astra",
                "models": ["gpt-6-astra"],
                "extra_body": {"text": {"verbosity": "low"}},
            },
        },
    }
    (home / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    # Only catalog/context discovery is substituted; provider selection and credentials are real.
    monkeypatch.setattr("agent.models_dev.fetch_models_dev", dict)
    monkeypatch.setattr("hermes_cli.models.fetch_api_models", lambda *_a, **_kw: ["gpt-6-astra"])
    monkeypatch.setattr("agent.model_metadata._query_local_context_length", lambda *_a, **_kw: None)

    runner = object.__new__(gateway_run.GatewayRunner)
    runner.config = GatewayConfig()
    runner.adapters = {}
    runner._voice_mode = {}
    runner._session_model_overrides = {}
    runner._pending_model_notes = {}
    runner._agent_cache = {}
    runner._agent_cache_lock = None
    runner._session_db = None
    runner._service_tier = None
    runner._ephemeral_system_prompt = "Keep the conversation's cached prompt."
    runner._prefill_messages = [{"role": "system", "content": "Pinned prefix"}]
    runner.session_store = SessionStore(home / "sessions", runner.config)
    source = SessionSource(
        platform=Platform.DISCORD, chat_id="discord-thread", chat_type="thread",
        thread_id="discord-thread", parent_chat_id="discord-channel", user_id="discord-user",
    )
    return runner, source


@pytest.mark.asyncio
@pytest.mark.parametrize("command, tier", [
    ("on", "priority"), ("ultrafast", "ultrafast"), ("auto", "auto"), ("cold", "cold"),
])
async def test_named_custom_model_switch_and_fast_reach_next_turn(named_custom_session, command, tier):
    runner, source = named_custom_session
    prompt = runner._ephemeral_system_prompt
    prefill = deepcopy(runner._prefill_messages)
    switched = await runner._handle_model_command(MessageEvent(
        text="/model gpt-6-astra --provider tegical-openai --session", source=source,
    ))
    assert "gpt-6-astra" in switched
    session_key = runner._session_key_for_source(source)
    model, runtime_before = runner._resolve_session_agent_runtime(source=source)
    overrides_before = deepcopy(runtime_before["request_overrides"])

    response = await runner._handle_fast_command(MessageEvent(text=f"/fast {command}", source=source))

    assert runner._resolve_session_service_tier(session_key=session_key) == tier, response
    model, runtime = runner._resolve_session_agent_runtime(source=source)
    route = runner._resolve_turn_agent_config("continue", model, runtime)
    assert route["model"] == "gpt-6-astra"
    assert route["runtime"]["provider"] == "tegical-openai"
    assert route["runtime"]["base_url"] == "https://relay.example/v1"
    assert route["runtime"]["api_key"] == "test-relay-key"
    assert route["runtime"]["api_mode"] == runtime_before["api_mode"]
    if tier in STATIC_TIERS:
        assert route["request_overrides"] == {**overrides_before, "service_tier": tier}
    else:
        assert route["request_overrides"] == overrides_before
        agent = SimpleNamespace(
            model=route["model"], service_tier=tier, request_overrides=route["request_overrides"],
            **route["runtime"],
        )
        begin_turn(agent, [])
        assert effective_request_overrides(agent) == {**overrides_before, "service_tier": "priority"}
        assert route["request_overrides"] == overrides_before
    assert runtime["request_overrides"] == overrides_before
    assert overrides_before == {"extra_body": {"text": {"verbosity": "low"}}}
    assert runner._ephemeral_system_prompt == prompt
    assert runner._prefill_messages == prefill
