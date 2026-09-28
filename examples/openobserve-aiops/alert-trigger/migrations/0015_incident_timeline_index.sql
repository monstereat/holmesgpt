BEGIN;

CREATE INDEX IF NOT EXISTS audit_events_incident_timeline_idx
    ON audit_events (incident_id, created_at DESC, id DESC)
    WHERE incident_id IS NOT NULL;

INSERT INTO schema_migrations (version) VALUES ('0015_incident_timeline_index')
ON CONFLICT (version) DO NOTHING;

COMMIT;
