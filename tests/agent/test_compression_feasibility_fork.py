"""The fork's small context floor applies to upstream's route-aware feasibility probe."""
from types import SimpleNamespace


def test_small_context_probe_runs_at_floor_and_after_route_change(monkeypatch):
    from agent import conversation_compression_feasibility as feasibility

    route = ['first']
    calls = []
    agent = SimpleNamespace(minimum_context_length=32_000, context_compressor=SimpleNamespace())
    monkeypatch.setattr(feasibility, 'compression_route_identity', lambda: tuple(route))
    monkeypatch.setattr(feasibility, '_probe', lambda _agent: calls.append(route[0]))

    feasibility.ensure_compression_feasibility_checked(agent, 31_999)
    assert calls == []
    feasibility.ensure_compression_feasibility_checked(agent, 32_000)
    assert calls == ['first']
    route[0] = 'second'
    feasibility.ensure_compression_feasibility_checked(agent, 32_000)
    assert calls == ['first', 'second']
