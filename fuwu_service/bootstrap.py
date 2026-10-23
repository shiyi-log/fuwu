"""Static anonymous bootstrap assets served by Fuwu."""

from __future__ import annotations

import hashlib
from pathlib import Path


BOOTSTRAP_SCRIPT_PATH = Path(__file__).with_name("heyue_bootstrap.sh")
PYTHON_BOOTSTRAP_SCRIPT_PATH = Path(__file__).with_name("heyue_bootstrap.py")
MAX_BOOTSTRAP_BYTES = 512 * 1024


def heyue_bootstrap_script() -> bytes:
    """Read the repository-owned bootstrap script without accepting a path."""
    try:
        body = BOOTSTRAP_SCRIPT_PATH.read_bytes()
    except OSError as error:
        raise RuntimeError("Heyue bootstrap asset is unavailable") from error
    if not body or len(body) > MAX_BOOTSTRAP_BYTES or b"\x00" in body:
        raise RuntimeError("Heyue bootstrap asset is invalid")
    if not body.startswith(b"#!/usr/bin/env bash\n"):
        raise RuntimeError("Heyue bootstrap asset is invalid")
    return body


def heyue_bootstrap_sha256() -> str:
    return hashlib.sha256(heyue_bootstrap_script()).hexdigest()


def heyue_python_bootstrap_script() -> bytes:
    try:
        body = PYTHON_BOOTSTRAP_SCRIPT_PATH.read_bytes()
    except OSError as error:
        raise RuntimeError("Heyue Python bootstrap asset is unavailable") from error
    if not body or len(body) > MAX_BOOTSTRAP_BYTES or b"\x00" in body:
        raise RuntimeError("Heyue Python bootstrap asset is invalid")
    if not body.startswith(b"#!/usr/bin/env python3\n"):
        raise RuntimeError("Heyue Python bootstrap asset is invalid")
    return body


def heyue_python_bootstrap_sha256() -> str:
    return hashlib.sha256(heyue_python_bootstrap_script()).hexdigest()
