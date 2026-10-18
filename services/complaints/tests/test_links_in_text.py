"""Links written inside the title/description (accuracy batch 2, 2026-10-18).

Extraction is not verification: the links are only parsed and checked lexically, offline.
"""

import socket

import pytest

from complaints.signals import MAX_EMBEDDED_URLS, MAX_URL, extract_urls


def test_extracts_scheme_www_and_defanged_links_with_their_evidence():
    text = ("Update KYC at http://sbi-kyc-update.top/login, or www.sbi.co.in.kyc-update.top/x. "
            "Old notice: hxxp://bank-verify[.]top")  # fmt: skip
    got = extract_urls(text)
    assert [u["normalised"] for u in got] == [
        "http://sbi-kyc-update.top/login", "www.sbi.co.in.kyc-update.top/x",
        "http://bank-verify.top",
    ]  # fmt: skip
    assert [u["defanged"] for u in got] == [False, False, True]
    first = got[0]
    assert text[first["start"]:first["end"]] == first["original"]  # evidence points to the text


def test_trailing_punctuation_and_brackets_are_not_part_of_the_link():
    assert [u["normalised"] for u in extract_urls("see (http://x.example/a).")] == [
        "http://x.example/a"
    ]


@pytest.mark.parametrize("text", ["", "http://", "Rs. 500 paid", "e.g. the bank", "no links here"])
def test_no_false_extraction(text):
    assert extract_urls(text) == []


def test_count_and_length_are_bounded():
    many = " ".join(f"http://a{i}.example" for i in range(20))
    assert len(extract_urls(many)) == MAX_EMBEDDED_URLS
    long = extract_urls("http://x.example/" + "a" * (MAX_URL + 50))
    assert long and long[0]["too_long"] is True


def test_duplicates_are_reported_once():
    assert len(extract_urls("http://a.example http://a.example HTTP://A.EXAMPLE")) == 1


async def test_pipeline_checks_links_in_text_without_any_network_access(monkeypatch):
    pytest.importorskip("call_guard")
    from complaints.analysis import ComplaintAnalysisPipeline

    def no_network(*a, **k):  # any DNS lookup or connection would fail the test
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    out = await ComplaintAnalysisPipeline().analyse({
        "category": "PHISHING", "title": "",
        "description": "Your account will be blocked today. Update KYC at http://sbi-kyc-update.top/login",
        "evidence": [],
    })  # fmt: skip
    links = out.extra["links_in_text"]
    assert len(links) == 1 and links[0]["status"] == "analysed"
    assert links[0]["extracted"] is True and links[0]["verified"] is False
    assert links[0]["fetched"] is False
    url_ind = [i for i in out.indicators if i.get("stage") == "url_heuristics"]
    assert url_ind and all(i["evidence"]["type"] == "text_link" for i in url_ind)
    assert any("not opened or verified" in p["text"] for p in out.extra["explanation_parts"])


async def test_unparseable_link_is_reported_not_scored():
    pytest.importorskip("call_guard")
    from complaints.analysis import ComplaintAnalysisPipeline

    out = await ComplaintAnalysisPipeline().analyse({
        "category": "OTHER", "title": "",
        "description": "Please look at http://[broken-host/path when you have some time today.",
        "evidence": [],
    })  # fmt: skip
    links = out.extra["links_in_text"]
    assert links and links[0]["status"] == "unparseable" and links[0]["indicators"] == []
    assert not [i for i in out.indicators if i.get("stage") == "url_heuristics"]
