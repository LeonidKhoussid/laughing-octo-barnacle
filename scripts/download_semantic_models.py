#!/usr/bin/env python3
"""Explicit preparation step for pinned local models; never called by the API."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import os
import tempfile
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def prepare(destination: Path, *, verify_only: bool = False) -> None:
    manifest = json.loads((ROOT / "configs" / "semantic-models.json").read_text())
    for name, spec in manifest["models"].items():
        for filename, asset in spec["files"].items():
            target = destination / name / filename
            if target.is_file() and sha256(target) == asset["sha256"]:
                print(f"Verified {name}/{filename}")
                continue
            if verify_only:
                raise RuntimeError(f"Missing or invalid model asset: {name}/{filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".download-", delete=False) as output:
                    temporary = Path(output.name)
                    digest = hashlib.sha256()
                    size = 0
                    with urlopen(asset["url"], timeout=90) as response:
                        while block := response.read(1024 * 1024):
                            size += len(block)
                            if size > asset["bytes"]:
                                raise RuntimeError("Model asset exceeds pinned size")
                            digest.update(block)
                            output.write(block)
                    output.flush()
                    os.fsync(output.fileno())
                if size != asset["bytes"] or digest.hexdigest() != asset["sha256"]:
                    raise RuntimeError("Model asset failed checksum verification")
                temporary.replace(target)
                print(f"Installed {name}/{filename}")
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, default=ROOT / "models" / "semantic")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    try:
        prepare(args.model_dir, verify_only=args.verify_only)
    except Exception:
        # Paths and fixed filenames are safe above; do not print network or
        # credential-bearing exception details from a proxy/environment.
        raise SystemExit("Model preparation failed. Check network access, space and pinned assets.") from None


if __name__ == "__main__":
    main()
