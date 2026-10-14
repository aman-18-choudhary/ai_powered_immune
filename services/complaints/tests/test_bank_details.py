"""Optional bank details on complaints: bank_name, bank_account (suspect/beneficiary), ifsc."""

import pytest
from conftest import MIN, create

BANK = ("bank_name", "bank_account", "ifsc")


async def test_mandatory_only_complaint_has_null_bank_details(client, tok):
    c = await create(client, tok("a"))
    for f in BANK:
        assert f in c and c[f] is None, f
    lst = (await client.get("/complaints", headers=tok("a"))).json()["items"][0]
    for f in BANK:
        assert lst[f] is None


async def test_blank_bank_details_are_null(client, tok):
    c = await create(client, tok("a"), bank_name="  ", bank_account="", ifsc=" ")
    assert all(c[f] is None for f in BANK)


async def test_full_bank_details_round_trip(client, tok, inv):
    c = await create(
        client, tok("a"), bank_name=" State Bank of India ", bank_account="1234 5678 9012",
        ifsc="sbin0001234",
    )  # fmt: skip
    assert c["bank_name"] == "State Bank of India"
    assert c["bank_account"] == "123456789012"
    assert c["ifsc"] == "SBIN0001234"
    # owner and staff see the same values (detail and list)
    for h in (tok("a"), inv):
        d = (await client.get(f"/complaints/{c['id']}", headers=h)).json()
        assert (d["bank_name"], d["bank_account"], d["ifsc"]) == (
            "State Bank of India", "123456789012", "SBIN0001234",
        )  # fmt: skip
        items = (await client.get("/complaints", headers=h)).json()["items"]
        assert items[0]["bank_account"] == "123456789012"


async def test_other_citizen_cannot_see_bank_details(client, tok):
    c = await create(client, tok("a"), bank_account="123456789012")
    r = await client.get(f"/complaints/{c['id']}", headers=tok("b"))
    assert r.status_code == 404 and "123456789012" not in r.text
    items = (await client.get("/complaints", headers=tok("b"))).json()["items"]
    assert items == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("ifsc", "SBIN1234567"),  # 5th character must be 0
        ("ifsc", "SBI00012345"),
        ("ifsc", "SBIN000123"),
        ("ifsc", "SBIN00012345"),
        ("ifsc", "SBIN0-01234"),
        ("bank_account", "12345"),  # too short
        ("bank_account", "1234567890123456789"),  # 19 digits
        ("bank_account", "12345abc90"),
        ("bank_account", "1234-5678-90"),
        ("bank_name", "Bank " * 21),  # > 100
        ("bank_name", "Bad\x00Bank"),
    ],
)
async def test_invalid_bank_details_422_without_echo(client, tok, field, value):
    r = await client.post("/complaints", json={**MIN, field: value}, headers=tok("a"))
    assert r.status_code == 422, r.text
    d = r.json()["detail"][0]
    assert "input" not in d and "ctx" not in d
    assert value.strip()[:40] not in r.text


async def test_staff_search_finds_complaints_by_bank_account_or_ifsc(client, tok, inv):
    a = await create(client, tok("a"), bank_account="555566667777", ifsc="HDFC0000001")
    await create(client, tok("b"), bank_account="111122223333")
    r = await client.get("/complaints", params={"q": "5555 6666 7777"}, headers=inv)
    assert [i["id"] for i in r.json()["items"]] == [a["id"]]
    r = await client.get("/complaints", params={"q": "hdfc0000001"}, headers=inv)
    assert [i["id"] for i in r.json()["items"]] == [a["id"]]
    # a citizen's search stays within their own complaints
    r = await client.get("/complaints", params={"q": "111122223333"}, headers=tok("a"))
    assert r.json()["items"] == []


async def test_idempotency_hash_unchanged_without_bank_details(client, tok):
    """A replay of a request without the new keys keeps working (hash stable)."""
    h = {**tok("a"), "Idempotency-Key": "bank-k1"}
    r1 = await client.post("/complaints", json=MIN, headers=h)
    r2 = await client.post("/complaints", json=MIN, headers=h)
    assert r1.status_code == 201 and r2.status_code == 200
    r3 = await client.post("/complaints", json={**MIN, "ifsc": "SBIN0001234"}, headers=h)
    assert r3.status_code == 409
