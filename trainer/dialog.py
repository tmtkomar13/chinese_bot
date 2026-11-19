"""Текст диалога и действия кнопок, независимые от формата сообщений ВК."""
import re

from .training import active_session, apply_action, current_task, start


MENU = [[("Начать тренировку", "start"), ("Открытые уроки", "lessons")],
        [("Точно знаю", "excluded"), ("Статистика", "stats")]]
BACK = [("Меню", "menu")]
LABELS = {label.casefold(): command for row in MENU for label, command in row}


def screen(text, buttons=None):
    return {"text": text, "buttons": MENU if buttons is None else buttons}


def exercise(db, session):
    if not session:
        return screen("Нет доступных карточек. Откройте урок или верните слово в повторение.")
    card = current_task(db, session)
    if not card:
        counts = dict(db.execute("SELECT grade,count(*) FROM tasks WHERE session=? GROUP BY grade",
                                 (session,)))
        return screen(f"Тренировка завершена.\nВспомнил — {counts.get('yes', 0)}"
                      f"\nНе вспомнил — {counts.get('no', 0)}\nТочно знаю — {counts.get('known', 0)}")
    token = f"{session} {card['number']}"
    title = f"Задание {card['number']} из {card['total']}"
    if not card["shown"]:
        return screen(f"{title}\n{card['translation']}", [[("Показать ответ", f"show {token}")], BACK])
    pos = card["pos"] or "не указана"
    return screen(f"{title}\n{card['hanzi']} · {card['pinyin']}\n{card['translation']}"
                  f"\nЧасть речи — {pos}\nУрок {card['lesson']}",
                  [[("Вспомнил", f"yes {token}"), ("Не вспомнил", f"no {token}")],
                   [("Точно знаю", f"known {token}")], BACK])


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


def respond(db, learner, text, command=None):
    db.execute("INSERT OR IGNORE INTO learners(id) VALUES (?)", (learner,))
    if command is None:
        text = text.strip().casefold()
        match = re.fullmatch(r"урок\s+(\d{1,5})", text)
        command = "lesson " + match[1] if match else LABELS.get(text, "menu")
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
        return screen(f"Попытки — {attempts}\nОшибки — {errors}\nТочно знаю — {known}")
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
