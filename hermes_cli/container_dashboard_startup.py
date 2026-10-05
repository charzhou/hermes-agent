"""Order the Docker dashboard after the supervised gateway's startup work."""
from __future__ import annotations

import logging
import math
from pathlib import Path
import subprocess
import time

logger = logging.getLogger(__name__)
DEFAULT_WAIT_SECONDS = 90.0


def wait_for_gateway(home: Path, service: Path, *, timeout: float) -> str:
    """Wait for this s6 child, never a persisted running record from an earlier boot.

    This runs before the dashboard CLI imports plugins or opens SessionDB.
    A stopped/absent gateway and a failed/timed-out boot leave the dashboard
    accessible for management. No database is opened by the readiness probe.
    """
    from gateway.control_socket import query_gateway_control
    from hermes_cli.service_manager import _s6_run

    if not service.is_dir() or timeout <= 0:
        return "skipped"
    deadline = time.monotonic() + timeout
    waiting = False
    while time.monotonic() < deadline:
        try:
            state = _s6_run("s6-svstat", "-o", "up,wantedup,pid", str(service),
                            timeout=min(5.0, deadline - time.monotonic()))
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("Cannot inspect supervised gateway; starting dashboard: %s", exc)
            return "unavailable"
        if state.returncode:
            logger.warning("Cannot inspect supervised gateway; starting dashboard: %s", state.stderr.strip())
            return "unavailable"
        up, wanted, pid = state.stdout.split()
        if wanted != "true":
            return "stopped"
        if not waiting:
            logger.info("Waiting up to %.0fs for gateway initialization before starting dashboard", timeout)
            waiting = True
        if up == "true":
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            status = query_gateway_control(home, "status", timeout=min(1.0, remaining))
            if status and str(status.get("pid")) == pid:
                phase = status.get("gateway_state")
                if phase in {"running", "degraded"}:
                    logger.info("Gateway PID %s initialized; starting dashboard", pid)
                    return "ready"
                if phase in {"startup_failed", "stopped", "draining"}:
                    return "stopped"
        time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))
    logger.warning("Gateway initialization wait expired; starting dashboard for management")
    return "timeout"


def main() -> None:
    from hermes_constants import get_hermes_home
    from hermes_cli.config_effective import load_user_config_effective

    logging.basicConfig(level=logging.INFO, format="[dashboard-startup] %(message)s")
    config = load_user_config_effective()
    dashboard = config.get("dashboard", {})
    value = dashboard.get("gateway_startup_wait_seconds", DEFAULT_WAIT_SECONDS)
    timeout = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else DEFAULT_WAIT_SECONDS
    if not math.isfinite(timeout) or timeout < 0:
        timeout = DEFAULT_WAIT_SECONDS
    wait_for_gateway(get_hermes_home(), Path("/run/service/gateway-default"), timeout=timeout)


if __name__ == "__main__":
    main()
