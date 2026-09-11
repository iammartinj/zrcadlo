"""Rezim TranslateGemma: prompt, prevod zvyrazneni, kontrola titulu, profil modelu.

Spusteni z korene repozitare:
    .venv\\Scripts\\python.exe -m unittest discover -s tests
"""
import unittest

from app import checks, config, glossary, llm, prompt

BOOK = {"style_register": "neutralni", "style_narrator": "neurceno",
        "style_address": "vykani", "feminize_surnames": 0, "style_note": "",
        "source_lang": "en", "target_lang": "cs"}
MAE = {"term_src": "Mae", "term_cs": "Mae", "category": "osoba", "gender": "f"}
RENESANCE = {"term_src": "Renaissance", "term_cs": "Renesance", "category": "misto",
             "gender": "f"}

# zneni overene proti chatove sablone ze souboru GGUF
NATIVNI = ("<start_of_turn>user\nYou are a professional English (en) to Czech (cs) "
           "translator. Your goal is to accurately convey the meaning and nuances of the "
           "original English text while adhering to Czech grammar, vocabulary, and "
           "cultural sensitivities.\nProduce only the Czech translation, without any "
           "additional explanations or commentary. Please translate the following "
           "English text into Czech:\n\n\nHello.<end_of_turn>\n")


class MarkdownEmphasis(unittest.TestCase):
    def test_kurziva_a_tucne(self):
        self.assertEqual(prompt.markdown_emphasis("**tučně** a *kurzíva*"),
                         "<strong>tučně</strong> a <em>kurzíva</em>")

    def test_hvezdicky_mimo_zvyrazneni_zustanou(self):
        for text in ("* * *", "5*3*2", "hvězdička * sama"):
            self.assertEqual(prompt.markdown_emphasis(text), text)

    def test_zvyrazneni_ze_zdroje_prezije_celou_cestu(self):
        src = "He was <em>so</em> proud, <em>so</em> very <em>proud</em>."
        vystup = "Byl *tak* hrdý, *tak* moc *hrdý*."
        html = checks.strip_added_markup(src, prompt.sanitize(prompt.markdown_emphasis(vystup)))
        self.assertEqual(checks.tag_counts(html), {"em": 3})
        self.assertNotIn("*", html)

    def test_zvyrazneni_navic_zmizi_i_s_hvezdickami(self):
        html = checks.strip_added_markup(
            "Plain source.", prompt.sanitize(prompt.markdown_emphasis("Prostý *text*.")))
        self.assertEqual(html, "Prostý text.")


class Prompt(unittest.TestCase):
    def test_nativni_zneni(self):
        self.assertEqual(prompt.tg_turn("Hello."), NATIVNI)

    def test_stavba_promptu(self):
        seg = {"kind": "para"}
        text = prompt.tg_prompt(BOOK, seg, "Mae looked up.",
                                [("First.", "První."), ("Second.", "Druhý.")], [MAE])
        self.assertTrue(text.startswith("<start_of_turn>user\nYou are a professional"))
        self.assertTrue(text.endswith("<start_of_turn>model\n"))
        self.assertNotIn("<bos>", text)
        # slovnicek, pak historie od nejstarsiho, pak aktualni odstavec
        self.assertLess(text.index("\nMae<end_of_turn>"), text.index("First."))
        self.assertLess(text.index("First."), text.index("Second."))
        self.assertLess(text.index("Druhý."), text.index("Mae looked up."))
        self.assertEqual(text.count("Notes:"), 1)   # pokyn jen v poslednim kole

    def test_pokyn_z_karty_a_slovnicku(self):
        notes = prompt.tg_notes(BOOK, [MAE, RENESANCE])
        self.assertIn("vykání", notes)
        self.assertIn("Mae is a woman.", notes)
        self.assertNotIn("Renesance is", notes)     # misto neni osoba
        self.assertIn("Do not feminize", notes)
        self.assertNotIn("{{1}}", notes)

    def test_nadpis_a_odkazy(self):
        text = prompt.tg_prompt(BOOK, {"kind": "head"}, "Chapter {{1}}")
        self.assertIn("heading", text)
        self.assertIn("{{1}}", text.split("Notes:")[1])

    def test_kontext_bez_odkazu_na_poznamky(self):
        html = 'Věta<sup><a class="noteref" data-note="n1">1</a></sup> dál.'
        self.assertEqual(prompt.tg_context(html), "Věta dál.")


class Titul(unittest.TestCase):
    def test_pani_navic(self):
        self.assertEqual(checks.honorific_added("Mae smiled.", "Paní Mae se usmála.", [MAE]),
                         ["Mae"])

    def test_titul_v_originale(self):
        self.assertEqual(checks.honorific_added("Mrs. Mae smiled.", "Paní Mae se usmála.",
                                                [MAE]), [])

    def test_bez_titulu(self):
        self.assertEqual(checks.honorific_added("Mae smiled.", "Mae se usmála.", [MAE]), [])

    def test_jen_osoby(self):
        self.assertEqual(checks.honorific_added("Renaissance.", "Paní Renesance.",
                                                [RENESANCE]), [])

    def test_inspect_hlasi_titul(self):
        seg = {"src_text": "Mae smiled.", "src_html": "Mae smiled."}
        kinds = [p["kind"] for p in checks.inspect(seg, "Paní Mae se usmála.",
                                                   "Paní Mae se usmála.", [MAE])]
        self.assertIn("honorific", kinds)


class Profil(unittest.TestCase):
    def test_rezim_podle_modelu(self):
        self.assertEqual(config.translation_mode("translategemma-12b-it"), "translategemma")
        self.assertEqual(config.translation_mode("mradermacher/translategemma-12b-it"),
                         "translategemma")
        self.assertEqual(config.translation_mode("gemma-3-12b-it-qat"), "chat")

    def test_teplota_z_profilu(self):
        self.assertEqual(llm._payload([], False, model="translategemma-12b-it")["temperature"],
                         0.1)
        self.assertEqual(llm._payload([], False, model="gemma-3-12b-it-qat")["temperature"],
                         config.CFG["inference"]["temperature"])

    def test_completions_bez_zprav(self):
        body = llm._completion_payload("text", True, model="translategemma-12b-it",
                                       stop=prompt.TG_STOP)
        self.assertNotIn("messages", body)
        self.assertEqual(body["prompt"], "text")
        self.assertEqual(body["stop"], ["<end_of_turn>"])
        self.assertEqual(body["model"], "translategemma-12b-it")


class PomocnyModel(unittest.TestCase):
    """Slovnicek se pta pomocneho modelu, jinak jen tam, kam se model preda."""

    def test_slovnicek_se_pta_pomocneho_modelu(self):
        zachyceno = []
        puvodni = llm.stream_chat

        def falesny(messages, should_stop=None, model=None):
            zachyceno.append(model)
            yield "delta", "[]"

        class Beh:
            state = {"tokens_out": 0}

            def should_stop(self):
                return False

        llm.stream_chat = falesny
        try:
            glossary.collect_text(Beh(), [])
            glossary.collect_text(Beh(), [], model="prekladovy-model")
        finally:
            llm.stream_chat = puvodni
        self.assertEqual(zachyceno, [config.helper_model(), "prekladovy-model"])


if __name__ == "__main__":
    unittest.main()
