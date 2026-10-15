"""Check current authored docs without changing historical evidence or using network."""

from __future__ import annotations

import ast
import json
import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml

ROOT = Path(__file__).resolve().parents[1]
HISTORICAL = {
    "ARCHITECTURE.md",
    "SOAK_TEST_REPORT_20260827.md",
    "PROFILE_A_REPAIR_JOURNAL.md",
    "docs/v4-final-independent-audit.md",
}


class AssetLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for key, value in attrs:
            if key in {"href", "src"} and value:
                self.links.append(value)


def maintained(path: Path) -> bool:
    relative = path.relative_to(ROOT).as_posix()
    return relative not in HISTORICAL and not relative.startswith(
        ("reports/", "results/", "docs/historical_benchmarks/")
    )


def anchors(text: str) -> set[str]:
    result = set(re.findall(r'id=["\']([^"\']+)', text))
    counts: dict[str, int] = {}
    for line in re.sub(r"```.*?```", "", text, flags=re.S).splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*$", line)
        if match is None:
            continue
        heading = re.sub(r"<[^>]+>", "", match[1]).lower()
        slug = re.sub(r"[^\w\- ]", "", heading).replace(" ", "-")
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        result.add(slug + (f"-{count}" if count else ""))
    return result


def main() -> int:
    files = [
        path
        for pattern in ("*.md", "docs/**/*.md", "mesa_*/README.md")
        for path in ROOT.glob(pattern)
        if maintained(path)
        and path.name not in {"MESA_v080_release_prompt.md", "AGENTS.md"}
    ]
    errors: list[str] = []
    notebook_fences = 0
    fences_checked = 0
    links_checked = 0
    for path in sorted(set(files)):
        text = path.read_text()
        relative = path.relative_to(ROOT)
        without_fences = re.sub(r"```.*?```", "", text, flags=re.S)
        references = dict(
            re.findall(r"^\[([^\]]+)\]:\s*(\S+)", without_fences, flags=re.M)
        )
        destinations = re.findall(
            r"!?\[[^\]]*\]\(([^\s)]+)(?:\s+[^)]*)?\)", without_fences
        )
        destinations += [
            references[label]
            for label in re.findall(r"\[[^\]]+\]\[([^\]]+)\]", without_fences)
            if label in references
        ]
        for destination in destinations:
            parsed = urlsplit(destination.strip("<>"))
            if parsed.scheme or destination.startswith("/"):
                continue
            target = (
                (path.parent / unquote(parsed.path)).resolve() if parsed.path else path
            )
            links_checked += 1
            if not target.exists():
                errors.append(f"{relative}: missing local target {destination}")
            elif parsed.fragment and target.suffix in {".md", ".html"}:
                if unquote(parsed.fragment) not in anchors(target.read_text()):
                    errors.append(f"{relative}: missing anchor {destination}")
        if len(re.findall(r"^```", text, flags=re.M)) % 2:
            errors.append(f"{relative}: unbalanced code fence")
        for language, body in re.findall(
            r"^```([^\n]*)\n(.*?)^```", text, flags=re.M | re.S
        ):
            language = language.strip().lower()
            try:
                if language == "python":
                    if re.search(r"^\s*[!%]", body, flags=re.M):
                        notebook_fences += 1
                        continue
                    ast.parse(body)
                elif language == "json":
                    json.loads(body)
                elif language in {"yaml", "yml"}:
                    yaml.safe_load(body)
                elif language in {"bash", "sh", "shell"}:
                    result = subprocess.run(
                        ["bash", "-n"], input=body, text=True, capture_output=True
                    )
                    if result.returncode:
                        raise ValueError(result.stderr.strip())
                else:
                    continue
                fences_checked += 1
            except (SyntaxError, ValueError, yaml.YAMLError) as error:
                errors.append(f"{relative}: {language} fence: {error}")
    for path in ROOT.glob("**/*.yml"):
        if any(
            part in {"node_modules", ".venv", ".serena", "results", "build"}
            for part in path.parts
        ):
            continue
        if not maintained(path):
            continue
        try:
            yaml.safe_load(path.read_text())
        except yaml.YAMLError as error:
            errors.append(f"{path.relative_to(ROOT)}: YAML: {error}")
    html_files = list((ROOT / "mesa_dashboard").glob("index.html")) + list(
        (ROOT / "mesa_dashboard/public").rglob("*.html")
    )
    html_links = 0
    for path in html_files:
        parser = AssetLinks()
        parser.feed(path.read_text())
        for destination in parser.links:
            parsed = urlsplit(destination)
            if parsed.scheme:
                continue
            if destination.startswith("/dashboard/"):
                relative = parsed.path.removeprefix("/dashboard/")
                if not relative:
                    continue
                target = ROOT / "mesa_dashboard/public" / relative
                if target.is_dir():
                    target = target / "index.html"
            elif parsed.path in {"/docs", "/redoc", "/openapi.json"}:
                continue  # Runtime API routes, exercised separately.
            elif parsed.path.startswith("/src/"):
                target = ROOT / "mesa_dashboard" / parsed.path.lstrip("/")
            else:
                target = path.parent / parsed.path if parsed.path else path
            html_links += 1
            if not target.exists():
                errors.append(f"{path.relative_to(ROOT)}: missing asset {destination}")
            elif parsed.fragment and target.suffix == ".html":
                if unquote(parsed.fragment) not in anchors(target.read_text()):
                    errors.append(
                        f"{path.relative_to(ROOT)}: missing HTML anchor {destination}"
                    )
    print(
        json.dumps(
            {
                "files": len(set(files)),
                "html_files": len(html_files),
                "html_asset_links": html_links,
                "local_links": links_checked,
                "syntax_fences": fences_checked,
                "colab_fences": notebook_fences,
                "external_urls": "NOT_CHECKED",
                "configured_docs_builder": "NONE",
                "errors": errors,
            },
            indent=2,
        )
    )
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
