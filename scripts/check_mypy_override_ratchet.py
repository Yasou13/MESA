#!/usr/bin/env python3
"""Refuse expansion of MESA's tracked progressive mypy exemptions.

The script intentionally uses only the Python standard library so it can run
on every supported CI interpreter, including Python 3.10.  Removing an entry
or restoring a strict flag is allowed; adding a production exemption is not.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_PROGRESSIVE_MARKER = "# ---- Progressive overrides for modules not yet remediated ----"
_NEXT_OVERRIDE_MARKER = "# Pydantic's runtime validators"


def validate(config_path: Path, baseline_path: Path) -> list[str]:
    config = config_path.read_text(encoding="utf-8")
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))

    try:
        section = config.split(_PROGRESSIVE_MARKER, 1)[1].split(
            _NEXT_OVERRIDE_MARKER, 1
        )[0]
    except IndexError:
        return ["progressive mypy override section was not found"]

    module_match = re.search(r"module\s*=\s*\[(.*?)\]", section, re.DOTALL)
    if module_match is None:
        return ["progressive mypy override module list was not found"]

    actual_modules = set(re.findall(r'"([^"]+)"', module_match.group(1)))
    allowed_modules = set(baseline["modules"])
    new_modules = sorted(actual_modules - allowed_modules)

    actual_relaxed = {
        option
        for option in baseline["relaxed_options"]
        if re.search(rf"^{re.escape(option)}\s*=\s*false\s*$", section, re.MULTILINE)
    }
    known_relaxed = set(baseline["relaxed_options"])
    all_relaxed = set(
        re.findall(r"^(\w+)\s*=\s*false\s*$", section, re.MULTILINE)
    )
    new_relaxations = sorted((all_relaxed - known_relaxed) | (actual_relaxed - known_relaxed))

    errors: list[str] = []
    if new_modules:
        errors.append("new progressive mypy modules: " + ", ".join(new_modules))
    if new_relaxations:
        errors.append("new relaxed mypy options: " + ", ".join(new_relaxations))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("pyproject.toml"))
    parser.add_argument(
        "--baseline",
        type=Path,
        default=Path("typing/mypy-progressive-overrides.json"),
    )
    args = parser.parse_args()
    errors = validate(args.config, args.baseline)
    if errors:
        for error in errors:
            print(f"mypy override ratchet failed: {error}", file=sys.stderr)
        return 1
    print("mypy override ratchet passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
