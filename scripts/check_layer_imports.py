"""Check MESA's production-layer direction and legal-module import boundaries."""

from __future__ import annotations

import ast
from collections.abc import Iterator
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
}
LEGAL_MODULES = ("mesa_storage.legal_identity", "mesa_storage.legal_rules")
LEGAL_IMPORTERS = {
    "mesa_api/v4_router.py",
    # Remove this compatibility exception in the Round 22 cleanup.
    "mesa_memory/retrieval/legal_resolver.py",
    "mesa_storage/dao.py",
    # Revisit this graph exception with the Round 23 Kùzu decision.
    "mesa_storage/kuzu_provider.py",
    "mesa_storage/legal_identity.py",
    "mesa_storage/legal_rules.py",
}


def _import_nodes(path: Path) -> Iterator[ast.Import | ast.ImportFrom]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node


def _imports(path: Path) -> set[str]:
    imports: set[str] = set()
    for node in _import_nodes(path):
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


def _full_imports(path: Path, package: tuple[str, ...]) -> set[str]:
    imports: set[str] = set()
    for node in _import_nodes(path):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
            continue
        module = node.module or ""
        if node.level:
            if node.level > len(package):
                continue
            module = ".".join(filter(None, (*package[: len(package) - node.level + 1], module)))
        imports.add(module)
        imports.update(f"{module}.{alias.name}" for alias in node.names)
    return imports


def find_new_legal_importers(root: Path = ROOT) -> list[str]:
    """Return imports of legal modules from files outside the D66 allow list."""
    violations: list[str] = []
    for path in (*root.glob("mesa_*/**/*.py"), *(root / "scripts").rglob("*.py")):
        relative = path.relative_to(root)
        if relative.as_posix() in LEGAL_IMPORTERS:
            continue
        imports = _full_imports(path, relative.parts[:-1])
        for module in LEGAL_MODULES:
            if any(name == module or name.startswith(f"{module}.") for name in imports):
                violations.append(f"{relative.as_posix()} -> {module}")
    return sorted(violations)


def main() -> int:
    violations = find_reverse_dependencies()
    if violations:
        raise SystemExit("reverse layer dependencies:\n" + "\n".join(violations))
    violations = find_new_legal_importers()
    if violations:
        raise SystemExit("new legal-module imports:\n" + "\n".join(violations))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
