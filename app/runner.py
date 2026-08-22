"""Spolecna obsluha dlouhych behu: vlakno, fronta udalosti, zapis do tabulky run.

Preklad i sber slovnicku bezi stejnym zpusobem. Na jeden projekt bezi vzdy
nejvys jeden beh, aby si dva zapisy nelezly do databaze.
"""
import queue
import threading
import time
from datetime import datetime

RUNS = {}
RUNS_LOCK = threading.Lock()


def now():
    return datetime.now().isoformat(timespec="seconds")


def plural(n, one, few, many):
    """Cestina sklonuje podle poctu: 1 jmeno, 2 az 4 jmena, 5 a vic jmen."""
    n = abs(n)
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


def with_num(n, one, few, many):
    return str(n) + " " + plural(n, one, few, many)


class Run:
    def __init__(self, slug, kind, params=None):
        self.slug = slug
        self.kind = kind
        self.params = params or {}
        self.stop_event = threading.Event()
        self.subs = []
        self.lock = threading.Lock()
        self.thread = None
        self.finished = False
        self.run_id = None
        self.final_status = None
        self.seconds = None
        self.segments_done = 0
        self.segments_failed = 0
        self.segments_review = 0
        self.segments_stripped = 0
        self.state = {
            "slug": slug, "kind": kind, "running": True,
            "done": 0, "total": 0, "message": "",
            "tps": None, "eta_s": None, "tokens_out": 0,
        }
        self.state.update(self.params)

    # -------------------------------------------------- odber udalosti
    def subscribe(self):
        q = queue.Queue()
        with self.lock:
            self.subs.append(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.subs:
                self.subs.remove(q)

    def emit(self, event):
        event.setdefault("kind", self.kind)
        with self.lock:
            subs = list(self.subs)
        for q in subs:
            q.put(event)

    def progress(self, **over):
        self.state.update(over)
        self.emit(dict(self.state, type="progress"))

    # -------------------------------------------------- zastaveni
    def request_stop(self):
        self.stop_event.set()

    def should_stop(self):
        return self.stop_event.is_set()


def active(slug):
    """Bezici beh projektu, nebo None."""
    with RUNS_LOCK:
        run = RUNS.get(slug)
        if run is not None and run.finished:
            return None
        return run


def latest(slug):
    """Posledni beh projektu, i kdyz uz dobehl.

    Kdyz beh skonci driv, nez se okno stihne pripojit k proudu udalosti,
    posluchac by jinak nedostal vysledek vubec.
    """
    with RUNS_LOCK:
        return RUNS.get(slug)


def start(slug, kind, worker, params=None):
    """Spusti beh. Vraci (beh, jestli je novy)."""
    with RUNS_LOCK:
        existing = RUNS.get(slug)
        if existing is not None and not existing.finished:
            return existing, False
        run = Run(slug, kind, params)
        RUNS[slug] = run
    run.thread = threading.Thread(target=worker, args=(run,), daemon=True)
    run.thread.start()
    return run, True


# ------------------------------------------------------ zaznam v tabulce run

def open_record(con, run):
    cur = con.execute(
        "INSERT INTO run (kind, started_at, status) VALUES (?,?,'running')",
        (run.kind, now()))
    run.run_id = cur.lastrowid
    con.commit()
    return run.run_id


def close_record(con, run, status, t0, error=None):
    try:
        con.execute(
            "UPDATE run SET finished_at=?, segments_done=?, tokens_out=?,"
            " tokens_per_s=?, status=?, error=? WHERE id=?",
            (now(), run.segments_done, run.state["tokens_out"],
             run.state["tps"], status, error, run.run_id))
        con.commit()
    except Exception:
        pass
    run.finished = True
    run.final_status = status
    run.seconds = round(time.time() - t0, 1)
    run.state["running"] = False
    run.emit(dict(run.state, type="end", status=status,
                  translated=run.segments_done, failed=run.segments_failed,
                  review=run.segments_review,
                  stripped=run.segments_stripped, seconds=run.seconds))
