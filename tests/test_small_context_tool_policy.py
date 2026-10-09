"""Small-context tool policy preserves I/O fallback without hiding programming errors."""

import logging

import pytest

import model_tools


def test_config_io_failure_disables_small_context_mode_with_traceback(monkeypatch, caplog):
    error = PermissionError("config unreadable")

    def unreadable_config():
        raise error

    monkeypatch.setattr(model_tools, "_active_model_config", unreadable_config)
    with caplog.at_level(logging.DEBUG, logger="model_tools"):
        assert model_tools._uses_small_context_tool_mode(32_000) is False

    record = next(r for r in caplog.records if "small-context tool mode" in r.getMessage())
    assert record.exc_info is not None
    assert record.exc_info[1] is error


def test_unexpected_config_failure_propagates(monkeypatch):
    def broken_config():
        raise RuntimeError("unexpected config failure")

    monkeypatch.setattr(model_tools, "_active_model_config", broken_config)
    with pytest.raises(RuntimeError, match="unexpected config failure"):
        model_tools._uses_small_context_tool_mode(32_000)
