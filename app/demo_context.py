"""Who is using the app.

This demo has no login, so everyone is the same fixed user. `main.py` asks for a
`Principal` on every request (via `Depends(principal)`); the rest of the app only ever
sees this object: `.tenant`, `.groups`, `.subject`, `.admin`.

To add real authentication later, replace `principal()` so it builds a Principal from
a verified token instead of returning the fixed one. Nothing else needs to change.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Principal:
    subject: str = "demo-operator"          # the user's ID (owner of uploaded documents)
    tenant: str = "demo-workspace"          # the company/workspace; separates all data
    groups: tuple[str, ...] = ("workspace",)  # groups that decide which documents are visible
    admin: bool = True                      # may review approvals and see guardrail metrics


def principal() -> Principal:
    return Principal()
