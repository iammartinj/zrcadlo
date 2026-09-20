"""Vyskyty vyrazu ze slovnicku: prohlizeni bere i neprelozene odstavce.

Spusteni z korene repozitare:
    .venv\\Scripts\\python.exe -m unittest discover -s tests
"""
import tempfile
import unittest
from pathlib import Path

from app import db, glossary, projects

SEGMENTY = [
    (1, 1, "para", "Mae opened the door.", "done"),
    (2, 1, "para", "Nothing about her here.", "done"),
    (3, 2, "para", "Mae looked up again.", "review"),
    (4, 2, "para", "Mae said nothing.", "pending"),
    (5, 2, "para", "Mae on a dropped page.", "skipped"),
]


class Vyskyty(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "kniha").mkdir()
        con = db.init(root / "kniha" / "project.db")
        con.execute("INSERT INTO chapter (ord, title) VALUES (1, 'První')")
        con.execute("INSERT INTO chapter (ord, title) VALUES (2, 'Druhá')")
        for ord_, chapter, kind, text, status in SEGMENTY:
            con.execute(
                "INSERT INTO segment (ord, chapter, kind, src_text, src_html,"
                " status, src_hash) VALUES (?,?,?,?,?,?,?)",
                (ord_, chapter, kind, text, text, status, "h" + str(ord_)))
        con.execute("INSERT INTO glossary (term_src, term_cs) VALUES ('Mae', 'Mae')")
        con.commit()
        con.close()
        self.puvodni = projects.PROJECTS_DIR
        projects.PROJECTS_DIR = root

    def tearDown(self):
        projects.PROJECTS_DIR = self.puvodni
        self.tmp.cleanup()

    def ordy(self, only_done):
        found = glossary.affected_segments("kniha", 1, only_done=only_done)
        return [s["ord"] for s in found["segments"]]

    def test_pro_preklad_znovu_jen_hotove(self):
        self.assertEqual(self.ordy(True), [1])

    def test_pro_prohlizeni_i_rozdelane(self):
        self.assertEqual(self.ordy(False), [1, 3, 4])

    def test_vyrazeny_odstavec_nikdy(self):
        self.assertNotIn(5, self.ordy(False))

    def test_neznama_polozka(self):
        self.assertIsNone(glossary.affected_segments("kniha", 99, only_done=False))


if __name__ == "__main__":
    unittest.main()
