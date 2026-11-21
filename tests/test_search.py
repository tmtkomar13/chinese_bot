import io
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from requests.exceptions import ReadTimeout

from trainer.search import PAGE_SIZE, pinyin_matches, pos_label, search_cards
from trainer.search_dialog import handle_search, search_is_open
from trainer.storage import database, import_vocabulary
from trainer.topics import REQUEST_TIMEOUT, RETRY_DELAY, print_topic_status, process_topic_batch, topic_progress


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/vocabulary.tsv"
LEARNER = 19


class DictionaryTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "trainer.sqlite3"
        shutil.copyfile(ROOT / "data/trainer.sqlite3", self.path)

    def test_combined_search_and_repeated_lesson(self):
        with database(self.path) as db:
            import_vocabulary(db, SOURCE)
            for lesson in (9, 23):
                rows, count, page, last = search_cards(
                    db, {"lesson": lesson, "pos": "verb", "hanzi": "看", "pinyin": "kan4",
                         "translation": "СМОТРЕТЬ"}, 200)
                self.assertEqual((count, page, last), (1, 0, 0))
                self.assertEqual(rows[0]["translation"], "смотреть")
                self.assertEqual(rows[0]["lessons"], "9, 23")
            _, count, _, _ = search_cards(db, {"hanzi": "看", "translation": "смотреть"}, 0)
            self.assertEqual(count, 1)
            rows, count, page, _ = search_cards(db, {}, 1)
            self.assertEqual(len(rows), PAGE_SIZE)
            self.assertEqual((count, page), (705, 1))
            self.assertEqual(search_cards(db, {"hanzi": "字典词条不存在"}, 1), ([], 0, 0, 0))
            self.assertTrue(search_cards(db, {"translation": "зелен"}, 0)[0])
            self.assertEqual(pos_label("гл./сущ."), "Глагол / Существительное")
            self.assertTrue(search_cards(db, {"pinyin": "xing4", "pos": "noun"}, 0)[0])
            self.assertTrue(search_cards(db, {"pinyin": "xing4", "pos": "verb"}, 0)[0])

    def test_pinyin_forms_and_explicit_tones(self):
        for form in ("nǐ hǎo", "ni3hao3", "ni3 hao3", "ni hao", "nǐhao3"):
            self.assertTrue(pinyin_matches("nǐ hǎo", form), form)
        for form in ("ni2hao3", "ni3hao4", "ni5hao", "ní hǎo"):
            self.assertFalse(pinyin_matches("nǐ hǎo", form), form)
        self.assertTrue(pinyin_matches("shéi / shuí", "shui2"))
        self.assertTrue(pinyin_matches("nǚ'ér", "nv3 er2"))
        self.assertTrue(pinyin_matches("nǚ'ér", "nu:3 er2"))
        self.assertTrue(pinyin_matches("xièxie", "xie4xie5"))
        self.assertFalse(pinyin_matches("xièxiè", "xie4xie0"))
        self.assertFalse(pinyin_matches("māo", "ma1"))
        self.assertTrue(pinyin_matches("méi (yǒu)", "mei2"))

    def test_search_draft_filters_and_page_persist(self):
        with database(self.path) as db:
            import_vocabulary(db, SOURCE)
            db.execute("INSERT OR IGNORE INTO learners(id,lesson) VALUES (?,1)", (LEARNER,))
            handle_search(db, LEARNER, "Поиск")
            handle_search(db, LEARNER, "", "search filter lesson")
        with database(self.path) as db:
            result = handle_search(db, LEARNER, "23")
            self.assertIn("Урок — 23", result["text"])
            handle_search(db, LEARNER, "", "search filter hanzi")
            result = handle_search(db, LEARNER, "看")
            self.assertIn("Уроки 9, 23", result["text"])
            self.assertIn("Иероглифы — 看", result["text"])
            result = handle_search(db, LEARNER, "", "search delete hanzi")
            self.assertIn("Урок — 23", result["text"])
            self.assertNotIn("Иероглифы — 看", result["text"])
            handle_search(db, LEARNER, "", "search page 1")
        with database(self.path) as db:
            self.assertIn("Страница 2", handle_search(db, LEARNER, "", "search")["text"])
            self.assertIsNone(handle_search(db, LEARNER, "Продолжить тренировку"))
            self.assertFalse(search_is_open(db, LEARNER))
            self.assertIn("Страница 2", handle_search(db, LEARNER, "", "search")["text"])
            self.assertIn("Фильтры не выбраны", handle_search(db, LEARNER, "", "search reset")["text"])

class TopicTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "trainer.sqlite3"
        self.source = self.path.with_suffix(".tsv")
        self.source.write_text(
            "key\tlesson\thanzi\tpinyin\tpos\ttranslation\n"
            "book\t1\t书\tshū\tсущ.\tкнига\ncar\t2\t车\tchē\tсущ.\tмашина, велосипед\n",
            encoding="utf-8",
        )

    def test_retry_after_restart_and_incremental_import(self):
        with database(self.path, create=True) as db:
            import_vocabulary(db, self.source)
            db.execute("INSERT INTO learners(id) VALUES (?)", (LEARNER,))
            handle_search(db, LEARNER, "", "search")
            handle_search(db, LEARNER, "", "search filter topic")
            result = handle_search(db, LEARNER, "", "search value topic school")
            self.assertIn("Темы заполнены для 0 из 2", result["text"])
            self.assertIn("Карточек по этим фильтрам нет", result["text"])
            with patch("trainer.topics.ai.ask", return_value={"book": ["forged"], "wrong-key": ["school"]}):
                self.assertEqual(process_topic_batch(db, now=100), "retry")
            self.assertEqual(topic_progress(db), (0, 2, 2))
            self.assertEqual(process_topic_batch(db, now=100 + RETRY_DELAY - 1), "idle")
        with database(self.path) as db:
            with patch("trainer.topics.ai.ask", return_value={"book": ["forged"], "car": ["transport"]}):
                self.assertEqual(process_topic_batch(db, now=100 + RETRY_DELAY), "retry")
            with patch("trainer.topics.ai.ask", return_value={"book": ["school", "leisure"], "car": ["transport"]}) as ai:
                self.assertEqual(process_topic_batch(db, now=100 + RETRY_DELAY * 3), "ready")
                self.assertEqual({card["id"] for card in ai.call_args.args[1]["cards"]}, {"book", "car"})
            self.assertEqual(topic_progress(db), (2, 2, 0))
            rows, count, _, _ = search_cards(
                db, {"lesson": 1, "topic": "school", "pos": "noun", "pinyin": "shu1",
                     "translation": "кни", "hanzi": "书"}, 0)
            self.assertEqual(count, 1)
            self.assertEqual(rows[0]["id"], "book")
            output = io.StringIO()
            with redirect_stdout(output):
                print_topic_status(db)
            for rubric in ("Школа", "Развлечения и спорт", "Транспорт"):
                self.assertIn(f"{rubric} — 1", output.getvalue())
            result = handle_search(db, LEARNER, "", "search")
            self.assertIn("书 · shū · книга", result["text"])
            self.assertNotIn("Тематическая выдача пока неполная", result["text"])
            import_vocabulary(db, self.source)
            self.assertEqual(topic_progress(db), (2, 2, 0))
            revised = self.source.read_text(encoding="utf-8").replace("книга", "пособие")
            self.source.write_text(revised + "fruit\t3\t水果\tshuǐguǒ\tсущ.\tфрукты\n", encoding="utf-8")
            import_vocabulary(db, self.source)
            self.assertEqual(topic_progress(db), (1, 3, 0))
            self.assertEqual(list(map(tuple, db.execute("SELECT * FROM card_topics"))), [("car", "transport")])
            self.assertEqual({row[0] for row in db.execute("SELECT card_key FROM topic_jobs")}, {"book", "fruit"})

    def test_inflight_topics_do_not_overwrite_edited_card(self):
        with database(self.path, create=True) as db:
            import_vocabulary(db, self.source)

            def change_card(*args, **kwargs):
                with database(self.path) as update:
                    update.execute("UPDATE cards SET translation='пособие' WHERE id='book'")
                return {"book": ["school", "leisure"], "car": ["transport"]}

            with patch("trainer.topics.ai.ask", side_effect=change_card):
                self.assertEqual(process_topic_batch(db), "ready")
            self.assertEqual(topic_progress(db), (1, 2, 0))
            self.assertEqual(list(map(tuple, db.execute("SELECT * FROM card_topics"))), [("car", "transport")])

    def test_timeout_requeues_one_batch_and_leaves_next_available(self):
        response = Mock(status_code=200)
        response.json.return_value = {"choices": [{"message": {"content": '{"car":["transport"]}'}}]}
        with database(self.path, create=True) as db:
            import_vocabulary(db, self.source)
            with patch("trainer.topics.BATCH_SIZE", 1), \
                    patch.dict(os.environ, YANDEX_FOLDER_ID="test", YANDEX_API_KEY="test"), \
                    patch("trainer.ai.requests.post", side_effect=[ReadTimeout(), response]) as post:
                self.assertEqual(process_topic_batch(db, now=100), "retry")
                self.assertEqual(post.call_count, 1)
                self.assertEqual(post.call_args.kwargs["timeout"], REQUEST_TIMEOUT)
                self.assertEqual(process_topic_batch(db, now=102), "ready")
                self.assertEqual(post.call_count, 2)
            job = db.execute("SELECT card_key,failures,retry_at FROM topic_jobs").fetchone()
            self.assertEqual(tuple(job), ("book", 1, 100 + RETRY_DELAY))
            self.assertEqual(topic_progress(db), (1, 2, 1))
