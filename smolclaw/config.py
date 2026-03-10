"""Structured config backed by ~/.smolclaw/smolclaw.json."""
from __future__ import annotations

import os
from typing import Any

from . import workspace

# Cache: (file_path, file_mtime, Config instance)
_cache: tuple[str, float, "Config"] | None = None


class Config:
    DEFAULTS: dict[str, Any] = {
        "model": "claude-sonnet-4-6",
        "max_turns": 10,
        "subagent_max_turns": 15,
        "subagent_timeout": 120,
    }

    def __init__(self, data: dict | None = None) -> None:
        self._data = dict(self.DEFAULTS)
        if data:
            self._data.update(data)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        global _cache
        if key not in self.DEFAULTS:
            raise KeyError(f"Unknown config key: {key!r}")
        expected = type(self.DEFAULTS[key])
        if not isinstance(value, expected):
            raise TypeError(f"{key!r} must be {expected.__name__}, got {type(value).__name__}")
        self._data[key] = value
        self._save()
        _cache = None  # invalidate on write

    def to_dict(self) -> dict:
        return dict(self._data)

    def _save(self) -> None:
        workspace.write_json(workspace.CONFIG, self._data)

    @classmethod
    def load(cls) -> "Config":
        global _cache
        path = workspace.CONFIG
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0

        if _cache is not None and _cache[0] == str(path) and _cache[1] == mtime:
            return _cache[2]

        data = workspace.read_json(path)

        # Migration: pick up env vars if smolclaw.json is missing those keys
        if "model" not in data:
            env_model = os.getenv("SMOLCLAW_MODEL")
            if env_model:
                data["model"] = env_model

        if "subagent_timeout" not in data:
            env_timeout = os.getenv("SMOLCLAW_SUBAGENT_TIMEOUT")
            if env_timeout:
                try:
                    data["subagent_timeout"] = int(env_timeout)
                except ValueError:
                    pass

        # Fill missing keys from defaults
        merged = dict(cls.DEFAULTS)
        merged.update(data)

        instance = cls(merged)
        _cache = (str(path), mtime, instance)
        return instance
