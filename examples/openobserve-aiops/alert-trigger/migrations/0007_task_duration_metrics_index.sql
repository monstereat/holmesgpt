BEGIN;

CREATE INDEX IF NOT EXISTS tasks_terminal_duration_completed_idx
    ON tasks (completed_at) INCLUDE (started_at)
    WHERE status IN ('completed', 'failed')
      AND started_at IS NOT NULL
      AND completed_at >= started_at;

INSERT INTO schema_migrations (version) VALUES ('0007_task_duration_metrics_index')
ON CONFLICT (version) DO NOTHING;

COMMIT;
