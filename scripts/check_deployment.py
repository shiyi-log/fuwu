#!/usr/bin/env python3
"""Exercise the shipped Nginx configuration without publishing any host ports.

Requires Docker and openssl. Uses disposable containers/network and a mock
upstream; does not access service data, SMTP, AWS, or production configuration.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]

UPSTREAM = r'''
from http.server import BaseHTTPRequestHandler, HTTPServer
import hashlib, json
DATA = b"fuwu-proxy-acceptance\n"
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def do_GET(self):
        if self.path == "/api/probe":
            body = json.dumps({"scheme": self.headers.get("X-Forwarded-Proto")}).encode()
            kind, cache = "application/json", "no-store"
        elif self.path in ("/releases/demo/v0.1.0/asset.tar.gz", "/api/downloads/demo/0.1.0/asset.tar.gz"):
            body, kind, cache = DATA, "application/octet-stream", "public, max-age=31536000, immutable"
        elif self.path == "/releases/demo/stable/latest.json":
            body, kind, cache = b'{"version":"0.1.0"}', "application/json", "no-store"
        else:
            self.send_error(404); return
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Fuwu-SHA256", hashlib.sha256(body).hexdigest())
        self.end_headers()
        if self.command != "HEAD": self.wfile.write(body)
    do_HEAD = do_GET
HTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
'''

CLIENT = r'''
import hashlib, json, ssl, urllib.error, urllib.request
context = ssl.create_default_context(cafile="/fixtures/cert.pem")
def get(url, headers=None, method="GET"):
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}, method=method), context=context, timeout=5)
def scheme(url, headers=None):
    with get(url + "/api/probe", headers) as response:
        return json.load(response)["scheme"]
# A client cannot claim HTTPS when connecting directly to the inner HTTP proxy.
assert scheme("http://frontend") == "http"
for value in ("https", "http, https", "https, http", "HTTPS"):
    assert scheme("http://frontend", {"X-Forwarded-Proto": value}) == "http", value
# The trusted edge overwrites headers on both actual HTTP and actual HTTPS.
assert scheme("http://edge", {"X-Forwarded-Proto": "https"}) == "http"
assert scheme("https://edge", {"X-Forwarded-Proto": "http"}) == "https"
for base in ("http://frontend", "https://edge"):
    for path in ("/releases/demo/v0.1.0/asset.tar.gz", "/api/downloads/demo/0.1.0/asset.tar.gz"):
        with get(base + path) as response:
            body = response.read()
            assert body == b"fuwu-proxy-acceptance\n"
            assert response.headers["Content-Type"] == "application/octet-stream"
            assert int(response.headers["Content-Length"]) == len(body)
            assert response.headers["X-Fuwu-SHA256"] == hashlib.sha256(body).hexdigest()
            assert "immutable" in response.headers["Cache-Control"]
        with get(base + path, method="HEAD") as response:
            assert response.read() == b""
            assert int(response.headers["Content-Length"]) == len(body)
    with get(base + "/releases/demo/stable/latest.json") as response:
        assert json.load(response)["version"] == "0.1.0"
        assert response.headers["Cache-Control"] == "no-store"
    try:
        get(base + "/releases/demo/unknown")
    except urllib.error.HTTPError as error:
        assert error.code == 404
    else:
        raise AssertionError("unknown release path fell back to the SPA")
print("PASS: trusted TLS, forged-header rejection, both download routes, HEAD, cache and SHA256")
'''

REAL_CLIENT = r'''
import json, ssl, time, urllib.error, urllib.request
context = ssl.create_default_context(cafile="/fixtures/cert.pem")
def post(base, extra=None):
    headers = {"Content-Type": "application/json", **(extra or {})}
    request = urllib.request.Request(base + "/api/auth/password-reset/code", data=b'{"email":"acceptance-unregistered@example.com"}', headers=headers)
    try:
        response = urllib.request.urlopen(request, context=context, timeout=5)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, json.load(response)
for attempt in range(30):
    try:
        with urllib.request.urlopen("http://realapi:8000/healthz", timeout=2) as response:
            assert response.status == 200
        break
    except (OSError, AssertionError):
        if attempt == 29: raise
        time.sleep(0.5)
for base, header in (("http://realfrontend", {}), ("http://realfrontend", {"X-Forwarded-Proto": "https"}), ("http://realedge", {"X-Forwarded-Proto": "https"})):
    status, body = post(base, header)
    assert status == 400 and body["code"] == "secure_transport_required", (status, body)
status, body = post("https://realedge", {"X-Forwarded-Proto": "http"})
assert status == 200 and body["code"] == 0, (status, body)
print("PASS: real Fuwu API rejects HTTP/forged headers and accepts the trusted HTTPS recovery route")
'''

EDGE = r'''
server {
  listen 80;
  listen 443 ssl;
  ssl_certificate /fixtures/cert.pem;
  ssl_certificate_key /fixtures/key.pem;
  resolver 127.0.0.11 valid=1s ipv6=off;
  location / {
    set $upstream http://frontend:80;
    proxy_pass $upstream;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
  }
}
'''


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and result.returncode:
        raise RuntimeError(f"{args[0]} {args[1]} failed: {result.stderr.strip()}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nginx-image", default="nginx:1.27-alpine")
    parser.add_argument("--python-image", default="python:3.13-slim")
    parser.add_argument("--api-image", help="Also test a built Fuwu API image with a new disposable SQLite database")
    args = parser.parse_args()
    name = "fuwu-proxy-check-" + uuid.uuid4().hex[:10]
    containers: list[str] = []
    network_created = False
    with tempfile.TemporaryDirectory(prefix=name) as directory:
        fixture = Path(directory)
        (fixture / "upstream.py").write_text(UPSTREAM)
        (fixture / "client.py").write_text(CLIENT)
        (fixture / "edge.conf").write_text(EDGE)
        run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
            "-keyout", str(fixture / "key.pem"), "-out", str(fixture / "cert.pem"),
            "-subj", "/CN=edge", "-addext", "subjectAltName=DNS:edge,DNS:realedge")
        os.chmod(fixture / "key.pem", 0o600)
        try:
            run("docker", "network", "create", "--internal", name)
            network_created = True
            def launch(suffix: str, image: str, extra: list[str], command: list[str] | None = None) -> str:
                container = name + "-" + suffix
                containers.append(container)
                run("docker", "run", "-d", "--name", container, "--network", name,
                    "--network-alias", suffix, "-v", f"{fixture}:/fixtures:ro", *extra,
                    image, *(command or []))
                return container
            launch("api", args.python_image, [], ["python", "/fixtures/upstream.py"])
            edge = launch("edge", args.nginx_image, ["-v", f"{fixture / 'edge.conf'}:/etc/nginx/conf.d/default.conf:ro"])
            ip = json.loads(run("docker", "inspect", edge).stdout)[0]["NetworkSettings"]["Networks"][name]["IPAddress"]
            frontend = launch("frontend", args.nginx_image, [
                "-e", f"FUWU_TRUSTED_PROXY_CIDRS={ip}/32",
                "-v", f"{ROOT / 'frontend/nginx.conf'}:/etc/nginx/conf.d/default.conf:ro",
                "-v", f"{ROOT / 'frontend/scripts/deploy/15-fuwu-trusted-proxies.sh'}:/docker-entrypoint.d/15-fuwu-trusted-proxies.sh:ro",
            ])
            # Bounded readiness check; every container remains on an isolated Docker network.
            for attempt in range(30):
                result = run("docker", "exec", frontend, "wget", "-q", "-O", "/dev/null", "http://127.0.0.1/healthz", check=False)
                if result.returncode == 0:
                    break
                if attempt == 29:
                    raise RuntimeError("isolated Nginx did not become healthy")
                time.sleep(0.5)
            run("docker", "exec", frontend, "nginx", "-t")
            client = name + "-client"
            containers.append(client)
            result = run("docker", "run", "--rm", "--name", client, "--network", name,
                         "-v", f"{fixture}:/fixtures:ro", args.python_image, "python", "/fixtures/client.py")
            print(result.stdout.strip())
            if args.api_image:
                launch("realapi", args.api_image, [
                    "-e", "FUWU_HOST=0.0.0.0", "-e", "FUWU_PORT=8000",
                    "-e", "FUWU_DATABASE=/data/acceptance.sqlite3",
                    "-e", "FUWU_STAGING_DIR=/data/staging", "-e", "FUWU_STORAGE=memory",
                    "-e", "FUWU_TRUST_X_FORWARDED_PROTO=1",
                    "-e", "DJANGO_SECRET_KEY=" + secrets.token_hex(32),
                ], ["sh", "/app/scripts/start_api.sh"])
                (fixture / "real-nginx.conf").write_text((ROOT / "frontend/nginx.conf").read_text().replace("api:8000", "realapi:8000"))
                (fixture / "real-edge.conf").write_text(EDGE.replace("frontend:80", "realfrontend:80"))
                real_edge = launch("realedge", args.nginx_image, ["-v", f"{fixture / 'real-edge.conf'}:/etc/nginx/conf.d/default.conf:ro"])
                real_ip = json.loads(run("docker", "inspect", real_edge).stdout)[0]["NetworkSettings"]["Networks"][name]["IPAddress"]
                launch("realfrontend", args.nginx_image, [
                    "-e", f"FUWU_TRUSTED_PROXY_CIDRS={real_ip}/32",
                    "-v", f"{fixture / 'real-nginx.conf'}:/etc/nginx/conf.d/default.conf:ro",
                    "-v", f"{ROOT / 'frontend/scripts/deploy/15-fuwu-trusted-proxies.sh'}:/docker-entrypoint.d/15-fuwu-trusted-proxies.sh:ro",
                ])
                (fixture / "real-client.py").write_text(REAL_CLIENT)
                real_client = name + "-real-client"
                containers.append(real_client)
                result = run("docker", "run", "--rm", "--name", real_client, "--network", name,
                             "-v", f"{fixture}:/fixtures:ro", args.python_image, "python", "/fixtures/real-client.py")
                print(result.stdout.strip())
            # Invalid entries cannot inject Nginx directives into the trust include.
            result = run("docker", "exec", "-e", "FUWU_TRUSTED_PROXY_CIDRS=0.0.0.0/0;evil", frontend,
                         "sh", "/docker-entrypoint.d/15-fuwu-trusted-proxies.sh", check=False)
            if result.returncode == 0:
                raise RuntimeError("invalid trust configuration was accepted")
            print("PASS: invalid trusted-proxy configuration rejected; no host ports published")
        finally:
            for container in reversed(containers):
                run("docker", "rm", "-f", container, check=False)
            if network_created:
                run("docker", "network", "rm", name, check=False)


if __name__ == "__main__":
    main()
