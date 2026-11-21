"""Состояние выбора и создание сессий повторения уроков."""
from .training import active_session, create_session


def open_lessons(db, learner):
    return [row[0] for row in db.execute("""SELECT DISTINCT lc.lesson FROM lesson_cards lc
        JOIN learners l ON l.id=? AND lc.lesson<=l.lesson ORDER BY lc.lesson""", (learner,))]


def choose_lesson(db, learner, lesson):
    if lesson not in open_lessons(db, learner):
        return False
    state = get_state(db, learner)
    if state is None or state["lesson"] != lesson:
        db.execute("DELETE FROM review_state WHERE learner=?", (learner,))
        db.execute("INSERT INTO review_state(learner,lesson) VALUES (?,?)", (learner, lesson))
    return True


def get_state(db, learner):
    return db.execute("SELECT * FROM review_state WHERE learner=?", (learner,)).fetchone()


def cards_for_review(db, learner, state, *, random_order=False):
    order = ("is_error DESC, CASE WHEN is_error THEN p.rated_at END DESC, random()"
             if random_order else "lc.position, c.id")
    return db.execute(f"""
        SELECT c.*, (coalesce(p.last_rating,p.last_grade,'')='no' AND coalesce(p.known,0)=0) AS is_error,
            rs.card_key IS NOT NULL AS selected
        FROM lesson_cards lc JOIN cards c ON c.id=lc.card_key
        LEFT JOIN progress p ON p.card=c.id AND p.learner=?
        LEFT JOIN review_selection rs ON rs.card_key=c.id AND rs.learner=?
        WHERE lc.lesson=? AND (?=0 OR is_error)
        ORDER BY {order}
    """, (learner, learner, state["lesson"], state["only_errors"])).fetchall()


def toggle_card(db, learner, state, key):
    if not any(row["id"] == key for row in cards_for_review(db, learner, state)):
        return
    deleted = db.execute("DELETE FROM review_selection WHERE learner=? AND card_key=?", (learner, key))
    if not deleted.rowcount:
        db.execute("INSERT INTO review_selection VALUES (?,?)", (learner, key))


def start_review(db, learner, state, mode):
    current = active_session(db, learner)
    if current:
        return current
    if state["lesson"] not in open_lessons(db, learner):
        return None
    rows = cards_for_review(db, learner, state, random_order=mode == "random")
    if mode == "manual":
        rows = [row for row in rows if row["selected"]]
    elif mode == "random":
        quantity = db.execute("SELECT quantity FROM learners WHERE id=?", (learner,)).fetchone()[0]
        rows = rows[:quantity]
    session = create_session(db, learner, "review", [row["id"] for row in rows], lesson=state["lesson"])
    if session:
        db.execute("DELETE FROM review_state WHERE learner=?", (learner,))
    return session
