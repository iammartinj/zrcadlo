"""Narovnani escapovanych znacek a odhaleni nepreloženeho odstavce.

Spusteni z korene repozitare:
    .venv\\Scripts\\python.exe -m unittest discover -s tests
"""
import unittest

from app import checks, prompt


class EscapovaneLomitko(unittest.TestCase):
    def test_uzaviraci_znacka_se_narovna(self):
        self.assertEqual(prompt.sanitize("<em>Modře!<\\/em> vykřikl do ticha."),
                         "<em>Modře!</em> vykřikl do ticha.")

    def test_kurziva_nepretece_pres_zbytek_odstavce(self):
        html = prompt.sanitize("„<em>Co?<\\/em>“ zeptal se.")
        self.assertEqual(checks.tag_counts(html), {"em": 1})
        self.assertNotIn("\\", html)
        self.assertTrue(html.endswith("“ zeptal se."))

    def test_lomitko_v_textu_zustane(self):
        self.assertEqual(prompt.sanitize("podíl 5\\3 a zpětné lomítko \\ samo o sobě"),
                         "podíl 5\\3 a zpětné lomítko \\ samo o sobě")


class Nepreloženo(unittest.TestCase):
    def test_shodny_text_se_hlasi(self):
        self.assertTrue(checks.untranslated("[ONE]", "[ONE]"))
        self.assertTrue(checks.untranslated("MEMO", "MEMO"))
        self.assertTrue(checks.untranslated("“Get in the car.”", "“Get in the car.”"))

    def test_bily_znak_navic_nerozhoduje(self):
        self.assertTrue(checks.untranslated("SUICIDE CULT CLAIMS SIX",
                                            " SUICIDE  CULT CLAIMS SIX "))

    def test_preloženy_odstavec_projde(self):
        self.assertFalse(checks.untranslated("Get in the car.", "Nastup do auta."))

    def test_bez_slov_projde(self):
        for text in ("• • •", "[I]", "2012046980", "1 2 3 4 5"):
            self.assertFalse(checks.untranslated(text, text), text)

    def test_jmeno_ve_vete_projde(self):
        self.assertFalse(checks.untranslated("Volal Max Barry.", "Volal Max Barry."))

    def test_odstavec_ze_sameho_jmena_jde_k_revizi(self):
        # Prvni slovo odstavce nejde odlisit od bezneho slova, takze holé
        # "Max Barry" skonci k revizi. Je to levnejsi nez propustit "Copyright".
        self.assertTrue(checks.untranslated("Max Barry", "Max Barry"))

    def test_inspect_hlasi_nepreloženo(self):
        seg = {"src_text": "[ONE]", "src_html": "[ONE]"}
        kinds = [p["kind"] for p in checks.inspect(seg, "[ONE]", "[ONE]", [])]
        self.assertIn("untranslated", kinds)


if __name__ == "__main__":
    unittest.main()
