import json
import logging
import os
import threading
import time
from pathlib import Path

from . import ai, media
from .dialog import exercise, screen
from .storage import database, transaction
from .training import active_session, current_task, moscow_now


def enqueue(db, owner, reply):
    db.execute("INSERT INTO outbox(peer,reply) VALUES (?,?)", (owner, json.dumps(reply, ensure_ascii=False)))


def remind(db, owner, now=None):
    now = now or moscow_now()
    day = now.date().isoformat()
    with transaction(db):
        db.execute("INSERT OR IGNORE INTO learners(id) VALUES (?)", (owner,))
        due = db.execute("SELECT reminders AND remind_time<=? FROM learners WHERE id=?",
                         (now.strftime("%H:%M"), owner)).fetchone()[0]
        daily = db.execute("SELECT finished FROM sessions WHERE learner=? AND kind='daily' AND day=?",
                           (owner, day)).fetchone()
        if not due or (daily and daily[0]):
            return
        if db.execute("INSERT OR IGNORE INTO reminders_sent VALUES (?,?)", (owner, day)).rowcount:
            active = db.execute("SELECT id FROM sessions WHERE learner=? AND finished=0", (owner,)).fetchone()
            verb = "Продолжить" if active else "Начать"
            reply = screen("Время заниматься китайским.", [[(verb + " тренировку", "start")]])
            reply["reminder_day"] = day
            enqueue(db, owner, reply)


def process(db, owner):
    job = db.execute("""SELECT j.id AS job_id, j.kind, t.*, c.hanzi,c.translation
            FROM jobs j JOIN tasks t USING(session,number) JOIN cards c ON t.card=c.id
            JOIN sessions s ON s.id=t.session WHERE s.learner=?
            ORDER BY j.id LIMIT 1""", (owner,)).fetchone()
    if not job:
        return False
    kind = job["kind"]
    key = (job["session"], job["number"])
    try:
        if kind == "generate":
            question = ai.generate(job)
        else:
            result = ai.check(job)
        failed = False
    except Exception as error:
        logging.warning("Задание %s: %s", kind, type(error).__name__)
        failed = True
    with transaction(db):
        if not db.execute("DELETE FROM jobs WHERE id=?", (job["job_id"],)).rowcount:
            return True
        if kind == "generate" and failed:
            fields = ("result", "Сейчас нет подходящего задания.", None, None)
        elif kind == "generate":
            fields = ("waiting", None, json.dumps(question, ensure_ascii=False), None)
        elif failed:
            fields = ("failed", None, job["question"], None)
        elif result["correct"]:
            fields = ("result", "Верно!", job["question"], "correct")
        else:
            fields = ("waiting", result["hint"] or "Попробуйте ещё раз.", job["question"], None)
        changed = db.execute("""UPDATE tasks SET phase=?,feedback=?,question=?,outcome=?
                              WHERE session=? AND number=? AND phase=?""", (*fields, *key, kind))
        if changed.rowcount:
            if kind == "check" and not failed:
                db.execute("UPDATE translation_attempts SET correct=?,hint=? WHERE session=? AND number=? AND attempt=?",
                           (result["correct"], result["hint"], *key, job["tries"]))
            reply = exercise(db, job["session"])
            reply["task_state"] = [*key, fields[0], job["tries"]]
            enqueue(db, owner, reply)
    return True


def prepare_next_gif(db, client, owner, cache):
    # Include both complete spellings and each Han character in a compound.
    item = db.execute("""
        WITH RECURSIVE letters(word,lesson,pos) AS (
            SELECT hanzi,lesson,1 FROM cards
            UNION ALL SELECT word,lesson,pos+1 FROM letters WHERE pos<length(word)
        ), vocabulary(word,lesson,part) AS (
            SELECT hanzi,lesson,0 FROM cards UNION ALL
            SELECT substr(word,pos,1),lesson,1 FROM letters WHERE substr(word,pos,1) BETWEEN '㐀' AND '鿿'
        ), upcoming AS (
            SELECT c.hanzi,min(t.number) AS priority FROM tasks t
            JOIN sessions s ON s.id=t.session JOIN cards c ON c.id=t.card
            WHERE s.learner=? AND s.finished=0 AND t.grade IS NULL GROUP BY c.hanzi
        )
        SELECT v.word,coalesce(r.failures,0) AS failures FROM vocabulary v
        LEFT JOIN upcoming u ON u.hanzi=v.word
        LEFT JOIN media m ON m.word=v.word AND m.style=?
        LEFT JOIN media_retry r ON r.word=v.word AND r.style=?
        WHERE m.word IS NULL AND coalesce(r.retry_at,0)<=?
        GROUP BY v.word ORDER BY u.priority IS NULL,u.priority,min(v.part),min(v.lesson),v.word LIMIT 1
    """, (owner, media.STYLE, media.STYLE, int(time.time()))).fetchone()
    if not item:
        return False
    word = item["word"]
    try:
        path = media.render(word, cache)
        attachment = media.upload(client, owner, path)
    except Exception as error:
        failures = item["failures"] + 1
        delay = min(3600, 30 * 2 ** min(failures - 1, 7))
        db.execute("INSERT OR REPLACE INTO media_retry VALUES (?,?,?,?)",
                   (word, media.STYLE, failures, int(time.time()) + delay))
        logging.warning("GIF %s: %s, повтор через %d с", word, type(error).__name__, delay)
        return True
    with transaction(db):
        db.execute("INSERT OR REPLACE INTO media VALUES (?,?,?)", (word, media.STYLE, attachment))
        db.execute("DELETE FROM media_retry WHERE word=? AND style=?", (word, media.STYLE))
        session = active_session(db, owner)
        card = current_task(db, session) if session else None
        if card and card["shown"] and card["grade"] is None and card["hanzi"] == word:
            reply = exercise(db, session)
            reply["task_state"] = [session, card["number"], "", card["tries"]]
            reply["requires_ungraded"] = True
            enqueue(db, owner, reply)
    return True


def warm_media(path, owner, stop):
    import vk_api
    client = vk_api.VkApi(token=os.environ["VK_TOKEN"])
    cache = Path(path).parent / "gif-cache"
    with database(path) as db:
        while not stop.is_set():
            try:
                busy = prepare_next_gif(db, client, owner, cache)
            except Exception as error:
                logging.warning("Подготовка GIF: %s", type(error).__name__)
                busy = False
            stop.wait(2 if busy else 5)


def work(path, owner, stop):
    thread = threading.Thread(target=warm_media, args=(path, owner, stop), daemon=True)
    thread.start()
    with database(path) as db:
        while not stop.is_set():
            try:
                if process(db, owner):
                    continue
            except Exception as error:
                logging.warning("Фоновая задача: %s", type(error).__name__)
            stop.wait(1)

