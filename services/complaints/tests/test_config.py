"""Configuration: refuse to start without required identity settings; no insecure fallback."""

import pytest

from complaints.config import ConfigError, Settings


def env(monkeypatch, tmp_path, **kv):
    for k in ("COMPLAINTS_AUTH_MODE", "CLERK_ISSUER", "CLERK_JWKS_URL", "COMPLAINTS_DEMO_JWT_SECRET",
              "CLERK_AUTHORIZED_PARTIES", "COMPLAINTS_CORS_ORIGINS", "INVESTIGATOR_ALLOWLIST",
              "ADMIN_BOOTSTRAP_EMAILS", "CLERK_SECRET_KEY", "CLERK_BACKEND_LOOKUP", "GATEWAY_JWT_SECRET"):
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
    env(monkeypatch, tmp_path, CLERK_ISSUER="http://c.example", CLERK_JWKS_URL="https://c.example/j")
    with pytest.raises(ConfigError, match="https"):
        Settings.from_env().validate()
    env(monkeypatch, tmp_path, CLERK_ISSUER="https://c.example", CLERK_JWKS_URL="http://c.example/j")
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
    env(monkeypatch, tmp_path, CLERK_ISSUER="https://c.example", CLERK_JWKS_URL="https://c.example/j")
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
