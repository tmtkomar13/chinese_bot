"""Операции над текущей тренировкой. Вызываются внутри транзакции."""
import random


def select_cards(db, learner, limit=10):
    pool = list(db.execute("""
        SELECT c.id, c.hanzi, coalesce(p.attempts, 0) AS attempts, p.last_grade
        FROM cards c JOIN learners l ON l.id=? AND c.lesson<=l.lesson
        LEFT JOIN progress p ON p.card=c.id AND p.learner=l.id
        WHERE coalesce(p.known, 0)=0
        ORDER BY attempts, random()
    """, (learner,)))
    size = min(len({row["hanzi"] for row in pool}), limit)
    failed = [row for row in pool if row["last_grade"] == "no"]
    others = [row for row in pool if row["last_grade"] != "no"]
    selected = {}
    # Only exceed the error quota when the remaining words cannot fill the round.
    for candidates, target in ((failed, size // 2), (others, size), (failed, size)):
        for row in candidates:
            if len(selected) >= target:
                break
            selected.setdefault(row["hanzi"], row["id"])
    cards = list(selected.values())
    random.shuffle(cards)
    return cards


def active_session(db, learner):
    row = db.execute("SELECT id FROM sessions WHERE learner=? AND finished=0",
                     (learner,)).fetchone()
    return row[0] if row else None


def start(db, learner):
    current = active_session(db, learner)
    if current is not None:
        return current
    cards = select_cards(db, learner)
    if not cards:
        return None
    session = db.execute("INSERT INTO sessions(learner) VALUES (?)", (learner,)).lastrowid
    db.executemany("INSERT INTO tasks(session,number,card) VALUES (?,?,?)",
                   ((session, i, card) for i, card in enumerate(cards, 1)))
    return session


def current_task(db, session):
    return db.execute("""
        SELECT t.*, c.hanzi, c.pinyin, c.pos, c.translation, c.lesson,
               (SELECT count(*) FROM tasks WHERE session=t.session) AS total
        FROM tasks t JOIN cards c ON c.id=t.card
        WHERE session=? AND grade IS NULL ORDER BY number LIMIT 1
    """, (session,)).fetchone()


def apply_action(db, learner, session, number, action):
    if active_session(db, learner) != session:
        return False
    task = current_task(db, session)
    if not task or number != task["number"]:
        return False
    if action == "show":
        db.execute("UPDATE tasks SET shown=1 WHERE session=? AND number=?", (session, number))
        return True
    if not task["shown"] or action not in ("yes", "no", "known"):
        return False
    db.execute("UPDATE tasks SET grade=? WHERE session=? AND number=?", (action, session, number))
    known = action == "known"
    db.execute("""
        INSERT INTO progress(learner,card,attempts,errors,last_grade,known,revision)
        VALUES (?,?,?,?,?,?,1)
        ON CONFLICT(learner,card) DO UPDATE SET attempts=attempts+excluded.attempts,
            errors=errors+excluded.errors, last_grade=coalesce(excluded.last_grade,last_grade),
            known=excluded.known, revision=revision+1
    """, (learner, task["card"], int(not known), int(action == "no"),
          None if known else action, int(known)))
    if number == task["total"]:
        db.execute("UPDATE sessions SET finished=1 WHERE id=?", (session,))
    return True
