"""Small, explicit validation for JSON request bodies (camelCase keys)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from .errors import ApiError, invalid


class Body:
    def __init__(self, data: object):
        if not isinstance(data, dict):
            raise ApiError("invalid-body", "Request body must be a JSON object.")
        self.data: dict[str, Any] = data

    def text(
        self,
        key: str,
        *,
        required: bool = False,
        max_len: int = 200,
        pattern: re.Pattern[str] | None = None,
    ) -> str:
        value = self.data.get(key)
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise invalid(key, "must be text")
        value = value.strip()
        if required and not value:
            raise invalid(key, "is required")
        if len(value) > max_len:
            raise invalid(key, f"must be at most {max_len} characters")
        if value and pattern is not None and not pattern.fullmatch(value):
            raise invalid(key, "contains characters that are not allowed")
        return value

    def choice(self, key: str, options: Sequence[str], default: str) -> str:
        value = self.data.get(key)
        if value is None:
            return default
        if not isinstance(value, str) or value not in options:
            raise invalid(key, f"must be one of: {', '.join(o or '(blank)' for o in options)}")
        return value

    def integer(self, key: str, *, minimum: int, maximum: int, default: int | None) -> int | None:
        value: object = self.data.get(key)
        if value is None:
            return default
        if isinstance(value, bool) or not isinstance(value, int):
            raise invalid(key, "must be a whole number")
        number: int = value
        if not minimum <= number <= maximum:
            raise invalid(key, f"must be between {minimum} and {maximum}")
        return number

    def text_list(self, key: str, *, max_items: int, max_len: int) -> list[str]:
        value = self.data.get(key)
        if value is None:
            return []
        if not isinstance(value, list):
            raise invalid(key, "must be a list")
        out: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise invalid(key, "must contain only text")
            item = item.strip()
            if not item:
                continue
            if len(item) > max_len:
                raise invalid(key, f"items must be at most {max_len} characters")
            out.append(item)
        if len(out) > max_items:
            raise invalid(key, f"must have at most {max_items} items")
        return out
