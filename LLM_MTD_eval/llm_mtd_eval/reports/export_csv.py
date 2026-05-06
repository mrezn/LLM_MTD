from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


def append_row(path: Path, row: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row.keys()))
        if not exists:
            writer.writeheader()
        writer.writerow(row)
    return path
