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

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import MetaData, inspect, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.schema import CreateTable

from .schema import EVIDENCE_STATUSES, metadata, schema_migrations

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


def _evidence_check_current(conn: Connection) -> bool:
    """Does the live ck_evidence_status CHECK already allow every status in the metadata?"""
    if conn.dialect.name == "postgresql":
        rows = conn.execute(
            text(
                "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c "
                "JOIN pg_class t ON t.oid = c.conrelid "
                "WHERE t.relname = 'complaint_evidence' AND c.conname = 'ck_evidence_status'"
            )
        ).scalars()
        defs = list(rows)
    else:
        sql = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :n"),
            {"n": "complaint_evidence"},
        ).scalar()
        defs = [sql or ""]
    return bool(defs) and all(f"'{s}'" in d for d in defs for s in EVIDENCE_STATUSES)


def _rebuild_sqlite_table(conn: Connection, name: str) -> None:
    """SQLite cannot alter a CHECK constraint: copy into a table created from the current
    metadata, drop the old one, rename, recreate the indexes. Nothing references
    ``complaint_evidence``, so foreign keys stay valid; every row and column value is kept."""
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
            allowed = ", ".join(f"'{s}'" for s in EVIDENCE_STATUSES)
            conn.execute(
                text("ALTER TABLE complaint_evidence DROP CONSTRAINT IF EXISTS ck_evidence_status")
            )
            conn.execute(
                text(
                    "ALTER TABLE complaint_evidence ADD CONSTRAINT ck_evidence_status "
                    f"CHECK (status IN ({allowed}))"
                )
            )
        else:
            _rebuild_sqlite_table(conn, "complaint_evidence")
    add_column(conn, "complaint_evidence", "deleted_at")


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "baseline", _baseline),
    Migration(2, "analyses_detail_column", _analyses_detail),
    Migration(3, "complaint_bank_details", _complaint_bank_details),
    Migration(4, "user_profile_privacy", _user_profile_privacy),
    Migration(5, "evidence_deleted_status", _evidence_deleted_status),
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
