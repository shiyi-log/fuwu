"""Check the Fuwu product version without touching Vben's internal packages."""
from pathlib import Path
import json
import re
import tomllib


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    if not re.fullmatch(r"[0-9]\.[0-9]\.[0-9]", version):
        raise SystemExit("Fuwu version must contain three single-digit components")
    app = root / "frontend/apps/web-antd"
    values = {"web-antd/package.json": json.loads((app / "package.json").read_text())["version"]}
    for name in (".env.example", ".env.development", ".env.production"):
        match = re.search(r"^VITE_APP_VERSION=(.+)$", (app / name).read_text(), re.M)
        values[f"web-antd/{name}"] = match.group(1).strip() if match else None
    match = re.search(r"^FUWU_IMAGE_TAG=(.+)$", (root / ".env.example").read_text(), re.M)
    values[".env.example"] = match.group(1).strip() if match else None
    defaults = re.findall(r"image: fuwu-(?:api|frontend):\$\{FUWU_IMAGE_TAG:-([^}]+)\}",
                          (root / "docker-compose.yml").read_text())
    if len(defaults) != 2 or any(value != version for value in defaults):
        raise SystemExit("Compose image defaults do not match the product version")
    mismatches = [name for name, value in values.items() if value != version]
    if mismatches:
        raise SystemExit("Product version mismatch in: " + ", ".join(mismatches))
    if not (root / "docs/releases" / f"{version}.md").is_file():
        raise SystemExit("Release notes are missing")
    print(f"Fuwu {version}: product versions and release notes agree")


if __name__ == "__main__":
    main()
