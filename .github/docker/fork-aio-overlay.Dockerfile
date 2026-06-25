# syntax=docker/dockerfile:1

ARG BASE_IMAGE=hermes-agent:base
FROM ${BASE_IMAGE} AS hermes_base
FROM ghcr.io/agent-infra/sandbox:1.11.0

USER root

# Preserve the full AIO runtime from the donor image and layer Hermes on top.
# This avoids reconstructing /opt/gem from the public repo, which does not
# ship the full runtime tree.
COPY --from=hermes_base /opt/hermes /opt/hermes
COPY --from=hermes_base /etc/s6-overlay /etc/s6-overlay
COPY --from=hermes_base /etc/cont-init.d /etc/cont-init.d
COPY --from=hermes_base /command /command
COPY --from=hermes_base /package /package
COPY --from=hermes_base /init /init
COPY --from=hermes_base /usr/local/bin/uv /usr/local/bin/uvx /usr/local/bin/
COPY --from=hermes_base /usr/bin/docker /usr/bin/docker

RUN --mount=type=bind,from=hermes_base,source=/root/.cache/uv,target=/mnt/hermes-uv-cache,readonly \
    --mount=type=bind,from=hermes_base,source=/usr,target=/mnt/hermes-usr,readonly \
    --mount=type=cache,target=/root/.cache/uv <<'EOF'
set -eu

mkdir -p /root/.cache/uv
cp -a /mnt/hermes-uv-cache/. /root/.cache/uv/ 2>/dev/null || true

mkdir -p /usr/include
cp -a /mnt/hermes-usr/include/olm /usr/include/

find /mnt/hermes-usr/lib -maxdepth 5 \
  \( -name 'libolm.so*' -o -path '*/pkgconfig/olm.pc' -o -path '*/cmake/Olm' \) \
  | while IFS= read -r src; do
    rel="${src#/mnt/hermes-usr/}"
    dest="/${rel}"
    mkdir -p "$(dirname "$dest")"
    cp -a "$src" "$dest"
  done

if ! getent group hermes >/dev/null 2>&1; then
    groupadd -g 10000 hermes
fi
if ! id -u hermes >/dev/null 2>&1; then
    useradd -M -u 10000 -g 10000 -d /opt/data -s /bin/bash hermes
fi

cd /opt/hermes
rm -rf .venv hermes_agent.egg-info
export UV_PYTHON=/opt/python3.12/bin/python3.12
uv sync --frozen --no-install-project --extra all --extra messaging --extra anthropic --extra bedrock --extra azure-identity --extra hindsight --extra matrix
uv pip install --python /opt/hermes/.venv/bin/python --no-cache-dir --no-deps -e /opt/hermes
/opt/hermes/.venv/bin/python - <<'PY'
import hermes_cli  # noqa: F401
import hermes_constants  # noqa: F401
PY

mkdir -p /opt/data /opt/data/workspace /opt/data/logs /opt/data/aio
rm -rf /home/gem /home/hermes
ln -s /opt/data /home/gem
ln -s /opt/data /home/hermes

mkdir -p /etc/s6-overlay/s6-rc.d/aio-runtime/dependencies.d
mkdir -p /etc/s6-overlay/s6-rc.d/user/contents.d

cat > /etc/s6-overlay/s6-rc.d/aio-runtime/type <<'TYPE'
longrun
TYPE

cat > /etc/s6-overlay/s6-rc.d/aio-runtime/dependencies.d/base <<'DEP'
base
DEP

cat > /etc/s6-overlay/s6-rc.d/user/contents.d/aio-runtime <<'CONTENTS'
aio-runtime
CONTENTS

cat > /opt/hermes/docker/aio-runtime-wrapper.sh <<'WRAP'
#!/command/with-contenv sh
set -eu

is_true() {
  case "${1:-}" in
    1|true|TRUE|True|yes|YES|Yes|on|ON|On) return 0 ;;
    *) return 1 ;;
  esac
}

if ! is_true "${HERMES_AIO_ENABLE:-}"; then
  exit 0
fi

api_enabled=false
if is_true "${HERMES_AIO_API_ENABLE:-}" || is_true "${HERMES_AIO_TERMINAL_ENABLE:-}"; then
  api_enabled=true
fi

export AIO_USER=hermes
export USER_UID="$(id -u hermes)"
export USER_GID="$(id -g hermes)"
export WORKSPACE="${HERMES_AIO_WORKSPACE:-/opt/data/workspace}"
export PUBLIC_PORT="${HERMES_AIO_PUBLIC_PORT:-18080}"
export LOG_DIR="${HERMES_AIO_LOG_DIR:-/opt/data/logs/aio}"

mkdir -p "$WORKSPACE" /opt/data/Downloads "$LOG_DIR" /opt/data/aio
chown -R hermes:hermes /opt/data /opt/data/Downloads "$WORKSPACE" "$LOG_DIR" /opt/data/aio 2>/dev/null || true

if is_true "${HERMES_AIO_BROWSER_ENABLE:-}"; then
  export DISABLE_BROWSER=false
  export DISABLE_VNC=false
  export DISABLE_MCP_BROWSER=false
else
  export DISABLE_BROWSER=true
  export DISABLE_VNC=true
  export DISABLE_MCP_BROWSER=true
  rm -f /opt/gem/nginx/nginx.ui_browser.conf /opt/gem/nginx/nginx.gembrowser_compat.conf
fi

if is_true "${HERMES_AIO_CODE_SERVER_ENABLE:-}"; then
  export DISABLE_CODE_SERVER=false
else
  export DISABLE_CODE_SERVER=true
  rm -f /opt/gem/nginx/nginx.code_server.conf
fi

if ! is_true "${HERMES_AIO_TERMINAL_ENABLE:-}"; then
  rm -f /opt/gem/nginx/nginx.ui_terminal.conf
fi

if [ "$api_enabled" != true ]; then
  rm -f /opt/gem/nginx/nginx.python_srv.conf /opt/gem/nginx/nginx.mcp_hub.conf
fi

exec /opt/gem/run.sh
WRAP

cat > /etc/s6-overlay/s6-rc.d/aio-runtime/run <<'RUNSCRIPT'
#!/command/with-contenv sh
set -eu

case "${HERMES_AIO_ENABLE:-}" in
  1|true|TRUE|True|yes|YES|Yes|on|ON|On) ;;
  *) exit 0 ;;
esac

exec /opt/hermes/docker/aio-runtime-wrapper.sh
RUNSCRIPT

cat > /etc/s6-overlay/s6-rc.d/aio-runtime/finish <<'FINISH'
#!/command/with-contenv sh

case "${HERMES_AIO_ENABLE:-}" in
  1|true|TRUE|True|yes|YES|Yes|on|ON|On) exit 0 ;;
  *) exit 125 ;;
esac
FINISH

chmod 0755 /opt/hermes/docker/aio-runtime-wrapper.sh
chmod 0755 /etc/s6-overlay/s6-rc.d/aio-runtime/run
chmod 0755 /etc/s6-overlay/s6-rc.d/aio-runtime/finish
chown -R root:root /opt/hermes
chmod -R a+rX /opt/hermes
chmod -R a-w /opt/hermes
ln -sf /init /usr/bin/tini
EOF

ENV HERMES_WEB_DIST=/opt/hermes/hermes_cli/web_dist
ENV HERMES_TUI_DIR=/opt/hermes/ui-tui
ENV HERMES_HOME=/opt/data
ENV HERMES_WRITE_SAFE_ROOT=/opt/data
ENV HERMES_DISABLE_LAZY_INSTALLS=1
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/hermes/.playwright
ENV PATH="/opt/hermes/bin:/opt/hermes/.venv/bin:/opt/data/.local/bin:${PATH}"

VOLUME [ "/opt/data" ]

ENTRYPOINT [ "/init", "/opt/hermes/docker/main-wrapper.sh" ]
CMD [ ]
