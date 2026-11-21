import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from PIL import Image, ImageSequence

from trainer import media
from trainer.storage import database
from trainer.vk import deliver
from trainer.worker import enqueue


class GifTest(unittest.TestCase):
    def test_delivery_discards_obsolete_attachments_at_all_speeds(self):
        with tempfile.TemporaryDirectory() as temp, database(Path(temp) / "db", create=True) as db:
            db.execute("INSERT INTO learners(id) VALUES (1)")
            db.execute("INSERT INTO cards(id,hanzi,pinyin,translation,lesson) VALUES ('a','一','yī','один',1)")
            db.execute("INSERT INTO sessions(id,learner) VALUES (1,1)")
            db.execute("INSERT INTO tasks(session,number,card,shown,gif_request) VALUES (1,1,'a',1,0)")

            for index, speed in enumerate(("slow", "normal", "fast"), start=1):
                with self.subTest(speed=speed):
                    old = f"doc1_{index}"
                    current = f"doc2_{index}"
                    db.execute("UPDATE learners SET speed=? WHERE id=1", (speed,))
                    db.executemany("INSERT INTO media VALUES ('一',?,?)",
                                   [("ink-144-transparent-v4-" + speed, old), (media.style_for(speed), current)])
                    reply = {"text": "Порядок черт", "gif_revision": 0, "task_state": [1, 1, "", 0],
                             "requires_ungraded": True, "gif_only": True}
                    enqueue(db, 1, {**reply, "attachment": old})
                    enqueue(db, 1, {"text": "Карточка", "buttons": [], "attachment": old})
                    enqueue(db, 1, {**reply, "attachment": current})

                    api = Mock()
                    deliver(db, api, 1)
                    self.assertEqual(api.messages.send.call_count, 2)
                    card, gif = api.messages.send.call_args_list
                    self.assertIn("keyboard", card.kwargs)
                    self.assertNotIn("attachment", card.kwargs)
                    self.assertEqual(gif.kwargs["attachment"], current)
                    self.assertEqual(db.execute("SELECT count(*) FROM outbox").fetchone()[0], 0)

    def test_opaque_animation_and_versioned_cache_at_all_speeds(self):
        if not os.environ.get("CHROMIUM_PATH"):
            self.skipTest("Задайте CHROMIUM_PATH для проверки рендера GIF")

        word = "一"
        elapsed = {}
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp)
            for speed in ("slow", "normal", "fast"):
                with self.subTest(speed=speed):
                    old_style = "ink-144-transparent-v4-" + speed
                    old_hash = hashlib.sha256((old_style + word).encode()).hexdigest()
                    old_gif = cache / (old_hash + ".gif")
                    old_gif.write_bytes(b"old cache")

                    gif = media.render(word, cache, speed)
                    self.assertNotEqual(gif, old_gif)
                    self.assertEqual(media.render(word, cache, speed), gif)
                    with Image.open(gif) as image:
                        self.assertEqual(image.size, (144, 144))
                        self.assertEqual(image.info["background"], 255)
                        darkest = []
                        elapsed[speed] = 0
                        for frame in ImageSequence.Iterator(image):
                            self.assertNotIn("transparency", frame.info)
                            self.assertEqual(frame.disposal_method, 1)
                            elapsed[speed] += frame.info["duration"]
                            visible = frame.convert("RGBA")
                            self.assertEqual(visible.getchannel("A").getextrema(), (255, 255))
                            for pixel in ((0, 0), (143, 0), (0, 143), (143, 143)):
                                self.assertEqual(visible.getpixel(pixel), (255, 255, 255, 255))
                            darkest.append(visible.getchannel("R").getextrema()[0])
                        self.assertGreater(len(darkest), 2)
                        self.assertEqual(darkest[0], 221)
                        self.assertEqual(darkest[-1], 0)
            if len(elapsed) == 3:
                self.assertGreater(elapsed["slow"], elapsed["normal"])
                self.assertGreater(elapsed["normal"], elapsed["fast"])


if __name__ == "__main__":
    unittest.main()
