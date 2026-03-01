"""Structured config backed by ~/.smolclaw/smolclaw.json."""
from __future__ import annotations

import os
from typing import Any

from . import workspace


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
        if key not in self.DEFAULTS:
            raise KeyError(f"Unknown config key: {key!r}")
        expected = type(self.DEFAULTS[key])
        if not isinstance(value, expected):
            raise TypeError(f"{key!r} must be {expected.__name__}, got {type(value).__name__}")
        self._data[key] = value
        self._save()

    def to_dict(self) -> dict:
        return dict(self._data)

    def _save(self) -> None:
        workspace.write_json(workspace.CONFIG, self._data)

    @classmethod
    def load(cls) -> Config:
        data = workspace.read_json(workspace.CONFIG)

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

        return cls(merged)
