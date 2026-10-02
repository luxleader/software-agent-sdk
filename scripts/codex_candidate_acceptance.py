"""Check the canonical Docker image over real HTTP on a hosted Linux runner.

Uses no account credentials. OAuth lifecycle checks replace only OpenAI's
transport; a separate unmocked run initiates/cancels a real OpenAI challenge.
"""

import json
import subprocess
import time
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "acceptance-results"
RESULTS.mkdir(exist_ok=True)
CONFIG = RESULTS / "config.json"
CONFIG.write_text(
    json.dumps(
        {
            "session_api_keys": ["candidate-test-session"],
            "secret_key": "candidate-fixture-encryption-only",
            "conversations_path": "/candidate-state/conversations",
            "workspace_path": "/candidate-state/workspace",
            "enable_vscode": False,
            "preload_tools": False,
        }
    )
)
REPORT = {"real_account_credentials_used": False, "real_model_turn": False}


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


def launch(fixture):
    args = [
        "run",
        "-d",
        "--name",
        "codex17372-server",
        "-p",
        "127.0.0.1:18001:8000",
        "-v",
        "codex17372-state:/candidate-state",
        "-v",
        f"{CONFIG}:/candidate-config.json:ro",
        "-v",
        f"{ROOT / 'scripts'}:/candidate-scripts:ro",
        "-e",
        "OPENHANDS_AGENT_SERVER_CONFIG_PATH=/candidate-config.json",
        "-e",
        "OH_PERSISTENCE_DIR=/candidate-state/persist",
        "-e",
        "CODEX_HOME=/candidate-state/codex",
        "-e",
        "OH_DISABLE_TELEMETRY=true",
        "-e",
        "OPENHANDS_SUPPRESS_BANNER=1",
        "codex-oauth-validation:test",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
    ]
    if fixture:
        args.extend(
            [
                "--extra-python-path",
                "/candidate-scripts",
                "--import-modules",
                "codex_candidate_fixture",
            ]
        )
    docker(*args)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            if httpx.get("http://127.0.0.1:18001/health").is_success:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise RuntimeError("Candidate server did not become healthy")


def stop():
    docker("rm", "-f", "codex17372-server")


def request(client, method, endpoint, **kwargs):
    response = client.request(method, "/api/acp/codex/auth/" + endpoint, **kwargs)
    response.raise_for_status()
    for private in (
        "fixture-private-id",
        "fixture-refresh-not-a-credential",
        "fixture-rotated-not-a-credential",
        "id_token",
        "access_token",
        "refresh_token",
    ):
        assert private not in response.text, "Credential appeared in an HTTP response"
    return response.json()


try:
    docker("volume", "create", "codex17372-state")
    docker(
        "run",
        "--rm",
        "--user",
        "0",
        "--entrypoint",
        "chown",
        "-v",
        "codex17372-state:/candidate-state",
        "codex-oauth-validation:test",
        "10001:10001",
        "/candidate-state",
    )
    launch(True)
    with httpx.Client(base_url="http://127.0.0.1:18001", timeout=60) as client:
        assert client.get("/api/acp/codex/auth/status").status_code == 401
        REPORT["session_auth_enforced"] = True
        client.headers["X-Session-API-Key"] = "candidate-test-session"
        assert not request(client, "GET", "status")["connected"]
        challenge = request(client, "POST", "device/start")
        handle = {"device_code": challenge["device_code"]}
        assert request(client, "POST", "device/poll", json=handle)["state"] == "pending"
        assert request(client, "POST", "device/poll", json=handle)["connected"]
        REPORT["docker_fixture_login"] = True
        time.sleep(3)
        assert request(client, "GET", "status")["connected"]
        REPORT["fixture_token_refresh"] = True
        # Only synthetic test tokens are inspected, inside their owned container.
        stored = docker(
            "exec", "codex17372-server", "cat", "/candidate-state/persist/secrets.json"
        )
        assert "fixture-rotated-not-a-credential" not in stored
        REPORT["encrypted_persistence"] = True
        assert (
            docker(
                "exec",
                "codex17372-server",
                "/agent-server/.venv/bin/python",
                "-c",
                "from importlib.metadata import version; "
                "from openhands.agent_server.codex_auth import CodexAuthService; "
                "print(version('openhands-sdk'))",
            )
            == "1.50.1"
        )
        REPORT["packaged_imports"] = True
        stop()
        launch(True)
        assert request(client, "GET", "status")["connected"]
        REPORT["restart_persistence"] = True
        challenge = request(client, "POST", "device/start")
        handle = {"device_code": challenge["device_code"]}
        assert not request(client, "POST", "logout")["connected"]
        assert not request(client, "POST", "device/poll", json=handle)["connected"]
        assert not request(client, "GET", "status")["connected"]
        REPORT["logout_cancels_pending_and_deletes_credentials"] = True
        stop()
        launch(False)
        # Actual OpenAI egress; the one-time code/handle never enters logs/artifacts.
        challenge = request(client, "POST", "device/start")
        REPORT["real_openai_device_start"] = True
        assert (
            request(
                client,
                "POST",
                "device/cancel",
                json={"device_code": challenge["device_code"]},
            )["state"]
            == "cancelled"
        )
        REPORT["real_openai_device_cancel"] = True
        REPORT["scope"] = (
            "Hosted Linux runner, canonical source-minimal Docker image, "
            "real session-authenticated HTTP. Lifecycle uses synthetic OAuth "
            "transport; separate live OpenAI initiation/cancel. "
            "No remote human account consent or model turn."
        )
        REPORT["passed"] = True
finally:
    (RESULTS / "container.json").write_text(json.dumps(REPORT, indent=2) + "\n")
    subprocess.run(["docker", "rm", "-f", "codex17372-server"], capture_output=True)
    subprocess.run(["docker", "volume", "rm", "codex17372-state"], capture_output=True)

print(json.dumps(REPORT, indent=2))
