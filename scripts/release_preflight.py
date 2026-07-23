#!/usr/bin/env python3
"""Fail closed before publishing a MESA release from a signed annotated tag."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
TAG_PATTERN = re.compile(r"^v(?P<version>\d+\.\d+\.\d+)$")


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _project_version() -> str | None:
    match = re.search(
        r'^version\s*=\s*"(?P<version>[^"]+)"\s*$',
        (ROOT / "pyproject.toml").read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    return match.group("version") if match else None


def validate(tag: str) -> list[str]:
    tag_match = TAG_PATTERN.fullmatch(tag)
    if tag_match is None:
        return ["tag must use the vMAJOR.MINOR.PATCH format"]

    errors: list[str] = []
    version = tag_match.group("version")
    if _project_version() != version:
        errors.append("tag version does not match pyproject.toml")

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if f"## [{version}]" not in changelog:
        errors.append("CHANGELOG.md has no matching release heading")

    if _run("git", "status", "--porcelain").stdout.strip():
        errors.append("working tree is not clean")
    if _run("git", "rev-parse", "--verify", f"refs/tags/{tag}").returncode:
        errors.append("tag does not exist locally")
        return errors
    if _run("git", "cat-file", "-t", tag).stdout.strip() != "tag":
        errors.append("tag must be annotated, not lightweight")
    if _run("git", "verify-tag", tag).returncode:
        errors.append("tag signature could not be verified by the local keyring")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", help="release tag, for example v0.7.0")
    args = parser.parse_args()
    errors = validate(args.tag)
    if errors:
        for error in errors:
            print(f"release preflight failed: {error}", file=sys.stderr)
        return 1
    print(f"release preflight passed: {args.tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
