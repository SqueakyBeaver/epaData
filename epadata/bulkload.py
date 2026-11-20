#  the "load all data" job.
#
# Why this exists: asking the EPA for every state and year in ONE click means thousands of requests
# in one go, and the EPA only allows about 1,000 requests per hour for each API key. This job
#   * works through ONE STATE AT A TIME and saves each state as soon as it arrives, so nothing is
#     lost if something goes wrong later,
#   * keeps itself under the hourly limit, and when the EPA says "429 too many requests" it waits
#     and tries again instead of giving up,
#   * skips every (state, year) that is already in the database, so it can be stopped and started
#     again at any time and picks up where it left off,
#   * runs in the background, so the website stays usable and the page shows live progress.
import threading
import time
from collections import deque
from datetime import datetime

from sqlalchemy import select

from epadata.campd import CAMPDClient, RateLimitError
from epadata.db import Session
from epadata.db.models import AnnualRecord, Facility
from epadata.importer import import_annual_data

REQUESTS_PER_HOUR = 900  # the EPA's limit is about 1,000; stay a little under it
MAX_RATE_LIMIT_WAITS = 12  # in a row, for one request, before giving up on that state

_lock = threading.Lock()  # protects _job (the web page and the background thread both use it)
_stop = threading.Event()
_thread = None
_job = {"running": False}


class Stopped(Exception):
    """The user pressed Stop."""


def snapshot():
    """A copy of the job's progress, safe to hand to a template."""
    with _lock:
        copy = dict(_job)
        copy["log"] = list(_job.get("log", []))
        copy["failed"] = list(_job.get("failed", []))
        # ready-made text for the page
        if copy.get("paused_until"):
            copy["paused_text"] = f"{datetime.fromtimestamp(copy['paused_until']):%H:%M:%S}"
        total = copy.get("states_total") or 0
        copy["percent"] = round(100 * copy.get("states_done", 0) / total) if total else 0
        return copy


def _set(**values):
    with _lock:
        _job.update(values)


def _log(message):
    with _lock:
        _job.setdefault("log", deque(maxlen=15)).appendleft(
            f"{datetime.now():%H:%M:%S}  {message}")


def _sleep(seconds, reason):
    """Wait, but wake up every second so Stop works. Shows 'paused until' on the page."""
    until = time.time() + seconds
    _set(paused_until=until, paused_reason=reason)
    _log(f"{reason} - waiting {int(seconds // 60)} min {int(seconds % 60)} s")
    while time.time() < until:
        if _stop.is_set():
            raise Stopped()
        time.sleep(1)
    _set(paused_until=None, paused_reason=None)


class _Pacer:
    """Counts requests and holds the next one back when the hourly allowance is used up."""

    def __init__(self, per_hour):
        self.per_hour = per_hour
        self.times = deque()

    def wait(self):
        while True:
            if _stop.is_set():
                raise Stopped()
            now = time.time()
            while self.times and now - self.times[0] > 3600:
                self.times.popleft()
            if len(self.times) < self.per_hour:
                self.times.append(now)
                with _lock:
                    _job["requests"] = _job.get("requests", 0) + 1
                return
            _sleep(3600 - (now - self.times[0]) + 1, "Hourly request allowance used up")


def _call(client, function):
    """Run one EPA request. On '429 too many requests', wait and try again."""
    for attempt in range(MAX_RATE_LIMIT_WAITS):
        try:
            result = function()
            _set(rate_remaining=client.rate_remaining, rate_limit=client.rate_limit)
            return result
        except RateLimitError as e:
            # Wait at least what the EPA asked for, and wait LONGER each time it says no again
            # (30 s, 1 min, 2 min ... up to 10 min). Retrying quickly only wastes the allowance.
            seconds = max(e.retry_after or 0, min(30 * 2 ** attempt, 600))
            _set(last_retry_after=e.retry_after)
            _sleep(seconds, f"The EPA says too many requests (it asked for {e.retry_after if e.retry_after is not None else '?'} s)")
    raise RuntimeError("still rate limited after waiting")


def _emissions_for(client, code, years):
    """All of one state's annual emissions. ONE request for all the years when the EPA allows it,
    otherwise (if the EPA refuses that form) one request per year."""
    try:
        rows = _call(client, lambda: client.get_annual_emissions_all_years(years, state_code=code))
        _log(f"{code}: the EPA sent {len(rows):,} rows covering "
             f"{len({r.get('year') for r in rows})} of {len(years)} years")
        return rows
    except RuntimeError as e:
        if "still rate limited" in str(e):
            raise
        _log(f"{code}: asking for all years at once failed ({type(e).__name__}); asking year by year")
    rows = []
    for year in years:
        _set(current=f"{code} {year}")
        rows += _call(client, lambda: client.get_annual_emissions([year], state_code=code))
    return rows


def _years_already_saved(state):
    with Session() as session:
        return set(session.scalars(
            select(AnnualRecord.reporting_year)
            .join(Facility, Facility.id == AnnualRecord.facility_id)
            .where(Facility.state == state)
            .distinct()
        ))


def _run(years, states, redo):
    try:
        client = CAMPDClient()
        client.before_request = _Pacer(REQUESTS_PER_HOUR).wait
    except Exception as e:  # e.g. no API key
        _set(running=False, finished=time.time(), error=f"Could not start ({type(e).__name__}).")
        return

    try:
        for code in states:
            if _stop.is_set():
                raise Stopped()
            _set(current=code)
            todo = list(years) if redo else [y for y in years if y not in _years_already_saved(code)]
            if not todo:
                _log(f"{code}: all years already saved, skipped")
                with _lock:
                    _job["states_done"] += 1
                continue
            try:
                _set(current=f"{code} ({todo[0]}-{todo[-1]})")
                annual = _emissions_for(client, code, todo)
                attributes = []
                if annual:
                    try:
                        attributes = _call(
                            client, lambda: client.get_facility_attributes_all_years(todo, state_code=code))
                    except RuntimeError as e:  # county / latitude are nice to have, not essential
                        if isinstance(e, RateLimitError) or "still rate limited" in str(e):
                            raise
                        _log(f"{code}: facility details unavailable ({type(e).__name__}); saving without them")
                with Session() as session:
                    summary = import_annual_data(
                        session, annual, attributes,
                        # CHANGED: ONE dataset per state, added to by every later run (no timestamp in
                        # the name). A run with nothing new creates no dataset at all.
                        name=f"EPA CAMPD {code} (bulk load)",
                        years=todo,
                        query_params={"years": todo, "state_code": code, "bulk_load": True},
                        reuse_dataset=True,
                    )
                with _lock:
                    _job["states_done"] += 1
                    _job["records_saved"] += summary.get("accepted", 0)
                if summary.get("accepted", 0):
                    _log(f"{code}: saved {summary['accepted']:,} records "
                         f"({summary.get('skipped_identical', 0):,} identical skipped, "
                         f"{summary.get('rejected', 0):,} rejected)")
                else:  # NEW: nothing to add, so no dataset was created
                    _log(f"{code}: already up to date ({summary.get('skipped_identical', 0):,} records "
                         f"identical to saved data, nothing new)")
            except Stopped:
                raise
            except Exception as e:  # one bad state must not end the whole job
                with _lock:
                    _job["states_done"] += 1
                    _job["failed"].append(code)
                _log(f"{code}: FAILED ({type(e).__name__}); will be tried again next time")
        _set(running=False, finished=time.time(), current="", message="Finished.")
    except Stopped:
        _set(running=False, finished=time.time(), current="",
             message="Stopped. Everything saved so far is kept; start again to continue.")
    except Exception as e:  # last resort: never leave the page saying "running" forever
        _set(running=False, finished=time.time(), current="", error=f"The job crashed ({type(e).__name__}).")


def start(years, states, redo=False):
    """Start the job. Returns False if one is already running."""
    global _thread
    with _lock:
        if _job.get("running"):
            return False
        _stop.clear()
        _job.clear()
        _job.update(
            running=True, started=time.time(), finished=None, years=list(years),
            states_total=len(states), states_done=0, records_saved=0, requests=0,
            current="", failed=[], log=deque(maxlen=15), rate_remaining=None, rate_limit=None,
            paused_until=None, paused_reason=None, message=None, error=None,
        )
    _thread = threading.Thread(target=_run, args=(years, states, redo), daemon=True)
    _thread.start()
    return True


def stop():
    _stop.set()