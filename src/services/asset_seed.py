"""Stable seed derivation for visual assets."""

from __future__ import annotations

import hashlib
import secrets
from typing import Any


def derive_asset_seed(
    project_id: str,
    asset_type: str,
    asset_id: str,
    view_type: str,
    prompt_template_version: int | str = 1,
) -> int:
    """Derive a process-stable positive provider seed from asset identity."""
    key = "|".join(
        str(value).strip()
        for value in (project_id, asset_type, asset_id, view_type, prompt_template_version)
    )
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % 999_999 + 1


def resolve_generation_seed(
    project_id: str,
    asset_type: str,
    asset_id: str,
    view_type: str,
    prompt_template_version: int | str = 1,
    *,
    retry_of: Any = None,
    user_regenerate: bool = False,
) -> int:
    """Reuse retry seeds, derive stable first-run seeds, randomize explicit regenerations."""
    if retry_of is not None and not user_regenerate:
        seed = int(getattr(retry_of, "seed", 0) or 0)
        if seed > 0:
            return seed
    if user_regenerate:
        return secrets.randbelow(999_999) + 1
    return derive_asset_seed(project_id, asset_type, asset_id, view_type, prompt_template_version)
