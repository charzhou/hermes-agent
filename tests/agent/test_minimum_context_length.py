"""Configuration policy for Hermes' supported model context floor."""

from agent.model_metadata import MINIMUM_CONTEXT_LENGTH
from agent.context_policy import is_small_context_mode, resolve_minimum_context_length


def test_resolve_minimum_context_length_accepts_explicit_32k():
    assert resolve_minimum_context_length(32_000) == 32_000


def test_resolve_minimum_context_length_preserves_default_for_invalid_values():
    assert resolve_minimum_context_length(31_999) == MINIMUM_CONTEXT_LENGTH
    assert resolve_minimum_context_length(True) == MINIMUM_CONTEXT_LENGTH
    assert resolve_minimum_context_length("32000") == MINIMUM_CONTEXT_LENGTH


def test_small_context_mode_requires_the_active_fork_floor():
    assert is_small_context_mode(32_000, 32_000)
    assert not is_small_context_mode(64_000, 32_000)
    assert not is_small_context_mode(32_000, 64_000)
