"""Fraud DNA (Phase 8): complaints described in similar words, found by sentence embeddings.

What it is: the title and description of a complaint, with identifiers removed, are turned into a
vector by a local multilingual sentence-embedding model and compared by cosine similarity. A high
similarity means the *descriptions* are alike (often the same scam method). It is **not** proof
that the same person, group or crime is involved, and it is a different thing from a shared
identifier (Phase 9, ``network_store``), which the response states separately.

Text (``dna_text``): title + description only. Links, email and UPI addresses, IFSC codes and
digit runs of 8 or more digits (phone, account, Aadhaar, card numbers) are replaced by
placeholders before embedding, so the vector reflects the method, not the identifiers, and no
identifier is sent to the model. Amounts (up to 7 digits) are kept. Nothing else is stored: the
index keeps the complaint id and a hash of the text, never the text.

Model: ``sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`` (384 dimensions,
Apache-2.0), loaded from the local Hugging Face cache only (``local_files_only=True``); nothing is
downloaded at runtime. Without the optional ``dna`` extra or the cached model the feature reports
``unavailable`` with the reason; it never falls back to invented matches.

Index: Qdrant in embedded (local) mode; one collection per model and dimension (a model change
starts a new collection). ``COMPLAINTS_DNA_DIR`` keeps it on disk; without it the index lives in
memory and is rebuilt from the database at start-up. The index is derived data: ``sync`` makes it
match the database (adds new or changed complaints, removes deleted, opted-out and too-short ones)
and is idempotent.

Privacy: only *matchable* complaints (not deleted, owner allows case matching) are indexed, and
every query re-checks the candidates against the database at request time with the same filter
as the complaint network, so an excluded complaint cannot appear or be counted even before the
index has caught up. Staff only (see ``dna_api``).
"""

import asyncio
import contextlib
import hashlib
import logging
import re
import threading
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import and_, func, select

from .network_store import NetworkStore, _matchable
from .schema import analyses, complaint_entities, complaints, users
from .store import ComplaintStore, NotFound

log = logging.getLogger("complaints.dna")

DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DNA_TEXT_VERSION = "dna-text-v1"
MAX_TEXT_CHARS = 2000
MIN_TEXT_LETTERS = 20  # fewer letters after redaction = too little to compare

# Thresholds on cosine similarity, chosen by a rule fixed in advance (lowest value with pairwise
# precision >= 0.90 / >= 0.98) on the DEV families of the SYNTHETIC set
# evaluation/datasets/fraud_dna_v1.jsonl; on its held-out TEST families they give precision 0.82
# / 0.91 and recall 0.52 / 0.35 (evaluation/results/fraud_dna_v1__phase8.json). Tied to the
# model; not calibrated on real complaints.
SIMILARITY_THRESHOLD = 0.58
STRONG_THRESHOLD = 0.62

MAX_CANDIDATES = 200  # nearest points read from the index per query (before the privacy re-check)
SYNC_BATCH = 64
SYNC_INTERVAL_S = 300.0

DISCLAIMER = (
    "Similar descriptions suggest a similar scam method. They are not proof that the same "
    "person, group or crime is involved."
)
LINKING_OFF = (
    "Case matching is off for this complaint at the complainant's request. It is not compared "
    "with other complaints."
)
STATUS_TEXT = {
    "disabled": "Fraud DNA is turned off on this server (COMPLAINTS_DNA_ENABLED=0).",
    "engine_not_installed": "The embedding library is not installed (optional 'dna' extra).",
    "model_not_available_offline": "The embedding model is not in the local cache; nothing is "
    "downloaded at runtime.",
    "index_unavailable": "The similarity index (Qdrant) could not be opened.",
    "load_failed": "The embedding model could not be loaded.",
    "loading": "The embedding model is loading. Try again in a moment.",
    "embedding_failed": "This complaint's text could not be embedded.",
    "insufficient_text": "This complaint has too little text to compare.",
}

_URL_RE = re.compile(r"(?:\b(?:h(?:tt|xx)ps?)://|\bwww\.)\S+", re.IGNORECASE)
_EMAILISH_RE = re.compile(r"\b[\w.+-]{2,}@[\w-]{2,}(?:\.[\w-]{2,})*\b")
_IFSC_RE = re.compile(r"\b[A-Za-z]{4}0[A-Za-z0-9]{6}\b")
_LONG_DIGITS_RE = re.compile(r"\+?\d(?:[\d\s\-.]{6,}\d)")
_SPACES_RE = re.compile(r"\s+")
_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)
_PLACEHOLDER_RE = re.compile(r"\[(?:link|address|ifsc|number)\]")


def _digits_redact(m: re.Match[str]) -> str:
    return "[number]" if sum(ch.isdigit() for ch in m.group(0)) >= 8 else m.group(0)


def dna_text(title: str | None, description: str | None) -> str | None:
    """The privacy-filtered text that is embedded, or None when too little remains."""
    raw = "\n".join(p.strip() for p in (title or "", description or "") if p and p.strip())
    if not raw:
        return None
    t = _URL_RE.sub("[link]", raw)
    t = _EMAILISH_RE.sub("[address]", t)
    t = _IFSC_RE.sub("[ifsc]", t)
    t = _LONG_DIGITS_RE.sub(_digits_redact, t)
    t = _SPACES_RE.sub(" ", t).strip()[:MAX_TEXT_CHARS]
    # placeholders do not count as text (a report made only of identifiers has nothing to compare)
    if len(_LETTER_RE.findall(_PLACEHOLDER_RE.sub(" ", t))) < MIN_TEXT_LETTERS:
        return None
    return t


def text_hash(model_id: str, text: str) -> str:
    return hashlib.sha256(f"{DNA_TEXT_VERSION}\x00{model_id}\x00{text}".encode()).hexdigest()


def band(score: float) -> str:
    return "strong" if score >= STRONG_THRESHOLD else "moderate"


class Embedder(Protocol):
    model_id: str

    def status(self) -> dict[str, Any]: ...

    def load(self) -> None: ...

    @property
    def dim(self) -> int: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class EmbedderUnavailable(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _cached_revision(model: str) -> str | None:
    """The snapshot of ``model`` in the local Hugging Face cache (no network), or None."""
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:
        return None
    path = try_to_load_from_cache(model, "modules.json")
    if not isinstance(path, str):
        return None
    return Path(path).parent.name  # .../snapshots/<revision>/modules.json


class SentenceTransformerEmbedder:
    """The cached sentence-transformers model, CPU, offline only. Loaded once, on first use."""

    def __init__(self, model_id: str = DEFAULT_MODEL) -> None:
        self.model_id = model_id
        self._model: Any = None
        self._dim = 0
        self._revision: str | None = None
        self._reason: str | None = None
        self._loading = False
        self._lock = threading.Lock()

    @property
    def dim(self) -> int:
        return self._dim

    def status(self) -> dict[str, Any]:
        base = {"model": self.model_id, "revision": self._revision, "dimensions": self._dim or None}
        if self._model is not None:
            return {**base, "status": "ready", "reason": None}
        if self._reason:
            return {**base, "status": "unavailable", "reason": self._reason}
        if self._loading:
            return {**base, "status": "loading", "reason": None}
        return {**base, "status": "not_loaded", "reason": None}

    def load(self) -> None:
        with self._lock:
            if self._model is not None:
                return
            if self._reason:
                raise EmbedderUnavailable(self._reason)
            self._loading = True
            try:
                try:
                    from sentence_transformers import SentenceTransformer
                except ImportError:
                    self._reason = "engine_not_installed"
                    raise EmbedderUnavailable(self._reason) from None
                self._revision = _cached_revision(self.model_id)
                if self._revision is None:
                    self._reason = "model_not_available_offline"
                    raise EmbedderUnavailable(self._reason)
                try:
                    model = SentenceTransformer(
                        self.model_id, device="cpu", local_files_only=True,
                        revision=self._revision,
                    )  # fmt: skip
                    dim = int(model.get_embedding_dimension())
                except Exception as exc:  # corrupt cache, incompatible versions
                    log.warning("dna model load failed: %s", type(exc).__name__)
                    self._reason = "load_failed"
                    raise EmbedderUnavailable(self._reason) from None
                self._model, self._dim = model, dim
            finally:
                self._loading = False

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._model is None:
            self.load()
        vecs = self._model.encode(
            texts, batch_size=16, normalize_embeddings=True, convert_to_numpy=True,
            show_progress_bar=False,
        )  # fmt: skip
        return [[float(x) for x in v] for v in vecs]


class IndexUnavailable(Exception):
    pass


class DnaIndex:
    """Qdrant embedded mode. All calls are serialised (the local client is not thread-safe)."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._client: Any = None
        self.collection: str | None = None
        self._lock = threading.Lock()

    @staticmethod
    def collection_name(model_id: str, dim: int) -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", model_id.casefold()).strip("_")
        return f"complaint_dna__{DNA_TEXT_VERSION.replace('-', '_')}__{slug}__{dim}"

    def open(self, model_id: str, dim: int) -> None:
        with self._lock:
            name = self.collection_name(model_id, dim)
            if self._client is not None and self.collection == name:
                return
            try:
                from qdrant_client import QdrantClient
                from qdrant_client.models import Distance, VectorParams
            except ImportError:
                raise IndexUnavailable("qdrant_client_not_installed") from None
            try:
                if self._client is None:
                    if self.path is not None:
                        self.path.mkdir(mode=0o700, parents=True, exist_ok=True)
                        # every call is serialised by self._lock but runs on asyncio worker
                        # threads; this flag also skips qdrant-client's SQLite thread probe,
                        # which leaves a connection unclosed (ResourceWarning) in 1.19.1
                        self._client = QdrantClient(
                            path=str(self.path), force_disable_check_same_thread=True
                        )
                    else:
                        self._client = QdrantClient(location=":memory:")
                if not self._client.collection_exists(name):
                    self._client.create_collection(
                        name, vectors_config=VectorParams(size=dim, distance=Distance.COSINE)
                    )
                else:
                    size = self._client.get_collection(name).config.params.vectors.size
                    if size != dim:
                        raise IndexUnavailable("dimension_mismatch")
            except IndexUnavailable:
                raise
            except Exception as exc:  # locked by another process, corrupt files
                log.warning("dna index open failed: %s", type(exc).__name__)
                raise IndexUnavailable("open_failed") from None
            self.collection = name

    @property
    def ready(self) -> bool:
        return self._client is not None and self.collection is not None

    def upsert(self, points: list[tuple[str, list[float], str]]) -> None:
        from qdrant_client.models import PointStruct

        with self._lock:
            self._client.upsert(
                self.collection,
                [PointStruct(id=cid, vector=v, payload={"complaint_id": cid, "text_hash": h})
                 for cid, v, h in points],
            )  # fmt: skip

    def delete(self, ids: Iterable[str]) -> None:
        from qdrant_client.models import PointIdsList

        ids = list(ids)
        if not ids:
            return
        with self._lock:
            self._client.delete(self.collection, points_selector=PointIdsList(points=ids))

    def hashes(self) -> dict[str, str]:
        """complaint id -> text hash of every point (payload only, no vectors)."""
        out: dict[str, str] = {}
        offset = None
        with self._lock:
            while True:
                pts, offset = self._client.scroll(
                    self.collection, limit=512, offset=offset, with_payload=True,
                    with_vectors=False,
                )  # fmt: skip
                for p in pts:
                    out[str(p.id)] = (p.payload or {}).get("text_hash", "")
                if offset is None:
                    return out

    def search(self, vector: list[float], limit: int) -> list[tuple[str, float]]:
        with self._lock:
            res = self._client.query_points(
                self.collection, query=vector, limit=limit, with_payload=False
            )
        return [(str(p.id), float(p.score)) for p in res.points]

    def close(self) -> None:
        with self._lock:
            if self._client is not None:
                with contextlib.suppress(Exception):
                    self._client.close()
            self._client = None
            self.collection = None


class FraudDna:
    """Embedding, index maintenance and similar-complaint queries. One per app."""

    def __init__(
        self,
        store: ComplaintStore,
        embedder: Embedder | None,
        index_path: Path | None,
        *,
        enabled: bool = True,
        background: bool = True,
        sync_interval_s: float = SYNC_INTERVAL_S,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.index = DnaIndex(index_path)
        self.enabled = enabled and embedder is not None
        self.background = background
        self.sync_interval_s = sync_interval_s
        self._dirty = True  # the database changed since the last sync
        self._task: asyncio.Task[None] | None = None
        self._wake: asyncio.Event | None = None
        self._index_reason: str | None = None
        self._ready = False
        self.last_sync: dict[str, Any] | None = None
        self._sync_lock: asyncio.Lock | None = None

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        """Start the background loader/sync task (idempotent; needs a running loop)."""
        if not self.enabled or not self.background:
            return
        if self._task is not None and not self._task.done():
            return
        self._wake = asyncio.Event()
        self._sync_lock = asyncio.Lock()
        self._task = asyncio.get_running_loop().create_task(self._run())

    def notify(self) -> None:
        """A complaint was created or deleted, or a user changed case matching."""
        if not self.enabled:
            return
        self._dirty = True  # the next query syncs first, so it never sees stale membership
        self.start()
        if self._wake is not None:
            self._wake.set()

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        await asyncio.to_thread(self.index.close)

    async def _prepare(self) -> None:
        if self._ready:
            return
        assert self.embedder is not None
        await asyncio.to_thread(self.embedder.load)
        try:
            await asyncio.to_thread(self.index.open, self.embedder.model_id, self.embedder.dim)
        except IndexUnavailable as exc:
            self._index_reason = str(exc)
            raise
        self._index_reason = None
        self._ready = True

    async def _run(self) -> None:
        assert self._wake is not None
        while True:
            try:
                await self.sync()
            except (EmbedderUnavailable, IndexUnavailable):
                return  # reported by status(); nothing to retry without a configuration change
            except Exception as exc:  # keep the service up; next wake retries
                log.warning("dna sync failed: %s", type(exc).__name__)
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), self.sync_interval_s)

    # ------------------------------------------------------------------ status
    def status(self) -> dict[str, Any]:
        if not self.enabled:
            return {
                "status": "unavailable", "reason": "disabled", "message": STATUS_TEXT["disabled"],
                "model": getattr(self.embedder, "model_id", None),
            }  # fmt: skip
        st = self.embedder.status() if self.embedder is not None else {}
        out = {
            "model": st.get("model"), "revision": st.get("revision"),
            "dimensions": st.get("dimensions"), "text_version": DNA_TEXT_VERSION,
            "thresholds": {"similar": SIMILARITY_THRESHOLD, "strong": STRONG_THRESHOLD},
            "index": {"backend": "qdrant", "mode": "local" if self.index.path else "memory",
                      "collection": self.index.collection},
            "last_sync": self.last_sync,
        }  # fmt: skip
        if st.get("status") == "unavailable":
            reason = st.get("reason") or "load_failed"
            return {**out, "status": "unavailable", "reason": reason,
                    "message": STATUS_TEXT.get(reason, reason)}  # fmt: skip
        if self._index_reason:
            return {
                **out, "status": "unavailable", "reason": "index_unavailable",
                "message": STATUS_TEXT["index_unavailable"], "detail": self._index_reason,
            }  # fmt: skip
        if st.get("status") == "ready" and self._ready:
            return {**out, "status": "ready", "reason": None, "message": None}
        return {**out, "status": "loading", "reason": "loading", "message": STATUS_TEXT["loading"]}

    # ------------------------------------------------------------------ data
    def _matchable_texts(self) -> dict[str, str]:
        """complaint id -> DNA text, for every matchable complaint with enough text."""
        out: dict[str, str] = {}
        with self.store.engine.connect() as conn:
            rows = conn.execute(
                select(complaints.c.id, complaints.c.title, complaints.c.description)
                .join(users, users.c.id == complaints.c.owner_user_id)
                .where(_matchable(complaints, users))
            )
            for r in rows:
                t = dna_text(r.title, r.description)
                if t is not None:
                    out[r.id] = t
        return out

    async def sync(self) -> dict[str, Any]:
        """Make the index match the database. Idempotent; returns counts."""
        if not self.enabled:
            raise EmbedderUnavailable("disabled")
        assert self.embedder is not None
        if self._sync_lock is None:
            self._sync_lock = asyncio.Lock()
        async with self._sync_lock:
            await self._prepare()
            self._dirty = False  # a change during this sync marks it dirty again
            t0 = time.monotonic()
            texts = await asyncio.to_thread(self._matchable_texts)
            have = await asyncio.to_thread(self.index.hashes)
            model = self.embedder.model_id
            want = {cid: text_hash(model, t) for cid, t in texts.items()}
            stale = [cid for cid, h in want.items() if have.get(cid) != h]
            gone = [cid for cid in have if cid not in want]
            await asyncio.to_thread(self.index.delete, gone)
            for i in range(0, len(stale), SYNC_BATCH):
                batch = stale[i : i + SYNC_BATCH]
                vecs = await asyncio.to_thread(self.embedder.embed, [texts[c] for c in batch])
                await asyncio.to_thread(
                    self.index.upsert, [(c, v, want[c]) for c, v in zip(batch, vecs, strict=True)]
                )
            self.last_sync = {
                "indexed": len(want), "added_or_updated": len(stale), "removed": len(gone),
                "seconds": round(time.monotonic() - t0, 2),
            }  # fmt: skip
            return self.last_sync

    # ------------------------------------------------------------------ query
    def _centre(self, cid: str) -> tuple[str, str | None]:
        """``(state, text)``; raises NotFound for an unknown or deleted complaint."""
        with self.store.engine.connect() as conn:
            row = conn.execute(
                select(
                    complaints.c.deleted_at, complaints.c.title, complaints.c.description,
                    users.c.allow_case_matching,
                )
                .join(users, users.c.id == complaints.c.owner_user_id)
                .where(complaints.c.id == cid)
            ).first()  # fmt: skip
        if row is None or row.deleted_at is not None:
            raise NotFound()
        if not row.allow_case_matching:
            return "opted_out", None
        return "ok", dna_text(row.title, row.description)

    def _enrich(
        self, cid: str, hits: list[tuple[str, float]], limit: int
    ) -> tuple[list[dict[str, Any]], int]:
        """Re-check privacy at request time, apply the threshold and explain each match."""
        ids = [h for h, _ in hits if h != cid]
        if not ids:
            return [], 0
        with self.store.engine.connect() as conn:
            ok = {
                r.id
                for r in conn.execute(
                    select(complaints.c.id)
                    .join(users, users.c.id == complaints.c.owner_user_id)
                    .where(complaints.c.id.in_(ids), _matchable(complaints, users))
                )
            }
            kept = [(h, s) for h, s in hits if h in ok and s >= SIMILARITY_THRESHOLD]
            total = len(kept)
            kept = kept[:limit]
            show = [cid] + [h for h, _ in kept]
            sums = NetworkStore._summaries(conn, show)
            codes = self._rule_codes(conn, show)
            links = self._identifier_links(conn, cid, [h for h, _ in kept])
        me = sums.get(cid, {})
        items = []
        for h, s in kept:
            if h not in sums:
                continue
            shared = sorted(codes.get(cid, set()) & codes.get(h, set()))
            same_cat = sums[h]["category"] == me.get("category")
            link = links.get(h)
            parts = [f"The descriptions are worded alike (similarity {s:.2f}, {band(s)})."]
            if shared:
                parts.append("Both latest analyses found: " + ", ".join(shared) + ".")
            if same_cat:
                parts.append("Same category chosen by both complainants.")
            if link:
                parts.append(
                    "Separately, they share an identifier (" + link + "; see Related complaints)."
                )
            items.append({
                "complaint": NetworkStore._public(sums[h]), "similarity": round(s, 3),
                "band": band(s), "shared_indicators": shared, "same_category": same_cat,
                "identifier_link": link, "explanation": " ".join(parts),
            })  # fmt: skip
        return items, total

    @staticmethod
    def _rule_codes(conn: Any, ids: list[str]) -> dict[str, set[str]]:
        latest = (
            select(analyses.c.complaint_id, func.max(analyses.c.version).label("v"))
            .where(analyses.c.complaint_id.in_(ids))
            .group_by(analyses.c.complaint_id)
            .subquery()
        )
        out: dict[str, set[str]] = {}
        for r in conn.execute(
            select(analyses.c.complaint_id, analyses.c.indicators).join(
                latest,
                and_(analyses.c.complaint_id == latest.c.complaint_id,
                     analyses.c.version == latest.c.v),
            )
        ):  # fmt: skip
            out[r.complaint_id] = {
                str(i["code"])
                for i in (r.indicators or [])
                if isinstance(i, dict) and i.get("source") == "rule" and i.get("code")
            }
        return out

    @staticmethod
    def _identifier_links(conn: Any, cid: str, others: list[str]) -> dict[str, str]:
        if not others:
            return {}
        me = select(complaint_entities.c.entity_type, complaint_entities.c.value_hash).where(
            complaint_entities.c.complaint_id == cid
        )
        mine = {(r.entity_type, r.value_hash) for r in conn.execute(me)}
        if not mine:
            return {}
        out: dict[str, str] = {}
        for r in conn.execute(
            select(
                complaint_entities.c.complaint_id, complaint_entities.c.entity_type,
                complaint_entities.c.value_hash,
            ).where(complaint_entities.c.complaint_id.in_(others))
        ):  # fmt: skip
            if (r.entity_type, r.value_hash) in mine:
                kind = "inferred" if r.entity_type == "domain" else "observed"
                if out.get(r.complaint_id) != "observed":
                    out[r.complaint_id] = kind
        return out

    async def similar(self, cid: str, limit: int) -> dict[str, Any]:
        base: dict[str, Any] = {
            "complaint_id": cid, "disclaimer": DISCLAIMER, "items": [], "total": 0,
            "limit": limit, "matching": "enabled", "matching_message": None,
            "thresholds": {"similar": SIMILARITY_THRESHOLD, "strong": STRONG_THRESHOLD},
        }  # fmt: skip
        state, text = await asyncio.to_thread(self._centre, cid)  # NotFound -> 404
        if state == "opted_out":
            return {**base, "status": "ok", "matching": "disabled", "matching_message": LINKING_OFF}
        if not self.enabled:
            return {**base, "status": "unavailable", "reason": "disabled",
                    "message": STATUS_TEXT["disabled"]}  # fmt: skip
        if text is None:
            return {**base, "status": "insufficient_text", "reason": "insufficient_text",
                    "message": STATUS_TEXT["insufficient_text"]}  # fmt: skip
        if not self._ready and not self.background:
            with contextlib.suppress(EmbedderUnavailable, IndexUnavailable):
                await self._prepare()
        self.start()
        st = self.status()
        if st["status"] != "ready":
            if st["status"] == "loading":
                self.notify()
            return {**base, "status": st["status"], "reason": st.get("reason"),
                    "message": st.get("message")}  # fmt: skip
        assert self.embedder is not None
        if self._dirty:
            try:
                await self.sync()
            except Exception as exc:  # the request-time privacy re-check still applies
                log.warning("dna sync before query failed: %s", type(exc).__name__)
        try:
            vec = (await asyncio.to_thread(self.embedder.embed, [text]))[0]
            await asyncio.to_thread(
                self.index.upsert, [(cid, vec, text_hash(self.embedder.model_id, text))]
            )
            hits = await asyncio.to_thread(self.index.search, vec, MAX_CANDIDATES)
        except Exception as exc:
            log.warning("dna query failed: %s", type(exc).__name__)
            return {**base, "status": "unavailable", "reason": "embedding_failed",
                    "message": STATUS_TEXT["embedding_failed"]}  # fmt: skip
        items, total = await asyncio.to_thread(self._enrich, cid, hits, limit)
        return {
            **base, "status": "ok", "items": items, "total": total,
            "truncated": len(hits) >= MAX_CANDIDATES,
            "model": {"id": self.embedder.model_id, "revision": st.get("revision")},
        }  # fmt: skip
