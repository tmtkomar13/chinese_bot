"""Экран поиска, редактирование фильтров и состояние диалога."""
import json
import re
import unicodedata

from .screens import BACK, screen
from .search import PARTS_OF_SPEECH, normalize_russian, parse_pinyin, pos_code, pos_label, search_cards
from .topics import TOPICS, topic_progress
from .training import active_session


FILTERS = {
    "lesson": ("Урок", "Введите номер урока, например 9."),
    "topic": ("Тема", "Выберите рубрику или введите её название."),
    "pos": ("Часть речи", "Выберите часть речи или введите её название."),
    "pinyin": ("Пиньинь", "Введите пиньинь, например nǐ hǎo, ni3 hao3 или ni hao."),
    "translation": ("Перевод", "Введите слово или часть русского перевода, например «зелёный»."),
    "hanzi": ("Иероглифы", "Введите китайское слово или отдельный иероглиф, например 看."),
}


def search_is_open(db, learner):
    state = db.execute("SELECT is_open FROM search_states WHERE learner=?", (learner,)).fetchone()
    return bool(state and state[0])


def close_search(db, learner):
    db.execute("UPDATE search_states SET is_open=0 WHERE learner=? AND is_open=1", (learner,))


def search_footer(db, learner):
    if active_session(db, learner):
        return [("Продолжить тренировку", "resume"), *BACK]
    return BACK


def filter_value(kind, text):
    if kind == "lesson":
        return int(text) if re.fullmatch("[1-9][0-9]{0,4}", text) else None
    if kind == "topic":
        value = normalize_russian(text)
        return next((key for key, label in TOPICS.items() if value in {key, normalize_russian(label)}), None)
    if kind == "pos":
        return pos_code(text)
    if kind == "pinyin":
        return text if parse_pinyin(text) else None
    if kind == "hanzi":
        return text if any(unicodedata.name(char, "").startswith("CJK UNIFIED IDEOGRAPH") for char in text) else None
    return text


def filter_summary(filters):
    labels = []
    for kind, (name, _) in FILTERS.items():
        if kind not in filters:
            continue
        value = filters[kind]
        if kind == "topic":
            value = TOPICS[value]
        elif kind == "pos":
            value = PARTS_OF_SPEECH[value]
        labels.append(f"{name} — {value}")
    return "Фильтры: " + ", ".join(labels) if labels else "Фильтры не выбраны."


def topic_status(db):
    ready, total, retrying = topic_progress(db)
    if ready == total:
        return ""
    status = f"Темы заполнены для {ready} из {total} карточек. Тематическая выдача пока неполная."
    if retrying:
        status += " Партии с ошибками ожидают повтора."
    return status


def result_screen(db, learner, filters, page):
    rows, count, page, last = search_cards(db, filters, page)
    db.execute("UPDATE search_states SET page=? WHERE learner=?", (page, learner))
    lines = [
        "Поиск по словарю",
        filter_summary(filters),
        "Выберите параметр и введите значение. Фильтры можно сочетать.",
    ]
    if "topic" in filters and (status := topic_status(db)):
        lines.append(status)
    if rows:
        lines += ["", f"Найдено карточек — {count}. Страница {page + 1} из {last + 1}."]
        for row in rows:
            lines += ["", f"{row['hanzi']} · {row['pinyin']} · {row['translation']}",
                      f"{pos_label(row['pos'])} · Уроки {row['lessons']}"]
    else:
        lines += ["", "Карточек по этим фильтрам нет. Измените или снимите фильтр."]
    available = list(FILTERS)
    buttons = [[(FILTERS[key][0], f"search filter {key}") for key in available[start:start + 3]]
               for start in range(0, len(available), 3)]
    buttons[0][1] = ("Список тем", "search filter topic")
    nav = []
    if page > 0:
        nav.append(("Назад", f"search page {page - 1}"))
    if page < last:
        nav.append(("Дальше", f"search page {page + 1}"))
    if nav:
        buttons.append(nav)
    if filters:
        buttons.append([("Снять фильтр", "search remove"), ("Сбросить всё", "search reset")])
    buttons.append(search_footer(db, learner))
    return screen("\n".join(lines), buttons)


def filter_screen(db, learner, filters, kind, error=None):
    lines = [filter_summary(filters), error or FILTERS[kind][1]]
    choices = {}
    if kind == "topic":
        choices = TOPICS
        if status := topic_status(db):
            lines.append(status)
    elif kind == "pos":
        choices = PARTS_OF_SPEECH
    options = list(choices.items())
    buttons = [[(label, f"search value {kind} {key}") for key, label in options[start:start + 2]]
               for start in range(0, len(options), 2)]
    buttons.append([("К результатам", "search results"), *search_footer(db, learner)])
    return screen("\n".join(lines), buttons)


def save_filter(db, learner, filters, kind, value):
    filters[kind] = value
    db.execute("UPDATE search_states SET filters=?,filter_kind=NULL,page=0 WHERE learner=?",
               (json.dumps(filters, ensure_ascii=False), learner))
    return result_screen(db, learner, filters, 0)


def handle_search(db, learner, text, command=None):
    state = db.execute("SELECT * FROM search_states WHERE learner=?", (learner,)).fetchone()
    opened = bool(state and state["is_open"])
    text = unicodedata.normalize("NFC", text.strip())
    if command == "search" or (not opened and command is None and text.casefold() == "поиск"):
        db.execute("""
            INSERT INTO search_states(learner,is_open) VALUES (?,1)
            ON CONFLICT(learner) DO UPDATE SET is_open=1
        """, (learner,))
        filters = json.loads(state["filters"]) if state else {}
        if state and state["filter_kind"]:
            return filter_screen(db, learner, filters, state["filter_kind"])
        return result_screen(db, learner, filters, state["page"] if state else 0)
    if not opened:
        return None
    if command in ("menu", "start", "resume") or (command is None and text.casefold() == "продолжить тренировку"):
        close_search(db, learner)
        return None
    filters = json.loads(state["filters"])
    current = state["filter_kind"]
    if command is None and current:
        value = filter_value(current, text) if 0 < len(text) <= 120 else None
        if value is not None:
            return save_filter(db, learner, filters, current, value)
        return filter_screen(db, learner, filters, current, "Проверьте значение. " + FILTERS[current][1])
    if command and (match := re.fullmatch(r"search filter (\w+)", command)) and match[1] in FILTERS:
        db.execute("UPDATE search_states SET filter_kind=? WHERE learner=?", (match[1], learner))
        return filter_screen(db, learner, filters, match[1])
    if command and (match := re.fullmatch(r"search value (topic|pos) (\w+)", command)):
        kind, value = match.groups()
        if current == kind and (parsed := filter_value(kind, value)):
            return save_filter(db, learner, filters, kind, parsed)
    if command == "search remove":
        db.execute("UPDATE search_states SET filter_kind=NULL WHERE learner=?", (learner,))
        buttons = [[(FILTERS[key][0], f"search delete {key}")] for key in FILTERS if key in filters]
        buttons.append([("К результатам", "search results"), *search_footer(db, learner)])
        return screen(filter_summary(filters) + "\nВыберите фильтр, который хотите снять.", buttons)
    if command == "search reset":
        filters.clear()
        db.execute("UPDATE search_states SET filters='{}',filter_kind=NULL,page=0 WHERE learner=?", (learner,))
        return result_screen(db, learner, filters, 0)
    if command and (match := re.fullmatch(r"search delete (\w+)", command)) and match[1] in filters:
        filters.pop(match[1])
        db.execute("UPDATE search_states SET filters=?,filter_kind=NULL,page=0 WHERE learner=?",
                   (json.dumps(filters, ensure_ascii=False), learner))
        return result_screen(db, learner, filters, 0)
    if command == "search results" or (command and (match := re.fullmatch(r"search page (\d{1,8})", command))):
        db.execute("UPDATE search_states SET filter_kind=NULL WHERE learner=?", (learner,))
        return result_screen(db, learner, filters, int(match[1]) if command.startswith("search page") else state["page"])
    if current:
        return filter_screen(db, learner, filters, current)
    return result_screen(db, learner, filters, state["page"])
