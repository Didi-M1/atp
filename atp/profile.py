"""Profile loader with YAML inheritance support.

Usage:
    from atp.profile import load_profile

    profile = load_profile("example_x86")
    # or with a custom directory:
    profile = load_profile("my_board", profiles_dir=Path("/etc/atp/profiles"))
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

PROFILES_DIR = Path(__file__).parent.parent / "profiles"

# Sections in base.yaml that carry this sentinel MUST be explicitly addressed
# (enabled: true / enabled: false / assert_absent: true) in every platform
# profile.  The sentinel is overwritten by _deep_merge as soon as the child
# profile sets any value, so it never appears in a complete profile.
_SENTINEL = "~REQUIRED~"


class ProfileIncompleteError(ValueError):
    """Raised when a profile leaves one or more required sections unaddressed."""


def _find_sentinels(data: dict, _path: str = "") -> list[str]:
    """Return dotted key-paths where the value is still the sentinel."""
    found = []
    for key, value in data.items():
        path = f"{_path}.{key}" if _path else key
        if value == _SENTINEL:
            found.append(path)
        elif isinstance(value, dict):
            found.extend(_find_sentinels(value, path))
    return found


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base*, returning a new dict.

    - Dicts are merged recursively.
    - All other types (lists, scalars) are replaced by the override value.
      This means a platform profile replaces an entire list — it doesn't append
      to the base list.
    """
    result = copy.deepcopy(base)
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_profile(
    name: str,
    profiles_dir: Path | None = None,
) -> dict[str, Any]:
    """Load a named profile, resolving the full inheritance chain.

    Raises:
        FileNotFoundError: if the profile YAML file does not exist.
        RecursionError:    if the inheritance chain is circular.
    """
    if profiles_dir is None:
        profiles_dir = PROFILES_DIR

    path = profiles_dir / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"Profile '{name}' not found at {path}\n"
            f"Available profiles: {[p.stem for p in profiles_dir.glob('*.yaml')]}"
        )

    with path.open() as fh:
        data: dict[str, Any] = yaml.safe_load(fh) or {}

    parent_name = data.get("extends")
    if parent_name:
        parent = load_profile(parent_name, profiles_dir)
        data = _deep_merge(parent, data)

    # Only validate fully-resolved profiles (not base itself, which is the
    # sentinel source).  Intermediate parents that still carry sentinels are
    # fine — they will be overwritten by the child further up the chain.
    if data.get("extends") is not None:  # not base (base has extends: null)
        missing = _find_sentinels(data)
        if missing:
            lines = "\n".join(f"  • {p}" for p in missing)
            raise ProfileIncompleteError(
                f"Profile '{name}' does not explicitly address all required sections.\n"
                f"Add each of the following to your profile YAML with\n"
                f"'enabled: true', 'enabled: false', or 'assert_absent: true':\n"
                f"{lines}"
            )

    return data
