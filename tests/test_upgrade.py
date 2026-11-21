import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from trainer.dialog import respond
from trainer.storage import database, transaction


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
