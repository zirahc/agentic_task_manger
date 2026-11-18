"""Submissions: structured reports that sessions write into their output.

This is the fenced-block path from the spec (§3.3). A session ends its answer with

    ```submission
    {"type": "test-result", "status": "fail", ...}
    ```

and the dashboard stores every such block when the session ends. A submission is
untrusted model output: it is validated here, and an invalid block is stored with the
reason (and its raw text) so it can be shown, never silently dropped.

Only `test-result` has a schema so far.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ..db import Database, Row, dumps, loads, new_id, now_iso
from ..errors import not_found

FENCE = re.compile(r"```submission[ \t]*\r?\n(.*?)\r?\n[ \t]*```", re.DOTALL)
TYPES = ("test-result",)
STATUSES = ("pass", "fail", "error")
OUTCOMES = ("tp", "fp", "tn", "fn", "pass", "fail", "skip")
COUNT_KEYS = ("passed", "failed", "skipped")
CONFUSION_KEYS = ("tp", "fp", "tn", "fn")
MAX_BLOCKS = 20
MAX_CASES = 2000
MAX_RAW = 20_000
MAX_NUMBER = 1_000_000_000
TEXT_LIMITS = {"suite": 120, "summary": 4000, "commit": 64}
CASE_LIMITS = {"expected": 300, "actual": 300, "note": 1000}


class SubmissionError(Exception):
    """A block is not a valid submission. The message lists every problem."""


@dataclass(frozen=True)
class Parsed:
    type: str
    payload: dict[str, Any]
    """The normalised payload when valid; `{"raw": <block text>}` when not."""
    error: str | None


# ---- parsing & validation -------------------------------------------------------------


def extract(texts: Iterable[str]) -> list[Parsed]:
    """Every ```submission block in `texts`, in order; identical blocks count once."""
    seen: set[str] = set()
    found: list[Parsed] = []
    for text in texts:
        for match in FENCE.finditer(text):
            block = match.group(1).strip()
            if block in seen:
                continue
            seen.add(block)
            found.append(parse_block(block))
            if len(found) >= MAX_BLOCKS:
                return found
    return found


def parse_block(block: str) -> Parsed:
    raw_payload = {"raw": block[:MAX_RAW]}
    try:
        raw = json.loads(block)
    except json.JSONDecodeError as err:
        problem = f"not valid JSON: {err.msg} (line {err.lineno})"
        return Parsed(_guess_type(block), raw_payload, problem)
    if not isinstance(raw, dict):
        return Parsed(_guess_type(block), raw_payload, "a submission must be a JSON object")
    kind = raw.get("type")
    if not isinstance(kind, str) or not kind.strip():
        return Parsed("unknown", raw_payload, "'type' is required")
    kind = kind.strip()[:40]
    if kind not in TYPES:
        supported = ", ".join(TYPES)
        return Parsed(kind, raw_payload, f"type '{kind}' is not supported (use: {supported})")
    try:
        payload = validate_test_result(raw)
    except SubmissionError as err:
        return Parsed(kind, raw_payload, str(err))
    return Parsed(kind, payload, None)


TYPE_HINT = re.compile(r'"type"\s*:\s*"([a-z][a-z0-9-]{0,39})"')


def _guess_type(block: str) -> str:
    """The type a broken block was meant to have, so it is listed where its rows belong
    (an unreadable test result still shows up in the Tests table, with its error)."""
    match = TYPE_HINT.search(block)
    return match.group(1) if match and match.group(1) in TYPES else "unknown"


def validate_test_result(raw: dict[str, Any]) -> dict[str, Any]:
    problems: list[str] = []
    status = raw.get("status")
    status = status.strip().lower() if isinstance(status, str) else status
    if status not in STATUSES:
        problems.append(f"'status' must be one of: {', '.join(STATUSES)}")
    out: dict[str, Any] = {"status": status}
    for key, limit in TEXT_LIMITS.items():
        value = raw.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            problems.append(f"'{key}' must be text")
        elif value.strip():
            out[key] = value.strip()[:limit]
    counts = _numbers(raw.get("counts"), COUNT_KEYS, "counts", problems)
    confusion = _numbers(raw.get("confusion"), CONFUSION_KEYS, "confusion", problems)
    cases = _cases(raw.get("cases"), problems)
    if problems:
        raise SubmissionError("; ".join(problems))
    if cases:
        out["cases"] = cases
        outcomes = [c["outcome"] for c in cases]
        if counts is None and any(o in ("pass", "fail", "skip") for o in outcomes):
            counts = {
                "passed": outcomes.count("pass"),
                "failed": outcomes.count("fail"),
                "skipped": outcomes.count("skip"),
            }
        if confusion is None and any(o in CONFUSION_KEYS for o in outcomes):
            confusion = {key: outcomes.count(key) for key in CONFUSION_KEYS}
    if counts is not None:
        out["counts"] = counts
    if confusion is not None:
        out["confusion"] = confusion
    return out


def _numbers(
    value: object, keys: tuple[str, ...], name: str, problems: list[str]
) -> dict[str, int] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        problems.append(f"'{name}' must be an object with {', '.join(keys)}")
        return None
    out = dict.fromkeys(keys, 0)
    for raw_key, number in value.items():
        key = str(raw_key).strip().lower()
        if key not in keys:
            problems.append(f"'{name}' has an unknown key '{raw_key}' (use: {', '.join(keys)})")
        elif isinstance(number, bool) or not isinstance(number, int):
            problems.append(f"'{name}.{key}' must be a whole number")
        elif not 0 <= number <= MAX_NUMBER:
            problems.append(f"'{name}.{key}' must be between 0 and {MAX_NUMBER}")
        else:
            out[key] = number
    return out


def _cases(value: object, problems: list[str]) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        problems.append("'cases' must be a list")
        return []
    if len(value) > MAX_CASES:
        problems.append(f"'cases' may have at most {MAX_CASES} items (got {len(value)})")
        return []
    cases: list[dict[str, str]] = []
    bad: list[str] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            bad.append(f"case {index} is not an object")
            continue
        name = item.get("name")
        outcome = item.get("outcome")
        outcome = outcome.strip().lower() if isinstance(outcome, str) else outcome
        if not isinstance(name, str) or not name.strip():
            bad.append(f"case {index}: 'name' is required")
            continue
        if outcome not in OUTCOMES:
            bad.append(f"case {index}: 'outcome' must be one of: {', '.join(OUTCOMES)}")
            continue
        case = {"name": name.strip()[:300], "outcome": str(outcome)}
        for key, limit in CASE_LIMITS.items():
            text = item.get(key)
            if isinstance(text, str) and text.strip():
                case[key] = text.strip()[:limit]
            elif text is not None and not isinstance(text, str):
                case[key] = json.dumps(text, ensure_ascii=False)[:limit]
        cases.append(case)
    if bad:
        more = f" (and {len(bad) - 5} more)" if len(bad) > 5 else ""
        problems.append("; ".join(bad[:5]) + more)
    return cases


def metrics(payload: dict[str, Any]) -> dict[str, float | None]:
    """Detection metrics from the confusion counts (None when undefined)."""
    confusion = payload.get("confusion")
    if not isinstance(confusion, dict):
        return {}
    tp, fp, tn, fn = (int(confusion.get(key, 0)) for key in CONFUSION_KEYS)
    return {
        "precision": _ratio(tp, tp + fp),
        "recall": _ratio(tp, tp + fn),
        "f1": _ratio(2 * tp, 2 * tp + fp + fn),
        "accuracy": _ratio(tp + tn, tp + fp + tn + fn),
        "fpr": _ratio(fp, fp + tn),
    }


def _ratio(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


# ---- storage ------------------------------------------------------------------------


def record(
    db: Database, *, session_id: str, run_id: str, texts: Iterable[str], commit: str | None
) -> int:
    """Store every submission block found in a finished session's output."""
    found = extract(texts)
    if not found:
        return 0
    now = now_iso()
    with db.transaction():
        for item in found:
            db.execute(
                "INSERT INTO submissions (id, session_id, run_id, type, payload, valid, error, "
                "commit_sha, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id("sub"),
                    session_id,
                    run_id,
                    item.type,
                    dumps(item.payload),
                    0 if item.error else 1,
                    item.error,
                    commit,
                    now,
                ),
            )
    return len(found)


SELECT = (
    "SELECT sb.*, s.role_name, s.tool, s.status AS session_status, r.task_id, "
    "t.title AS task_title FROM submissions sb "
    "JOIN sessions s ON s.id = sb.session_id "
    "JOIN runs r ON r.id = sb.run_id JOIN tasks t ON t.id = r.task_id"
)


def serialize(row: Row) -> dict[str, Any]:
    payload = loads(row["payload"], {})
    valid = bool(row["valid"])
    return {
        "id": row["id"],
        "sessionId": row["session_id"],
        "runId": row["run_id"],
        "taskId": row["task_id"],
        "taskTitle": row["task_title"],
        "roleName": row["role_name"],
        "tool": row["tool"],
        "sessionStatus": row["session_status"],
        "type": row["type"],
        "valid": valid,
        "error": row["error"],
        "payload": payload,
        "metrics": metrics(payload) if valid and row["type"] == "test-result" else {},
        "commit": row["commit_sha"],
        "createdAt": row["created_at"],
    }


def list_submissions(
    db: Database, *, kind: str = "", task_id: str = "", limit: int = 500
) -> list[dict[str, Any]]:
    rows = db.all(
        SELECT + " WHERE (? = '' OR sb.type = ?) AND (? = '' OR r.task_id = ?) "
        "ORDER BY sb.created_at DESC, sb.rowid DESC LIMIT ?",
        (kind, kind, task_id, task_id, limit),
    )
    return [serialize(row) for row in rows]


def delete_submission(db: Database, submission_id: str) -> None:
    if not db.execute("DELETE FROM submissions WHERE id = ?", (submission_id,)):
        raise not_found("submission", submission_id)
