"""Reject imports that reverse MESA's production-layer dependency direction."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[1]
LAYER = {
    "mesa_contracts": 0,
    "mesa_storage": 1,
    "mesa_memory": 2,
    "mesa_workers": 3,
    "mesa_api": 4,
    "mesa_client": 4,
    "mesa_mcp": 4,
    "mesa_runtime": 5,
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


def find_package_cycles(root: Path = ROOT) -> list[str]:
    """Return top-level production package cycles in canonical code.

    Compatibility shims are deliberately omitted: they point old import paths
    at the new runtime during the deprecation window but are not canonical
    dependency edges.
    """
    graph: dict[str, set[str]] = {package: set() for package in LAYER}
    for package in LAYER:
        package_root = root / package
        if not package_root.exists():
            continue
        for path in package_root.rglob("*.py"):
            if path.relative_to(root).as_posix() in COMPOSITION_ROOTS:
                continue
            graph[package].update(
                imported
                for imported in _imports(path)
                if imported in LAYER and imported != package
            )

    cycles: set[tuple[str, ...]] = set()

    def visit(origin: str, current: str, path: tuple[str, ...]) -> None:
        for dependency in graph[current]:
            if dependency == origin:
                cycle = path + (origin,)
                body = cycle[:-1]
                rotations = [body[index:] + body[:index] for index in range(len(body))]
                canonical = min(rotations)
                cycles.add(canonical + (canonical[0],))
            elif dependency not in path:
                visit(origin, dependency, path + (dependency,))

    for package in graph:
        visit(package, package, (package,))
    return [" -> ".join(cycle) for cycle in sorted(cycles)]


def main() -> int:
    violations = find_reverse_dependencies()
    cycles = find_package_cycles()
    errors: list[str] = []
    if violations:
        errors.append("reverse layer dependencies:\n" + "\n".join(violations))
    if cycles:
        errors.append("package dependency cycles:\n" + "\n".join(cycles))
    if errors:
        raise SystemExit("\n\n".join(errors))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
