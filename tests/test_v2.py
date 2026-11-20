import json
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from trainer.ai import generate
from trainer.dialog import respond
from trainer.media import style_for
from trainer.storage import database, import_vocabulary, transaction
from trainer.worker import prepare_next_gif, process, remind
from trainer.vk import accept, deliver, keyboard

ROOT = Path(__file__).resolve().parents[1]
Q = {"ru":"Я человек.", "zh":"我是人。", "pinyin":"wǒ shì rén", "explanation":"Утверждение."}


class DailyTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.dir = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.path = self.dir / "db"
        self.conn = ExitStack()
        self.stack.callback(self.conn.close)
        self.open()
        import_vocabulary(self.db, ROOT / "data/vocabulary.tsv")
        self.day = datetime(2026, 11, 19, 23, 59, tzinfo=ZoneInfo("Europe/Moscow"))
        for module in ("dialog", "training", "vk"):
            self.stack.enter_context(patch(f"trainer.{module}.moscow_now", side_effect=lambda: self.day))
        self.say("слов 1")

    def open(self):
        self.db = self.conn.enter_context(database(self.path, create=True))

    def say(self, text):
        with transaction(self.db):
            return respond(self.db, 1, text)

    def action(self, command):
        with transaction(self.db):
            return respond(self.db, 1, "", command)

    def job(self):
        self.assertTrue(process(self.db, 1))

    def grade(self):
        self.action("start")
        self.action("show 1 1")
        self.action("yes 1 1")

    def test_background_gifs_retry_restart_and_compounds(self):
        # No incoming messages or active training are needed to build the cache.
        self.db.execute("DELETE FROM cards")
        self.db.executemany("INSERT INTO cards(id,lesson,hanzi,pinyin,translation) VALUES (?,?,?,?,?)",
                            [("a",1,"人人","rénrén","все"), ("b",30,"大人","dàrén","взрослый")])
        with patch("trainer.worker.media.render", side_effect=lambda word, cache, speed: cache / word) as render, \
                patch("trainer.worker.media.upload", side_effect=[ConnectionError(), "doc1_2", "doc1_3", "doc1_4", "doc1_5"]):
            self.assertTrue(prepare_next_gif(self.db, None, 1, self.dir))
            self.assertEqual(self.db.execute("SELECT word FROM media_retry").fetchone()[0], "人人")
            self.conn.close()
            self.open()
            for _ in range(3):
                self.assertTrue(prepare_next_gif(self.db, None, 1, self.dir))
            self.assertFalse(prepare_next_gif(self.db, None, 1, self.dir))
            self.db.execute("UPDATE media_retry SET retry_at=0")
            self.assertTrue(prepare_next_gif(self.db, None, 1, self.dir))
            self.assertFalse(prepare_next_gif(self.db, None, 1, self.dir))
            self.assertEqual(render.call_count, 5)
        self.assertEqual({row[0] for row in self.db.execute("SELECT word FROM media")}, {"人","大","人人","大人"})
        self.action("start")
        self.assertEqual(self.action("show 1 1")["attachment"], "doc1_5")

    def test_page_link_modes_and_restart(self):
        self.action("start")
        self.action("animation off")
        self.assertNotIn("GIF", self.action("show 1 1")["text"])
        self.action("animation page")
        self.action("speed slow")
        self.conn.close()
        self.open()
        with patch.dict("os.environ", STROKES_PAGE_URL=""):
            self.assertIn("временно недоступна", self.action("start")["text"])
        with patch.dict("os.environ", STROKES_PAGE_URL="http://192.168.1.10:8765/"):
            rows = json.loads(keyboard(self.action("start")["buttons"]))["buttons"]
            link = rows[0][0]["action"]
            self.assertEqual(link["type"], "open_link")
            self.assertIn("word=", link["link"])
            self.assertIn("speed=slow", link["link"])

    def test_pending_gif_rechecks_settings_and_grade(self):
        self.action("translations 0")
        self.action("start")
        self.action("speed fast")
        self.action("show 1 1")
        with patch("trainer.worker.media.render") as render, patch("trainer.worker.media.upload", return_value="doc1_2"):
            prepare_next_gif(self.db, None, 1, self.dir)
            self.assertEqual(render.call_args.args[-1], "fast")
            self.assertEqual(self.db.execute("SELECT count(*) FROM outbox").fetchone()[0], 1)
            self.action("speed slow")
            api = Mock()
            deliver(self.db, api, 1)
            api.messages.send.assert_not_called()
            self.action("start")
            prepare_next_gif(self.db, None, 1, self.dir)
            self.action("animation off")
            self.action("animation gif")
            deliver(self.db, api, 1)
            api.messages.send.assert_not_called()
            self.action("speed normal")
            self.action("start")
            prepare_next_gif(self.db, None, 1, self.dir)
            self.action("yes 1 1")
            deliver(self.db, api, 1)
            api.messages.send.assert_not_called()

    def test_gif_delivery_and_navigation(self):
        self.action("start")
        self.action("show 1 1")
        api = Mock()
        with patch("trainer.worker.media.render"), patch("trainer.worker.media.upload", return_value="doc1_2"):
            prepare_next_gif(self.db, None, 1, self.dir)
            deliver(self.db, api, 1)
            self.assertEqual(api.messages.send.call_args.kwargs["attachment"], "doc1_2")
            self.action("speed slow")
            self.action("start")
            prepare_next_gif(self.db, None, 1, self.dir)
            with transaction(self.db):
                accept(self.db, 1, {"from_id": 1, "peer_id": 1, "conversation_message_id": 10,
                                    "payload": json.dumps({"action": "settings"})})
            api.reset_mock()
            deliver(self.db, api, 1)
            api.messages.send.assert_called_once()
            self.assertNotIn("attachment", api.messages.send.call_args.kwargs)

    def test_rejected_cached_gif_reuploaded_for_open_card(self):
        self.action("start")
        word = self.db.execute("SELECT hanzi FROM cards JOIN tasks ON card=id").fetchone()[0]
        self.db.execute("INSERT INTO media VALUES (?,?,?)", (word, style_for("normal"), "doc1_1"))
        with transaction(self.db):
            accept(self.db, 1, {"from_id": 1, "peer_id": 1, "conversation_message_id": 1,
                                "payload": json.dumps({"action": "show 1 1"})})
        api = Mock()
        invalid = RuntimeError()
        invalid.code = 100
        api.messages.send.side_effect = [101, invalid, 102]
        deliver(self.db, api, 1)
        card, cached = api.messages.send.call_args_list
        self.assertIn("keyboard", card.kwargs)
        self.assertNotIn("attachment", card.kwargs)
        self.assertNotIn("keyboard", cached.kwargs)
        self.assertEqual(cached.kwargs["attachment"], "doc1_1")
        with patch("trainer.worker.media.render"), patch("trainer.worker.media.upload", return_value="doc1_2"):
            prepare_next_gif(self.db, None, 1, self.dir)
        deliver(self.db, api, 1)
        self.assertEqual(api.messages.send.call_args.kwargs["attachment"], "doc1_2")
        self.assertNotIn("keyboard", api.messages.send.call_args.kwargs)

    def test_midnight_extra_and_reminders(self):
        self.action("translations 0")
        self.action("start")
        self.say("слов 3")
        self.day += timedelta(minutes=2)
        self.conn.close()
        self.open()
        self.assertIn("1 из 1", self.action("start")["text"])
        self.action("show 1 1")
        self.assertIn("Подборка на сегодня", str(self.action("yes 1 1")["buttons"]))
        self.assertIn("1 из 3", self.action("start")["text"])
        self.day = self.day.replace(hour=19)
        remind(self.db, 1, self.day)
        self.say("время 18:00")
        remind(self.db, 1, self.day)
        self.assertEqual(self.db.execute("SELECT count(*) FROM reminders_sent").fetchone()[0], 1)
        for n in range(1, 4):
            self.action(f"show 2 {n}")
            self.action(f"yes 2 {n}")
        api = Mock()
        deliver(self.db, api, 1)
        api.messages.send.assert_not_called()
        self.assertIn("Дополнительная", str(self.action("start")["buttons"]))
        self.action("extra 2")
        self.action("extra 2")
        self.assertEqual(self.db.execute("SELECT count(*) FROM sessions").fetchone()[0], 3)

    def test_restart_generation_retry_alternative_and_next(self):
        self.grade()
        self.conn.close()
        self.open()
        with patch("trainer.ai.generate", return_value=Q):
            self.job()
        self.assertIn(Q["ru"], self.action("start")["text"])
        self.say("我")
        with patch("trainer.ai.check", return_value={"correct":False, "hint":"Проверьте сказуемое."}):
            self.job()
        self.assertIn("сказуемое", self.action("start")["text"])
        self.say("我是一个人。")
        self.conn.close()
        self.open()
        with patch("trainer.ai.check", side_effect=ConnectionError):
            self.job()
        self.assertIn("сохранён", self.action("start")["text"])
        self.action("retry 1 1")
        with patch("trainer.ai.check", return_value={"correct":True, "hint":""}):
            self.job()
        self.assertIn("Верно", self.action("start")["text"])
        self.action("next 1 1")
        self.assertIn("Переводов выполнено — 1\nС первой попытки — 0", self.action("stats")["text"])
        attempts = self.db.execute("SELECT answer,correct FROM translation_attempts ORDER BY attempt")
        self.assertEqual(list(map(tuple, attempts)), [("我",0), ("我是一个人。",1)])

    def test_skip_during_generation(self):
        self.grade()
        def late(_):
            self.action("skip 1 1")
            return Q
        with patch("trainer.ai.generate", side_effect=late):
            self.job()
        self.assertIn("завершена", self.action("start")["text"])
        self.assertEqual(self.db.execute("SELECT count(*) FROM outbox").fetchone()[0], 0)
        self.assertIn("Пропущено — 1", self.action("stats")["text"])

    def test_solution(self):
        self.grade()
        with patch("trainer.ai.generate", return_value=Q):
            self.job()
        result = self.action("solution 1 1")["text"]
        for key in ("zh","pinyin","explanation"):
            self.assertIn(Q[key], result)
        self.assertIn("завершена", self.action("next 1 1")["text"])

    def test_reminder_after_downtime(self):
        remind(self.db, 1, self.day)
        self.conn.close()
        self.open()
        self.day += timedelta(days=4)
        remind(self.db, 1, self.day)
        api = Mock()
        deliver(self.db, api, 1)
        api.messages.send.assert_called_once()
        remind(self.db, 1, self.day)
        self.assertEqual(self.db.execute("SELECT count(*) FROM outbox").fetchone()[0], 0)

    def test_enable_next_word_disable_pending(self):
        self.say("слов 2")
        self.action("translations 0")
        self.action("start")
        self.action("translations 1")
        self.action("show 1 1")
        self.assertIn("2 из 2", self.action("yes 1 1")["text"])
        self.action("show 1 2")
        self.assertIn("предложение", self.action("yes 1 2")["text"])
        self.action("translations 0")
        self.assertIn("завершена", self.action("start")["text"])
        self.assertIsNone(self.db.execute("SELECT 1 FROM jobs WHERE kind='generate'").fetchone())

    def test_allowed_characters(self):
        task = {"hanzi":"人", "translation":"человек", "allowed":"我是人"}
        invalid = dict(Q, zh="我是超人。")
        with patch("trainer.ai.ask", side_effect=[{},invalid,Q]) as ask:
            self.assertEqual(generate(task), Q)
            self.assertEqual(ask.call_count, 3)
        with patch("trainer.ai.ask", return_value=invalid):
            with self.assertRaises(ValueError):
                generate(task)

