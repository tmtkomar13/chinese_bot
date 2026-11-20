ALTER TABLE learners ADD COLUMN difficulty TEXT NOT NULL DEFAULT 'easy' CHECK (difficulty IN ('easy','medium','hard'));
ALTER TABLE tasks ADD COLUMN difficulty TEXT NOT NULL DEFAULT 'easy' CHECK (difficulty IN ('easy','medium','hard'));
