"""Fail closed when release candidates contain private or local-only artifacts."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


SENSITIVE_SUFFIXES = {
    ".db",
    ".gguf",
    ".key",
    ".log",
    ".pem",
    ".sqlite",
    ".sqlite3",
}
SENSITIVE_NAMES = {".env", "llama.json"}
PRIVATE_DIRECTORY_NAMES = {
    "chat-data",
    "private-chat",
    "private-data",
    "real-chat-data",
    "real-data",
}
CONTENT_EXEMPT = {"release_check.py", "test_release.py"}
CONTENT_PATTERNS = (
    (
        "absolute local path",
        re.compile(r"\b[A-Za-z]:\\+(?:Users|Models|llama|Program Files)\\+", re.I),
    ),
    ("API key", re.compile(r"\bsk-[A-Za-z0-9_-]{12,}")),
    ("private key", re.compile(r"BEGIN (?:RSA |OPENSSH )?PRIVATE KEY")),
)


def _release_candidates(root: Path) -> list[Path]:
    """Return tracked plus non-ignored untracked files when *root* is a Git tree."""

    try:
        top_level = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        )
        if Path(top_level.stdout.strip()).resolve() != root:
            raise subprocess.CalledProcessError(1, top_level.args)
        process = subprocess.run(
            [
                "git", "-C", str(root), "ls-files", "--cached", "--others",
                "--exclude-standard", "-z", "--", ".",
            ],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return [
            path for path in root.rglob("*")
            if ".git" not in path.relative_to(root).parts
        ]
    names = process.stdout.decode("utf-8", errors="surrogateescape").split("\0")
    return [root / Path(name) for name in names if name]


def _looks_sensitive_path(relative: Path) -> str | None:
    lower_parts = {part.lower() for part in relative.parts}
    if lower_parts & PRIVATE_DIRECTORY_NAMES:
        return "real/private data directory"
    name = relative.name.lower()
    if name in SENSITIVE_NAMES or name.startswith(".env."):
        return "secret/local configuration"
    if any(name.endswith(suffix) or f"{suffix}-" in name for suffix in SENSITIVE_SUFFIXES):
        return "runtime data/model/log artifact"
    if name.endswith((".local.json", ".local.toml", ".local.yaml", ".local.yml")):
        return "local configuration"
    return None


def scan_tree(root: Path) -> list[str]:
    """Return deterministic blockers from actual Git release candidates."""

    root = Path(root).resolve()
    problems: list[str] = []
    for path in sorted(_release_candidates(root), key=lambda item: item.as_posix().lower()):
        relative = path.relative_to(root)
        path_problem = _looks_sensitive_path(relative)
        if path_problem:
            problems.append(f"{relative.as_posix()}: {path_problem}")
            if path.is_dir():
                continue
        if (
            not path.is_file()
            or path.name in CONTENT_EXEMPT
            or relative.parts[:2] == ("docs", "superpowers")
        ):
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for label, pattern in CONTENT_PATTERNS:
            if pattern.search(content):
                problems.append(f"{relative.as_posix()}: {label}")
    return sorted(set(problems))


def main() -> int:
    problems = scan_tree(Path(__file__).resolve().parent)
    if problems:
        print("release check failed:")
        for problem in problems:
            print(f"- {problem}")
        return 1
    print("release check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
