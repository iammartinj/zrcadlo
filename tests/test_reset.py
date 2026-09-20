"""Nový překlad: zahození hotového překladu celé knihy.

Spusteni z korene repozitare:
    .venv\\Scripts\\python.exe -m unittest discover -s tests
"""
import tempfile
import unittest
from pathlib import Path

from app import db, projects

SEGMENTY = [
    (1, "Mae opened the door.", "Mae otevřela dveře.", "done"),
    (2, "She said nothing.", "Neřekla nic.", "review"),
    (3, "A third one.", "Třetí.", "failed"),
    (4, "Not translated yet.", None, "pending"),
    (5, "Page 14", "Page 14", "skipped"),
]


class NovyPreklad(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "kniha").mkdir()
        con = db.init(self.root / "kniha" / "project.db")
        con.execute("INSERT INTO chapter (ord, title) VALUES (1, 'První')")
        for ord_, src, tgt, status in SEGMENTY:
            con.execute(
                "INSERT INTO segment (ord, chapter, kind, src_text, src_html,"
                " tgt_text, tgt_html, status, src_hash, attempts, review_note)"
                " VALUES (?,1,'para',?,?,?,?,?,?,2,'něco')",
                (ord_, src, src, tgt, tgt, status, "h" + str(ord_)))
        con.execute("INSERT INTO glossary (term_src, term_cs) VALUES ('Mae', 'Mae')")
        con.commit()
        con.close()
        self.puvodni = projects.PROJECTS_DIR
        projects.PROJECTS_DIR = self.root

    def tearDown(self):
        projects.PROJECTS_DIR = self.puvodni
        self.tmp.cleanup()

    def stavy(self):
        con = db.connect(self.root / "kniha" / "project.db")
        try:
            return {r["ord"]: dict(r) for r in con.execute(
                "SELECT ord, status, tgt_text, tgt_html, attempts, review_note"
                " FROM segment")}
        finally:
            con.close()

    def test_vraci_pocet_zahozenych(self):
        self.assertEqual(projects.reset_book("kniha")["reset"], 3)

    def test_prelozene_se_vyprazdni(self):
        projects.reset_book("kniha")
        for ord_ in (1, 2, 3):
            seg = self.stavy()[ord_]
            self.assertEqual(seg["status"], "pending", ord_)
            self.assertIsNone(seg["tgt_text"], ord_)
            self.assertIsNone(seg["tgt_html"], ord_)
            self.assertIsNone(seg["review_note"], ord_)
            self.assertEqual(seg["attempts"], 0, ord_)

    def test_vyrazeny_odstavec_zustane(self):
        projects.reset_book("kniha")
        seg = self.stavy()[5]
        self.assertEqual(seg["status"], "skipped")
        self.assertEqual(seg["tgt_text"], "Page 14")

    def test_slovnicek_zustane(self):
        projects.reset_book("kniha")
        con = db.connect(self.root / "kniha" / "project.db")
        try:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM glossary").fetchone()[0], 1)
        finally:
            con.close()

    def test_zaloha_drzi_puvodni_stav(self):
        vysledek = projects.reset_book("kniha")
        zaloha = self.root / "kniha" / vysledek["backup"]
        self.assertTrue(zaloha.exists())
        con = db.connect(zaloha)
        try:
            row = con.execute("SELECT tgt_text, status FROM segment WHERE ord = 1").fetchone()
        finally:
            con.close()
        self.assertEqual(row["tgt_text"], "Mae otevřela dveře.")
        self.assertEqual(row["status"], "done")

    def test_neznamy_projekt(self):
        self.assertIsNone(projects.reset_book("takovy-neni"))


if __name__ == "__main__":
    unittest.main()
