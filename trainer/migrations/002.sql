ALTER TABLE learners ADD COLUMN quantity INTEGER NOT NULL DEFAULT 10 CHECK(quantity BETWEEN 1 AND 100);
ALTER TABLE learners ADD COLUMN remind_time TEXT NOT NULL DEFAULT '19:00';
ALTER TABLE learners ADD COLUMN reminders INTEGER NOT NULL DEFAULT 1;
ALTER TABLE learners ADD COLUMN translations INTEGER NOT NULL DEFAULT 1;
ALTER TABLE sessions ADD COLUMN kind TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE sessions ADD COLUMN day TEXT;
CREATE UNIQUE INDEX daily ON sessions(learner,day) WHERE kind='daily';
ALTER TABLE tasks ADD COLUMN translate INTEGER;
ALTER TABLE tasks ADD COLUMN phase TEXT NOT NULL DEFAULT '';
ALTER TABLE tasks ADD COLUMN allowed TEXT;
ALTER TABLE tasks ADD COLUMN question TEXT;
ALTER TABLE tasks ADD COLUMN answer TEXT;
ALTER TABLE tasks ADD COLUMN feedback TEXT;
ALTER TABLE tasks ADD COLUMN tries INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tasks ADD COLUMN outcome TEXT;
CREATE TABLE translation_attempts(session INTEGER, number INTEGER, attempt INTEGER, answer TEXT NOT NULL, correct INTEGER, hint TEXT,
    PRIMARY KEY(session,number,attempt), FOREIGN KEY(session,number) REFERENCES tasks);
CREATE TABLE jobs(id INTEGER PRIMARY KEY AUTOINCREMENT, session INTEGER, number INTEGER, kind TEXT, UNIQUE(session,number,kind), FOREIGN KEY(session,number) REFERENCES tasks);
CREATE TABLE reminders_sent(learner INTEGER, day TEXT, PRIMARY KEY(learner,day));
CREATE TABLE outbox(id INTEGER PRIMARY KEY AUTOINCREMENT, peer INTEGER NOT NULL, reply TEXT NOT NULL);
INSERT INTO outbox(id,peer,reply) SELECT id,peer,reply FROM vk_messages WHERE reply IS NOT NULL;
INSERT INTO sqlite_sequence(name,seq) SELECT 'outbox',0 WHERE NOT EXISTS(SELECT 1 FROM sqlite_sequence WHERE name='outbox');
UPDATE sqlite_sequence SET seq=max(seq,(SELECT coalesce(max(id),0) FROM vk_messages)) WHERE name='outbox';
UPDATE vk_messages SET reply=NULL;
CREATE TABLE media(word TEXT, style TEXT, attachment TEXT, PRIMARY KEY(word,style));

