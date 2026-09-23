"""Build a source-only submission ZIP via an explicit allowlist.

Excludes caches, venv, artifacts, secrets, model weights, and large datasets.
The ZIP is written to artifacts/alfagen_source.zip and its sha256 is printed.
"""
from __future__ import annotations

import hashlib
import os
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "artifacts" / "alfagen_source.zip"

# Explicit allowlist of source files and text configs/instructions.
# Directories are walked recursively; individual files are added as-is.
ALLOWLIST: list[str] = [
    "app",
    "configs",
    "scripts",
    "tests",
    "docs",
    "frontend",
    "pyproject.toml",
    "README.md",
    ".env.example",
    ".gitignore",
    "rules.md",
    "memory.md",
    "AGENTS.md",
    "kilo.jsonc",
    "master_prompt.md",
    "Dockerfile",
    "compose.yaml",
]

# Excluded path fragments (caches, venv, secrets, artifacts, weights, datasets).
EXCLUDE_FRAGMENTS: tuple[str, ...] = (
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".git",
    ".idea",
    ".vscode",
    "node_modules",
    "build",
    "dist",
    "target",
    "out",
    "bin",
    "obj",
    "coverage",
    "artifacts",
    ".env",
    ".egg-info",
    "*.pyc",
    "*.pyo",
    "*.log",
    ".DS_Store",
    "*.tsbuildinfo",
    "vite.config.js",
    "vite.config.d.ts",
    "model",
    "models",
    "weights",
    ".bin",
    ".pt",
    ".pth",
    ".onnx",
    ".h5",
    ".joblib",
    ".pkl",
    ".parquet",
    ".csv",
    ".jsonl",
    ".ndjson",
    ".dump",
    ".rdb",
    ".aof",
    ".zip",
    ".tar",
    ".gz",
    ".bz2",
    ".xz",
    ".7z",
    ".mp4",
    ".mp3",
    ".mov",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
)


def _excluded(rel: str) -> bool:
    parts = rel.split(os.sep)
    name = parts[-1]
    for frag in EXCLUDE_FRAGMENTS:
        if frag in parts:
            return True
        if frag.startswith("*") and name.endswith(frag[1:]):
            return True
        if frag == name:
            return True
    return False


def _collect() -> list[Path]:
    files: list[Path] = []
    for entry in ALLOWLIST:
        p = ROOT / entry
        if not p.exists():
            print(f"WARNING: allowlisted path missing: {entry}", file=sys.stderr)
            continue
        if p.is_dir():
            for root, dirs, names in os.walk(p):
                dirs[:] = [d for d in dirs if not _excluded(os.path.relpath(os.path.join(root, d), ROOT))]
                for n in names:
                    fp = Path(root) / n
                    rel = os.path.relpath(fp, ROOT)
                    if not _excluded(rel):
                        files.append(fp)
        else:
            rel = os.path.relpath(p, ROOT)
            if not _excluded(rel):
                files.append(p)
    return sorted(files)


def main() -> int:
    files = _collect()
    if not files:
        print("ERROR: no files collected", file=sys.stderr)
        return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as zf:
        for fp in files:
            rel = os.path.relpath(fp, ROOT)
            zf.write(fp, rel)

    sha = hashlib.sha256(OUT.read_bytes()).hexdigest()
    size = OUT.stat().st_size
    print(f"ZIP: {OUT}")
    print(f"FILES: {len(files)}")
    print(f"SIZE_BYTES: {size}")
    print(f"SHA256: {sha}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
