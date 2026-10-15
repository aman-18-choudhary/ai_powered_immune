"""GET /models: model facts from the shipped metrics and model card, runtime status, and the
Ollama status without contacting a non-local host (fake transport only; Ollama is not
installed here)."""

import httpx
import pytest
from test_pipeline_v2 import StubCategory, StubScorer, pipe

from complaints.analysis import ComplaintAnalysisPipeline
from complaints.explain import OllamaClient
from complaints.models_info import card_limitations


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_models_requires_auth_any_role(make_app, tok, inv, admin):
    cl = _client(make_app(analysis=pipe()))
    assert (await cl.get("/models")).status_code == 401
    for h in (tok("a"), inv, admin):
        assert (await cl.get("/models", headers=h)).status_code == 200


async def test_category_model_facts_from_metrics_and_card(make_app, tok):
    pytest.importorskip("sklearn")
    cl = _client(make_app(analysis=ComplaintAnalysisPipeline(scorer=StubScorer())))
    m = (await cl.get("/models", headers=tok("a"))).json()
    cat = next(x for x in m["models"] if x["id"] == "category-v1")
    assert cat["source"] == "ml" and cat["version"] == "category-v1"
    assert len(cat["artifact"]["sha256"]) == 64
    assert cat["artifact"]["sha256"] == cat["artifact"]["sha256_recorded_at_training"]
    td = cat["training_data"]
    assert td["synthetic"] is True and "100% synthetic" in td["description"]
    h = cat["metrics"]
    assert h["basis"].startswith("held-out") and h["n"] == 727
    assert round(h["macro_f1"], 3) == 0.715 and round(h["accuracy"], 3) == 0.714
    assert round(h["abstention_rate"], 3) == 0.391 and h["abstention_threshold"] == 0.25
    assert set(h["per_class"]) >= {"PHISHING", "SOCIAL_MEDIA_FRAUD"}
    assert set(h["per_class"]["PHISHING"]) >= {"precision", "recall", "f1", "support"}
    leak = cat["leaky_random_split"]
    assert leak["is_headline"] is False and round(leak["macro_f1"], 3) == 0.985
    assert "NOT the headline" in leak["note"]
    assert any("Not validated on any real complaint" in x for x in cat["limitations"])
    assert cat["runtime"]["status"] == "loaded"
    assert m["synthetic_data_warning"]


async def test_call_guard_classifier_entry_is_honest(make_app, tok):
    pytest.importorskip("call_guard")
    cl = _client(make_app(analysis=ComplaintAnalysisPipeline(category_loader=lambda: None)))
    m = (await cl.get("/models", headers=tok("a"))).json()
    cg = next(x for x in m["models"] if x["id"] == "call_guard.clf-v1")
    assert cg["source"] == "ml" and cg["version"] == "clf-v1"
    assert len(cg["artifact"]["sha256"]) == 64
    assert cg["training_data"]["synthetic"] is True
    assert cg["metrics"] is None and cg["metrics_unavailable_reason"]
    assert cg["limitations"] and cg["runtime"]["status"] in ("loaded", "fallback_rules_only")
    cat = next(x for x in m["models"] if x["id"] == "category-v1")
    assert cat["runtime"] == {"status": "unavailable", "reason": "load_failed"}


async def test_runtime_unavailable_when_scorer_cannot_load(make_app, tok):
    def boom():
        raise ImportError("x")

    cl = _client(make_app(analysis=ComplaintAnalysisPipeline(
        scorer_loader=boom, category_model=StubCategory())))  # fmt: skip
    m = (await cl.get("/models", headers=tok("a"))).json()
    cg = next(x for x in m["models"] if x["id"] == "call_guard.clf-v1")
    assert cg["runtime"] == {"status": "unavailable", "reason": "ImportError"}


async def test_ollama_not_configured_and_never_contacted(make_app, tok):
    cl = _client(make_app(analysis=pipe()))
    m = (await cl.get("/models", headers=tok("a"))).json()
    assert m["llm"] == {
        "provider": "ollama", "status": "not_configured", "model": None, "reason": None,
        "used_for": "explanation wording only; never the score, level or category",
    }  # fmt: skip


@pytest.mark.parametrize(
    ("handler", "status", "reason"),
    [
        (lambda r: httpx.Response(200, json={"models": [{"name": "qwen2.5:3b"}]}), "ready", None),
        (lambda r: httpx.Response(200, json={"models": [{"name": "llama3:8b"}]}), "unavailable",
         "model_not_pulled"),
        (lambda r: (_ for _ in ()).throw(httpx.ConnectError("no", request=r)), "unavailable",
         "unreachable"),
        (lambda r: httpx.Response(200, text="garbage"), "unavailable", "bad_response"),
    ],
)  # fmt: skip
async def test_ollama_status_local_fake_transport(make_app, tok, handler, status, reason):
    cl = _client(
        make_app(
            analysis=pipe(),
            ollama=OllamaClient(
                "http://localhost:11434", "qwen2.5:3b", 5.0, transport=httpx.MockTransport(handler)
            ),
        )
    )
    m = (await cl.get("/models", headers=tok("a"))).json()  # fmt: skip
    assert m["llm"]["status"] == status and m["llm"]["reason"] == reason
    assert m["llm"]["model"] == "qwen2.5:3b"


async def test_non_local_ollama_host_is_not_contacted(make_app, tok):
    calls: list = []

    def h(r):
        calls.append(r)
        return httpx.Response(200, json={"models": [{"name": "qwen2.5:3b"}]})

    client = OllamaClient("https://llm.example.com", "qwen2.5:3b", 5.0,
                          transport=httpx.MockTransport(h))  # fmt: skip
    cl = _client(make_app(analysis=pipe(StubScorer(0.8), explainer=client), ollama=client))
    m = (await cl.get("/models", headers=tok("a"))).json()
    assert m["llm"]["status"] == "unavailable" and m["llm"]["reason"] == "non_local_host"
    r = await client.explain("some text here", {"indicators": []})
    assert r.status == "unavailable" and r.reason == "non_local_host"
    assert await client.probe() == "non_local_host"
    assert calls == []


def test_card_limitations_parsed():
    lim = card_limitations()
    assert len(lim) >= 5 and all(x and not x.startswith("*") for x in lim)
    assert any("calibration" in x for x in lim)
