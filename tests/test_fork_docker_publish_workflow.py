"""Tests for the fork-specific Docker publish workflow.

This workflow exists specifically so forks can publish their own container
images without editing the upstream-only docker-publish.yml.
"""

from __future__ import annotations

import pathlib

import pytest
import yaml


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


class TestForkDockerPublishWorkflow:
    WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "docker-publish-fork.yml"

    def test_workflow_exists(self):
        assert self.WORKFLOW_PATH.exists(), (
            f"Fork Docker publish workflow missing: {self.WORKFLOW_PATH}"
        )

    def test_workflow_yaml_is_valid(self):
        content = self.WORKFLOW_PATH.read_text(encoding="utf-8")
        try:
            parsed = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            pytest.fail(f"docker-publish-fork.yml is not valid YAML: {exc}")
        assert isinstance(parsed, dict)
        assert "jobs" in parsed

    def test_has_manual_dispatch_and_main_push(self):
        parsed = yaml.safe_load(self.WORKFLOW_PATH.read_text(encoding="utf-8"))
        triggers = parsed.get("on", parsed.get(True))
        assert "workflow_dispatch" in triggers
        assert "push" in triggers
        assert triggers["push"]["branches"] == ["main"]

    def test_uses_fork_image_variable_and_ghcr(self):
        content = self.WORKFLOW_PATH.read_text(encoding="utf-8")
        assert "FORK_IMAGE_NAME" in content
        assert "ghcr.io" in content
        assert "docker/login-action" in content
        assert "docker/build-push-action" in content
