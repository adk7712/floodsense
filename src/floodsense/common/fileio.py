"""Small file helpers shared by the alerts code."""

import json
import os
import tempfile
from pathlib import Path


def write_json_atomic(path: Path, data: object) -> None:
    """Write ``data`` as JSON: a temp file in the same folder, then a rename, so a reader never
    sees a half-written file. Creates the folder. Raises OSError (the temp file is removed)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False
        ) as tmp:
            tmp_name = tmp.name
            json.dump(data, tmp)
        os.replace(tmp_name, path)
    except OSError:
        if tmp_name:
            Path(tmp_name).unlink(missing_ok=True)
        raise
