"""Phase 28.4 -- in-process sandbox secret injection.

Secrets reach the sandboxed operation ONLY through this module, and only as
an in-memory dict inside the sandbox subprocess. They are:
  * never written to the sandbox filesystem;
  * never placed in ``os.environ`` (the sandbox profile's environment
    validator already fails closed on secret-like keys);
  * never logged (the bootstrap never prints payload contents);
  * gone the moment the sandbox process exits (the process is the only owner
    of the memory).

The host process must NOT use this module -- it is loaded inside the sandbox
child (the child sets the values from the stdin payload). Operations that need
a secret read it via :func:`get_secret`; a missing or un-injected secret
fails closed.
"""

from __future__ import annotations

_secrets: dict[str, str] = {}


def set_secrets(values: dict[str, str]) -> None:
    """Called by the sandbox bootstrap with the current execution's secrets."""
    global _secrets
    _secrets = dict(values or {})


def get_secret(name: str) -> str:
    """Return the named secret for THIS execution, or fail closed."""
    try:
        return _secrets[name]
    except KeyError as error:
        from app.exceptions import SecretNotFound

        raise SecretNotFound(f"Sandbox secret {name!r} not injected") from error


def has_secret(name: str) -> bool:
    return name in _secrets


def clear() -> None:
    global _secrets
    _secrets = {}
