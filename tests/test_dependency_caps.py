import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.specifiers import Specifier


def test_critical_dependencies_have_upper_version_caps():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with pyproject.open("rb") as source:
        dependencies = tomllib.load(source)["project"]["dependencies"]
    requirements = {requirement.name: requirement for requirement in map(Requirement, dependencies)}

    for package, cap in {
        "fastapi": "0.142",
        "lancedb": "0.42",
        "pydantic": "3",
    }.items():
        assert Specifier(f"<{cap}") in set(requirements[package].specifier)
