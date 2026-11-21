"""Рубрики словаря и фоновая разметка через общий сервис ИИ."""
import logging
import os
import time

from . import ai
from .storage import database, transaction


TOPICS = {
    "school": "Школа",
    "work": "Работа",
    "people": "Люди и семья",
    "home": "Дом и быт",
    "food": "Еда и напитки",
    "shopping": "Покупки и деньги",
    "transport": "Транспорт",
    "places": "Места и путешествия",
    "time": "Время и числа",
    "nature": "Природа и погода",
    "health": "Здоровье и тело",
    "clothes": "Одежда",
    "leisure": "Развлечения и спорт",
    "technology": "Техника и связь",
    "communication": "Общение",
    "description": "Описание и эмоции",
    "grammar": "Служебные слова и конструкции",
    "other": "Прочее",
}
BATCH_SIZE = 6
REQUEST_TIMEOUT = (10, 75)
RETRY_DELAY = 30
INSTRUCTION = (
    'Разметь каждую карточку по её русскому значению. Используй от одной до трёх рубрик '
    'из словаря topics, предпочитай самые уместные и выбирай other, только если ни одна '
    'другая рубрика не подходит. Книга получает school и leisure, машина в значении '
    'автомобиля получает transport. Объект результата должен содержать ровно ключи '
    'всех переданных карточек. Значение каждого поля — массив разных идентификаторов рубрик '
    'без пояснений. Пример ответа для одной карточки: {"id-карточки":["school","leisure"]}.'
)


def topic_progress(db):
    total, pending, retrying = db.execute("""
        SELECT (SELECT count(*) FROM cards), count(*), count(*) FILTER(WHERE failures > 0)
        FROM topic_jobs
    """).fetchone()
    return total - pending, total, retrying


def print_topic_status(db):
    ready, total, retrying = topic_progress(db)
    print(f"Размечено {ready}/{total} карточек, после ошибки ожидают повтора {retrying}.")
    counts = dict(db.execute("SELECT topic,count(*) FROM card_topics GROUP BY topic"))
    for topic, label in TOPICS.items():
        print(f"{label} — {counts.get(topic, 0)}")


def classify(batch):
    expected = {card["id"] for card in batch}
    cards = [{key: card[key] for key in ("id", "hanzi", "pos", "translation")} for card in batch]
    values = ai.ask(INSTRUCTION, {"cards": cards, "topics": TOPICS},
                    timeout=REQUEST_TIMEOUT, attempts=1)
    if not isinstance(values, dict) or values.keys() != expected:
        raise ValueError("Ответ ИИ содержит неожиданные ключи карточек")
    for topics in values.values():
        if (not isinstance(topics, list) or not 1 <= len(topics) <= 3
                or any(not isinstance(topic, str) or topic not in TOPICS for topic in topics)
                or len(set(topics)) != len(topics)):
            raise ValueError("Ответ ИИ содержит недопустимые рубрики")
    return values


def process_topic_batch(db, now=None):
    pending = db.execute("""
        SELECT c.id,c.hanzi,c.pinyin,c.pos,c.translation,j.failures,j.retry_at
        FROM topic_jobs j JOIN cards c ON c.id=j.card_key
        WHERE j.retry_at<=? ORDER BY c.lesson,c.id LIMIT ?
    """, (int(time.time()) if now is None else now, BATCH_SIZE)).fetchall()
    if not pending:
        return "idle"
    batch = [{key: row[key] for key in ("id", "hanzi", "pinyin", "pos", "translation")} for row in pending]
    try:
        values = classify(batch)
    except Exception as error:
        logging.warning("Тематическая разметка: %s. Партия будет повторена.", type(error).__name__)
        values = None
    finished = int(time.time()) if now is None else now
    with transaction(db):
        for card, job in zip(batch, pending):
            current = db.execute("""
                SELECT c.hanzi,c.pinyin,c.pos,c.translation,j.failures,j.retry_at
                FROM topic_jobs j JOIN cards c ON c.id=j.card_key WHERE c.id=?
            """, (card["id"],)).fetchone()
            if not current or tuple(current) != tuple(job)[1:]:
                # Импорт мог изменить карточку во время запроса к ИИ.
                continue
            if values is None:
                delay = min(3600, RETRY_DELAY * 2 ** min(job["failures"], 7))
                db.execute("UPDATE topic_jobs SET failures=failures+1,retry_at=? WHERE card_key=?",
                           (finished + delay, card["id"]))
                continue
            db.executemany("INSERT INTO card_topics(card_key,topic) VALUES (?,?)",
                           ((card["id"], topic) for topic in values[card["id"]]))
            db.execute("DELETE FROM topic_jobs WHERE card_key=?", (card["id"],))
    return "retry" if values is None else "ready"


def warm_topics(path, stop):
    if not (os.getenv("YANDEX_FOLDER_ID") and os.getenv("YANDEX_API_KEY")):
        logging.info("Тематическая разметка ожидает настройки ИИ.")
        return
    with database(path) as db:
        while not stop.is_set():
            try:
                result = process_topic_batch(db)
            except Exception as error:
                logging.warning("Фоновая разметка: %s", type(error).__name__)
                stop.wait(RETRY_DELAY)
                continue
            # Срок повтора хранится в SQLite. Остальные партии уже можно обработать.
            stop.wait(10 if result == "idle" else 2)
