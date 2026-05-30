from pathlib import Path

from .settings import WORKDIR


def safe_path(p: str) -> Path:
    path = (WORKDIR / p).resolve()
    try:
        path.relative_to(WORKDIR)
    except ValueError:
        raise ValueError(f"Path escapes workspace: {p}")
    return path
