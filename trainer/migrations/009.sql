ALTER TABLE sessions ADD COLUMN lesson_limit INTEGER NOT NULL DEFAULT 1 CHECK (lesson_limit >= 1);

UPDATE sessions
SET lesson_limit = max(
    (SELECT lesson FROM learners WHERE id=sessions.learner),
    coalesce(sessions.lesson, 1),
    coalesce((SELECT max(c.lesson) FROM tasks t JOIN cards c ON c.id=t.card
              WHERE t.session=sessions.id), 1)
)
WHERE finished=0;

-- Extend only queued generation inputs, keeping their original vocabulary and ready questions.
WITH RECURSIVE generation_chars(session, number, remaining, allowed) AS (
    SELECT t.session, t.number, c.hanzi, coalesce(t.allowed, '')
    FROM jobs j JOIN tasks t USING (session, number) JOIN cards c ON c.id=t.card
    WHERE j.kind='generate' AND t.phase='generate' AND t.question IS NULL
    UNION ALL
    SELECT session, number, substr(remaining, 2),
           CASE WHEN substr(remaining, 1, 1) BETWEEN '㐀' AND '鿿'
                     AND instr(allowed, substr(remaining, 1, 1))=0
                THEN allowed || substr(remaining, 1, 1) ELSE allowed END
    FROM generation_chars WHERE remaining!=''
)
UPDATE tasks
SET allowed=g.allowed
FROM generation_chars g
WHERE tasks.session=g.session AND tasks.number=g.number AND g.remaining='';
