"""The test process must not read an untracked ``.env``.

``Settings`` resolves ``env_file=".env"`` against the current working directory,
so any key a developer puts in the repository-root ``.env`` outranks the class
defaults and reconfigures the suite. This file exists because that leaked three
separate times, each diagnosed only after a confusing failure:

  * ``RBAC_TRUSTED_PROXY_SECRET`` -- the documented docker-compose quickstart
    writes a real proxy secret, every authenticated API test returned 401.
  * ``CAP_ZAP_API_KEY`` -- ``create_app()`` seeds the secret provider from
    settings, so a developer key silently provisioned ZAP inside the
    incident-plane tests (fixed by 41bbc49 + a pinned ``_TEST_CONFIG`` entry).
  * ``APP_VERSION`` -- a stale value made ``/health`` report the *previous*
    release, failing ``test_health`` on a tree whose 16 version carriers agreed.

Pinning a whitelist of keys cannot scale, because the failure mode is per-key and
silent. ``backend/tests/conftest.py`` therefore clears ``env_file`` on the
Settings class before any app module is imported, and these tests hold that
invariant -- including the negative control, which would go green vacuously if
the mechanism were removed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config.settings import Settings

#: Keys a developer ``.env`` is likely to carry, with a value no test could
#: confuse with the application default.
_LEAK_CANARY = {
    "APP_VERSION": "9.9.9-DOTENV-LEAK",
    "CAP_ZAP_API_KEY": "dotenv-leaked-zap-key",
    "RBAC_TRUSTED_PROXY_SECRET": "dotenv-leaked-proxy-secret",
    "DATABASE_URL": "postgresql+asyncpg://leak:leak@leak.invalid:5432/leak",
}


def _write_env(directory: Path) -> Path:
    lines = [f"{key}={value}" for key, value in _LEAK_CANARY.items()]
    path = directory / ".env"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_settings_class_does_not_load_a_dotenv_file() -> None:
    """The mechanism itself: conftest cleared ``env_file`` before app import."""
    assert Settings.model_config.get("env_file") in (None, [], (), ""), (
        "backend/tests/conftest.py must clear Settings.model_config['env_file'] "
        f"before any app module is imported; found "
        f"{Settings.model_config.get('env_file')!r}"
    )


def test_dotenv_in_cwd_cannot_reconfigure_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control: a poisoned .env sitting in the CWD changes nothing.

    Without the conftest patch this fails on every canary, which is what makes
    the assertion above more than a check of one dict key.
    """
    env_path = _write_env(tmp_path)
    assert env_path.exists()
    monkeypatch.chdir(tmp_path)

    settings = Settings()

    assert settings.app_version != _LEAK_CANARY["APP_VERSION"]
    assert settings.cap_zap_api_key != _LEAK_CANARY["CAP_ZAP_API_KEY"]
    assert (
        settings.rbac_trusted_proxy_secret
        != _LEAK_CANARY["RBAC_TRUSTED_PROXY_SECRET"]
    )
    assert settings.database_url != _LEAK_CANARY["DATABASE_URL"]


def test_canaries_are_outranked_by_process_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What *is* allowed to reconfigure Settings in a test: process env.

    Guards against an over-broad fix (e.g. freezing the whole class), because
    the app-under-test has to stay configurable the way a container is.
    """
    _write_env(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_VERSION", "0.0.0-PROCESS-ENV")

    assert Settings().app_version == "0.0.0-PROCESS-ENV"


def test_reported_version_tracks_the_canonical_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pairing that broke: 16 carriers at 1.0.6-rc1, .env one release behind.

    Asserted against ``Settings`` rather than the fixture-built app, because the
    client fixture constructs the app (and resolves settings) before the test
    body can move the CWD -- a chdir-then-GET /health would have passed no
    matter what. test_health.py covers the HTTP leg on the real app.
    """
    root = Path(__file__).resolve().parents[2]
    canonical = (root / "VERSION").read_text("utf-8").strip()
    _write_env(tmp_path)
    monkeypatch.chdir(tmp_path)

    assert Settings().app_version == canonical
