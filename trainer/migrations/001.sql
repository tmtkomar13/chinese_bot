CREATE TABLE cards (
    id TEXT PRIMARY KEY NOT NULL,
    hanzi TEXT NOT NULL,
    pinyin TEXT NOT NULL,
    pos TEXT,
    translation TEXT NOT NULL,
    lesson INTEGER NOT NULL CHECK (lesson >= 1),
    UNIQUE (hanzi, pinyin, translation)
);
CREATE TABLE learners (
    id INTEGER PRIMARY KEY,
    lesson INTEGER NOT NULL DEFAULT 13 CHECK (lesson >= 1)
);
CREATE TABLE progress (
    learner INTEGER NOT NULL REFERENCES learners,
    card TEXT NOT NULL REFERENCES cards,
    attempts INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    last_grade TEXT CHECK (last_grade IN ('yes','no')),
    known INTEGER NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (learner, card)
);
CREATE TABLE sessions (
    id INTEGER PRIMARY KEY,
    learner INTEGER NOT NULL REFERENCES learners,
    finished INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX one_active_session ON sessions(learner) WHERE finished = 0;
CREATE TABLE tasks (
    session INTEGER NOT NULL REFERENCES sessions,
    number INTEGER NOT NULL,
    card TEXT NOT NULL REFERENCES cards,
    shown INTEGER NOT NULL DEFAULT 0,
    grade TEXT CHECK (grade IN ('yes','no','known')),
    PRIMARY KEY (session, number),
    UNIQUE (session, card)
);
CREATE TABLE vk_messages (
    id INTEGER PRIMARY KEY,
    peer INTEGER NOT NULL,
    incoming INTEGER NOT NULL,
    reply TEXT,
    UNIQUE (peer, incoming)
);
CREATE INDEX pending_messages ON vk_messages(id) WHERE reply IS NOT NULL;
CREATE TABLE vk_cursor (
    community INTEGER PRIMARY KEY,
    ts TEXT NOT NULL
);
