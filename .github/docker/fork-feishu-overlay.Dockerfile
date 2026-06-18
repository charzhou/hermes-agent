ARG BASE_IMAGE=hermes-agent:base
FROM ${BASE_IMAGE}

USER root
WORKDIR /opt/hermes

# Fork-only overlay: keep tracking the upstream Dockerfile while still
# preinstalling every runtime lazy dependency into the immutable venv.
RUN python3 - <<'PY' > /tmp/fork-feishu-requirements.txt
from tools.lazy_deps import LAZY_DEPS

specs = dict.fromkeys(
    spec
    for feature_specs in LAZY_DEPS.values()
    for spec in feature_specs
)
if not specs:
    raise SystemExit("tools.lazy_deps.LAZY_DEPS is empty")
for spec in specs:
    print(spec)
PY

RUN uv pip install --python /opt/hermes/.venv/bin/python \
        --no-cache \
        -r /tmp/fork-feishu-requirements.txt && \
    rm -f /tmp/fork-feishu-requirements.txt && \
    chown -R root:root /opt/hermes && \
    chmod -R a+rX /opt/hermes && \
    chmod -R a-w /opt/hermes
