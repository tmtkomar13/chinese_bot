"""Поиск по словарю и нормализация его полей."""
import re
import unicodedata
from functools import lru_cache


PARTS_OF_SPEECH = {
    "noun": "Существительное",
    "verb": "Глагол",
    "adjective": "Прилагательное",
    "adverb": "Наречие",
    "pronoun": "Местоимение",
    "classifier": "Счётное слово",
    "particle": "Частица",
    "numeral": "Числительное",
    "conjunction": "Союз",
    "interjection": "Междометие",
    "preposition": "Предлог",
    "suffix": "Суффикс",
    "modal": "Модальный глагол",
    "unspecified": "Не указана",
}
POS_ABBREVIATIONS = {
    "сущ": "noun", "гл": "verb", "прил": "adjective", "нареч": "adverb",
    "мест": "pronoun", "сч сл": "classifier", "част": "particle", "числ": "numeral",
    "союз": "conjunction", "межд": "interjection", "предл": "preposition",
    "суф": "suffix", "мод гл": "modal",
}
TONES = {"\u0304": 1, "\u0301": 2, "\u030c": 3, "\u0300": 4}
SEPARATORS = " '-’‘ʼ().…"
NUCLEUS = re.compile(r"([aeiouü]+)(?:ng|n|r)?$")
PAGE_SIZE = 5


def normalize_russian(value):
    return unicodedata.normalize("NFC", value).casefold().replace("ё", "е")


def pos_code(value):
    value = normalize_russian(value).replace(".", " ").strip()
    value = " ".join(value.split())
    for key, label in PARTS_OF_SPEECH.items():
        if value in {key, normalize_russian(label)}:
            return key
    return POS_ABBREVIATIONS.get(value)


@lru_cache(maxsize=128)
def parse_pos(value):
    if not value:
        return (("unspecified", PARTS_OF_SPEECH["unspecified"]),)
    fields = []
    for part in value.split("/"):
        name, delimiter, qualification = part.strip().partition("(")
        code = pos_code(name)
        label = PARTS_OF_SPEECH.get(code, name.strip())
        if delimiter:
            label += " (" + qualification.strip()
        fields.append((code, label))
    return tuple(fields)


def pos_label(value):
    return " / ".join(label for _, label in parse_pos(value))


def pos_matches(value, expected):
    return any(code == expected or (code == "modal" and expected == "verb")
               for code, _ in parse_pos(value))


def tone_index(syllable):
    match = NUCLEUS.search(syllable)
    if not match:
        return 0 if syllable in {"m", "n", "ng"} else None
    vowels = match[1]
    for group in ("a", "e", "ou"):
        if group in vowels:
            return match.start() + vowels.index(group)
    return match.end(1) - 1


@lru_cache(maxsize=4096)
def parse_pinyin(value):
    expanded = unicodedata.normalize("NFD", value.casefold().replace("u:", "ü").replace("v", "ü"))
    letters = []
    tones = {}
    start = 0
    for char in expanded:
        if "a" <= char <= "z":
            letters.append(char)
        elif char in TONES and letters and len(letters) > start:
            if len(letters) - 1 in tones or letters[-1] not in "aeiouümn":
                return None
            tones[len(letters) - 1] = TONES[char]
        elif char == "\u0308" and letters and letters[-1] == "u" and len(letters) > start:
            letters[-1] = "ü"
        elif char in "012345":
            offset = tone_index("".join(letters[start:]))
            if offset is None:
                return None
            index = start + offset
            tone = int(char) % 5
            if index in tones and tones[index] != tone:
                return None
            tones[index] = tone
            start = len(letters)
        elif char in SEPARATORS or char.isspace():
            start = len(letters)
        else:
            return None
    if letters:
        return "".join(letters), tones
    return None


def pinyin_matches(value, expected):
    query = parse_pinyin(expected)
    if query is None:
        return False
    needle, required = query
    variants = value.split("/")
    for variant in tuple(variants):
        if "(" in variant:
            variants.append(re.sub(r"\([^()]*\)", "", variant))
    for variant in variants:
        parsed = parse_pinyin(variant)
        if parsed is None:
            continue
        letters, tones = parsed
        if letters == needle and all(tones.get(offset, 0) == tone for offset, tone in required.items()):
            return True
    return False


def search_cards(db, filters, page):
    db.create_function("pos_matches", 2, pos_matches, deterministic=True)
    db.create_function("pinyin_matches", 2, pinyin_matches, deterministic=True)
    db.create_function("normalize_russian", 1, normalize_russian, deterministic=True)
    clauses = {
        "lesson": "EXISTS(SELECT 1 FROM lesson_cards WHERE card_key=c.id AND lesson=?)",
        "topic": "EXISTS(SELECT 1 FROM card_topics WHERE card_key=c.id AND topic=?)",
        "pos": "pos_matches(c.pos,?)",
        "pinyin": "pinyin_matches(c.pinyin,?)",
        "translation": "instr(normalize_russian(c.translation), normalize_russian(?))>0",
        "hanzi": "instr(c.hanzi,?)>0",
    }
    conditions = [clauses[key] for key in filters]
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    params = tuple(filters.values())
    count = db.execute("SELECT count(*) FROM cards c" + where, params).fetchone()[0]
    last = max(0, (count - 1) // PAGE_SIZE)
    page = max(0, min(last, page))
    cards = db.execute("""
        SELECT c.*, (SELECT group_concat(lesson, ', ') FROM (
            SELECT lesson FROM lesson_cards WHERE card_key=c.id ORDER BY lesson
        )) AS lessons FROM cards c
    """ + where + " ORDER BY c.lesson,c.id LIMIT ? OFFSET ?", (*params, PAGE_SIZE, page * PAGE_SIZE))
    return cards.fetchall(), count, page, last
