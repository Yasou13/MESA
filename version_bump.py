import os

ignored_dirs = [".git", "venv", "__pycache__", "docs/historical_benchmarks"]
ignored_files = ["CHANGELOG.md"]


def replace_in_file(filepath: str) -> None:
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    if "0.5.1" in content:
        new_content = content.replace("0.5.1", "0.5.2")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(new_content)
        print(f"Updated {filepath}")


for root, dirs, files in os.walk("."):
    dirs[:] = [
        d
        for d in dirs
        if not any(ign in os.path.join(root, d) for ign in ignored_dirs)
        and d not in ignored_dirs
    ]
    for file in files:
        if file in ignored_files or file == "version_bump.py":
            continue
        if file.endswith(".pyc") or file.endswith(".jsonl") or file.endswith(".json"):
            continue
        filepath = os.path.join(root, file)
        try:
            replace_in_file(filepath)
        except Exception:
            pass
