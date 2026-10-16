"""Versioned, additive schema migrations (SQLite and Postgres).

``schema_migrations`` records one row per applied version. ``run`` executes in ONE transaction;
on Postgres it first takes an advisory transaction lock so replicas starting together serialise
(DDL is transactional there; on SQLite the store's writers are serialised anyway).

Rules for adding a migration
* Append to ``MIGRATIONS`` with the next version; never renumber or edit a released one.
* Every step is additive and idempotent: it inspects the live schema and only changes what is
  missing (a column, an index, a widened CHECK). Version 1 is ``create_all`` of the CURRENT
  metadata, so on a fresh database the later steps find nothing to do; on a database created
  before versioning they add what is missing. Because they are idempotent, applied steps are also
  re-checked on every start (an inspection only), which repairs a column dropped by hand.
* Nothing is ever dropped or rewritten destructively; data is preserved.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import MetaData, insert, inspect, select, text, update
from sqlalchemy.engine import Connection
from sqlalchemy.schema import CreateTable

from .schema import (
    ANALYSIS_STATUSES,
    EVIDENCE_STATUSES,
    RISK_LEVELS,
    analyses,
    metadata,
    schema_migrations,
)

ADVISORY_LOCK_SCHEMA = 727201


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[Connection], None]


def _columns(conn: Connection, table: str) -> set[str]:
    return {c["name"] for c in inspect(conn).get_columns(table)}


def add_column(conn: Connection, table: str, column: str) -> bool:
    """Add ``table.column`` as declared in the metadata if it is missing. Only nullable columns
    or NOT NULL columns with a server default are allowed (additive on a populated table)."""
    if column in _columns(conn, table):
        return False
    col = metadata.tables[table].c[column]
    ddl = f"ALTER TABLE {table} ADD COLUMN {column} {col.type.compile(dialect=conn.dialect)}"
    if col.server_default is not None:
        default = col.server_default.arg  # type: ignore[attr-defined]
        ddl += " DEFAULT " + str(default.compile(dialect=conn.dialect))
    if not col.nullable:
        if col.server_default is None:
            raise RuntimeError(f"{table}.{column}: NOT NULL needs a server default")
        ddl += " NOT NULL"
    conn.execute(text(ddl))
    return True


def ensure_index(conn: Connection, table: str, index: str) -> None:
    for idx in metadata.tables[table].indexes:
        if idx.name == index:
            idx.create(conn, checkfirst=True)
            return
    raise KeyError(index)


# ---------------------------------------------------------------------------------- steps
def _baseline(conn: Connection) -> None:
    metadata.create_all(conn)


def _analyses_detail(conn: Connection) -> None:
    add_column(conn, "analyses", "detail")


def _complaint_bank_details(conn: Connection) -> None:
    for col in ("bank_name", "bank_account", "ifsc"):
        add_column(conn, "complaints", col)
    ensure_index(conn, "complaints", "ix_complaints_bank_account")


def _user_profile_privacy(conn: Connection) -> None:
    add_column(conn, "users", "allow_case_matching")
    add_column(conn, "users", "display_name_set")


def _check_current(conn: Connection, table: str, constraint: str, values: tuple[str, ...]) -> bool:
    """Does the live CHECK ``constraint`` on ``table`` already allow every one of ``values``?
    (SQLite: the table's whole CREATE statement is inspected.)"""
    if conn.dialect.name == "postgresql":
        rows = conn.execute(
            text(
                "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c "
                "JOIN pg_class t ON t.oid = c.conrelid "
                "WHERE t.relname = :t AND c.conname = :c"
            ),
            {"t": table, "c": constraint},
        ).scalars()
        defs = list(rows)
    else:
        sql = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :n"),
            {"n": table},
        ).scalar()
        defs = [sql or ""]
    return bool(defs) and all(f"'{s}'" in d for d in defs for s in values)


def _swap_pg_check(conn: Connection, table: str, constraint: str, column: str,
                   values: tuple[str, ...], nullable: bool) -> None:  # fmt: skip
    allowed = ", ".join(f"'{s}'" for s in values)
    cond = f"{column} IN ({allowed})"
    if nullable:
        cond = f"{column} IS NULL OR {cond}"
    conn.execute(text(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint}"))
    conn.execute(text(f"ALTER TABLE {table} ADD CONSTRAINT {constraint} CHECK ({cond})"))


def _evidence_check_current(conn: Connection) -> bool:
    return _check_current(conn, "complaint_evidence", "ck_evidence_status", EVIDENCE_STATUSES)


def _rebuild_sqlite_table(conn: Connection, name: str) -> None:
    """SQLite cannot alter a CHECK constraint: copy into a table created from the current
    metadata, drop the old one, rename, recreate the indexes. Used for ``complaint_evidence`` and
    ``analyses``; every row and column value is kept. ``evidence_transcripts`` (migration 9)
    references ``complaint_evidence``: on a database old enough to need the evidence rebuild
    (before migration 5) the baseline step has just created it empty, so the drop finds no child
    rows, and SQLite resolves the reference by name again after the rename."""
    table = metadata.tables[name]
    tmp_name = f"{name}__migrating"
    scratch = MetaData()  # the referenced tables must exist in the copy for its foreign keys
    for t in metadata.sorted_tables:
        t.to_metadata(scratch)
    tmp = table.to_metadata(scratch, name=tmp_name)
    conn.execute(text(f"DROP TABLE IF EXISTS {tmp_name}"))
    conn.execute(CreateTable(tmp))  # no indexes yet: their names are global in SQLite
    old_cols = _columns(conn, name)
    cols = ", ".join(c.name for c in table.columns if c.name in old_cols)
    conn.execute(text(f"INSERT INTO {tmp_name} ({cols}) SELECT {cols} FROM {name}"))
    conn.execute(text(f"DROP TABLE {name}"))
    conn.execute(text(f"ALTER TABLE {tmp_name} RENAME TO {name}"))
    for idx in table.indexes:
        idx.create(conn, checkfirst=True)


def _evidence_deleted_status(conn: Connection) -> None:
    if not _evidence_check_current(conn):
        if conn.dialect.name == "postgresql":
            _swap_pg_check(conn, "complaint_evidence", "ck_evidence_status", "status",
                           EVIDENCE_STATUSES, nullable=False)  # fmt: skip
        else:
            _rebuild_sqlite_table(conn, "complaint_evidence")
    add_column(conn, "complaint_evidence", "deleted_at")


def _backfill_critical(conn: Connection) -> int:
    """Rows written before migration 6 stored CRITICAL as HIGH and kept the exact level in
    ``detail.risk_level``: store the exact level in the column. Nothing else is changed."""
    rows = conn.execute(
        select(analyses.c.id, analyses.c.detail).where(
            analyses.c.risk_level == "HIGH", analyses.c.detail.is_not(None)
        )
    ).all()
    ids = [
        r.id
        for r in rows
        if isinstance(r.detail, dict) and r.detail.get("risk_level") == "CRITICAL"
    ]
    for aid in ids:
        conn.execute(update(analyses).where(analyses.c.id == aid).values(risk_level="CRITICAL"))
    return len(ids)


def _analyses_critical_level(conn: Connection) -> None:
    """Allow CRITICAL in ``analyses.risk_level`` (Postgres: CHECK swapped; SQLite: table rebuilt
    with every row) and backfill the rows that stored CRITICAL as HIGH. The backfill only runs
    when the CHECK had to be widened: after that no row is written the old way."""
    if _check_current(conn, "analyses", "ck_analysis_level", RISK_LEVELS):
        return
    if conn.dialect.name == "postgresql":
        _swap_pg_check(conn, "analyses", "ck_analysis_level", "risk_level", RISK_LEVELS,
                       nullable=True)  # fmt: skip
    else:
        _rebuild_sqlite_table(conn, "analyses")
    _backfill_critical(conn)


def _analyses_insufficient_input(conn: Connection) -> None:
    """Allow status INSUFFICIENT_INPUT (18 characters): Postgres widens the column to 24 and
    swaps the CHECK; SQLite (no length limits) rebuilds the table with every row."""
    if conn.dialect.name == "postgresql":
        length = conn.execute(
            text(
                "SELECT character_maximum_length FROM information_schema.columns "
                "WHERE table_name = 'analyses' AND column_name = 'status'"
            )
        ).scalar()
        if length is not None and int(length) < 24:
            conn.execute(text("ALTER TABLE analyses ALTER COLUMN status TYPE VARCHAR(24)"))
    if _check_current(conn, "analyses", "ck_analysis_status", ANALYSIS_STATUSES):
        return
    if conn.dialect.name == "postgresql":
        _swap_pg_check(conn, "analyses", "ck_analysis_status", "status", ANALYSIS_STATUSES,
                       nullable=False)  # fmt: skip
    else:
        _rebuild_sqlite_table(conn, "analyses")


def _alerts_risk_level(conn: Connection) -> None:
    add_column(conn, "alerts", "risk_level")


def _evidence_transcripts(conn: Connection) -> None:
    """Table ``evidence_transcripts`` (one row per transcribed audio evidence file)."""
    metadata.tables["evidence_transcripts"].create(conn, checkfirst=True)
    ensure_index(conn, "evidence_transcripts", "ix_transcripts_complaint")


NETWORK_VERSION = 10
BACKFILL_CHUNK = 500


def backfill_entities(conn: Connection, now: datetime | None = None) -> int:
    """Write the identifiers of every stored complaint that are not there yet (idempotent: an
    existing (complaint, type, value) row is never duplicated). Soft-deleted complaints are
    included: they are excluded from linking at query time. Returns the rows written."""
    from .network import IDENTIFIER_FIELDS, extract_entities, value_hash

    ce = metadata.tables["complaint_entities"]
    cx = metadata.tables["complaints"]
    present = _columns(conn, "complaints")
    cols = [cx.c.id] + [cx.c[f] for f in IDENTIFIER_FIELDS if f in present]
    stamp = now or datetime.now(UTC)
    written = 0
    last = ""
    while True:
        rows = conn.execute(
            select(*cols).where(cx.c.id > last).order_by(cx.c.id).limit(BACKFILL_CHUNK)
        ).all()
        if not rows:
            return written
        last = rows[-1].id
        ids = [r.id for r in rows]
        have = {
            (r.complaint_id, r.entity_type, r.value_hash)
            for r in conn.execute(
                select(ce.c.complaint_id, ce.c.entity_type, ce.c.value_hash).where(
                    ce.c.complaint_id.in_(ids)
                )
            )
        }
        new: list[dict[str, object]] = []
        for r in rows:
            for e in extract_entities(dict(r._mapping)):
                h = value_hash(e.entity_type, e.value_norm)
                if (r.id, e.entity_type, h) in have:
                    continue
                have.add((r.id, e.entity_type, h))
                new.append({
                    "id": str(uuid.uuid4()), "complaint_id": r.id, "entity_type": e.entity_type,
                    "value_norm": e.value_norm, "value_hash": h, "display": e.display,
                    "source_field": e.source_field, "created_at": stamp,
                })  # fmt: skip
        if new:
            conn.execute(insert(ce), new)
            written += len(new)


def _complaint_network(conn: Connection) -> None:
    """Tables ``complaint_entities`` and ``link_reviews`` (Phase 9) and the identifiers of the
    complaints stored before them. The backfill runs when the version is not yet recorded, or when
    the entities table is empty while complaints exist (it was dropped and has just been recreated
    by the baseline step); otherwise this start-up re-check is an inspection."""
    ce = metadata.tables["complaint_entities"]
    ce.create(conn, checkfirst=True)
    metadata.tables["link_reviews"].create(conn, checkfirst=True)
    for idx in ("ix_entities_value", "ix_entities_complaint"):
        ensure_index(conn, "complaint_entities", idx)
    ensure_index(conn, "link_reviews", "ix_link_reviews_high")
    recorded = conn.execute(
        select(schema_migrations.c.version).where(schema_migrations.c.version == NETWORK_VERSION)
    ).first()
    emptied = (
        conn.execute(select(ce.c.id).limit(1)).first() is None
        and conn.execute(select(metadata.tables["complaints"].c.id).limit(1)).first() is not None
    )
    if recorded is None or emptied:
        backfill_entities(conn)


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "baseline", _baseline),
    Migration(2, "analyses_detail_column", _analyses_detail),
    Migration(3, "complaint_bank_details", _complaint_bank_details),
    Migration(4, "user_profile_privacy", _user_profile_privacy),
    Migration(5, "evidence_deleted_status", _evidence_deleted_status),
    Migration(6, "analyses_critical_level", _analyses_critical_level),
    Migration(7, "analyses_insufficient_input", _analyses_insufficient_input),
    Migration(8, "alerts_risk_level", _alerts_risk_level),
    Migration(9, "evidence_transcripts", _evidence_transcripts),
    Migration(NETWORK_VERSION, "complaint_network", _complaint_network),
)
LATEST = MIGRATIONS[-1].version


def run(conn: Connection, now: datetime | None = None) -> list[int]:
    """Apply pending migrations in order inside the caller's transaction. Returns the versions
    applied by this call (empty when the schema was already current)."""
    if conn.dialect.name == "postgresql":
        conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": ADVISORY_LOCK_SCHEMA})
    schema_migrations.create(conn, checkfirst=True)
    applied = {int(v) for v in conn.execute(select(schema_migrations.c.version)).scalars()}
    done: list[int] = []
    for m in MIGRATIONS:
        m.apply(conn)  # idempotent: also re-checks already applied steps
        if m.version not in applied:
            conn.execute(
                schema_migrations.insert().values(
                    version=m.version, name=m.name, applied_at=now or datetime.now(UTC)
                )
            )
            done.append(m.version)
    return done


def current_version(conn: Connection) -> int | None:
    if "schema_migrations" not in inspect(conn).get_table_names():
        return None
    from sqlalchemy import func

    v = conn.execute(select(func.max(schema_migrations.c.version))).scalar()
    return int(v) if v is not None else None
