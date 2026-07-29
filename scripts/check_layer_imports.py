"""Reject imports that reverse MESA's production-layer dependency direction."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[1]
LAYER = {
    "mesa_storage": 0,
    "mesa_memory": 1,
    "mesa_workers": 2,
    "mesa_api": 3,
    "mesa_mcp": 3,
}
COMPOSITION_ROOTS = {
    "mesa_memory/api/server.py",
    "mesa_memory/runtime_entrypoint.py",
    "mesa_memory/worker_runtime.py",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".", 1)[0])
    return imports


def find_reverse_dependencies(root: Path = ROOT) -> list[str]:
    """Return production imports from a lower layer into a higher one."""
    violations: list[str] = []
    for package, layer in LAYER.items():
        for path in (root / package).rglob("*.py"):
            relative = path.relative_to(root).as_posix()
            if relative in COMPOSITION_ROOTS:
                continue
            for imported in _imports(path):
                imported_layer = LAYER.get(imported)
                if imported_layer is not None and imported_layer > layer:
                    violations.append(f"{relative} -> {imported}")
    return sorted(violations)


def main() -> int:
    violations = find_reverse_dependencies()
    if violations:
        raise SystemExit("reverse layer dependencies:\n" + "\n".join(violations))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
