"""Offline OAuth transport only; all application routes and storage stay real."""

import base64
import json
import time

from openhands.agent_server.codex_auth import OpenAICodexOAuthProvider
from openhands.sdk.llm.auth.openai import DeviceCode


def jwt(claims):
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"e30.{payload}.fixture-signature"


async def start(self):
    self.fixture_polls = 0
    return DeviceCode(
        "https://auth.openai.com/codex/device", "DEMO-ONLY", "fixture-private-id", 1
    )


async def poll(self, _challenge):
    self.fixture_polls += 1
    if self.fixture_polls == 1:
        return None
    return {
        "id_token": jwt(
            {"https://api.openai.com/auth": {"chatgpt_account_id": "fixture"}}
        ),
        "access_token": jwt({"exp": int(time.time()) + 2}),
        "refresh_token": "fixture-refresh-not-a-credential",
    }


async def refresh(_self, _refresh_token):
    return {
        "id_token": jwt(
            {"https://api.openai.com/auth": {"chatgpt_account_id": "fixture"}}
        ),
        "access_token": jwt({"exp": int(time.time()) + 3600}),
        "refresh_token": "fixture-rotated-not-a-credential",
    }


OpenAICodexOAuthProvider.start = start
OpenAICodexOAuthProvider.poll = poll
OpenAICodexOAuthProvider.refresh = refresh
