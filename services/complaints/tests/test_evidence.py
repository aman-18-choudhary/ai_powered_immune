"""Evidence uploads: magic-byte sniffing, caps, atomic storage, authorisation, streaming."""

import asyncio
import dataclasses
import hashlib
import os
import stat

import httpx
import pytest
from conftest import EXE, JPEG, M4A, MP3, OGG, PDF, PNG, WAV, WEBP, bearer, create


async def up(client, headers, cid, name, data, ctype="application/octet-stream"):
    return await client.post(
        f"/complaints/{cid}/evidence", files={"file": (name, data, ctype)}, headers=headers
    )


@pytest.mark.parametrize(
    "name,data,mime",
    [
        ("shot.png", PNG, "image/png"),
        ("pic.jpg", JPEG, "image/jpeg"),
        ("pic.webp", WEBP, "image/webp"),
        ("bill.pdf", PDF, "application/pdf"),
        ("call.mp3", MP3, "audio/mpeg"),
        ("call.wav", WAV, "audio/wav"),
        ("call.ogg", OGG, "audio/ogg"),
        ("call.m4a", M4A, "audio/mp4"),
    ],
)
async def test_valid_types_stored(client, tok, settings, name, data, mime):
    c = await create(client, tok("a"))
    r = await up(client, tok("a"), c["id"], name, data, "application/x-wrong-on-purpose")
    assert r.status_code == 201, r.text
    ev = r.json()
    assert ev["mime_type"] == mime and ev["status"] == "stored"
    assert ev["size_bytes"] == len(data) and ev["sha256"] == hashlib.sha256(data).hexdigest()
    assert "storage_key" not in ev  # internal path material is never returned
    files = [p for p in settings.evidence_dir.rglob("*") if p.is_file()]
    assert len(files) == 1
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o600
    assert files[0].read_bytes() == data
    assert name not in str(files[0])  # filename is never part of the path


async def test_exe_renamed_png_rejected(client, tok, settings):
    c = await create(client, tok("a"))
    r = await up(client, tok("a"), c["id"], "totally.png", EXE, "image/png")
    assert r.status_code == 415 and r.json()["error"] == "unsupported_file_type"
    assert not [p for p in settings.evidence_dir.rglob("*") if p.is_file()]
    assert (await client.get(f"/complaints/{c['id']}/evidence", headers=tok("a"))).json()["items"] == []


async def test_extension_content_mismatch_rejected(client, tok):
    c = await create(client, tok("a"))
    r = await up(client, tok("a"), c["id"], "invoice.pdf", PNG, "application/pdf")
    assert r.status_code == 415 and r.json()["error"] == "file_type_mismatch"


async def test_html_and_svg_and_zip_rejected(client, tok):
    c = await create(client, tok("a"))
    for name, data in [("a.png", b"<html><script>1</script></html>"),
                       ("a.png", b"<svg xmlns='http://www.w3.org/2000/svg'/>"),
                       ("a.pdf", b"PK\x03\x04" + b"0" * 30), ("a.png", b""),
                       ("a.docx", b"PK\x03\x04" + b"0" * 30)]:  # fmt: skip
        r = await up(client, tok("a"), c["id"], name, data)
        assert r.status_code in (415, 422), (name, r.status_code)


async def test_oversize_rejected_without_row_or_file(make_app, settings, idp):
    s = dataclasses.replace(settings, max_image_bytes=1024, max_audio_bytes=2048)
    app = make_app(settings=s)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        h = bearer(idp.token(sub="o", email="o@example.org"))
        cid = (await create(c, h))["id"]
        r = await up(c, h, cid, "big.png", PNG + b"\x00" * 2000)
        assert r.status_code == 413 and r.json()["error"] == "file_too_large"
        assert (await up(c, h, cid, "ok.png", PNG)).status_code == 201
        assert (await up(c, h, cid, "au.mp3", MP3 + b"\x00" * 1500)).status_code == 201  # audio cap
        r = await up(c, h, cid, "au2.mp3", MP3 + b"\x00" * 3000)
        assert r.status_code == 413
        items = (await c.get(f"/complaints/{cid}/evidence", headers=h)).json()["items"]
        assert len(items) == 2
        assert len([p for p in settings.evidence_dir.rglob("*") if p.is_file()]) == 2
        # no temp files left behind either
        assert not list((settings.evidence_dir / ".tmp").glob("*"))


async def test_too_many_files(make_app, settings, idp):
    s = dataclasses.replace(settings, max_evidence_per_complaint=3)
    app = make_app(settings=s)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        h = bearer(idp.token(sub="o", email="o@example.org"))
        cid = (await create(c, h))["id"]
        for i in range(3):
            assert (await up(c, h, cid, f"{i}.png", PNG + bytes([i]))).status_code == 201
        r = await up(c, h, cid, "x.png", PNG + b"9")
        assert r.status_code == 409 and r.json()["error"] == "evidence_limit_reached"
        assert len([p for p in settings.evidence_dir.rglob("*") if p.is_file()]) == 3


async def test_default_limit_is_ten(settings):
    assert settings.max_evidence_per_complaint == 10
    assert settings.max_image_bytes == 10 * 1024 * 1024
    assert settings.max_audio_bytes == 25 * 1024 * 1024


async def test_path_traversal_filename_neutralised(client, tok, settings):
    c = await create(client, tok("a"))
    r = await up(client, tok("a"), c["id"], "../../../etc/passwd\x00.png", PNG)
    assert r.status_code == 201
    name = r.json()["original_filename"]
    assert "/" not in name and "\\" not in name and ".." not in name and "\x00" not in name
    r = await up(client, tok("a"), c["id"], "..\\..\\evil name<>|.png", PNG + b"1")
    assert r.status_code == 201 and all(ch not in r.json()["original_filename"] for ch in "\\<>|")
    root = settings.evidence_dir.resolve()
    for p in root.rglob("*"):
        assert root in p.resolve().parents or p.resolve() == root
    assert not (settings.evidence_dir.parent / "etc").exists()


async def test_failed_write_leaves_no_stored_row(client, tok, settings, monkeypatch):
    c = await create(client, tok("a"))

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr("complaints.evidence.os.replace", boom)
    r = await up(client, tok("a"), c["id"], "a.png", PNG)
    assert r.status_code == 503 and r.json()["error"] == "evidence_storage_failed"
    assert "disk full" not in r.text
    monkeypatch.undo()
    assert (await client.get(f"/complaints/{c['id']}/evidence", headers=tok("a"))).json()["items"] == []
    assert not [p for p in settings.evidence_dir.rglob("*") if p.is_file()]


async def test_db_failure_after_write_removes_file(client, tok, settings, monkeypatch):
    c = await create(client, tok("a"))
    from complaints import store as store_mod

    def boom(self, *a, **k):
        raise store_mod.StoreError("injected")

    monkeypatch.setattr(store_mod.ComplaintStore, "add_evidence", boom)
    r = await up(client, tok("a"), c["id"], "a.png", PNG)
    assert r.status_code >= 500
    assert not [p for p in settings.evidence_dir.rglob("*") if p.is_file()]


async def test_hash_mismatch_after_write_is_not_stored(client, tok, settings, monkeypatch):
    c = await create(client, tok("a"))
    from complaints import evidence as ev_mod

    monkeypatch.setattr(ev_mod, "_rehash", lambda path: ("0" * 64, 1))
    r = await up(client, tok("a"), c["id"], "a.png", PNG)
    assert r.status_code == 503
    assert (await client.get(f"/complaints/{c['id']}/evidence", headers=tok("a"))).json()["items"] == []
    assert not [p for p in settings.evidence_dir.rglob("*") if p.is_file()]


async def test_download_authz_and_headers(client, tok, inv, admin):
    c = await create(client, tok("a"))
    ev = (await up(client, tok("a"), c["id"], "my shot.png", PNG)).json()
    url = f"/complaints/{c['id']}/evidence/{ev['id']}/download"
    for h in (tok("a"), inv, admin):
        r = await client.get(url, headers=h)
        assert r.status_code == 200 and r.content == PNG
        assert r.headers["content-type"] == "image/png"
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["content-disposition"].startswith("attachment;")
        assert "filename=" in r.headers["content-disposition"]
        assert r.headers["cache-control"] == "no-store"
    assert (await client.get(url, headers=tok("b"))).status_code == 404
    assert (await client.get(url)).status_code == 401
    # wrong complaint id paired with a real evidence id
    c2 = await create(client, tok("a"))
    r = await client.get(f"/complaints/{c2['id']}/evidence/{ev['id']}/download", headers=tok("a"))
    assert r.status_code == 404


async def test_upload_authz(client, tok, inv, admin):
    c = await create(client, tok("a"))
    assert (await up(client, tok("b"), c["id"], "a.png", PNG)).status_code == 404
    assert (await up(client, inv, c["id"], "a.png", PNG)).status_code == 403  # staff read only
    assert (await up(client, admin, c["id"], "a.png", PNG)).status_code == 403
    assert (await client.get(f"/complaints/{c['id']}/evidence", headers=tok("b"))).status_code == 404
    assert (await client.get(f"/complaints/{c['id']}/evidence", headers=inv)).status_code == 200


async def test_upload_blocked_for_closed_and_deleted_complaints(client, tok, inv):
    c = await create(client, tok("a"))
    await client.patch(f"/complaints/{c['id']}/status", json={"status": "REJECTED"}, headers=inv)
    r = await up(client, tok("a"), c["id"], "a.png", PNG)
    assert r.status_code == 409
    d = await create(client, tok("a"))
    await client.delete(f"/complaints/{d['id']}", headers=tok("a"))
    assert (await up(client, tok("a"), d["id"], "a.png", PNG)).status_code == 404


async def test_one_file_per_request_and_no_extra_fields(client, tok):
    c = await create(client, tok("a"))
    files = [("file", ("a.png", PNG, "image/png")), ("file", ("b.png", PNG + b"1", "image/png"))]
    r = await client.post(f"/complaints/{c['id']}/evidence", files=files, headers=tok("a"))
    assert r.status_code == 422
    r = await client.post(f"/complaints/{c['id']}/evidence", data={"x": "1"}, headers=tok("a"))
    assert r.status_code == 422
    r = await client.post(f"/complaints/{c['id']}/evidence", json={"a": 1}, headers=tok("a"))
    assert r.status_code == 415 or r.status_code == 422


async def test_upload_is_streamed_with_hard_cap(make_app, settings, idp):
    """A chunked body far over the cap is aborted early; the consumer sees few chunks."""
    s = dataclasses.replace(settings, max_image_bytes=64 * 1024)
    app = make_app(settings=s)
    h = bearer(idp.token(sub="s", email="s@example.org"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        cid = (await create(c, h))["id"]
        boundary = "BOUNDARY"
        pulled = 0

        async def body():
            nonlocal pulled
            yield (
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.png\"\r\n"
                f"Content-Type: image/png\r\n\r\n"
            ).encode() + PNG
            for _ in range(2000):  # 2000 x 64 KiB = 125 MiB if fully consumed
                pulled += 1
                yield b"\x00" * 65536
            yield f"\r\n--{boundary}--\r\n".encode()

        r = await c.post(
            f"/complaints/{cid}/evidence",
            content=body(),
            headers={**h, "Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        assert r.status_code == 413
        assert pulled < 50
        assert not [p for p in settings.evidence_dir.rglob("*") if p.is_file()]


async def test_declared_content_length_over_cap_rejected_before_read(make_app, settings, idp):
    s = dataclasses.replace(settings, max_image_bytes=1024, max_audio_bytes=1024)
    app = make_app(settings=s)
    h = bearer(idp.token(sub="s", email="s@example.org"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        cid = (await create(c, h))["id"]
        r = await c.post(
            f"/complaints/{cid}/evidence",
            content=b"x",
            headers={**h, "Content-Type": "multipart/form-data; boundary=b",
                     "Content-Length": str(10 * 1024 * 1024)},
        )  # fmt: skip
        assert r.status_code == 413


async def test_concurrent_uploads_respect_cap(make_app, settings, idp):
    s = dataclasses.replace(settings, max_evidence_per_complaint=4)
    app = make_app(settings=s)
    h = bearer(idp.token(sub="s", email="s@example.org"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        cid = (await create(c, h))["id"]
        rs = await asyncio.gather(
            *[up(c, h, cid, f"{i}.png", PNG + bytes([i])) for i in range(10)]
        )
        codes = sorted(r.status_code for r in rs)
        assert codes.count(201) == 4 and codes.count(409) == 6
        items = (await c.get(f"/complaints/{cid}/evidence", headers=h)).json()["items"]
        assert len(items) == 4
        assert len([p for p in settings.evidence_dir.rglob("*") if p.is_file()]) == 4


async def test_evidence_dir_modes(client, settings, tok):
    c = await create(client, tok("a"))
    await up(client, tok("a"), c["id"], "a.png", PNG)
    assert stat.S_IMODE(os.stat(settings.evidence_dir).st_mode) == 0o700


async def test_missing_file_on_disk_is_honest_404(client, tok, settings):
    c = await create(client, tok("a"))
    ev = (await up(client, tok("a"), c["id"], "a.png", PNG)).json()
    for p in settings.evidence_dir.rglob("*"):
        if p.is_file():
            p.unlink()
    r = await client.get(f"/complaints/{c['id']}/evidence/{ev['id']}/download", headers=tok("a"))
    assert r.status_code == 404 and r.json()["error"] == "evidence_file_missing"
