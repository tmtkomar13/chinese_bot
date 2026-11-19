INSERT OR IGNORE INTO jobs(session,number,kind)
SELECT t.session,t.number,'media'
FROM tasks t JOIN sessions s ON s.id=t.session
WHERE s.kind='legacy' AND s.finished=0 AND t.grade IS NULL;

UPDATE tasks SET translate=(SELECT l.translations FROM sessions s
    JOIN learners l ON l.id=s.learner WHERE s.id=tasks.session)
WHERE shown=1 AND grade IS NULL AND translate IS NULL;
