import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock

from requests.exceptions import ConnectionError
from vk_api.exceptions import ApiError

from trainer.storage import database
from trainer.vk import deliver, keyboard
from trainer.worker import enqueue


OWNER = 42
BUTTONS = [[("Меню", "menu")]]


def rejected():
    return ApiError(None, "messages.send", {}, False,
                    {"error_code": 100, "error_msg": "Invalid parameter"})


class VkDeliveryTest(unittest.TestCase):
    def setUp(self):
        resources = ExitStack()
        self.addCleanup(resources.close)
        self.path = Path(resources.enter_context(tempfile.TemporaryDirectory())) / "trainer.sqlite3"
        self.db = resources.enter_context(database(self.path, create=True))
        self.api = Mock()

    def enqueue(self, message):
        enqueue(self.db, OWNER, {"text": message, "buttons": BUTTONS})
        random_id = self.db.execute("SELECT last_insert_rowid()").fetchone()[0]
        return {"peer_id": OWNER, "random_id": random_id, "message": message}

    def attempts(self):
        return [call.kwargs for call in self.api.messages.send.call_args_list]

    def test_retries_same_message_without_keyboard(self):
        plain = self.enqueue("Первый ответ")
        self.api.messages.send.side_effect = [rejected(), 1]

        deliver(self.db, self.api, OWNER)

        self.assertEqual(self.attempts(), [{**plain, "keyboard": keyboard(BUTTONS)}, plain])
        self.assertIsNone(self.db.execute("SELECT id FROM outbox").fetchone())

    def test_two_permanent_rejections_do_not_block_next_reply(self):
        plain = self.enqueue("Первый ответ")
        next_reply = self.enqueue("Следующий ответ")
        self.api.messages.send.side_effect = [rejected(), rejected(), 1]

        with self.assertLogs(level="WARNING") as logs:
            deliver(self.db, self.api, OWNER)

        self.assertEqual(self.attempts(), [
            {**plain, "keyboard": keyboard(BUTTONS)}, plain,
            {**next_reply, "keyboard": keyboard(BUTTONS)},
        ])
        self.assertIn(f"outbox {plain['random_id']}: код 100", logs.output[0])
        self.assertIsNone(self.db.execute("SELECT id FROM outbox").fetchone())

    def test_restarts_without_keyboard_after_network_failure(self):
        plain = self.enqueue("Первый ответ")
        next_reply = self.enqueue("Следующий ответ")
        self.api.messages.send.side_effect = [rejected(), ConnectionError("offline"), 1, 2]

        with self.assertRaises(ConnectionError):
            deliver(self.db, self.api, OWNER)

        self.assertEqual(self.attempts(), [{**plain, "keyboard": keyboard(BUTTONS)}, plain])
        self.db.close()
        with database(self.path) as restarted:
            queued = restarted.execute("SELECT id FROM outbox ORDER BY id").fetchall()
            self.assertEqual([row["id"] for row in queued], [plain["random_id"], next_reply["random_id"]])
            deliver(restarted, self.api, OWNER)
            self.assertIsNone(restarted.execute("SELECT id FROM outbox").fetchone())

        self.assertEqual(self.attempts()[2:], [plain, {**next_reply, "keyboard": keyboard(BUTTONS)}])
