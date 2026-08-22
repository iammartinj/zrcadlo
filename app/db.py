"""SQLite vrstva. Jedna kniha = jeden projekt = jedna project.db."""
import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS book (
    id                INTEGER PRIMARY KEY CHECK (id = 1),
    title             TEXT NOT NULL,
    author            TEXT,
    source_path       TEXT NOT NULL,
    source_hash       TEXT NOT NULL,
    source_lang       TEXT NOT NULL DEFAULT 'en',
    target_lang       TEXT NOT NULL DEFAULT 'cs',
    style_register    TEXT NOT NULL DEFAULT 'neutralni',
    style_narrator    TEXT NOT NULL DEFAULT 'neurceno',
    style_address     TEXT NOT NULL DEFAULT 'neurceno',
    style_note        TEXT NOT NULL DEFAULT '',
    feminize_surnames INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chapter (
    id        INTEGER PRIMARY KEY,
    ord       INTEGER NOT NULL UNIQUE,
    title     TEXT NOT NULL,
    href      TEXT
);

CREATE TABLE IF NOT EXISTS segment (
    id         INTEGER PRIMARY KEY,
    ord        INTEGER NOT NULL UNIQUE,
    chapter    INTEGER NOT NULL REFERENCES chapter(ord),
    kind       TEXT NOT NULL,               -- head | para | quote | note
    level      INTEGER NOT NULL DEFAULT 0,  -- uroven nadpisu, 0 = neni nadpis
    src_text   TEXT NOT NULL,               -- holy text
    src_html   TEXT NOT NULL,               -- text vcetne kurzivy a odkazu na poznamku
    tgt_text   TEXT,
    tgt_html   TEXT,
    status     TEXT NOT NULL DEFAULT 'pending',  -- pending | done | failed | review
    src_hash   TEXT NOT NULL,
    attempts   INTEGER NOT NULL DEFAULT 0,
    note_id    TEXT,        -- u kind='note': id kotvy, na kterou je poznamka navazana
    note_ord   INTEGER,     -- u kind='note': ord segmentu, kde se odkaz vyskytl
    note_txt   TEXT,        -- u kind='note': znacka odkazu (napr. "1")
    review_note TEXT        -- proc segment skoncil ve stavu 'review'
);

CREATE INDEX IF NOT EXISTS segment_chapter ON segment(chapter, ord);
CREATE INDEX IF NOT EXISTS segment_status  ON segment(status);

CREATE TABLE IF NOT EXISTS glossary (
    id            INTEGER PRIMARY KEY,
    term_src      TEXT NOT NULL UNIQUE,
    term_cs       TEXT NOT NULL DEFAULT '',   -- cesky tvar v prvnim pade
    category      TEXT NOT NULL DEFAULT 'pojem',  -- osoba | misto | organizace | pojem
    gender        TEXT NOT NULL DEFAULT '',   -- m | f | n | ''
    note          TEXT NOT NULL DEFAULT '',   -- jedna veta oduvodneni
    occurrences   INTEGER NOT NULL DEFAULT 0,
    first_chapter INTEGER,
    locked        INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT
);

CREATE TABLE IF NOT EXISTS run (
    id            INTEGER PRIMARY KEY,
    kind          TEXT NOT NULL,             -- import | glossary | translate | export
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    segments_done INTEGER NOT NULL DEFAULT 0,
    tokens_out    INTEGER NOT NULL DEFAULT 0,
    tokens_per_s  REAL,
    status        TEXT NOT NULL DEFAULT 'running',  -- running | done | stopped | error
    error         TEXT
);
"""


# sloupce doplnene az po zalozeni prvnich projektu
MIGRATIONS = {
    "segment": {
        "review_note": "ALTER TABLE segment ADD COLUMN review_note TEXT",
    },
}


def migrate(con: sqlite3.Connection) -> None:
    """Doplni sloupce, ktere v starsich projektech chybi.

    Na prazdne databazi se nedela nic: tabulky jeste nejsou a schema je zalozi
    rovnou i s temito sloupci.
    """
    for table, columns in MIGRATIONS.items():
        have = {r["name"] for r in con.execute("PRAGMA table_info(" + table + ")")}
        if not have:
            continue                      # tabulka neexistuje, neni co doplnovat
        for name, sql in columns.items():
            if name not in have:
                con.execute(sql)
                con.commit()


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    migrate(con)
    return con


def init(db_path: Path) -> sqlite3.Connection:
    con = connect(db_path)
    con.executescript(SCHEMA)
    con.commit()
    return con
