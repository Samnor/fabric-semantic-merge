"""Private local artifacts and stable identifiers."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import uuid


class MergeError(Exception):
    """An actionable validation, API or migration error."""


def require(condition, message):
    if not condition:
        raise MergeError(message)


def uid(value):
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise MergeError(f"Expected a UUID, got {value!r}") from exc


def now():
    return datetime.now(timezone.utc).isoformat()


def encoded(value):
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        + "\n"
    )


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(path, value, *, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(encoded(value))
        handle.flush()
        os.fsync(handle.fileno())
