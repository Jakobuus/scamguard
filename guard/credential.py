# HACK: one hardcoded credential
_VALID = {"demo-credential": {"agent": "Broccoli scheduling agent", "principal": "Broccoli Inc."}}


def check(token: str | None) -> dict | None:
    return _VALID.get(token or "")
