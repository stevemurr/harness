# Each plugin is importable by its own name from this folder.
import sys
from pathlib import Path

for plugin in (Path(__file__).parent / "plugins").iterdir():
    if plugin.is_dir():
        sys.path.insert(0, str(plugin.parent))
