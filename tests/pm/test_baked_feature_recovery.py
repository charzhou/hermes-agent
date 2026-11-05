"""An image's feature inventory and the user's opt-outs both survive overlay recovery."""

def test_refresh_uses_baked_feature_inventory_without_reenabling_declined_extra(tmp_path, monkeypatch):
    import pm.client
    import pm.install
    from pm import paths
    from pm.environments import runtime_facts_path
    from pm.extras import record_declined_extras
    from pm.lock import Facts
    from pm.recovery import refresh_dependencies

    root = tmp_path / 'core'
    root.mkdir()
    (root / 'pyproject.toml').write_text(
        '[project]\nname="core"\nversion="1"\n[project.optional-dependencies]\n'
        'all=[]\nfal=[]\nfeishu=[]\n', encoding='utf-8')
    store = tmp_path / 'tools'
    store.mkdir()
    monkeypatch.setattr(paths, 'repo_root', lambda: root)
    monkeypatch.setattr(paths, 'store_root', lambda: store)
    monkeypatch.setattr('pm.features.read_features', lambda: ['all', 'fal', 'feishu'])
    monkeypatch.setattr(pm.install, 'venv_is_current', lambda **kwargs: True)
    Facts(runtime_facts_path(root)).record_state('venv', 'old', ['all'])
    record_declined_extras(add=['feishu'], project_root=root)
    calls = []
    monkeypatch.setattr(pm.client, 'sync_venv', lambda extras=None, **kwargs: calls.append((extras, kwargs)))

    assert refresh_dependencies(root) == 'restored fal'
    assert calls == [(['fal'], {'explicit': True, 'automatic': ['fal']})]
