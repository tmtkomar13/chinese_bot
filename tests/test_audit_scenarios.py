import tempfile
import unittest
from contextlib import ExitStack, suppress
from pathlib import Path
from unittest.mock import Mock

from vk_api.exceptions import ApiError

from trainer.dialog import respond
from trainer.search_dialog import search_is_open
from trainer.storage import database, import_vocabulary, transaction
from trainer.vk import deliver
from trainer.worker import enqueue


VOCABULARY = Path(__file__).resolve().parents[1] / "data" / "vocabulary.tsv"
OWNER = 42


class AuditScenariosTest(unittest.TestCase):
    def setUp(self):
        resources = ExitStack()
        self.addCleanup(resources.close)
        directory = Path(resources.enter_context(tempfile.TemporaryDirectory()))
        self.db = resources.enter_context(database(directory / "trainer.sqlite3", create=True))

    def reply(self, text="", command=None):
        with transaction(self.db):
            return respond(self.db, OWNER, text, command)

    def test_permanent_vk_rejection_allows_next_reply(self):
        first, second = "Первый ответ", "Второй ответ"
        for message in (first, second):
            enqueue(self.db, OWNER, {"text": message, "buttons": []})

        def send(**params):
            if params["message"] == first:
                raise ApiError(None, "messages.send", params, False,
                               {"error_code": 100, "error_msg": "Invalid parameter"})
            return 1

        api = Mock()
        api.messages.send.side_effect = send
        for _ in range(2):
            # The listener retries delivery after a VK error.
            with suppress(ApiError):
                deliver(self.db, api, OWNER)

        attempts = [call.kwargs["message"] for call in api.messages.send.call_args_list]
        self.assertIn(second, attempts)

    def test_lowering_lesson_keeps_review_target_writable(self):
        import_vocabulary(self.db, VOCABULARY)
        self.reply(text="урок 2")
        self.reply(command="review lesson 2")
        self.reply(command="review at 2 manual")
        self.reply(command="review at 2 toggle B02-01")
        prompt = self.reply(command="review at 2 selected")
        self.reply(text="урок 1")
        answer = self.reply(command=prompt["buttons"][0][0][1])
        self.assertIn("同学", answer["text"])
        self.reply(command=answer["buttons"][0][0][1])  # Вспомнил

        job = self.db.execute("""
            SELECT c.hanzi, t.allowed FROM jobs j
            JOIN tasks t USING (session, number) JOIN cards c ON c.id=t.card
            WHERE j.kind='generate'
        """).fetchone()
        self.assertIsNotNone(job)
        self.assertEqual(job["hanzi"], "同学")
        self.assertEqual(set(job["hanzi"]) - set(job["allowed"]), set(),
                         "В задании на перевод запрещены знаки обязательного слова")

    def test_typed_menu_leaves_search_results(self):
        menu = self.reply(text="Меню")
        self.reply(text="Поиск")
        self.assertTrue(search_is_open(self.db, OWNER))

        reply = self.reply(text="Меню")
        self.assertEqual(
            (reply["text"].splitlines()[0], search_is_open(self.db, OWNER)),
            (menu["text"].splitlines()[0], False),
        )
        self.assertEqual(reply["buttons"], menu["buttons"])
