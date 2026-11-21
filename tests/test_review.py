import csv
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack, closing
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from trainer.dialog import respond
from trainer.storage import database, import_vocabulary, transaction
from trainer.vk import deliver
from trainer.worker import process, remind

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/vocabulary.tsv"
OWNER = 42


class ReviewTest(unittest.TestCase):
    def setUp(self):
        with SOURCE.open(encoding="utf-8-sig", newline="") as source:
            self.lesson23 = [row["key"] for row in csv.DictReader(source, delimiter="\t") if row["lesson"] == "23"]
        storage = ExitStack()
        self.addCleanup(storage.close)
        directory = storage.enter_context(tempfile.TemporaryDirectory())
        self.path = Path(directory) / "trainer.sqlite3"
        shutil.copyfile(ROOT / "data/trainer.sqlite3", self.path)
        with closing(sqlite3.connect(self.path)) as old:
            old.execute("INSERT INTO learners VALUES (?,23)", (OWNER,))
            old.execute("INSERT INTO progress VALUES (?,'B09-15',6,2,'no',0,6)", (OWNER,))
            old.commit()
        self.connection = ExitStack()
        storage.callback(self.connection.close)
        self.db = self.connection.enter_context(database(self.path))
        self.action("translations 0")
        self.action("animation off")

    def action(self, command=None, text=""):
        with transaction(self.db):
            return respond(self.db, OWNER, text, command)

    def cards(self, session):
        return [row[0] for row in self.db.execute("SELECT card FROM tasks WHERE session=? ORDER BY number", (session,))]

    def test_upgrade_reimport_order_and_independent_daily(self):
        def lesson_links():
            return list(map(tuple, self.db.execute("SELECT * FROM lesson_cards ORDER BY lesson,position,card_key")))

        self.assertEqual([row[0] for row in self.db.execute(
            "SELECT lesson FROM lesson_cards WHERE card_key='B09-15' ORDER BY lesson")], [9, 23])
        self.assertEqual(tuple(self.db.execute("SELECT card,attempts,errors FROM progress").fetchone()),
                         ("B09-15", 6, 2))
        links = lesson_links()
        self.assertEqual(import_vocabulary(self.db, SOURCE), 705)
        self.assertEqual(lesson_links(), links)
        backup, = self.path.parent.glob("*.bak")
        with closing(sqlite3.connect(backup)) as saved:
            self.assertEqual(saved.execute("PRAGMA user_version").fetchone()[0], 1)
        self.assertIn("Повторить урок", str(self.action("menu")["buttons"]))
        self.assertIn("Выберите открытый урок", self.action("review lesson 24")["text"])
        self.action("review lesson 23")
        self.action("review at 23 ordered")
        self.assertEqual(self.cards(1), self.lesson23)
        self.assertIn("Продолжить тренировку", str(self.action("start")["buttons"]))
        self.assertIn("Продолжить тренировку", str(self.action("review")["buttons"]))
        self.assertEqual(self.db.execute("SELECT count(*) FROM sessions").fetchone()[0], 1)
        for number, key in enumerate(self.lesson23, 1):
            answer = self.action(f"show 1 {number}")
            if key == "B09-15":
                self.assertIn("Урок 23", answer["text"])
            final = self.action(f"yes 1 {number}")
        self.assertIn("Подборка на сегодня", str(final["buttons"]))
        self.assertEqual(self.db.execute("SELECT attempts FROM progress WHERE card='B09-15'").fetchone()[0], 7)
        remind(self.db, OWNER, datetime.now().replace(hour=23))
        self.assertEqual(self.db.execute("SELECT count(*) FROM reminders_sent").fetchone()[0], 1)
        self.action("start")
        self.assertEqual(list(map(tuple, self.db.execute("SELECT kind,finished FROM sessions ORDER BY id"))),
                         [("review", 1), ("daily", 0)])

    def test_random_priority_and_errors_filter(self):
        recent, known = self.lesson23[:2]
        self.db.executemany("""INSERT INTO progress(learner,card,last_grade,known,rated_at)
                               VALUES (?,?,'no',?,?)""", [(OWNER, recent, 0, 100), (OWNER, known, 1, 200)])
        self.action(text="слов 1")
        self.action("review lesson 23")
        self.action("review at 23 random")
        self.assertEqual(self.cards(1), [recent])
        self.action("show 1 1")
        self.action("yes 1 1")
        self.action("review lesson 23")
        self.action("review at 23 errors")
        self.action("review at 23 ordered")
        self.assertEqual(self.cards(2), ["B09-15"])
        self.action("show 2 1")
        self.action("known 2 1")
        self.action("review lesson 23")
        no_errors = self.action("review at 23 errors")
        self.assertIn("Снимите фильтр", no_errors["text"])
        self.action("review at 23 errors")
        self.action("review at 23 ordered")
        number = self.lesson23.index("B09-15") + 1
        for current in range(1, number + 1):
            self.action(f"show 3 {current}")
            self.action(f"no 3 {current}")
        self.assertEqual(tuple(self.db.execute("SELECT known,last_rating FROM progress WHERE card='B09-15'").fetchone()),
                         (0, "no"))

    def test_manual_selection_restart_and_reimport(self):
        self.action("review lesson 23")
        self.action("review at 23 manual")
        self.action("review at 23 page 1")
        self.action(f"review at 23 toggle {self.lesson23[5]}")
        self.action("review at 23 page 0")
        self.action(f"review at 23 toggle {self.lesson23[0]}")
        self.action("review at 23 page 1")
        self.connection.close()
        self.db = self.connection.enter_context(database(self.path))
        import_vocabulary(self.db, SOURCE)
        restored = self.action("review")
        self.assertIn("выбрано 2 · страница 2", restored["text"])
        self.assertIn("✓", restored["text"])
        self.action("review at 23 selected")
        self.assertEqual(self.cards(1), [self.lesson23[0], self.lesson23[5]])

    def test_translation_finishes_while_search_open(self):
        self.action("translations 1")
        self.action("review lesson 1")
        self.action("review at 1 random")
        self.action("show 1 1")
        self.action("no 1 1")
        self.action("search")
        self.action("search filter hanzi")
        self.connection.close()
        self.db = self.connection.enter_context(database(self.path))
        question = {"ru": "Это я.", "zh": "是我。", "pinyin": "shì wǒ", "explanation": "Связка.", "difficulty": "easy"}
        with patch("trainer.ai.generate", return_value=question):
            self.assertTrue(process(self.db, OWNER))
        api = Mock()
        deliver(self.db, api, OWNER)
        api.messages.send.assert_not_called()
        self.assertIn("Иероглифы — 看", self.action(text="看")["text"])
        self.assertIn(question["ru"], self.action("resume")["text"])
