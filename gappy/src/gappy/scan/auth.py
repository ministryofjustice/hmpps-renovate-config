"""Explicit GitHub token selection and runtime-only scanner transport."""

import base64
import json
import os
import re
from dataclasses import dataclass

from gappy.io import GappyError


@dataclass(frozen=True, repr=False)
class GhcrCredentials:
    username: str
    password: str

    def host_rules(self) -> str:
        return json.dumps(
            [
                {
                    "hostType": "docker",
                    "matchHost": "https://ghcr.io",
                    "username": self.username,
                    "password": self.password,
                }
            ]
        )

    def redact(self, text: str) -> str:
        text = text.replace(
            base64.b64encode(f"{self.username}:{self.password}".encode()).decode(), "[REDACTED]"
        )
        return redact(text, self.password).replace(json.dumps(self.password)[1:-1], "[REDACTED]")


def ghcr_credentials(username: str | None, variable: str | None) -> GhcrCredentials | None:
    if username is None and variable is None:
        return None
    if not username or variable is None:
        raise GappyError("GHCR authentication requires both --ghcr-username and --ghcr-token-env.")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", username) is None:
        raise GappyError("GHCR username must be a GitHub account name.")
    try:
        token = github_token(variable)
    except GappyError:
        raise GappyError(
            "Selected GHCR token environment variable is missing or invalid."
        ) from None
    assert token is not None
    return GhcrCredentials(username, token)


def github_token(variable: str | None) -> str | None:
    if variable is None:
        return None
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", variable) is None:
        raise GappyError("GitHub token option requires an environment variable name, not a token.")
    token = os.environ.get(variable)
    if not token or token != token.strip() or any(char.isspace() for char in token):
        raise GappyError("Selected GitHub token environment variable is missing, empty or invalid.")
    return token


def redact(text: str, token: str | None) -> str:
    if token is None:
        return text
    for value in (
        base64.b64encode(f"x-access-token:{token}".encode()).decode(),
        base64.b64encode(token.encode()).decode(),
        token,
    ):
        text = text.replace(value, "[REDACTED]")
    return text
