"""Проверка на нетбуке во время занятия владельца. Запуск через sudo."""
import sqlite3
import subprocess
import time
from contextlib import closing
from pathlib import Path

STATE = Path("/var/lib/chinese-trainer")


def service(*args):
    subprocess.run(["systemctl", *args], check=True)


def snapshot(db):
    return db.execute("""
        SELECT s.id, count(*) AS total, count(t.grade) AS graded
        FROM sessions s JOIN tasks t ON t.session=s.id
        WHERE s.finished=0 GROUP BY s.id ORDER BY s.id DESC LIMIT 1
    """).fetchone()


def main():
    service("is-active", "chinese-trainer.service", "chinese-trainer-backup.timer")
    with closing(sqlite3.connect((STATE / "trainer.sqlite3").as_uri() + "?mode=ro", uri=True)) as db:
        print("Карточек в рабочей базе:", db.execute("SELECT count(*) FROM cards").fetchone()[0])
        print("В личных сообщениях ВК начните тренировку и оцените первую карточку.", flush=True)
        while True:
            current = snapshot(db)
            if current and current[2] > 0:
                break
            time.sleep(1)
        session, total, graded = current
        print(f"Сохранено оценок: {graded} из {total}. Перезапускаю службу бота.", flush=True)
        service("restart", "chinese-trainer.service")
        service("is-active", "chinese-trainer.service")
        remaining = db.execute("SELECT count(*) FROM tasks WHERE session=? AND grade IS NULL",
                               (session,)).fetchone()[0]
        print(f"После перезапуска осталось {remaining} заданий. Нажмите «Начать тренировку» "
              "и завершите оставшиеся задания.", flush=True)
        while not db.execute("SELECT finished FROM sessions WHERE id=?", (session,)).fetchone()[0]:
            time.sleep(1)
        grades = dict(db.execute("SELECT grade,count(*) FROM tasks WHERE session=? GROUP BY grade",
                                 (session,)))
        if sum(grades.values()) != total or None in grades:
            raise RuntimeError("Расхождение между подборкой и результатами")
        print("Тренировка завершена. Вспомнил:", grades.get("yes", 0),
              "Ошибок:", grades.get("no", 0), "Исключено:", grades.get("known", 0))
        service("start", "chinese-trainer-backup.service")
        latest = max((STATE / "backups").glob("trainer-????-??-??.sqlite3"))
        with closing(sqlite3.connect(latest.as_uri() + "?mode=ro", uri=True)) as saved:
            assert saved.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert dict(saved.execute("SELECT grade,count(*) FROM tasks WHERE session=? GROUP BY grade",
                                      (session,))) == grades
        print("Проверено сохранение результатов в рабочей базе и резервной копии.", flush=True)


if __name__ == "__main__":
    main()
