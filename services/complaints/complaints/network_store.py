"""Complaint network queries (Phase 9), plain SQL over ``complaint_entities`` (no graph database).

Link types (owner decision)
* **observed**: two complaints share exactly the same normalised identifier (any type except
  ``domain``).
* **inferred**: the only thing two complaints share is the registrable domain of their links,
  with different URLs. A lead, never proof.
* **confirmed** / **rejected**: the latest investigator review of the pair (``link_reviews``,
  highest ``seq`` wins; every review is kept). A review is only accepted for a pair that has an
  observed or inferred relation at that moment; ``basis`` keeps which one.

Nothing else is inferred and no link row is ever created to fill a graph: relations are computed
from the stored identifiers at query time.

Privacy: a complaint is *matchable* only while it is not soft-deleted and its owner's
``users.allow_case_matching`` is true, evaluated in every query (a change takes effect on the
next request). A complaint that is not matchable never appears as related, in a graph, a cluster
or a search result; asked about directly, it reports that linking is off and nothing else.

Bounds: graph depth 1 or 2, caps on rows, complaint and identifier nodes and edges, and on
clusters; every response says whether a cap was hit (``truncated``).
"""

import hashlib
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, func, insert, or_, select
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from .schema import (
    ENTITY_TYPES,
    analyses,
    complaint_entities,
    complaints,
    link_reviews,
    users,
)
from .store import ComplaintStore, Conflict, NotFound, new_id

DISCLAIMER = "Shared identifiers are leads, not proof that the same person is involved."
LINKING_OFF = (
    "Linking is off for this complaint at the complainant's request. It is not compared with "
    "other complaints."
)
# caps (module constants so tests can lower them)
MAX_RELATED_ROWS = 1000  # shared-identifier rows read for one complaint
MAX_GRAPH_ROWS = 2000  # identifier rows read per graph level
MAX_GRAPH_COMPLAINTS = 40
MAX_GRAPH_ENTITIES = 60
MAX_GRAPH_EDGES = 250
MAX_ENTITY_MEMBERS = 25  # complaints added through one identifier
MAX_CLUSTER_ROWS = 20000
MAX_CLUSTER_MEMBERS_LISTED = 20
MAX_CLUSTER_IDS = 200
LINK_ORDER = {"confirmed": 0, "observed": 1, "inferred": 2, "rejected": 3}
TYPE_ORDER = {t: i for i, t in enumerate(ENTITY_TYPES)}
FIELD_LABEL = {
    "suspect_phone": "suspect phone", "upi_id": "UPI ID", "suspect_email": "suspect email",
    "suspicious_url": "suspicious link", "bank_account": "bank account",
    "social_account": "social account",
}  # fmt: skip

ce = complaint_entities


def _aware(dt: Any) -> Any:
    if isinstance(dt, datetime) and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _iso(dt: Any) -> str | None:
    dt = _aware(dt)
    return dt.astimezone(UTC).isoformat() if isinstance(dt, datetime) else None


def _matchable(c: Any, u: Any) -> Any:
    return and_(c.c.deleted_at.is_(None), u.c.allow_case_matching.is_(True))


def _relation(types: Iterable[str]) -> str:
    return "observed" if any(t != "domain" for t in types) else "inferred"


class NetworkStore:
    def __init__(self, store: ComplaintStore) -> None:
        self.store = store

    # ------------------------------------------------------------------ helpers
    def _state(self, conn: Connection, cid: str) -> str:
        """``ok``, ``opted_out``; raises NotFound for an unknown or soft-deleted complaint."""
        row = conn.execute(
            select(complaints.c.deleted_at, users.c.allow_case_matching)
            .join(users, users.c.id == complaints.c.owner_user_id)
            .where(complaints.c.id == cid)
        ).first()
        if row is None or row.deleted_at is not None:
            raise NotFound()
        return "ok" if row.allow_case_matching else "opted_out"

    @staticmethod
    def _summaries(conn: Connection, ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        ids = list(dict.fromkeys(ids))
        if not ids:
            return {}
        rows = conn.execute(
            select(
                complaints.c.id, complaints.c.title, complaints.c.category, complaints.c.status,
                complaints.c.created_at, complaints.c.ifsc,
            ).where(complaints.c.id.in_(ids))
        ).all()  # fmt: skip
        latest = (
            select(analyses.c.complaint_id, func.max(analyses.c.version).label("v"))
            .where(analyses.c.complaint_id.in_(ids))
            .group_by(analyses.c.complaint_id)
            .subquery()
        )
        levels: dict[str, str | None] = {}
        for r in conn.execute(
            select(analyses.c.complaint_id, analyses.c.risk_level, analyses.c.detail).join(
                latest,
                and_(
                    analyses.c.complaint_id == latest.c.complaint_id,
                    analyses.c.version == latest.c.v,
                ),
            )
        ):
            d = r.detail if isinstance(r.detail, dict) else {}
            levels[r.complaint_id] = d.get("risk_level") or r.risk_level
        return {
            r.id: {
                "id": r.id, "title": r.title, "category": r.category, "status": r.status,
                "risk_level": levels.get(r.id), "created_at": _iso(r.created_at), "_ifsc": r.ifsc,
            }
            for r in rows
        }  # fmt: skip

    @staticmethod
    def _public(summary: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in summary.items() if not k.startswith("_")}

    @staticmethod
    def _reviews(conn: Connection, pairs: Iterable[tuple[str, str]]) -> dict[tuple[str, str], Any]:
        """Latest review and count per ordered pair."""
        pairs = {tuple(sorted(p)) for p in pairs}
        if not pairs:
            return {}
        lows = {p[0] for p in pairs} | {p[1] for p in pairs}
        rows = conn.execute(
            select(link_reviews)
            .where(link_reviews.c.complaint_low.in_(lows), link_reviews.c.complaint_high.in_(lows))
            .order_by(link_reviews.c.seq)
        ).all()
        out: dict[tuple[str, str], Any] = {}
        counts: dict[tuple[str, str], int] = defaultdict(int)
        for r in rows:
            key = (r.complaint_low, r.complaint_high)
            if key in pairs:
                out[key] = r
                counts[key] += 1
        return {k: (v, counts[k]) for k, v in out.items()}

    @staticmethod
    def _review_view(row: Any, count: int | None = None) -> dict[str, Any]:
        out = {
            "id": row.id, "decision": row.decision, "basis": row.basis,
            "reviewer_user_id": row.reviewer_user_id, "note": row.note,
            "created_at": _iso(row.created_at),
        }  # fmt: skip
        if count is not None:
            out["review_count"] = count
        return out

    def _shared_rows(
        self, conn: Connection, cid: str, cap: int, other: str | None = None
    ) -> list[Any]:
        e1, e2 = ce.alias("e1"), ce.alias("e2")
        c2, u2 = complaints.alias("c2"), users.alias("u2")
        q = (
            select(
                e2.c.complaint_id.label("other"), e1.c.entity_type, e1.c.value_hash,
                e1.c.display, e1.c.source_field.label("this_field"),
                e2.c.source_field.label("other_field"), c2.c.created_at,
            )
            .select_from(
                e1.join(e2, and_(e1.c.entity_type == e2.c.entity_type,
                                 e1.c.value_hash == e2.c.value_hash,
                                 e2.c.complaint_id != e1.c.complaint_id))
                .join(c2, c2.c.id == e2.c.complaint_id)
                .join(u2, u2.c.id == c2.c.owner_user_id)
            )
            .where(e1.c.complaint_id == cid, _matchable(c2, u2))
            .order_by(c2.c.created_at.desc(), e2.c.complaint_id, e1.c.entity_type)
            .limit(cap + 1)
        )  # fmt: skip
        if other is not None:
            q = q.where(e2.c.complaint_id == other)
        return list(conn.execute(q).all())

    @staticmethod
    def _group(rows: Iterable[Any]) -> dict[str, dict[str, Any]]:
        by: dict[str, dict[str, Any]] = {}
        for r in rows:
            g = by.setdefault(r.other, {"rows": [], "created_at": _aware(r.created_at)})
            g["rows"].append(r)
        for g in by.values():
            types = {r.entity_type for r in g["rows"]}
            g["basis"] = _relation(types)
            # the same URL implies the same domain: list the domain only when it is the link
            g["rows"] = sorted(
                (r for r in g["rows"] if not (r.entity_type == "domain" and "url" in types)),
                key=lambda r: TYPE_ORDER[r.entity_type],
            )
        return by

    # ------------------------------------------------------------------ related
    def related(self, cid: str, limit: int, offset: int) -> dict[str, Any]:
        with self.store.engine.connect() as conn:
            base = {"complaint_id": cid, "disclaimer": DISCLAIMER, "limit": limit,
                    "offset": offset}  # fmt: skip
            if self._state(conn, cid) == "opted_out":
                return {**base, "linking": "disabled", "linking_message": LINKING_OFF,
                        "items": [], "total": 0, "truncated": False}  # fmt: skip
            rows = self._shared_rows(conn, cid, MAX_RELATED_ROWS)
            truncated = len(rows) > MAX_RELATED_ROWS
            groups = self._group(rows[:MAX_RELATED_ROWS])
            reviews = self._reviews(conn, [(cid, o) for o in groups])
            ranked = []
            for other, g in groups.items():
                rev = reviews.get(tuple(sorted((cid, other))))  # type: ignore[arg-type]
                link_type = rev[0].decision if rev else g["basis"]
                ranked.append((other, g, rev, link_type))
            ranked.sort(
                key=lambda x: (
                    LINK_ORDER[x[3]], -len(x[1]["rows"]),
                    -(x[1]["created_at"].timestamp() if x[1]["created_at"] else 0), x[0],
                )
            )  # fmt: skip
            page = ranked[offset : offset + limit]
            sums = self._summaries(conn, [cid] + [p[0] for p in page])
        own_ifsc = sums.get(cid, {}).get("_ifsc")
        items = []
        for other, g, rev, link_type in page:
            s = sums.get(other)
            if s is None:  # pragma: no cover - removed between the two reads
                continue
            shared = []
            for r in g["rows"]:
                item = {
                    "type": r.entity_type, "display": r.display,
                    "link": "inferred" if r.entity_type == "domain" else "observed",
                    "this_field": r.this_field, "other_field": r.other_field,
                }  # fmt: skip
                if r.entity_type == "bank_account":
                    theirs = s.get("_ifsc")
                    item["ifsc"] = (
                        "not_given" if not own_ifsc or not theirs
                        else "same" if own_ifsc == theirs else "different"
                    )  # fmt: skip
                shared.append(item)
            items.append({
                "complaint": self._public(s), "link_type": link_type, "basis": g["basis"],
                "shared": shared,
                "review": self._review_view(rev[0], rev[1]) if rev else None,
            })  # fmt: skip
        return {**base, "linking": "enabled", "linking_message": None, "items": items,
                "total": len(ranked), "truncated": truncated}  # fmt: skip

    # ------------------------------------------------------------------ graph
    def graph(self, cid: str, depth: int) -> dict[str, Any]:
        limits = {"depth": depth, "complaints": MAX_GRAPH_COMPLAINTS,
                  "entities": MAX_GRAPH_ENTITIES, "edges": MAX_GRAPH_EDGES}  # fmt: skip
        with self.store.engine.connect() as conn:
            state = self._state(conn, cid)
            if state == "opted_out":
                s = self._summaries(conn, [cid])[cid]
                node = self._complaint_node(s, 0, True)
                return {"center": cid, "depth": depth, "linking": "disabled",
                        "linking_message": LINKING_OFF, "disclaimer": DISCLAIMER,
                        "nodes": [node], "edges": [], "truncated": False,
                        "counts": {"complaints": 1, "entities": 0, "edges": 0},
                        "limits": limits}  # fmt: skip
            distance: dict[str, int] = {cid: 0}
            # identifier key -> {type, hash, display, members: {complaint_id: source_field}}
            ents: dict[tuple[str, str], dict[str, Any]] = {}
            truncated = False
            frontier = [cid]
            for level in range(1, depth + 1):
                if not frontier:
                    break
                rows, hit = self._level_rows(conn, frontier)
                truncated |= hit
                found: dict[tuple[str, str], dict[str, Any]] = {}
                for r in rows:
                    key = (r.entity_type, r.value_hash)
                    f = found.setdefault(key, {"type": r.entity_type, "hash": r.value_hash,
                                               "display": r.display, "members": {},
                                               "order": []})  # fmt: skip
                    if r.member not in f["members"]:
                        f["members"][r.member] = r.source_field
                        f["order"].append(r.member)
                urls = self._url_hashes(conn, {m for f in found.values() for m in f["members"]})
                new: list[str] = []
                for key in sorted(found, key=lambda k: (TYPE_ORDER[k[0]], found[k]["display"])):
                    f = found[key]
                    members = f["order"]
                    if not self._show_entity(f, cid, urls):
                        continue
                    if key not in ents:
                        if len(ents) >= MAX_GRAPH_ENTITIES:
                            truncated = True
                            continue
                        ents[key] = {**f, "members": {}}
                    if len(members) > MAX_ENTITY_MEMBERS:
                        truncated = True
                    for m in members[:MAX_ENTITY_MEMBERS]:
                        ents[key]["members"][m] = f["members"][m]
                        if m in distance:
                            continue
                        if len(distance) >= MAX_GRAPH_COMPLAINTS:
                            truncated = True
                            continue
                        distance[m] = level
                        new.append(m)
                frontier = new
            sums = self._summaries(conn, distance)
            reviews = self._reviews(conn, [(x, y) for x in distance for y in distance if x < y])
        nodes: list[dict[str, Any]] = [
            self._complaint_node(sums[c], d, c == cid) for c, d in distance.items() if c in sums
        ]
        edges: list[dict[str, Any]] = []
        for (etype, h), e in ents.items():
            eid = f"e:{etype}:{h[:16]}"
            members = [m for m in e["members"] if m in distance]
            nodes.append({"id": eid, "kind": "entity", "entity_type": etype,
                          "display": e["display"], "complaint_count": len(members)})  # fmt: skip
            for m in members:
                if len(edges) >= MAX_GRAPH_EDGES:
                    truncated = True
                    break
                field = e["members"][m]
                edges.append({
                    "id": f"{eid}|c:{m}", "kind": "identifier", "source": f"c:{m}",
                    "target": eid, "entity_type": etype,
                    "link": "inferred" if etype == "domain" else "observed",
                    "source_field": field, "evidence": self._evidence(etype, field),
                })  # fmt: skip
        for (lo, hi), (row, count) in reviews.items():
            if len(edges) >= MAX_GRAPH_EDGES:
                truncated = True
                break
            edges.append({
                "id": f"review|c:{lo}|c:{hi}", "kind": "review", "source": f"c:{lo}",
                "target": f"c:{hi}", "decision": row.decision, "basis": row.basis,
                "reviewer_user_id": row.reviewer_user_id, "note": row.note,
                "created_at": _iso(row.created_at), "review_count": count,
            })  # fmt: skip
        n_c = sum(1 for n in nodes if n["kind"] == "complaint")
        return {
            "center": cid, "depth": depth, "linking": "enabled", "linking_message": None,
            "disclaimer": DISCLAIMER, "nodes": nodes, "edges": edges, "truncated": truncated,
            "counts": {"complaints": n_c, "entities": len(nodes) - n_c, "edges": len(edges)},
            "limits": limits,
        }  # fmt: skip

    @staticmethod
    def _complaint_node(s: dict[str, Any], distance: int, center: bool) -> dict[str, Any]:
        return {
            "id": f"c:{s['id']}", "kind": "complaint", "complaint_id": s["id"],
            "title": s["title"], "category": s["category"], "status": s["status"],
            "risk_level": s["risk_level"], "created_at": s["created_at"], "center": center,
            "distance": distance,
        }  # fmt: skip

    @staticmethod
    def _evidence(etype: str, field: str) -> str:
        label = FIELD_LABEL.get(field, field)
        if etype == "domain":
            return (
                f"Registrable domain of this complaint's {label} ({field}). A shared domain with "
                "a different link is inferred, not proof."
            )
        return f"Same normalised value in this complaint's {label} field ({field})."

    @staticmethod
    def _show_entity(f: dict[str, Any], center: str, urls: dict[str, str]) -> bool:
        members = f["order"]
        if center in members and len(members) == 1:
            return True  # the centre's own identifier, even when nothing else shares it
        if len(members) < 2:
            return False
        if f["type"] == "domain":
            # only when it links complaints whose URLs differ (otherwise the URL node does)
            return len({urls.get(m) for m in members}) >= 2
        return True

    def _level_rows(self, conn: Connection, frontier: list[str]) -> tuple[list[Any], bool]:
        e1, e2 = ce.alias("e1"), ce.alias("e2")
        c2, u2 = complaints.alias("c2"), users.alias("u2")
        q = (
            select(
                e2.c.complaint_id.label("member"), e2.c.entity_type, e2.c.value_hash,
                e2.c.display, e2.c.source_field,
            )
            .select_from(
                e1.join(e2, and_(e1.c.entity_type == e2.c.entity_type,
                                 e1.c.value_hash == e2.c.value_hash))
                .join(c2, c2.c.id == e2.c.complaint_id)
                .join(u2, u2.c.id == c2.c.owner_user_id)
            )
            .where(e1.c.complaint_id.in_(frontier), _matchable(c2, u2))
            .order_by(c2.c.created_at.desc(), e2.c.complaint_id)
            .limit(MAX_GRAPH_ROWS + 1)
        )  # fmt: skip
        rows = list(conn.execute(q).all())
        return rows[:MAX_GRAPH_ROWS], len(rows) > MAX_GRAPH_ROWS

    @staticmethod
    def _url_hashes(conn: Connection, ids: set[str]) -> dict[str, str]:
        if not ids:
            return {}
        rows = conn.execute(
            select(ce.c.complaint_id, ce.c.value_hash).where(
                ce.c.complaint_id.in_(ids), ce.c.entity_type == "url"
            )
        ).all()
        return {r.complaint_id: r.value_hash for r in rows}

    # ------------------------------------------------------------------ clusters
    def clusters(self, limit: int, offset: int) -> dict[str, Any]:
        e3, c3, u3 = ce.alias("e3"), complaints.alias("c3"), users.alias("u3")
        shared = (
            select(e3.c.entity_type, e3.c.value_hash)
            .select_from(e3.join(c3, c3.c.id == e3.c.complaint_id)
                         .join(u3, u3.c.id == c3.c.owner_user_id))
            .where(_matchable(c3, u3), e3.c.entity_type != "domain")
            .group_by(e3.c.entity_type, e3.c.value_hash)
            .having(func.count(func.distinct(e3.c.complaint_id)) >= 2)
            .subquery()
        )  # fmt: skip
        q = (
            select(ce.c.entity_type, ce.c.value_hash, ce.c.complaint_id, complaints.c.created_at)
            .select_from(
                ce.join(shared, and_(ce.c.entity_type == shared.c.entity_type,
                                     ce.c.value_hash == shared.c.value_hash))
                .join(complaints, complaints.c.id == ce.c.complaint_id)
                .join(users, users.c.id == complaints.c.owner_user_id)
            )
            .where(_matchable(complaints, users))
            .order_by(ce.c.entity_type, ce.c.value_hash, ce.c.complaint_id)
            .limit(MAX_CLUSTER_ROWS + 1)
        )  # fmt: skip
        with self.store.engine.connect() as conn:
            rows = list(conn.execute(q).all())
            truncated = len(rows) > MAX_CLUSTER_ROWS
            rows = rows[:MAX_CLUSTER_ROWS]
            parent: dict[str, str] = {}

            def find(x: str) -> str:
                parent.setdefault(x, x)
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x

            created: dict[str, datetime] = {}
            by_key: dict[tuple[str, str], list[str]] = defaultdict(list)
            for r in rows:
                by_key[(r.entity_type, r.value_hash)].append(r.complaint_id)
                created[r.complaint_id] = _aware(r.created_at)
            for members in by_key.values():
                root = find(members[0])
                for m in members[1:]:
                    other = find(m)
                    if other != root:
                        parent[other] = root
            comps: dict[str, set[str]] = defaultdict(set)
            for cid in created:
                comps[find(cid)].add(cid)
            types: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
            for (etype, _), members in by_key.items():
                if len(set(members)) >= 2:
                    types[find(members[0])][etype] += 1
            clusters = []
            for root, members in comps.items():
                if len(members) < 2:
                    continue
                ordered = sorted(members, key=lambda m: (-created[m].timestamp(), m))
                clusters.append((root, ordered))
            clusters.sort(key=lambda x: (-len(x[1]), -created[x[1][0]].timestamp(), x[1][0]))
            page = clusters[offset : offset + limit]
            sums = self._summaries(
                conn, [m for _, ms in page for m in ms[:MAX_CLUSTER_MEMBERS_LISTED]]
            )
        items = []
        for root, ordered in page:
            items.append({
                "id": "cl-" + hashlib.sha256("|".join(sorted(ordered)).encode()).hexdigest()[:12],
                "size": len(ordered),
                "member_ids": ordered[:MAX_CLUSTER_IDS],
                "member_ids_truncated": len(ordered) > MAX_CLUSTER_IDS,
                "members": [self._public(sums[m]) for m in ordered[:MAX_CLUSTER_MEMBERS_LISTED]
                            if m in sums],
                "members_truncated": len(ordered) > MAX_CLUSTER_MEMBERS_LISTED,
                "connecting_types": dict(sorted(types[root].items())),
                "latest_created_at": _iso(created[ordered[0]]),
                "earliest_created_at": _iso(created[ordered[-1]]),
            })  # fmt: skip
        return {"items": items, "total": len(clusters), "limit": limit, "offset": offset,
                "truncated": truncated, "disclaimer": DISCLAIMER,
                "basis": "observed shared identifiers (inferred domains and reviews are not "
                         "used to form clusters)"}  # fmt: skip

    # ------------------------------------------------------------------ search
    def search(self, etype: str, vhash: str, limit: int, offset: int) -> dict[str, Any]:
        cond = [ce.c.entity_type == etype, ce.c.value_hash == vhash, _matchable(complaints, users)]
        src = ce.join(complaints, complaints.c.id == ce.c.complaint_id).join(
            users, users.c.id == complaints.c.owner_user_id
        )
        with self.store.engine.connect() as conn:
            total = conn.execute(select(func.count()).select_from(src).where(*cond)).scalar_one()
            rows = conn.execute(
                select(ce.c.complaint_id, ce.c.source_field)
                .select_from(src)
                .where(*cond)
                .order_by(complaints.c.created_at.desc(), ce.c.complaint_id)
                .limit(limit)
                .offset(offset)
            ).all()
            sums = self._summaries(conn, [r.complaint_id for r in rows])
        items = [
            {**self._public(sums[r.complaint_id]), "source_field": r.source_field}
            for r in rows
            if r.complaint_id in sums
        ]
        return {"items": items, "total": int(total), "limit": limit, "offset": offset,
                "disclaimer": DISCLAIMER}  # fmt: skip

    # ------------------------------------------------------------------ reviews
    def review(
        self, a: str, b: str, decision: str, note: str | None, reviewer_id: str, now: datetime
    ) -> dict[str, Any]:
        for attempt in (1, 2, 3):
            try:
                return self._review(a, b, decision, note, reviewer_id, now)
            except IntegrityError:  # a concurrent review took the same seq: re-read and retry
                if attempt == 3:
                    raise
        raise AssertionError("unreachable")  # pragma: no cover

    def _review(
        self, a: str, b: str, decision: str, note: str | None, reviewer_id: str, now: datetime
    ) -> dict[str, Any]:
        low, high = sorted((a, b))
        with self.store._tx() as conn:
            rows = conn.execute(
                select(complaints.c.id, complaints.c.deleted_at, users.c.allow_case_matching)
                .join(users, users.c.id == complaints.c.owner_user_id)
                .where(complaints.c.id.in_((low, high)))
            ).all()
            if len(rows) != 2:
                raise NotFound()
            if any(r.deleted_at is not None for r in rows):
                raise Conflict("link_complaint_deleted")
            if not all(r.allow_case_matching for r in rows):
                raise Conflict("link_matching_disabled")
            shared = self._shared_rows(conn, low, MAX_RELATED_ROWS, other=high)
            if not shared:
                raise Conflict("link_no_relation")
            basis = _relation(r.entity_type for r in shared)
            prev = conn.execute(
                select(link_reviews)
                .where(link_reviews.c.complaint_low == low, link_reviews.c.complaint_high == high)
                .order_by(link_reviews.c.seq.desc())
                .limit(1)
            ).first()
            rid = new_id()
            seq = (prev.seq if prev else 0) + 1
            conn.execute(
                insert(link_reviews).values(
                    id=rid, complaint_low=low, complaint_high=high, seq=seq, decision=decision,
                    basis=basis, reviewer_user_id=reviewer_id, note=note, created_at=now,
                )
            )  # fmt: skip
            # audited without identifier values and without the note text
            self.store._audit(
                conn, reviewer_id, "link_confirm" if decision == "confirmed" else "link_reject",
                None, {"previous_decision": prev.decision if prev else None},
                {"complaint_low": low, "complaint_high": high, "decision": decision,
                 "basis": basis, "review_id": rid, "seq": seq, "note_given": note is not None},
                now,
            )  # fmt: skip
            row = conn.execute(select(link_reviews).where(link_reviews.c.id == rid)).one()
            count = conn.execute(
                select(func.count())
                .select_from(link_reviews)
                .where(or_(and_(link_reviews.c.complaint_low == low,
                                link_reviews.c.complaint_high == high)))
            ).scalar_one()  # fmt: skip
        return {
            "review": self._review_view(row, int(count)),
            "link_type": decision,
            "previous_decision": prev.decision if prev else None,
            "complaint_low": low, "complaint_high": high,
        }  # fmt: skip
