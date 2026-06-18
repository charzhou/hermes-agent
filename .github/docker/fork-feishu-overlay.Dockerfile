ARG BASE_IMAGE=hermes-agent:base
FROM ${BASE_IMAGE}

USER root
WORKDIR /opt/hermes

# Fork-only overlay: keep tracking the upstream Dockerfile while still
# preinstalling the Feishu SDK into the immutable runtime venv.
RUN python3 - <<'PY' > /tmp/fork-feishu-requirements.txt
import tomllib
from pathlib import Path

data = tomllib.loads(Path("/opt/hermes/pyproject.toml").read_text())
feishu_specs = data["project"]["optional-dependencies"].get("feishu")
if not feishu_specs:
    raise SystemExit("pyproject.toml is missing project.optional-dependencies.feishu")
for spec in feishu_specs:
    print(spec)
PY

RUN uv pip install --python /opt/hermes/.venv/bin/python \
        --no-cache \
        -r /tmp/fork-feishu-requirements.txt && \
    rm -f /tmp/fork-feishu-requirements.txt && \
    chown -R root:root /opt/hermes && \
    chmod -R a+rX /opt/hermes && \
    chmod -R a-w /opt/hermes
