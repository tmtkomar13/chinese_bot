import json
import shutil
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

from trainer.dialog import respond
from trainer.storage import backup, database, import_vocabulary, transaction
from trainer.training import select_cards
from trainer.vk import accept, deliver, poll_once

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/vocabulary.tsv"
OWNER = 141


class TrainingTest(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enter_temp())
        self.path = self.directory / "live.sqlite3"
        shutil.copyfile(ROOT / "data/trainer.sqlite3", self.path)
        self.connections = ExitStack()
        self.addCleanup(self.connections.close)
        self.open()

    def enter_temp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return temporary.name

    def open(self):
        self.db = self.connections.enter_context(database(self.path))

    def action(self, command):
        with transaction(self.db):
            return respond(self.db, OWNER, "", command)

    def test_textbook_and_import(self):
        self.assertEqual(import_vocabulary(self.db, SOURCE), 705)
        count, lessons = self.db.execute("SELECT count(*), count(DISTINCT lesson) FROM cards").fetchone()
        self.assertEqual((count, lessons), (705, 30))
        readings = list(self.db.execute("SELECT lesson,translation FROM cards WHERE hanzi='看' ORDER BY lesson"))
        self.assertEqual([tuple(row) for row in readings], [(9, "смотреть"), (20, "навестить, обратиться к врачу")])
        self.assertEqual(self.db.execute("SELECT pinyin FROM cards WHERE hanzi='联欢会'").fetchone()[0], "liánhuānhuì")
        self.assertFalse(self.db.execute("PRAGMA foreign_key_check").fetchall())

    def test_full_round_restart_reimport_and_backup(self):
        self.action("translations 0")
        prompt = self.action("start")
        tasks = list(self.db.execute("SELECT t.card,c.lesson FROM tasks t JOIN cards c ON t.card=c.id ORDER BY t.number"))
        self.assertEqual(len(set(r["card"] for r in tasks)), 10)
        self.assertTrue(all(r["lesson"] <= 13 for r in tasks))
        self.action("yes 1 1")
        self.assertFalse(self.db.execute("SELECT * FROM progress").fetchall())
        card = self.db.execute("SELECT * FROM cards WHERE id=?", (tasks[0]["card"],)).fetchone()
        self.assertEqual(prompt["text"], "Задание 1 из 10\n" + card["translation"])
        answer = self.action("show 1 1")
        for value in (card["hanzi"], card["pinyin"], f"Урок {card['lesson']}"):
            self.assertIn(value, answer["text"])
        self.connections.close()
        self.open()
        self.assertIn(("Продолжить тренировку", "resume"), self.action("start")["buttons"][0])
        self.assertEqual(self.action("resume"), answer)
        self.action("no 1 1")
        self.action("yes 1 1")
        self.action("show 1 1")
        self.assertEqual(import_vocabulary(self.db, SOURCE), 705)
        for number in range(2, 11):
            self.action(f"show 1 {number}")
            grade = "known" if number == 2 else "yes"
            result = self.action(f"{grade} 1 {number}")
        self.assertEqual(result["text"], "Тренировка завершена.\nВспомнил — 8\nНе вспомнил — 1\nТочно знаю — 1")
        self.assertEqual(self.action("stats")["text"], "Попытки — 9\nОшибки — 1\nТочно знаю — 1\nПереводов выполнено — 0\nС первой попытки — 0\nПропущено — 0")
        self.assertNotIn(tasks[1]["card"], select_cards(self.db, OWNER))
        self.assertIn(tasks[0]["card"], select_cards(self.db, OWNER))
        copy = backup(self.db, self.directory / "backups")
        with database(copy) as restored:
            self.assertEqual(respond(restored, OWNER, "", "stats"), self.action("stats"))
        restore = self.action("excluded")["buttons"][0][0][1]
        self.action(restore)
        self.assertIn("Точно знаю — 0", self.action("stats")["text"])

    def test_selection_and_small_pool(self):
        self.action("translations 0")
        self.action("lesson 1")
        self.db.execute("""
            INSERT INTO progress(learner,card,attempts,errors,last_grade)
            SELECT ?,id,8,1,'no' FROM cards WHERE lesson=1 ORDER BY id LIMIT 7
        """, (OWNER,))
        chosen = select_cards(self.db, OWNER)
        failed = {r[0] for r in self.db.execute("SELECT card FROM progress")}
        self.assertEqual(len(failed.intersection(chosen)), 5)
        self.action("lesson 30")
        self.assertEqual(self.db.execute("SELECT lesson FROM learners").fetchone()[0], 30)
        self.action("lesson 1")
        self.db.execute("UPDATE progress SET known=1")
        self.db.execute("""
            INSERT OR IGNORE INTO progress(learner,card,known)
            SELECT ?,id,1 FROM cards WHERE lesson=1 AND id!='B01-20'
        """, (OWNER,))
        self.assertEqual(select_cards(self.db, OWNER), ["B01-20"])
        self.action("start")
        self.action("show 1 1")
        self.action("known 1 1")
        self.assertIn("завершена", self.action("start")["text"])
        self.assertIn("Нет доступных", self.action("extra 1")["text"])

    def test_backup_retention(self):
        directory = self.directory / "backups"
        directory.mkdir()
        for day in range(1, 9):
            (directory / f"trainer-2000-01-{day:02}.sqlite3").touch()
        backup(self.db, directory)
        self.assertEqual(len(list(directory.glob("*.sqlite3"))), 7)
        self.assertFalse((directory / "trainer-2000-01-02.sqlite3").exists())

    def test_owner_and_redelivery(self):
        def incoming(sender, peer, number, action):
            return {"from_id": sender, "peer_id": peer, "conversation_message_id": number,
                    "text": "", "payload": json.dumps({"action": action})}
        with transaction(self.db):
            accept(self.db, OWNER, incoming(5, 5, 11, "start"))
            accept(self.db, OWNER, incoming(OWNER, 2000000001, 12, "start"))
        self.assertEqual(self.db.execute("SELECT count(*) FROM learners").fetchone()[0], 0)
        with transaction(self.db):
            for number, action in enumerate(["start", "show 1 1", "no 1 1"], 1):
                msg = incoming(OWNER, OWNER, number, action)
                accept(self.db, OWNER, msg)
                accept(self.db, OWNER, msg)
        self.assertEqual(self.db.execute("SELECT sum(attempts) FROM progress").fetchone()[0], 1)
        api = Mock()
        api.messages.send.side_effect = ConnectionError
        with self.assertRaises(ConnectionError):
            deliver(self.db, api, OWNER)
        first = api.messages.send.call_args
        api.messages.send.side_effect = None
        deliver(self.db, api, OWNER)
        self.assertEqual(api.messages.send.call_args_list[1], first)
        self.assertEqual(api.messages.send.call_count, 4)
        self.assertFalse(self.db.execute("SELECT id FROM outbox").fetchall())

    def test_vk_library_and_reconnect(self):
        import requests
        from vk_api.bot_longpoll import VkBotMessageEvent, VkBotEvent
        from trainer.vk import run
        raw = {"type": "message_new", "group_id": 15, "object": {"message": {
            "from_id": OWNER, "peer_id": OWNER, "conversation_message_id": 21, "text": "Начать тренировку"}}}
        poll = Mock(ts="101")
        poll.check.return_value = [VkBotEvent(dict(raw, type="unknown")), VkBotMessageEvent(raw)]
        poll_once(self.db, poll, OWNER, 15)
        self.assertEqual(self.db.execute("SELECT count(*) FROM sessions").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT ts FROM vk_cursor").fetchone()[0], "101")
        with patch.dict("os.environ", VK_GROUP_ID="15", VK_OWNER_ID=str(OWNER), VK_TOKEN="test"), \
                patch("trainer.vk.work"), patch("vk_api.VkApi"), patch("vk_api.bot_longpoll.VkBotLongPoll") as connect, \
                patch("trainer.vk.time.sleep") as sleep:
            poll.check.side_effect = [requests.ConnectionError, requests.Timeout, KeyboardInterrupt]
            connect.return_value = poll
            with self.assertRaises(KeyboardInterrupt):
                run(self.db)
            self.assertEqual([call.args for call in sleep.call_args_list], [(1,), (2,)])
            self.assertEqual(poll.ts, "101")
