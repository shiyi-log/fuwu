#!/usr/bin/env python3
"""Small Python entrypoint that verifies the Fuwu shell bootstrap locally."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


BASE_URL = "https://fuwu.1213.cc/api/downloads/heyue/bootstrap/install.sh"
MAX_BOOTSTRAP_BYTES = 512 * 1024


class BootstrapError(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, newurl):
        return None


def fetch() -> bytes:
    opener = build_opener(ProxyHandler({}), NoRedirect())
    request = Request(BASE_URL, headers={"Accept": "text/x-shellscript", "User-Agent": "heyue-fuwu-bootstrap"})
    try:
        with opener.open(request, timeout=45) as response:
            if response.status != 200:
                raise BootstrapError("Fuwu bootstrap fetch failed.")
            length = response.headers.get("Content-Length")
            if length is None or int(length) <= 0 or int(length) > MAX_BOOTSTRAP_BYTES:
                raise BootstrapError("Fuwu bootstrap response size is invalid.")
            body = response.read(MAX_BOOTSTRAP_BYTES + 1)
            reported = response.headers.get("X-Fuwu-SHA256", "").lower()
    except BootstrapError:
        raise
    except (HTTPError, OSError, URLError, ValueError) as error:
        raise BootstrapError("Fuwu bootstrap fetch failed.") from error
    if len(body) != int(length) or hashlib.sha256(body).hexdigest() != reported:
        raise BootstrapError("Fuwu bootstrap response digest does not match.")
    if not body.startswith(b"#!/usr/bin/env bash\n"):
        raise BootstrapError("Fuwu bootstrap response is invalid.")
    return body


def main() -> int:
    try:
        body = fetch()
        with tempfile.TemporaryDirectory(prefix="heyue-fuwu-bootstrap-") as temporary:
            script = Path(temporary) / "install.sh"
            script.write_bytes(body)
            os.chmod(script, 0o700)
            return subprocess.run(["/bin/bash", str(script), *sys.argv[1:]], check=False).returncode
    except BootstrapError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
