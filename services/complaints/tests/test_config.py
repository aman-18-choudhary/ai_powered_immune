"""Configuration: refuse to start without required identity settings; no insecure fallback."""

import pytest

from complaints.config import ConfigError, Settings


def env(monkeypatch, tmp_path, **kv):
    for k in (
        "COMPLAINTS_AUTH_MODE",
        "CLERK_ISSUER",
        "CLERK_JWKS_URL",
        "COMPLAINTS_DEMO_JWT_SECRET",
        "CLERK_AUTHORIZED_PARTIES",
        "COMPLAINTS_CORS_ORIGINS",
        "INVESTIGATOR_ALLOWLIST",
        "ADMIN_BOOTSTRAP_EMAILS",
        "CLERK_SECRET_KEY",
        "CLERK_BACKEND_LOOKUP",
        "GATEWAY_JWT_SECRET",
        "COMPLAINTS_LOCAL_AUTH_OUTBOX",
        "COMPLAINTS_ENV",
        "COMPLAINTS_EVIDENCE_KEY",
    ):
        monkeypatch.delenv(k, raising=False)  # fmt: skip
    monkeypatch.setenv("COMPLAINTS_DATABASE_URL", "sqlite://")
    monkeypatch.setenv("COMPLAINTS_EVIDENCE_DIR", str(tmp_path / "ev"))
    for k, v in kv.items():
        monkeypatch.setenv(k, v)


def test_default_mode_is_clerk_and_requires_issuer_and_jwks(monkeypatch, tmp_path):
    env(monkeypatch, tmp_path)
    with pytest.raises(ConfigError, match="CLERK_ISSUER"):
        Settings.from_env().validate()
    env(monkeypatch, tmp_path, CLERK_ISSUER="https://c.example")
    with pytest.raises(ConfigError, match="CLERK_JWKS_URL"):
        Settings.from_env().validate()
    env(monkeypatch, tmp_path, CLERK_ISSUER="https://c.example",
        CLERK_JWKS_URL="https://c.example/.well-known/jwks.json")  # fmt: skip
    s = Settings.from_env()
    s.validate()
    assert s.auth_mode == "clerk"


def test_insecure_urls_and_wildcard_cors_refused(monkeypatch, tmp_path):
    env(
        monkeypatch, tmp_path, CLERK_ISSUER="http://c.example", CLERK_JWKS_URL="https://c.example/j"
    )
    with pytest.raises(ConfigError, match="https"):
        Settings.from_env().validate()
    env(
        monkeypatch, tmp_path, CLERK_ISSUER="https://c.example", CLERK_JWKS_URL="http://c.example/j"
    )
    with pytest.raises(ConfigError, match="https"):
        Settings.from_env().validate()
    env(monkeypatch, tmp_path, CLERK_ISSUER="https://c.example", CLERK_JWKS_URL="https://c.example/j",
        COMPLAINTS_CORS_ORIGINS="*")  # fmt: skip
    with pytest.raises(ConfigError, match="CORS"):
        Settings.from_env().validate()


def test_demo_mode_needs_explicit_flag_and_strong_secret(monkeypatch, tmp_path):
    env(monkeypatch, tmp_path, COMPLAINTS_AUTH_MODE="demo")
    with pytest.raises(ConfigError, match="DEMO_JWT_SECRET"):
        Settings.from_env().validate()
    env(monkeypatch, tmp_path, COMPLAINTS_AUTH_MODE="demo", COMPLAINTS_DEMO_JWT_SECRET="short")
    with pytest.raises(ConfigError, match="32"):
        Settings.from_env().validate()
    env(monkeypatch, tmp_path, COMPLAINTS_AUTH_MODE="demo", COMPLAINTS_DEMO_JWT_SECRET="s" * 40)
    Settings.from_env().validate()
    env(monkeypatch, tmp_path, COMPLAINTS_AUTH_MODE="whatever")
    with pytest.raises(ConfigError, match="COMPLAINTS_AUTH_MODE"):
        Settings.from_env().validate()


def test_database_url_required_from_env(monkeypatch, tmp_path):
    env(
        monkeypatch,
        tmp_path,
        CLERK_ISSUER="https://c.example",
        CLERK_JWKS_URL="https://c.example/j",
    )
    monkeypatch.delenv("COMPLAINTS_DATABASE_URL")
    with pytest.raises(ConfigError, match="COMPLAINTS_DATABASE_URL"):
        Settings.from_env().validate()


def test_secret_key_not_in_repr(monkeypatch, tmp_path):
    env(monkeypatch, tmp_path, CLERK_ISSUER="https://c.example", CLERK_JWKS_URL="https://c.example/j",
        CLERK_SECRET_KEY="sk_test_hunter2", COMPLAINTS_DEMO_JWT_SECRET="d" * 40)  # fmt: skip
    s = Settings.from_env()
    assert "hunter2" not in repr(s) and "d" * 40 not in repr(s) and "hunter2" not in str(s)


def test_service_refuses_to_start_without_env(monkeypatch, tmp_path):
    env(monkeypatch, tmp_path)
    from complaints.api import create_service_app

    with pytest.raises(ConfigError):
        create_service_app()


def test_local_auth_outbox_is_test_only_and_refused_in_clerk_mode(monkeypatch, tmp_path):
    env(monkeypatch, tmp_path, CLERK_ISSUER="https://clerk.example", CLERK_JWKS_URL="https://clerk.example/jwks",
        COMPLAINTS_LOCAL_AUTH_OUTBOX=str(tmp_path / "outbox.jsonl"))  # fmt: skip
    with pytest.raises(ConfigError, match="test-only"):
        Settings.from_env().validate()
    env(monkeypatch, tmp_path, COMPLAINTS_AUTH_MODE="local", COMPLAINTS_DEMO_JWT_SECRET="s" * 40,
        COMPLAINTS_LOCAL_AUTH_OUTBOX=str(tmp_path / "outbox.jsonl"))  # fmt: skip
    s = Settings.from_env()
    s.validate()
    assert s.local_auth_outbox == tmp_path / "outbox.jsonl"


CLERK = {"CLERK_ISSUER": "https://c.example", "CLERK_JWKS_URL": "https://c.example/j"}
KEY = "A" * 43 + "="  # urlsafe base64 of 32 bytes (test value)


def test_production_refuses_every_development_option(monkeypatch, tmp_path):
    # finding 5: the development sign-in must not be possible in production
    for mode in ("demo", "local"):
        env(monkeypatch, tmp_path, COMPLAINTS_ENV="production", COMPLAINTS_AUTH_MODE=mode,
            COMPLAINTS_DEMO_JWT_SECRET="s" * 40)  # fmt: skip
        with pytest.raises(ConfigError, match="production requires COMPLAINTS_AUTH_MODE=clerk"):
            Settings.from_env().validate()
    env(monkeypatch, tmp_path, COMPLAINTS_ENV="production", **CLERK)
    with pytest.raises(ConfigError, match="requires COMPLAINTS_EVIDENCE_KEY"):
        Settings.from_env().validate()  # evidence must be encrypted at rest in production
    env(monkeypatch, tmp_path, COMPLAINTS_ENV="production", COMPLAINTS_EVIDENCE_KEY=KEY,
        COMPLAINTS_CORS_ORIGINS="http://localhost:5173", **CLERK)  # fmt: skip
    with pytest.raises(ConfigError, match="https in production"):
        Settings.from_env().validate()
    env(monkeypatch, tmp_path, COMPLAINTS_ENV="staging", **CLERK)
    with pytest.raises(ConfigError, match="COMPLAINTS_ENV"):
        Settings.from_env().validate()
    env(monkeypatch, tmp_path, COMPLAINTS_ENV="production", COMPLAINTS_EVIDENCE_KEY=KEY,
        COMPLAINTS_CORS_ORIGINS="https://app.example", **CLERK)  # fmt: skip
    s = Settings.from_env()
    s.validate()
    assert s.environment == "production" and s.auth_mode == "clerk"
    # development stays the default and keeps local sign-in available
    env(monkeypatch, tmp_path, COMPLAINTS_AUTH_MODE="local", COMPLAINTS_DEMO_JWT_SECRET="s" * 40)
    s = Settings.from_env()
    s.validate()
    assert s.environment == "development" and s.local_auth_enabled


def test_evidence_key_format(monkeypatch, tmp_path):
    env(monkeypatch, tmp_path, COMPLAINTS_EVIDENCE_KEY="not base64 !!", **CLERK)
    with pytest.raises(ConfigError, match="urlsafe base64"):
        Settings.from_env()
    env(monkeypatch, tmp_path, COMPLAINTS_EVIDENCE_KEY="AAAA", **CLERK)
    with pytest.raises(ConfigError, match="32 bytes"):
        Settings.from_env()
    env(monkeypatch, tmp_path, COMPLAINTS_EVIDENCE_KEY=KEY, **CLERK)
    s = Settings.from_env()
    assert s.evidence_key == bytes(32) and "AAAA" not in repr(s)

