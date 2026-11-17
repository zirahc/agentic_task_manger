from __future__ import annotations


class ApiError(Exception):
    """A coded error that the API returns as `{code, message, field?}`."""

    def __init__(self, code: str, message: str, status: int = 400, field: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.field = field

    def to_json(self) -> dict[str, str]:
        out = {"code": self.code, "message": self.message}
        if self.field is not None:
            out["field"] = self.field
        return out


def not_found(kind: str, ident: str) -> ApiError:
    return ApiError(f"{kind}-not-found", f"{kind.capitalize()} '{ident}' was not found.", 404)


def invalid(field: str, problem: str) -> ApiError:
    return ApiError("invalid-field", f"{field} {problem}.", 400, field)
