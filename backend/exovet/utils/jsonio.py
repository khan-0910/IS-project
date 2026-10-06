"""JSON helpers that keep reports strictly valid (no NaN/Infinity).

Reports legitimately carry NaN when a statistic is undefined (e.g. the
false-alarm probability of a non-significant TLS run).  Python's json writes
it happily, but strict encoders (Starlette responses, browsers) reject it —
a single NaN in a stored report used to 500 the /api/jobs endpoint.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


def finite_json(obj: Any) -> Any:
    """Return ``obj`` with non-finite floats replaced by ``None``, recursively."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: finite_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [finite_json(v) for v in obj]
    return obj


def read_json(path: Path | str) -> Any:
    """Load JSON from ``path`` and sanitize non-finite floats."""
    return finite_json(json.loads(Path(path).read_text(encoding="utf-8")))
