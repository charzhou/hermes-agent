"""The dashboard's Docker entry gate stays outside concurrent DB initialization."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading

import pytest

from hermes_cli import container_dashboard_startup as startup


@pytest.mark.platforms("posix")
def test_dashboard_waits_for_live_gateway_to_commit_initialization(tmp_path, monkeypatch):
    from gateway import control_socket
    from hermes_cli import service_manager

    home = tmp_path / "profile"
    home.mkdir()
    service = tmp_path / "gateway-default"
    service.mkdir()
    child = subprocess.Popen([sys.executable, "-u", "-c", """
import asyncio, os, sqlite3, sys
from pathlib import Path
from gateway.control_socket import GatewayControlServer
home = Path(sys.argv[1])
async def run():
    db = sqlite3.connect(home / 'state.db')
    db.execute('PRAGMA journal_mode=DELETE')
    db.execute('BEGIN IMMEDIATE')
    db.execute('CREATE TABLE startup_result (value TEXT)')
    db.execute("INSERT INTO startup_result VALUES ('committed')")
    state = {'pid': os.getpid(), 'gateway_state': 'starting'}
    server = GatewayControlServer(home, verb_handlers={'status': lambda: state})
    assert await server.start()
    print('initializing', flush=True)
    await asyncio.to_thread(sys.stdin.readline)
    db.commit()
    db.close()
    state['gateway_state'] = 'running'
    await asyncio.to_thread(sys.stdin.readline)
    await server.stop()
asyncio.run(run())
""", str(home)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env={**os.environ, "HERMES_HOME": str(home)})
    try:
        assert child.stdout.readline().strip() == "initializing"
        monkeypatch.setattr(service_manager, "_s6_run", lambda *args, **kwargs:
                            subprocess.CompletedProcess(args, 0, f"true true {child.pid}\n", ""))
        observed_starting = threading.Event()
        query = control_socket.query_gateway_control

        def observe(*args, **kwargs):
            status = query(*args, **kwargs)
            if status and status.get("gateway_state") == "starting":
                observed_starting.set()
            return status

        monkeypatch.setattr(control_socket, "query_gateway_control", observe)
        # A leftover running snapshot from an earlier boot cannot release the gate.
        (home / "gateway_state.json").write_text(json.dumps({"pid": child.pid - 1, "gateway_state": "running"}))
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(startup.wait_for_gateway, home, service, timeout=8)
            assert observed_starting.wait(5)
            assert not pending.done()
            child.stdin.write("commit\n")
            child.stdin.flush()
            assert pending.result(timeout=8) == "ready"
        with sqlite3.connect(home / "state.db") as db:
            assert db.execute("SELECT value FROM startup_result").fetchone() == ("committed",)
    finally:
        if child.poll() is None:
            child.stdin.write("finish\n")
            child.stdin.flush()
        try:
            child.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.communicate(timeout=5)
        assert child.returncode == 0


@pytest.mark.parametrize("scenario, expected", [
    ("stopped", "stopped"), ("missing", "skipped"), ("failed", "stopped"),
    ("timeout", "timeout"), ("foreign_pid", "timeout"), ("disabled", "skipped"),
])
def test_management_ui_starts_when_gateway_cannot_be_ready(tmp_path, monkeypatch, scenario, expected):
    from gateway import control_socket
    from hermes_cli import service_manager

    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    (home / "config.yaml").write_text("dashboard:\n  gateway_startup_wait_seconds: 0\n")
    observed = []
    wait = startup.wait_for_gateway
    monkeypatch.setattr(startup, "wait_for_gateway", lambda h, s, *, timeout: observed.append((h, timeout)))
    startup.main()
    assert observed == [(home, 0)]
    monkeypatch.setattr(startup, "wait_for_gateway", wait)

    service = tmp_path / "gateway-default"
    if scenario != "missing":
        service.mkdir()
    wanted = "false" if scenario == "stopped" else "true"
    monkeypatch.setattr(service_manager, "_s6_run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args, 0, f"true {wanted} 42\n", ""))
    monkeypatch.setattr(control_socket, "query_gateway_control", lambda *args, **kwargs: {
        "pid": 41 if scenario == "foreign_pid" else 42,
        "gateway_state": "startup_failed" if scenario == "failed" else
                         "running" if scenario == "foreign_pid" else "starting",
    })
    assert wait(home, service, timeout=0 if scenario == "disabled" else 0.1) == expected
    assert not (home / "state.db").exists()
