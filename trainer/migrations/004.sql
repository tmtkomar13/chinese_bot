CREATE TABLE media_retry (
    word TEXT NOT NULL,
    style TEXT NOT NULL,
    failures INTEGER NOT NULL,
    retry_at INTEGER NOT NULL,
    PRIMARY KEY (word, style)
);
DELETE FROM jobs WHERE kind='media';
