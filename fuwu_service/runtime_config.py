"""Safe project-root configuration for fuwu bootstrap settings.

The checked-in configuration contains only non-secret defaults. Secrets such as
an initial administrator password are read from an environment variable named
by the configuration and are never written to the file, database, logs, or API
responses.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Mapping

from .core import ReleaseError

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT_DIR / "fuwu.config.json"
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")


def config_path(value: str | Path | None = None) -> Path:
    if value is None:
        value = os.environ.get("FUWU_CONFIG_FILE") or DEFAULT_CONFIG_PATH
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path.resolve()


def load_config(value: str | Path | None = None) -> dict[str, Any]:
    path = config_path(value)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ReleaseError("config_not_found", "fuwu configuration file was not found", 503) from None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ReleaseError("invalid_config", "fuwu configuration file is invalid", 503) from None
    if not isinstance(raw, dict):
        raise ReleaseError("invalid_config", "fuwu configuration file is invalid", 503)
    return raw


def superadmin_bootstrap_config(value: str | Path | None = None) -> dict[str, str]:
    data = load_config(value)
    raw = data.get("superadmin")
    if not isinstance(raw, dict):
        raise ReleaseError("invalid_config", "superadmin configuration is invalid", 503)
    username = raw.get("username")
    display_name = raw.get("displayName", raw.get("display_name", username))
    password_env = raw.get("passwordEnv", raw.get("password_env", "FUWU_SUPERADMIN_PASSWORD"))
    if not isinstance(username, str) or not isinstance(display_name, str) or not isinstance(password_env, str):
        raise ReleaseError("invalid_config", "superadmin configuration is invalid", 503)
    username = username.strip().casefold()
    display_name = display_name.strip()
    password_env = password_env.strip()
    if not username or not display_name or not _ENV_NAME.fullmatch(password_env):
        raise ReleaseError("invalid_config", "superadmin configuration is invalid", 503)
    return {"username": username, "displayName": display_name, "passwordEnv": password_env}


def github_credentials_file(value: str | Path | None = None) -> Path:
    """Resolve the private GitHub credential file from the root config.

    An explicit ``FUWU_GITHUB_CREDENTIALS_FILE`` environment value wins so a
    container can mount secrets outside the source tree.  The checked-in
    config only contains a relative, non-secret default path.
    """
    env_value = os.environ.get("FUWU_GITHUB_CREDENTIALS_FILE")
    if env_value:
        path = Path(env_value).expanduser()
        return path if path.is_absolute() else ROOT_DIR / path
    data = load_config(value)
    raw = data.get("github", {})
    if not isinstance(raw, dict):
        raise ReleaseError("invalid_config", "GitHub configuration is invalid", 503)
    configured = raw.get("credentialFile", raw.get("credential_file", "secrets/github_accounts.json"))
    if not isinstance(configured, str) or not configured.strip():
        raise ReleaseError("invalid_config", "GitHub credential file configuration is invalid", 503)
    path = Path(configured).expanduser()
    return path if path.is_absolute() else ROOT_DIR / path


def bootstrap_from_config(database: str | Path | None = None, *, config: str | Path | None = None,
                          env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Create the configured first administrator using an environment secret.

    This operation is intentionally explicit (the ``bootstrap-config`` CLI
    command). It refuses to replace or modify an existing account database.
    """
    from .accounts import AccountStore

    env = os.environ if env is None else env
    settings = superadmin_bootstrap_config(config)
    password = env.get(settings["passwordEnv"])
    if not isinstance(password, str) or not password:
        raise ReleaseError("superadmin_password_required", "superadmin password environment variable is required", 400)
    with AccountStore(database) as store:
        return store.bootstrap_superadmin(settings["username"], password, settings["displayName"])
