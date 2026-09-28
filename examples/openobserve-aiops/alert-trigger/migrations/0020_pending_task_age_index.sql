BEGIN;

CREATE INDEX IF NOT EXISTS tasks_pending_created_at_idx
    ON tasks (created_at)
    WHERE status IN ('queued', 'running', 'retrying');

INSERT INTO schema_migrations (version) VALUES ('0020_pending_task_age_index')
ON CONFLICT (version) DO NOTHING;

COMMIT;
