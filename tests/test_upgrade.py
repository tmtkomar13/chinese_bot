import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from trainer.dialog import respond
from trainer.storage import database, transaction
from trainer.worker import process


class UpgradeTest(unittest.TestCase):
    def test_active_session_pending_message_and_backup(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trainer.sqlite3"
            with closing(sqlite3.connect(path)) as old:
                old.executescript((root / "trainer/migrations/001.sql").read_text(encoding="utf-8"))
                old.executescript("""
                    PRAGMA user_version=1;
                    INSERT INTO learners VALUES (12,2);
                    INSERT INTO cards VALUES ('a','人','rén',NULL,'человек',2);
                    INSERT INTO progress VALUES (12,'a',3,2,'no',0,3);
                    INSERT INTO sessions VALUES (17,12,0);
                    INSERT INTO tasks VALUES (17,1,'a',1,NULL);
                    INSERT INTO vk_messages VALUES (21,12,1,'{"text":"Ответ","buttons":[]}');
                    INSERT INTO vk_messages VALUES (23,12,2,NULL);
                    INSERT INTO vk_cursor VALUES (100,'55');
                """)
            with database(path) as db:
                self.assertEqual(tuple(db.execute("SELECT id,peer FROM outbox").fetchone()), (21,12))
                message = db.execute("INSERT INTO outbox(peer,reply) VALUES (12,'{}')")
                self.assertGreater(message.lastrowid, 23)
                self.assertEqual(db.execute("SELECT ts FROM vk_cursor").fetchone()[0], "55")
                with transaction(db):
                    self.assertIn("rén", respond(db, 12, "", "resume")["text"])
                    self.assertIn("Готовлю GIF", respond(db, 12, "", "resume")["text"])
                    self.assertIn("Готовлю предложение", respond(db, 12, "", "yes 17 1")["text"])
                    self.assertIn("Подборка на сегодня", str(respond(db, 12, "", "skip 17 1")["buttons"]))
                    respond(db, 12, "", "start")
                self.assertEqual(list(map(tuple, db.execute("SELECT kind,finished FROM sessions ORDER BY id"))),
                                 [("legacy",1), ("daily",0)])
                self.assertEqual(tuple(db.execute("SELECT attempts,errors FROM progress").fetchone()), (4,2))
                self.assertEqual(db.execute("SELECT lesson FROM learners").fetchone()[0], 2)
            backup, = path.parent.glob("*.bak")
            with closing(sqlite3.connect(backup)) as saved:
                self.assertEqual(saved.execute("PRAGMA user_version").fetchone()[0], 1)
                self.assertEqual(tuple(saved.execute("SELECT shown,grade FROM tasks").fetchone()), (1,None))
                self.assertEqual(saved.execute("SELECT count(*) FROM vk_messages WHERE reply IS NOT NULL").fetchone()[0], 1)

    def test_v8_copy_recovers_lesson_limits_and_queued_generation(self):
        root = Path(__file__).resolve().parents[1]
        question = '{"ru":"Кто там?","zh":"谁在那儿？","difficulty":"easy"}'
        with tempfile.TemporaryDirectory() as directory:
            old_path = Path(directory) / "v8.sqlite3"
            shutil.copyfile(root / "data/trainer.sqlite3", old_path)
            with closing(sqlite3.connect(old_path)) as old:
                for number in range(2, 9):
                    migration = root / "trainer/migrations" / f"{number:03}.sql"
                    old.executescript(migration.read_text(encoding="utf-8"))
                old.executescript("""
                    PRAGMA user_version=8;
                    INSERT INTO learners(id,lesson) VALUES (101,1),(102,1),(103,13);
                    INSERT INTO cards(id,hanzi,pinyin,translation,lesson)
                    VALUES ('queued','龘靐靐','dá bìng bìng','цель задания',4),
                           ('ready','麤','cū','готовое задание',2);
                    INSERT INTO sessions(id,learner,kind,lesson)
                    VALUES (101,101,'daily',NULL),(102,102,'review',6),(103,103,'review',2);
                    INSERT INTO tasks(session,number,card,shown,grade,phase,allowed)
                    VALUES (101,1,'queued',1,'yes','generate','人龘'),
                           (102,1,'ready',1,'yes','waiting','人'),
                           (103,1,'ready',0,NULL,'',NULL);
                    INSERT INTO jobs(session,number,kind) VALUES (101,1,'generate');
                """)
                old.execute("UPDATE tasks SET question=? WHERE session=102", (question,))
                old.commit()
                path = old_path.with_name("upgrade.sqlite3")
                with closing(sqlite3.connect(path)) as copy:
                    old.backup(copy)

            with database(path) as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 9)
                self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
                limits = db.execute("SELECT lesson_limit FROM sessions ORDER BY id")
                self.assertEqual([row[0] for row in limits], [4, 6, 13])
                self.assertEqual(tuple(db.execute("SELECT allowed,question FROM tasks WHERE session=102").fetchone()),
                                 ("人", question))
                with patch("trainer.worker.ai.generate", return_value={"ru": "Готовый перевод"}) as generate:
                    self.assertTrue(process(db, 101))
                self.assertEqual(generate.call_args.args[0]["allowed"], "人龘靐")
                with transaction(db):
                    self.assertIn("Готовый перевод", respond(db, 101, "", "resume")["text"])
                    self.assertIn("Кто там?", respond(db, 102, "", "resume")["text"])
            with closing(sqlite3.connect(old_path)) as old:
                self.assertEqual(old.execute("PRAGMA user_version").fetchone()[0], 8)
                self.assertEqual(old.execute("SELECT allowed FROM tasks WHERE session=101").fetchone()[0], "人龘")
