#!/usr/bin/env python3
"""Build a complete, reproducible Fuwu update bundle from one Git commit."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import tomllib
from pathlib import Path
from typing import BinaryIO


ROOT = Path(__file__).resolve().parents[1]
VERSION_RE = re.compile(r"^[0-9]\.[0-9]\.[0-9]$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SOURCE_FILES = (".python-version", "manage.py", "pyproject.toml", "uv.lock")
SOURCE_DIRS = ("config", "fuwu_service", "release_api")
FRONTEND_DIST = "frontend/apps/web-antd/dist"


def run(
    command: list[str],
    *,
    cwd: Path,
    stdout: BinaryIO | None = None,
    env: dict[str, str] | None = None,
) -> None:
    subprocess.run(command, cwd=cwd, check=True, stdout=stdout, env=env)


def git_text(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def source_version(source_sha: str) -> str:
    raw = subprocess.check_output(
        ["git", "show", f"{source_sha}:pyproject.toml"], cwd=ROOT
    )
    return tomllib.loads(raw.decode("utf-8"))["project"]["version"]


def normalize_tarinfo(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    info.pax_headers = {}
    return info


def add_tree(archive: tarfile.TarFile, bundle_root: Path, relative: str) -> None:
    source = bundle_root / relative
    paths = [source]
    if source.is_dir():
        paths = [source]
        for current, directories, files in os.walk(source):
            directories.sort()
            files.sort()
            current_path = Path(current)
            paths.extend(current_path / name for name in directories)
            paths.extend(current_path / name for name in files)

    for path in paths:
        if path.is_symlink():
            raise ValueError(f"update bundle cannot contain symlinks: {path}")
        arcname = path.relative_to(bundle_root).as_posix()
        info = normalize_tarinfo(archive.gettarinfo(str(path), arcname=arcname))
        info.mode = stat.S_IMODE(path.stat().st_mode)
        if path.is_file():
            with path.open("rb") as stream:
                archive.addfile(info, stream)
        else:
            archive.addfile(info)


def validate_bundle(bundle_path: Path, expected: dict[str, object]) -> None:
    required = {
        "fuwu-update.json",
        *SOURCE_FILES,
        *SOURCE_DIRS,
        f"{FRONTEND_DIST}/index.html",
    }
    with tarfile.open(bundle_path, "r:gz") as archive:
        members = archive.getmembers()
        names = {member.name for member in members}
        if not required.issubset(names):
            missing = sorted(required - names)
            raise ValueError(f"update bundle is incomplete: {', '.join(missing)}")
        if any(member.issym() or member.islnk() for member in members):
            raise ValueError("update bundle cannot contain links")
        metadata = json.load(archive.extractfile("fuwu-update.json"))
        if metadata != expected:
            raise ValueError("update bundle metadata did not match the selected source")


def build(args: argparse.Namespace) -> Path:
    source_sha = args.source_sha
    if not SHA_RE.fullmatch(source_sha):
        raise ValueError("source SHA must be a full lowercase commit SHA")
    if git_text("rev-parse", "--verify", f"{source_sha}^{{commit}}") != source_sha:
        raise ValueError("source SHA did not resolve to the requested commit")
    if git_text("rev-parse", "HEAD") != source_sha:
        raise ValueError("build checkout HEAD does not match the requested source SHA")

    version = source_version(source_sha)
    if not VERSION_RE.fullmatch(version) or args.version != version:
        raise ValueError("requested version does not match the source pyproject.toml")
    if args.tag != f"v{version}":
        raise ValueError("release tag must exactly match v<source version>")

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_name = f"fuwu-update-{version}.tar.gz"
    bundle_path = output_dir / archive_name
    expected_metadata: dict[str, object] = {
        "formatVersion": 1,
        "project": "fuwu",
        "version": version,
        "tag": args.tag,
        "revision": source_sha,
    }

    with tempfile.TemporaryDirectory(prefix="fuwu-update-build-") as temporary:
        temporary_path = Path(temporary)
        checkout = temporary_path / "source"
        checkout.mkdir()
        source_tar = temporary_path / "source.tar"
        with source_tar.open("wb") as stream:
            run(["git", "archive", "--format=tar", source_sha], cwd=ROOT, stdout=stream)
        with tarfile.open(source_tar, "r:") as source_archive:
            source_archive.extractall(checkout, filter="data")

        for relative in (*SOURCE_FILES, *SOURCE_DIRS):
            if not (checkout / relative).exists():
                raise ValueError(f"required source path is missing: {relative}")

        frontend = checkout / "frontend"
        install_env = os.environ.copy()
        install_env["CI"] = "true"
        run(
            ["pnpm", "install", "--frozen-lockfile"],
            cwd=frontend,
            env=install_env,
        )
        run(["pnpm", "--filter", "@vben/web-antd", "build"], cwd=frontend)

        built_dist = checkout / FRONTEND_DIST
        if not (built_dist / "index.html").is_file():
            raise ValueError("frontend build did not produce dist/index.html")

        bundle_root = temporary_path / "bundle"
        bundle_root.mkdir()
        for relative in SOURCE_FILES:
            shutil.copy2(checkout / relative, bundle_root / relative)
        for relative in SOURCE_DIRS:
            shutil.copytree(checkout / relative, bundle_root / relative, symlinks=True)
        shutil.copytree(built_dist, bundle_root / FRONTEND_DIST, symlinks=True)
        (bundle_root / "fuwu-update.json").write_text(
            json.dumps(expected_metadata, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )

        with bundle_path.open("wb") as raw_stream:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw_stream, mtime=0) as gzip_stream:
                with tarfile.open(fileobj=gzip_stream, mode="w|", format=tarfile.PAX_FORMAT) as archive:
                    for relative in ("fuwu-update.json", *SOURCE_FILES, *SOURCE_DIRS, FRONTEND_DIST):
                        add_tree(archive, bundle_root, relative)

    validate_bundle(bundle_path, expected_metadata)
    digest = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
    (output_dir / "SHA256SUMS").write_text(
        f"{digest}  {archive_name}\n", encoding="ascii"
    )
    print(f"Built {bundle_path} ({bundle_path.stat().st_size} bytes)")
    print(f"SHA256 {digest}")
    return bundle_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    return parser.parse_args()


if __name__ == "__main__":
    try:
        build(parse_args())
    except (OSError, subprocess.CalledProcessError, KeyError, ValueError, tarfile.TarError) as error:
        raise SystemExit(f"update bundle build failed: {error}") from None
