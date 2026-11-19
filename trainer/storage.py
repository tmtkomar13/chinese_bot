import csv
import os
import sqlite3
import tempfile
import uuid
import unicodedata
from contextlib import closing, contextmanager
from datetime import date
from pathlib import Path


@contextmanager
def transaction(db):
    db.execute("BEGIN IMMEDIATE")
    try:
        yield
        db.commit()
    except BaseException:
        db.rollback()
        raise


@contextmanager
def database(path, *, create=False):
    path = Path(path).resolve()
    uri = path.as_uri() + ("?mode=rwc" if create else "?mode=rw")
    with closing(sqlite3.connect(uri, uri=True, timeout=30, isolation_level=None)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        migrations = sorted(Path(__file__).with_name("migrations").glob("*.sql"))
        with transaction(db):
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > len(migrations):
                raise ValueError("База создана более новой версией тренажёра")
            if 0 < version < len(migrations):
                # The reserved write lock keeps this separate read snapshot consistent.
                with closing(sqlite3.connect(uri, uri=True)) as source:
                    snapshot = path.with_name(f"{path.name}.v{version}-{uuid.uuid4().hex}.bak")
                    with closing(sqlite3.connect(snapshot)) as dest:
                        source.backup(dest)
                        if dest.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                            raise ValueError("Ошибка проверки копии перед миграцией")
            for number, script in enumerate(migrations[version:], version + 1):
                if int(script.stem) != number:
                    raise ValueError("Нарушена нумерация миграций")
                statement = ""
                for line in script.read_text(encoding="utf-8").splitlines(True):
                    statement += line
                    if sqlite3.complete_statement(statement):
                        db.execute(statement)
                        statement = ""
                if statement.strip():
                    raise ValueError(f"Незавершённый SQL в {script.name}")
                db.execute(f"PRAGMA user_version={number}")
        yield db


def import_vocabulary(db, path):
    entries = {}
    with open(path, encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream, delimiter="\t", strict=True)
        if next(reader, []) != ["key", "lesson", "hanzi", "pinyin", "pos", "translation"]:
            raise ValueError("Неверный заголовок TSV")
        for row in reader:
            try:
                key, number, hanzi, pinyin, pos, meaning = (
                    unicodedata.normalize("NFC", s.strip()) for s in row
                )
                lesson = int(number)
                if lesson < 1 or not all((key, hanzi, pinyin, meaning)):
                    raise ValueError("Пропущено обязательное поле или неверный урок")
                values = (hanzi, pinyin, pos or None, meaning)
                if key in entries:
                    previous, earliest = entries[key]
                    if previous != values:
                        raise ValueError(f"Разные значения под ключом {key}")
                    lesson = min(earliest, lesson)
                entries[key] = (values, lesson)
            except ValueError as exc:
                raise ValueError(f"Строка {reader.line_num}: {exc}") from exc
    if not entries:
        raise ValueError("Пустой словарь")
    with transaction(db):
        db.executemany("""
            INSERT INTO cards(id, hanzi, pinyin, pos, translation, lesson)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET hanzi=excluded.hanzi, pinyin=excluded.pinyin,
                pos=excluded.pos, translation=excluded.translation,
                lesson=min(cards.lesson, excluded.lesson)
        """, ((key, *values, lesson) for key, (values, lesson) in entries.items()))
    return len(entries)


def backup(db, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"trainer-{date.today().isoformat()}.sqlite3"
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with closing(sqlite3.connect(temporary)) as copy:
            db.backup(copy)
            if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Ошибка проверки резервной копии")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    for old in sorted(directory.glob("trainer-????-??-??.sqlite3"))[:-7]:
        old.unlink()
    return target
