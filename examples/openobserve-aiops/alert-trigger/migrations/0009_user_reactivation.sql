ALTER TABLE users
    ADD COLUMN session_generation INTEGER NOT NULL DEFAULT 0 CHECK (session_generation >= 0),
    ADD COLUMN reactivation_requested_at TIMESTAMPTZ;
