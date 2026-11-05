"""Completion retries keep their receipt after upstream folds queued results."""
import pytest

from tests.gateway.test_completion_fold import _discord_session


@pytest.mark.asyncio
async def test_retry_of_an_individual_or_folded_completion_does_not_fold_again(tmp_path, monkeypatch):
    authority, _human, complete, queued = await _discord_session(tmp_path, monkeypatch)
    try:
        await complete('proc_a')
        await complete('proc_a')
        assert len(queued()) == 2
        await complete('proc_b')
        before = queued()[-1]
        await complete('proc_a')
        await complete('proc_b')
        after = queued()
        assert len(after) == 2
        assert after[-1]['admission_id'] == before['admission_id']
        assert after[-1]['payload'] == before['payload']
    finally:
        authority.db.close()
