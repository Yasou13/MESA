from pathlib import Path

schemas_path = Path("mesa_storage/schemas.py")
content = schemas_path.read_text()

# Extract everything from "# Node operations" to the end of find_nodes_by_name
# (before "# FTS5 lexical pre-filtering")
start_marker = "# ---------------------------------------------------------------------------\n# Node operations\n# ---------------------------------------------------------------------------\n"
end_marker = "# ---------------------------------------------------------------------------\n# FTS5 lexical pre-filtering — zero-VRAM search\n# ---------------------------------------------------------------------------\n"

start_idx = content.find(start_marker)
end_idx = content.find(end_marker)

if start_idx != -1 and end_idx != -1:
    extracted_functions = content[start_idx:end_idx]

    # Save to tests/utils/storage_helpers.py
    helpers_path = Path("tests/utils/storage_helpers.py")
    helpers_path.write_text(f"""import logging
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncEngine

{extracted_functions}
""")

    # Remove from schemas.py
    new_schemas = content[:start_idx] + content[end_idx:]
    schemas_path.write_text(new_schemas)
    print("Successfully moved functions to storage_helpers.py")
else:
    print("Could not find markers")

# Update test files manually to replace imports
