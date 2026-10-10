"""Full-text search for the stored data, built on Whoosh.

How the pieces fit together
---------------------------
* SQLite stays the single source of truth. Whoosh only keeps a search index (a folder of
  files) that can be thrown away and rebuilt from the database at any time.
* One Whoosh document = one AnnualRecord (one unit, one year, one dataset), together with
  the words from its facility, unit and dataset.
* Whoosh answers "which record ids match, best first?". The rows themselves are then loaded
  from SQLite with SQLAlchemy, so what the user sees is always the stored data.
* The index updates itself: SQLAlchemy events (below) notice records being added, changed or
  deleted and update the index right after the transaction commits. The upload page and the
  EPA importer therefore need no extra code. This is the same idea Flask-WhooshAlchemy uses,
  written for plain SQLAlchemy because that package only works with the old Flask-SQLAlchemy.
* If the index folder is missing or its record count no longer matches the database (for
  example a fresh clone of the repository), it is rebuilt automatically.
"""

import logging
import re
import threading
from pathlib import Path

from sqlalchemy import event, func, select
from sqlalchemy.orm import Session as SASession
from whoosh import index
from whoosh.analysis import IDAnalyzer, StemmingAnalyzer
from whoosh.fields import ID, NUMERIC, TEXT, Schema
from whoosh.qparser import AndGroup, FuzzyTermPlugin, MultifieldParser, QueryParser
from whoosh.query import And, NumericRange, Or, Term

from epadata.db import Session
from epadata.db.models import AnnualRecord, Dataset, Facility, Unit
from epadata.nlsearch import METRICS, STATE_NAMES, interpret  # NEW: sentence -> conditions

log = logging.getLogger(__name__)

PER_PAGE = 25
MAX_QUERY_LENGTH = 200

# ---- the index layout ---------------------------------------------------------------

_exact = IDAnalyzer(lowercase=True)  # the whole value is one word: "CT1", "KY", "1355"
# Lower-cases, drops filler words ("the", "of") and reduces words to their stem
# ("plants" -> "plant"). minsize=1 keeps one-letter words, as in "E W Brown".
_words = StemmingAnalyzer(minsize=1)

SCHEMA = Schema(
    record_id=ID(stored=True, unique=True),  # annual_record.id, the link back to SQLite
    dataset_id=NUMERIC(int, stored=True),
    dataset_name=TEXT(analyzer=_words),
    facility_id=ID(analyzer=_exact),
    facility_name=TEXT(analyzer=_words),
    state=ID(analyzer=_exact),
    state_name=TEXT(analyzer=_words),
    county=TEXT(analyzer=_words),
    unit_id=ID(analyzer=_exact),
    unit_type=TEXT(analyzer=_words),
    primary_fuel=TEXT(analyzer=_words),
    secondary_fuel=TEXT(analyzer=_words),
    source_category=TEXT(analyzer=_words),
    program_code=TEXT(analyzer=_words),
    controls=TEXT(analyzer=_words),  # SO2 + NOx + PM control equipment
    year=NUMERIC(int),  # for ranges:  year:[2020 TO 2022]
    year_text=ID(analyzer=_exact),  # for plain words: a bare "2025"
    # NEW: measurements, so "CO2 greater than 500,000" can be answered by the index. A record
    # with no value for a measurement is left out of that field, so it can never match a range.
    **{name: NUMERIC(float, bits=64) for name in METRICS},
)

# Fields searched when the user types plain words, with a weight (higher = ranks higher).
FIELD_WEIGHTS = {
    "facility_name": 3.0,
    "county": 2.0,
    "state": 2.0,
    "state_name": 2.0,
    "unit_id": 1.5,
    "facility_id": 1.5,
    "unit_type": 1.0,
    "primary_fuel": 1.0,
    "secondary_fuel": 1.0,
    "source_category": 1.0,
    "program_code": 1.0,
    "controls": 1.0,
    "year_text": 1.0,
    "dataset_name": 0.5,
}

_lock = threading.RLock()  # Whoosh allows one writer at a time
_index_dir: Path | None = None
_PENDING = "search_pending_record_ids"  # kept in Session.info until the commit


def init_search(app):
    """Called once from create_app: remember where the index lives, add the CLI command."""
    global _index_dir
    _index_dir = Path(app.config["SEARCH_INDEX_DIR"])

    @app.cli.command("reindex")
    def reindex_command():
        """Rebuild the full-text search index from the database."""
        print(f"Indexed {rebuild_index()} record(s) into {_index_dir}")

    try:
        ensure_index()
    except Exception:  # never stop the site from starting; search retries on first use
        app.logger.exception("Could not prepare the search index")


# ---- reading rows from SQLite -------------------------------------------------------


def _record_query(*extra_columns):
    """One row per AnnualRecord with its dataset, facility and unit joined in."""
    return (
        select(
            AnnualRecord.id,
            AnnualRecord.dataset_id,
            Dataset.name.label("dataset_name"),
            AnnualRecord.facility_id,
            Facility.name.label("facility_name"),
            Facility.state,
            Facility.county,
            Facility.source_category,
            Unit.epa_unit_id.label("unit_id"),
            Unit.type.label("unit_type"),
            Unit.primary_fuel,
            Unit.secondary_fuel,
            AnnualRecord.reporting_year.label("year"),
            AnnualRecord.program_code,
            AnnualRecord.so2_controls,
            AnnualRecord.nox_controls,
            AnnualRecord.pm_controls,
            AnnualRecord.operating_time,
            AnnualRecord.gross_load,
            AnnualRecord.steam_load,
            AnnualRecord.heat_input,
            AnnualRecord.co2_mass,
            AnnualRecord.so2_mass,
            AnnualRecord.nox_mass,
            *extra_columns,
        )
        .join(Dataset, Dataset.id == AnnualRecord.dataset_id)
        .join(Facility, Facility.id == AnnualRecord.facility_id)
        .join(
            Unit,
            (Unit.internal_id == AnnualRecord.unit_key)
            & (Unit.facility_id == AnnualRecord.facility_id),
        )
    )


def _document(row) -> dict:
    """Turn one database row into the words and numbers Whoosh will index."""
    controls = " ".join(
        c for c in (row["so2_controls"], row["nox_controls"], row["pm_controls"]) if c
    )
    return dict(
        record_id=str(row["id"]),
        dataset_id=row["dataset_id"],
        dataset_name=row["dataset_name"] or "",
        facility_id=str(row["facility_id"]),
        facility_name=row["facility_name"] or "",
        state=row["state"] or "",
        state_name=STATE_NAMES.get(row["state"], ""),
        county=row["county"] or "",
        unit_id=str(row["unit_id"]),
        unit_type=row["unit_type"] or "",
        primary_fuel=row["primary_fuel"] or "",
        secondary_fuel=row["secondary_fuel"] or "",
        source_category=row["source_category"] or "",
        program_code=row["program_code"] or "",
        controls=controls,
        year=row["year"],
        year_text=str(row["year"]),
        # only real numbers are indexed (None would otherwise look like 0)
        **{name: float(row[name]) for name in METRICS if row[name] is not None},
    )


# ---- keeping the index in step with the database -----------------------------------


def _open_index():
    _index_dir.mkdir(parents=True, exist_ok=True)
    if index.exists_in(_index_dir):
        return index.open_dir(_index_dir)
    return index.create_in(_index_dir, SCHEMA)


def sync_records(engine, record_ids):
    """Bring these records up to date in the index.

    The ids are only hints about WHAT changed. The content always comes from the
    database: a record that exists is (re)indexed, one that does not is removed.
    """
    record_ids = sorted(record_ids)
    with _lock:
        writer = _open_index().writer()
        try:
            with engine.connect() as conn:
                for start in range(0, len(record_ids), 500):
                    chunk = record_ids[start : start + 500]
                    rows = conn.execute(
                        _record_query().where(AnnualRecord.id.in_(chunk))
                    ).mappings()
                    found = set()
                    for row in rows:
                        writer.update_document(**_document(row))
                        found.add(row["id"])
                    for missing in set(chunk) - found:
                        writer.delete_by_term("record_id", str(missing))
        except Exception:
            writer.cancel()
            raise
        writer.commit()


def rebuild_index() -> int:
    """Throw the index away and index every record in the database. Returns the count."""
    with _lock:
        _index_dir.mkdir(parents=True, exist_ok=True)
        ix = index.create_in(_index_dir, SCHEMA)  # starts empty
        writer = ix.writer()
        count = 0
        with Session() as session:
            for row in session.execute(_record_query()).mappings():
                writer.add_document(**_document(row))
                count += 1
        writer.commit()
        return count


def ensure_index() -> None:
    """Rebuild the index if it is missing or does not match the database."""
    with _lock:
        with Session() as session:
            expected = session.scalar(select(func.count(AnnualRecord.id))) or 0
        if index.exists_in(_index_dir):
            ix = index.open_dir(_index_dir)
            # also rebuild if this file's index layout changed since the index was made
            if ix.doc_count() == expected and set(ix.schema.names()) == set(SCHEMA.names()):
                return
        rebuild_index()


@event.listens_for(SASession, "after_flush")
def _remember_changed_records(session, flush_context):
    """During a flush, note which annual records were added, changed or deleted."""
    pending = session.info.setdefault(_PENDING, set())
    for obj in (*session.new, *session.dirty, *session.deleted):
        if isinstance(obj, AnnualRecord):
            pending.add(obj.id)
    # A renamed facility, unit or dataset changes the words of all of its records.
    for obj in session.dirty:
        if not session.is_modified(obj):
            continue
        if isinstance(obj, Facility):
            column, value = AnnualRecord.facility_id, obj.id
        elif isinstance(obj, Unit):
            column, value = AnnualRecord.unit_key, obj.internal_id
        elif isinstance(obj, Dataset):
            column, value = AnnualRecord.dataset_id, obj.id
        else:
            continue
        pending.update(session.scalars(select(AnnualRecord.id).where(column == value)))


@event.listens_for(SASession, "after_commit")
def _update_index_after_commit(session):
    pending = session.info.pop(_PENDING, None)
    if not pending or _index_dir is None:
        return
    try:
        sync_records(session.get_bind().engine, pending)
    except Exception:
        # The data is already saved. A stale index is repaired by ensure_index().
        log.exception("Could not update the search index")


# Nothing needs to happen on rollback: the collected ids are only hints. At commit time
# each one is checked against the database, so ids from rolled-back work do no harm.


# ---- searching ----------------------------------------------------------------------


def _parser(ix):
    parser = MultifieldParser(
        list(FIELD_WEIGHTS), ix.schema, fieldboosts=FIELD_WEIGHTS, group=AndGroup
    )
    parser.add_plugin(FuzzyTermPlugin())  # brown~  also finds  brwon
    return parser


def _conditions_query(ix, understood, with_numbers=True):
    """NEW: the Whoosh query for a sentence that nlsearch.interpret() understood.

    Every part must match (AND). Inside one part, several values are alternatives (OR):
    "Ohio and Kentucky" = state OH or KY.
    """
    parts = []
    if understood.words:
        parts.append(_parser(ix).parse(understood.words))
    if understood.fuels:
        fuel_parser = QueryParser("primary_fuel", ix.schema, group=AndGroup)
        parts.append(Or([fuel_parser.parse(f) for f in understood.fuels]))
    if understood.states:
        parts.append(Or([Term("state", code.lower()) for code in understood.states]))
    if understood.years:
        parts.append(Or([Term("year_text", str(y)) for y in understood.years]))
    if with_numbers:
        for c in understood.numbers:
            parts.append(NumericRange(c.field, c.lo, c.hi, c.lo_excl, c.hi_excl))
    return And(parts)


def _page(ix, query, page, per_page):
    """Run a query. Returns (total, page, pages, [record ids, best first])."""
    with ix.searcher() as searcher:
        if per_page is None:  # every hit (used by the CSV download)
            ids = [int(h["record_id"]) for h in searcher.search(query, limit=None)]
            return len(ids), 1, 1, ids
        results = searcher.search_page(query, page, pagelen=per_page)
        ids = [int(h["record_id"]) for h in results]
        return results.total, results.pagenum, max(results.pagecount, 1), ids


def _count(ix, query) -> int:
    with ix.searcher() as searcher:
        return len(searcher.search(query, limit=None, scored=False))


def search(
    text: str,
    page: int = 1,
    per_page: int | None = PER_PAGE,
    relax: bool = False,
) -> dict:
    """Search ALL stored data (uploaded files and EPA data alike). Never raises for bad input.

    Two kinds of text are understood:
    * a sentence ("coal-fired units in Kentucky in 2025 with CO2 greater than 500,000") is
      split into conditions by nlsearch.interpret();
    * search syntax (quotes, field:value, OR, NOT, brow*, brown~) goes straight to Whoosh.

    relax: if a sentence matches nothing only because of its number conditions (for example
    nothing has a CO2 value saved), show the records that match everything else.

    Returns: query, rows (dicts, best match first), total, page, pages, notice, error,
    understood (labels showing how the sentence was read), relaxable (how many records match
    everything except the number conditions, or None), missing (for each number condition,
    how many of those records have no value saved), relaxed (True when relax was applied).
    """
    text = (text or "").strip()[:MAX_QUERY_LENGTH]
    out = dict(
        query=text, rows=[], total=0, page=1, pages=1, notice=None, error=None,
        understood=[], relaxable=None, missing={}, relaxed=False,
    )
    if not text:
        return out

    ensure_index()
    ix = index.open_dir(_index_dir)
    page = requested_page = max(page, 1)

    try:
        understood = interpret(text)
        if understood is not None and understood.found:
            out["understood"] = understood.chips()
            query = _conditions_query(ix, understood)
            total, page, pages, ids = _page(ix, query, page, per_page)
            if total == 0 and understood.numbers:
                loose = _conditions_query(ix, understood, with_numbers=False)
                loose_total = _count(ix, loose)
                if loose_total:
                    for c in understood.numbers:
                        have = _count(ix, And([loose, NumericRange(c.field, None, None)]))
                        out["missing"][c.describe()] = loose_total - have
                    if relax:
                        total, page, pages, ids = _page(ix, loose, requested_page, per_page)
                        out["relaxed"] = True
                    else:
                        out["relaxable"] = loose_total
        else:
            total, page, pages, ids = _plain(ix, text, page, per_page, out)
    except Exception:
        out["error"] = (
            "That search could not be understood. Check quotes and brackets, "
            "or see the examples above."
        )
        out["understood"] = []
        return out

    out.update(total=total, page=max(page, 1), pages=pages)
    if ids:
        with Session() as session:
            found = {
                r["id"]: dict(r)
                for r in session.execute(
                    _record_query().where(AnnualRecord.id.in_(ids))
                ).mappings()
            }
        out["rows"] = [found[i] for i in ids if i in found]  # keep Whoosh's order
    return out


def _plain(ix, text, page, per_page, out):
    """Text that is not a sentence: words and/or Whoosh search syntax, all fields."""
    total, page, pages, ids = _page(ix, _parser(ix).parse(text), page, per_page)
    # Nothing found for plain words: retry treating each word as the start of a word,
    # so "brow" finds "Brown". Skipped when the user used search syntax of their own.
    if total == 0 and re.fullmatch(r"[\w\s-]+", text):
        starts = " ".join(f"{word}*" for word in text.split())
        total, page, pages, ids = _page(ix, _parser(ix).parse(starts), 1, per_page)
        if total:
            out["notice"] = (
                f"No exact matches for \u201c{text}\u201d. "
                "Showing results where a word starts with what you typed."
            )
    return total, page, pages, ids