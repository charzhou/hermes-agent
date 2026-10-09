# Hermes fork maintenance

This file records the intentional differences that must be reviewed when
bringing `upstream/main` into this fork. It is a maintenance index, not a
second source of truth for runtime behavior.

## Current ownership groups

| Fork-owned area | Main entry points | Sync rule |
| --- | --- | --- |
| Context floor policy | `agent/context_policy.py`, callers in `agent/`, `model_tools.py`, and `hermes_cli/` | Keep the 32K opt-in policy separate from upstream model metadata. If upstream adds an equivalent policy, compare behavior and remove the duplicate fork layer. |
| Feishu delivery behavior | `plugins/platforms/feishu/adapter_mentions.py`, `adapter_reply.py`, gateway delivery metadata and adapter plumbing | Preserve mention targeting, reply threading, and delivery metadata. Resolve conflicts by tracing the real gateway-to-adapter path and run Feishu gateway tests. |
| TUI/source integration | TUI gateway and desktop/TUI session source propagation | Preserve explicit session source/platform identity. Validate with the TUI gateway tests after any gateway session refactor. |
| OpenAI image compatibility | `plugins/image_gen/openai/__init__.py` and its focused tests | Use upstream's endpoint resolver for custom base URLs, named providers, and profile-scoped credentials. The earlier unused fork resolver has been removed. |
| Fork Docker dependency closure | `Dockerfile`, `docker/stage2-hook.sh`, `.github/workflows/docker-publish-fork.yml` | Fork images build every Linux-container-compatible optional extra into the sealed venv, test each native architecture, and publish the exact tested image bytes. The image provenance marker makes the baked environment authoritative, so stage2 skips PM generation refresh and first-boot installs. A commit tag (`sha-<commit>`) is always published; `prod` and `latest` advance only from a `prod` push. The cross-architecture KittenTTS/Torch CUDA closure remains excluded because its arm64 wheel set fails PM validation. Keep the official image's curated default unchanged. |
| Fork packaging/CI | Fork-owned Docker/workflow files | Keep deployment changes fork-specific unless upstream adopts the same workflow. |

## Upstream sync procedure

1. Fetch both remotes and record the merge base and the two branch tips.
2. Review the incoming merge with `git diff --stat` and `git diff --name-status`.
3. Search for each fork-owned symbol and path in the incoming tree. If upstream
   now provides the same behavior, delete the fork duplicate instead of adding
   another compatibility layer.
4. Resolve conflicts by ownership group. Do not change upstream runtime code
   merely to satisfy a fork-only test.
5. Run focused tests for every touched group, then `git diff --check` and the
   applicable `scripts/run_tests.sh` suite.
6. Before publishing, verify that the fork tip contains the intended merge and
   that the remote branch and peeled tags point at the expected commits.

Useful review commands:

```bash
git fetch upstream main
git diff --stat main...upstream/main
git diff --name-status main...upstream/main
git diff upstream/main..main
git log --oneline --decorate --graph --max-count=30 main upstream/main
```

The authoritative fork behavior remains in code and tests; update this index
when a customization is added, removed, or absorbed upstream.
