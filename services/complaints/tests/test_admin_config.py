"""Phase 12 configuration view: admin only, no secrets, no personal data."""

import dataclasses

from conftest import ADMIN_EMAIL, INVESTIGATOR_EMAIL


async def test_admin_sees_effective_settings_without_secrets(make_app, settings, tok, idp):
    import httpx

    s = dataclasses.replace(settings, clerk_secret_key="sk_test_SECRETVALUE123",
                            ollama_url="http://127.0.0.1:11434", rate_write_per_min=7)  # fmt: skip
    app = make_app(settings=s)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/admin/config", headers=tok("admin"))
        assert r.status_code == 200, r.text
        cfg = r.json()
        assert cfg["environment"] == "development" and cfg["auth"]["mode"] == "clerk"
        assert cfg["auth"]["clerk_issuer_host"] == "clerk.test.example"
        assert cfg["auth"]["investigator_allowlist_size"] == 1
        assert cfg["rate_limits_per_min"]["write"] == 7
        assert cfg["llm"] == {"configured": True, "host": "127.0.0.1", "model": "qwen2.5:0.5b",
                              "timeout_s": 8.0}  # fmt: skip
        assert cfg["storage"]["database"] in ("sqlite", "postgresql")
        blob = r.text
        for secret in ("SECRETVALUE", "sk_test", INVESTIGATOR_EMAIL, ADMIN_EMAIL, "sqlite:///",
                       str(settings.evidence_dir)):  # fmt: skip
            assert secret not in blob
        for who in ("a", "inv"):
            assert (await c.get("/admin/config", headers=tok(who))).status_code == 403
        assert (await c.get("/admin/config")).status_code == 401
