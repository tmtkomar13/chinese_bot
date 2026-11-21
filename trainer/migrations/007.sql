CREATE TABLE lesson_cards (
    lesson INTEGER NOT NULL CHECK (lesson >= 1),
    card_key TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    position INTEGER NOT NULL CHECK (position >= 1),
    PRIMARY KEY (lesson, card_key)
);
CREATE INDEX lesson_cards_order ON lesson_cards(lesson, position);
CREATE INDEX lesson_cards_by_card ON lesson_cards(card_key, lesson);
INSERT INTO lesson_cards(lesson, card_key, position)
SELECT lesson, id, row_number() OVER (PARTITION BY lesson ORDER BY id) FROM cards;

ALTER TABLE sessions ADD COLUMN lesson INTEGER CHECK (lesson >= 1);
ALTER TABLE progress ADD COLUMN last_rating TEXT CHECK (last_rating IN ('yes','no','known'));
ALTER TABLE progress ADD COLUMN rated_at INTEGER;
CREATE INDEX graded_tasks_by_card ON tasks(card, session) WHERE grade IS NOT NULL;
UPDATE progress SET last_rating=CASE WHEN known=1 THEN 'known' ELSE coalesce((
    SELECT t.grade FROM tasks t JOIN sessions s ON s.id=t.session
    WHERE s.learner=progress.learner AND t.card=progress.card AND t.grade IS NOT NULL
    ORDER BY s.id DESC, t.number DESC LIMIT 1
), last_grade) END,
rated_at=(
    SELECT cast(strftime('%s', s.day) AS INTEGER)*1000000000
    FROM tasks t JOIN sessions s ON s.id=t.session
    WHERE s.learner=progress.learner AND t.card=progress.card AND t.grade IS NOT NULL
    ORDER BY s.id DESC, t.number DESC LIMIT 1
);

CREATE TABLE review_state (
    learner INTEGER PRIMARY KEY REFERENCES learners(id) ON DELETE CASCADE,
    lesson INTEGER NOT NULL CHECK (lesson >= 1),
    only_errors INTEGER NOT NULL DEFAULT 0 CHECK (only_errors IN (0,1)),
    manual INTEGER NOT NULL DEFAULT 0 CHECK (manual IN (0,1)),
    page INTEGER NOT NULL DEFAULT 0 CHECK (page >= 0)
);
CREATE TABLE review_selection (
    learner INTEGER NOT NULL REFERENCES review_state(learner) ON DELETE CASCADE,
    card_key TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    PRIMARY KEY (learner, card_key)
);
