from pathlib import Path

for test_file in Path("tests").rglob("*.py"):
    content = test_file.read_text()
    if "from mesa_storage.schemas import" in content:
        # We need to find if it imports initialize_schema or verify_schema, and move others.
        # But wait, it's easier to just blindly replace:
        # from mesa_storage.schemas import (
        #     initialize_schema,
        #     insert_node,
        #     bulk_insert_nodes,
        #     ...
        # )

        # Let's just do simple string replacements.
        # It's safer to just run a regex on the entire content

        # Helper lists
        storage_funcs = [
            "insert_node",
            "bulk_insert_nodes",
            "soft_delete_node",
            "mark_consolidated",
            "get_active_nodes",
            "find_nodes_by_name",
        ]

        # For multi-line imports
        # Actually a simple AST or regex might be tricky if it's formatted.
        # Let's use `sed` or simple replace.
        lines = content.split("\n")
        new_lines = []
        in_import = False
        funcs_to_move = []
        keep_in_schemas = []

        i = 0
        while i < len(lines):
            line = lines[i]
            if line.startswith("from mesa_storage.schemas import ("):
                in_import = True
                i += 1
                while i < len(lines) and lines[i].strip() != ")":
                    func_name = lines[i].strip().strip(",")
                    if func_name in storage_funcs:
                        funcs_to_move.append(func_name)
                    elif func_name:
                        keep_in_schemas.append(func_name)
                    i += 1
                in_import = False

                if keep_in_schemas:
                    new_lines.append("from mesa_storage.schemas import (")
                    for k in keep_in_schemas:
                        new_lines.append(f"    {k},")
                    new_lines.append(")")
                if funcs_to_move:
                    new_lines.append("from tests.utils.storage_helpers import (")
                    for f in funcs_to_move:
                        new_lines.append(f"    {f},")
                    new_lines.append(")")
            elif line.startswith("from mesa_storage.schemas import"):
                # single line
                parts = line.split("import")[1].split(",")
                parts = [p.strip() for p in parts]
                keep = [p for p in parts if p not in storage_funcs]
                move = [p for p in parts if p in storage_funcs]
                if keep:
                    new_lines.append(
                        f"from mesa_storage.schemas import {', '.join(keep)}"
                    )
                if move:
                    new_lines.append(
                        f"from tests.utils.storage_helpers import {', '.join(move)}"
                    )
            else:
                new_lines.append(line)
            i += 1

        test_file.write_text("\n".join(new_lines))
        print(f"Updated {test_file}")
