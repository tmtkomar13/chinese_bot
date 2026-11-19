ALTER TABLE learners ADD COLUMN animation TEXT NOT NULL DEFAULT 'gif' CHECK(animation IN ('off','gif','page'));
ALTER TABLE learners ADD COLUMN speed TEXT NOT NULL DEFAULT 'normal' CHECK(speed IN ('slow','normal','fast'));
ALTER TABLE learners ADD COLUMN animation_revision INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tasks ADD COLUMN gif_request INTEGER;
