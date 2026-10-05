"""Credential resolution shared by the application and management-server launcher."""
import os
import tomllib
from pathlib import Path
from typing import Mapping


def sandbox_api_key(environ: Mapping[str, str] | None = None) -> str:
    """Explicit server TOML wins; otherwise use OPEN_SANDBOX_API_KEY.

    Launchers load the workspace .env first without replacing process environment.
    Never fall back to unauthenticated access or an unrelated legacy key name.
    """
    env = os.environ if environ is None else environ
    source = env.get("OPEN_SANDBOX_CONFIG_FILE")
    if source:
        with Path(source).expanduser().open("rb") as handle:
            key = tomllib.load(handle).get("server", {}).get("api_key")
    else:
        key = env.get("OPEN_SANDBOX_API_KEY")
    if not isinstance(key, str) or not key.strip():
        raise ValueError("Sandbox authentication requires a non-empty API key in the selected configuration source.")
    return key
