import sys
import os
import importlib.util
from pathlib import Path

# Add original MESA dir to sys.path so unmutated packages can be found
root_dir = str(Path(__file__).resolve().parent)
if root_dir not in sys.path:
    sys.path.append(root_dir)

# If mutmut is running, it creates a partial mesa_memory package in mutants/.
# We need to extend its __path__ so Python also looks in the original directory.
try:
    import mesa_memory
    original_path = os.path.join(root_dir, "mesa_memory")
    if hasattr(mesa_memory, "__path__") and original_path not in mesa_memory.__path__:
        mesa_memory.__path__.append(original_path)
except ImportError:
    pass

# Also extend consolidation specifically just in case
try:
    import mesa_memory.consolidation
    original_cons_path = os.path.join(root_dir, "mesa_memory", "consolidation")
    if hasattr(mesa_memory.consolidation, "__path__") and original_cons_path not in mesa_memory.consolidation.__path__:
        mesa_memory.consolidation.__path__.append(original_cons_path)
except ImportError:
    pass
