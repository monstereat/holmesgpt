BEGIN;

CREATE INDEX IF NOT EXISTS tasks_metrics_status_attempt_idx
    ON tasks (status) INCLUDE (attempt);

CREATE INDEX IF NOT EXISTS outbox_investigation_task_available_idx
    ON outbox_events ((payload->>'task_id'), available_at)
    INCLUDE (created_at, published_at, delivery_attempts)
    WHERE event_type = 'investigation.requested';

INSERT INTO schema_migrations (version) VALUES ('0021_metrics_scan_indexes')
ON CONFLICT (version) DO NOTHING;

COMMIT;
