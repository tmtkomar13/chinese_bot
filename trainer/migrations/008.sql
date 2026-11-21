CREATE TABLE search_states (
    learner INTEGER PRIMARY KEY REFERENCES learners(id) ON DELETE CASCADE,
    is_open INTEGER NOT NULL DEFAULT 0 CHECK (is_open IN (0,1)),
    filters TEXT NOT NULL DEFAULT '{}',
    filter_kind TEXT,
    page INTEGER NOT NULL DEFAULT 0 CHECK (page >= 0)
);

CREATE TABLE topic_jobs (
    card_key TEXT PRIMARY KEY REFERENCES cards(id) ON DELETE CASCADE,
    failures INTEGER NOT NULL DEFAULT 0 CHECK (failures >= 0),
    retry_at INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE card_topics (
    card_key TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    topic TEXT NOT NULL,
    PRIMARY KEY (card_key, topic)
) WITHOUT ROWID;

CREATE INDEX card_topics_by_topic ON card_topics(topic, card_key);

INSERT INTO topic_jobs(card_key) SELECT id FROM cards;

CREATE TRIGGER queue_new_card_topics AFTER INSERT ON cards
BEGIN
    INSERT INTO topic_jobs(card_key) VALUES(NEW.id);
END;

CREATE TRIGGER queue_updated_card_topics AFTER UPDATE OF hanzi,pinyin,pos,translation ON cards
WHEN NEW.hanzi IS NOT OLD.hanzi OR NEW.pinyin IS NOT OLD.pinyin
    OR NEW.pos IS NOT OLD.pos OR NEW.translation IS NOT OLD.translation
BEGIN
    DELETE FROM card_topics WHERE card_key=NEW.id;
    INSERT INTO topic_jobs(card_key) VALUES(NEW.id)
        ON CONFLICT(card_key) DO UPDATE SET failures=0,retry_at=0;
END;
