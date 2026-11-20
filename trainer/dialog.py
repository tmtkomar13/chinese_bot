"""Текст диалога и действия кнопок, независимые от формата сообщений ВК."""
import json
import os
import re
import time
from urllib.parse import urlencode, urlsplit

from .media import style_for
from .training import moscow_now

from .training import active_session, apply_action, current_task, start


MENU = [[("Начать тренировку", "start"), ("Открытые уроки", "lessons")],
        [("Точно знаю", "excluded"), ("Статистика", "stats")], [("Настройки", "settings")]]
BACK = [("Меню", "menu")]
LABELS = {label.casefold(): command for row in MENU for label, command in row}
MODES = {"off": "Без анимации", "gif": "GIF в сообщении", "page": "Интерактивная страница"}
SPEEDS = {"slow": "Медленно", "normal": "Обычно", "fast": "Быстро"}


def screen(text, buttons=None):
    return {"text": text, "buttons": MENU if buttons is None else buttons}


def exercise(db, session):
    if not session:
        return screen("Нет доступных карточек. Откройте урок или верните слово в повторение.")
    card = current_task(db, session)
    if not card:
        counts = dict(db.execute("SELECT grade,count(*) FROM tasks WHERE session=? GROUP BY grade",
                                 (session,)))
        info = db.execute("SELECT day,kind FROM sessions WHERE id=?", (session,)).fetchone()
        today = info["day"] == moscow_now().date().isoformat()
        next_button = ("Дополнительная тренировка", f"extra {session}") if today else ("Подборка на сегодня", "start")
        return screen(f"Тренировка завершена.\nВспомнил — {counts.get('yes', 0)}"
                      f"\nНе вспомнил — {counts.get('no', 0)}\nТочно знаю — {counts.get('known', 0)}", [[next_button], BACK])
    token = f"{session} {card['number']}"
    title = f"Задание {card['number']} из {card['total']}"
    phase = card["phase"]
    skip = ("Пропустить", f"skip {token}")
    if phase == "result":
        return screen(card["feedback"], [[("Далее", f"next {token}")], BACK])
    if phase == "failed":
        return screen("Не удалось проверить ответ. Ответ сохранён.", [[("Повторить проверку", f"retry {token}"), skip], BACK])
    if phase in ("generate", "check"):
        return screen("Готовлю предложение…" if phase == "generate" else "Проверяю перевод…", [[skip], BACK])
    if phase == "waiting":
        question = json.loads(card["question"])
        return screen((card["feedback"] or "Переведите на китайский.") + "\n" + question["ru"],
                      [[("Показать перевод", f"solution {token}"), skip], BACK])
    db.execute("""UPDATE tasks SET translate=coalesce(translate,(SELECT translations FROM learners
                  JOIN sessions ON learners.id=sessions.learner WHERE sessions.id=?)) WHERE session=? AND number=?""",
               (session, session, card["number"]))
    if not card["shown"]:
        return screen(f"{title}\n{card['translation']}", [[("Показать ответ", f"show {token}")], BACK])
    prefs = db.execute("SELECT l.* FROM learners l JOIN sessions s ON s.learner=l.id WHERE s.id=?",
                       (session,)).fetchone()
    pos = card["pos"] or "не указана"
    reply = screen(f"{title}\n{card['hanzi']} · {card['pinyin']}\n{card['translation']}"
                  f"\nЧасть речи — {pos}\nУрок {card['lesson']}",
                  [[("Вспомнил", f"yes {token}"), ("Не вспомнил", f"no {token}")],
                   [("Точно знаю", f"known {token}")], BACK])
    if prefs["animation"] == "off":
        return reply
    if prefs["animation"] == "page":
        base = os.getenv("STROKES_PAGE_URL", "").strip()
        url = urlsplit(base)
        if url.scheme in ("http", "https") and url.netloc and not url.query and not url.fragment:
            query = urlencode({"word": card["hanzi"], "speed": prefs["speed"]})
            reply["buttons"].insert(0, [("Порядок черт", base + "?" + query)])
        else:
            reply["text"] += "\nИнтерактивная страница временно недоступна."
        return reply
    style = style_for(prefs["speed"])
    media = db.execute("SELECT attachment FROM media WHERE word=? AND style=?", (card["hanzi"], style)).fetchone()
    db.execute("UPDATE tasks SET gif_request=? WHERE session=? AND number=?",
               (prefs["animation_revision"], session, card["number"]))
    if media:
        reply["attachment"] = media[0]
        reply["gif_revision"] = prefs["animation_revision"]
        reply["task_state"] = [session, card["number"], "", card["tries"]]
        reply["requires_ungraded"] = True
    else:
        retry = db.execute("SELECT retry_at FROM media_retry WHERE word=? AND style=?",
                           (card["hanzi"], style)).fetchone()
        if retry:
            db.execute("UPDATE media_retry SET retry_at=min(retry_at,?) WHERE word=? AND style=?",
                       (int(time.time()) + 5, card["hanzi"], style))
            reply["text"] += "\nПредыдущая попытка подготовить GIF не удалась. Повторю для этой карточки."
        else:
            reply["text"] += "\nГотовлю GIF. Можно оценить ответ сейчас."
    return reply


def excluded(db, learner, page):
    size = 4
    count = db.execute("SELECT count(*) FROM progress WHERE learner=? AND known=1",
                       (learner,)).fetchone()[0]
    if not count:
        return screen("Список «Точно знаю» пуст.")
    last = (count - 1) // size
    page = min(page, last)
    rows = db.execute("""
        SELECT c.*, p.revision FROM progress p JOIN cards c ON c.id=p.card
        WHERE p.learner=? AND p.known=1 ORDER BY c.lesson,c.id LIMIT ? OFFSET ?
    """, (learner, size, page * size))
    lines = [f"Точно знаю · страница {page + 1} из {last + 1}"]
    buttons = []
    for i, row in enumerate(rows, 1):
        lines.append(f"{i}. {row['hanzi']} · {row['pinyin']} · {row['translation']} (урок {row['lesson']})")
        buttons.append([(f"Вернуть {i}", f"restore {row['id']} {row['revision']} {page}")])
    nav = []
    if page:
        nav.append(("Назад", f"excluded {page - 1}"))
    if page < last:
        nav.append(("Дальше", f"excluded {page + 1}"))
    return screen("\n".join(lines), buttons + [nav + BACK])


def lessons(db, learner, new=None):
    maximum = db.execute("SELECT coalesce(max(lesson), 1) FROM cards").fetchone()[0]
    if new is not None and not 1 <= new <= maximum:
        return screen(f"Введите «урок N», где N от 1 до {maximum}.", [BACK])
    if new is not None:
        db.execute("UPDATE learners SET lesson=? WHERE id=?", (new, learner))
    level = db.execute("SELECT lesson FROM learners WHERE id=?", (learner,)).fetchone()[0]
    buttons = []
    if level > 1:
        buttons.append(("−1 урок", f"lesson {min(maximum, level - 1)}"))
    if level < maximum:
        buttons.append(("+1 урок", f"lesson {level + 1}"))
    return screen(f"Открыты уроки 1–{min(level, maximum)} из {maximum}.\n"
                  "Введите «урок N», чтобы выбрать последний открытый урок. "
                  "Выбор действует со следующей подборки.", [buttons + BACK])


def advance(db, card):
    key = (card["session"], card["number"])
    db.execute("DELETE FROM jobs WHERE session=? AND number=?", key)
    db.execute("UPDATE tasks SET phase='' WHERE session=? AND number=?", key)
    if card["number"] == card["total"]:
        db.execute("UPDATE sessions SET finished=1 WHERE id=?", (card["session"],))


def settings(db, learner):
    row = db.execute("SELECT * FROM learners WHERE id=?", (learner,)).fetchone()
    return screen(f"Слов в день — {row['quantity']}. Напоминание — {row['remind_time']} МСК.\n"
                  "Введите «слов 10» (от 1 до 100) или «время 19:00».",
                  [[("Отключить напоминания" if row["reminders"] else "Включить напоминания", f"reminders {1-row['reminders']}")],
                   [("Отключить переводы" if row["translations"] else "Включить переводы", f"translations {1-row['translations']}")],
                   [("Анимация", "animation")], BACK])


def respond(db, learner, text, command=None):
    db.execute("INSERT OR IGNORE INTO learners(id) VALUES (?)", (learner,))
    if command is None:
        original = text.strip()
        text = original.casefold()
        if text.startswith(("слов ", "время ")):
            if match := re.fullmatch(r"слов ([1-9][0-9]?|100)", text):
                db.execute("UPDATE learners SET quantity=? WHERE id=?", (int(match[1]), learner))
            elif match := re.fullmatch(r"время ((?:[01][0-9]|2[0-3]):[0-5][0-9])", text):
                db.execute("UPDATE learners SET remind_time=? WHERE id=?", (match[1], learner))
            return settings(db, learner)
        card = current_task(db, active_session(db, learner))
        if card and card["phase"] == "waiting" and text not in LABELS and not text.startswith("урок "):
            if not re.search("[㐀-鿿]", original):
                return exercise(db, card["session"])
            db.execute("UPDATE tasks SET phase='check', answer=?, tries=tries+1 WHERE session=? AND number=?",
                       (original, card["session"], card["number"]))
            db.execute("INSERT INTO translation_attempts VALUES (?,?,?,?,NULL,NULL)",
                       (card["session"], card["number"], card["tries"] + 1, original))
            db.execute("INSERT INTO jobs(session,number,kind) VALUES (?,?,'check')", (card["session"], card["number"]))
            return exercise(db, card["session"])
        match = re.fullmatch(r"урок\s+(\d{1,5})", text)
        command = "lesson " + match[1] if match else LABELS.get(text, "menu")
    if command == "settings":
        return settings(db, learner)
    if command == "animation" or re.fullmatch(r"(animation (off|gif|page)|speed (slow|normal|fast))", command):
        if " " in command:
            column, value = command.split()
            db.execute(f"UPDATE learners SET {column}=?, animation_revision=animation_revision+1 WHERE id=? AND {column}!=?",
                       (value, learner, value))
        row = db.execute("SELECT animation,speed FROM learners WHERE id=?", (learner,)).fetchone()
        return screen(f"Режим — {MODES[row['animation']]}. Скорость — {SPEEDS[row['speed']]}.",
                      [[(label, "animation " + key)] for key, label in MODES.items()] +
                      [[(label, "speed " + key) for key, label in SPEEDS.items()], [("Настройки", "settings")]])
    if match := re.fullmatch(r"(translations|reminders) ([01])", command):
        column, value = match.groups()
        db.execute(f"UPDATE learners SET {column}=? WHERE id=?", (int(value), learner))
        if column == "translations" and value == "0":
            card = current_task(db, active_session(db, learner))
            if card:
                db.execute("UPDATE tasks SET translate=0 WHERE session=? AND number=?", (card["session"], card["number"]))
                if card["phase"]:
                    advance(db, card)
        return settings(db, learner)
    if match := re.fullmatch(r"extra (\d{1,18})", command):
        session = db.execute("SELECT id FROM sessions WHERE learner=? AND finished=1 AND day=? ORDER BY id DESC LIMIT 1",
                             (learner, moscow_now().date().isoformat())).fetchone()
        return exercise(db, start(db, learner, extra=bool(session and session[0] == int(match[1]))))
    if match := re.fullmatch(r"(skip|solution|retry|next) (\d{1,18}) (\d{1,3})", command):
        action, sid, num = match.groups()
        current = active_session(db, learner)
        card = current_task(db, current)
        if card and current == int(sid) and card["number"] == int(num) and card["phase"]:
            key = (current, int(num))
            if action == "next" and card["phase"] == "result":
                advance(db, card)
            elif action == "skip" and card["phase"] != "result":
                db.execute("UPDATE tasks SET outcome='skipped' WHERE session=? AND number=?", key)
                advance(db, card)
            elif action == "solution" and card["phase"] == "waiting":
                q = json.loads(card["question"])
                db.execute("UPDATE tasks SET phase='result',outcome='solution',feedback=? WHERE session=? AND number=?",
                           ("\n".join(q[k] for k in ("zh","pinyin","explanation")), *key))
            elif action == "retry" and card["phase"] == "failed":
                db.execute("UPDATE tasks SET phase='check' WHERE session=? AND number=?", key)
                db.execute("INSERT INTO jobs(session,number,kind) VALUES (?,?,'check')", key)
        return exercise(db, current) if current else screen("Эта карточка уже закрыта.")
    if command == "start":
        return exercise(db, start(db, learner))
    card = re.fullmatch(r"(show|yes|no|known) (\d{1,18}) (\d{1,3})", command)
    if card:
        action, session, number = card.groups()
        if apply_action(db, learner, int(session), int(number), action):
            return exercise(db, int(session))
        current = active_session(db, learner)
        return exercise(db, current) if current else screen("Эта карточка уже закрыта.")
    if command == "stats":
        attempts, errors, known = db.execute("""
            SELECT coalesce(sum(attempts),0), coalesce(sum(errors),0), coalesce(sum(known),0)
            FROM progress WHERE learner=?
        """, (learner,)).fetchone()
        done, first, skipped = db.execute("""SELECT count(*) FILTER(WHERE outcome='correct'),
                count(*) FILTER(WHERE outcome='correct' AND tries=1),count(*) FILTER(WHERE outcome='skipped')
                FROM tasks JOIN sessions ON session=id WHERE learner=?""", (learner,)).fetchone()
        return screen(f"Попытки — {attempts}\nОшибки — {errors}\nТочно знаю — {known}\nПереводов выполнено — {done}\nС первой попытки — {first}\nПропущено — {skipped}")
    if command == "lessons":
        return lessons(db, learner)
    if match := re.fullmatch(r"lesson (\d{1,5})", command):
        return lessons(db, learner, int(match[1]))
    if match := re.fullmatch(r"excluded(?: (\d{1,8}))?", command):
        return excluded(db, learner, int(match[1] or 0))
    if match := re.fullmatch(r"restore (\S+) (\d{1,18}) (\d{1,8})", command):
        key, revision, page = match.groups()
        db.execute("""
            UPDATE progress SET known=0, revision=revision+1
            WHERE learner=? AND card=? AND revision=? AND known=1
        """, (learner, key, int(revision)))
        return excluded(db, learner, int(page))
    return screen("Выберите действие.")
