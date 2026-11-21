"""Кнопки и списки для повторения открытых уроков."""
import re

from . import review
from .screens import BACK, offer_resume, screen
from .training import active_session

LESSONS_PER_PAGE = 8
CARDS_PER_PAGE = 5


def lesson_picker(db, learner, page=0):
    lessons = review.open_lessons(db, learner)
    if not lessons:
        return screen("Нет открытых уроков с карточками.", [BACK])
    last = (len(lessons) - 1) // LESSONS_PER_PAGE
    page = min(page, last)
    visible = lessons[page * LESSONS_PER_PAGE:(page + 1) * LESSONS_PER_PAGE]
    buttons = [(f"Урок {number}", f"review lesson {number}") for number in visible]
    rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    rows += navigation("review page", page, last)
    if (state := review.get_state(db, learner)) and state["lesson"] in lessons and state["manual"]:
        rows.append([("Продолжить выбор карточек", f"review lesson {state['lesson']}")])
    return screen(f"Выберите открытый урок · страница {page + 1} из {last + 1}", rows + [BACK])


def navigation(command, page, last):
    row = []
    if page:
        row.append(("Назад", f"{command} {page - 1}"))
    if page < last:
        row.append(("Дальше", f"{command} {page + 1}"))
    return [row] if row else []


def options(db, learner, state, notice=None):
    only_errors = bool(state["only_errors"])
    lesson = state["lesson"]
    command = f"review at {lesson}"
    toggle = f"Только мои ошибки · {'да' if only_errors else 'нет'}"
    filter_row = [(toggle, f"{command} errors")]
    back = [("Другой урок", "review page 0"), *BACK]
    cards = review.cards_for_review(db, learner, state)
    if not cards:
        text = ("В этом уроке нет карточек с последней оценкой «Не вспомнил». Снимите фильтр."
                if only_errors else "В этом уроке нет карточек.")
        return screen(f"Урок {lesson}\n{text}", [filter_row, back])
    if not state["manual"]:
        return screen(f"Повторение урока {lesson}\nКарточек доступно — {len(cards)}.",
                      [[("По порядку", f"{command} ordered")],
                       [("Случайная подборка", f"{command} random")],
                       [("Выбрать вручную", f"{command} manual")], filter_row, back])
    last = (len(cards) - 1) // CARDS_PER_PAGE
    page = min(state["page"], last)
    count = sum(row["selected"] for row in cards)
    lines = [f"Урок {lesson} · выбрано {count} · страница {page + 1} из {last + 1}"]
    if notice:
        lines.append(notice)
    rows = []
    visible = cards[page * CARDS_PER_PAGE:(page + 1) * CARDS_PER_PAGE]
    for row in visible:
        mark = "✓" if row["selected"] else "○"
        lines.append(f"{mark} {row['hanzi']} · {row['pinyin']} · {row['translation']}")
        rows.append([(f"{mark} {row['hanzi']}"[:40], f"{command} toggle {row['id']}")])
    rows += navigation(f"{command} page", page, last)
    rows.append(filter_row)
    rows.append([("Начать с выбранными", f"{command} selected"), ("Режимы", f"{command} modes")])
    return screen("\n".join(lines), rows + [back])


def handle_review(db, learner, command, exercise):
    if command != "review" and not command.startswith("review "):
        return None
    if active_session(db, learner):
        return offer_resume()
    if command == "review":
        state = review.get_state(db, learner)
        if state and state["manual"] and state["lesson"] in review.open_lessons(db, learner):
            return options(db, learner, state)
        return lesson_picker(db, learner)
    if match := re.fullmatch(r"review page (\d{1,9})", command):
        return lesson_picker(db, learner, int(match[1]))
    if match := re.fullmatch(r"review lesson (\d{1,9})", command):
        if review.choose_lesson(db, learner, int(match[1])):
            return options(db, learner, review.get_state(db, learner))
        return lesson_picker(db, learner)
    match = re.fullmatch(r"review at (\d{1,9}) (ordered|random|manual|selected|modes|errors|page \d{1,9}|toggle .+)", command)
    state = review.get_state(db, learner)
    if not match or not state or state["lesson"] != int(match[1]) or state["lesson"] not in review.open_lessons(db, learner):
        return lesson_picker(db, learner)
    action = match[2]
    if action in ("ordered", "random", "selected"):
        session = review.start_review(db, learner, state, "manual" if action == "selected" else action)
        if session:
            return exercise(db, session)
        return options(db, learner, state, "Отметьте хотя бы одну карточку.")
    if action == "errors":
        db.execute("UPDATE review_state SET only_errors=1-only_errors,page=0 WHERE learner=?", (learner,))
    elif action in ("manual", "modes"):
        db.execute("UPDATE review_state SET manual=? WHERE learner=?", (int(action == "manual"), learner))
    elif action.startswith("page "):
        db.execute("UPDATE review_state SET page=? WHERE learner=?", (int(action[5:]), learner))
    else:
        review.toggle_card(db, learner, state, action[7:])
    return options(db, learner, review.get_state(db, learner))
