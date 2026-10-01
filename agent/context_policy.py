"""Runtime policy for the minimum context window used by Hermes.

The model metadata registry owns provider and model facts.  This module owns the
fork policy that decides when an explicitly configured small context window is
allowed, so upstream metadata changes do not have to carry fork-specific policy.
"""

from __future__ import annotations

from agent.model_metadata import MINIMUM_CONTEXT_LENGTH


SMALL_CONTEXT_MINIMUM_LENGTH = 32_000


def resolve_minimum_context_length(raw: object) -> int:
    """Return the configured context floor, preserving the 64K default."""
    if isinstance(raw, int) and not isinstance(raw, bool) and raw >= SMALL_CONTEXT_MINIMUM_LENGTH:
        return raw
    return MINIMUM_CONTEXT_LENGTH


def is_small_context_mode(context_length: int | None, minimum_context_length: int | None) -> bool:
    """Whether the runtime is using the explicit below-default context policy."""
    return (
        isinstance(context_length, int)
        and 0 < context_length < MINIMUM_CONTEXT_LENGTH
        and minimum_context_length == SMALL_CONTEXT_MINIMUM_LENGTH
    )
