BEGIN;

ALTER TABLE incidents
    ADD COLUMN severity TEXT NOT NULL DEFAULT 'medium'
        CHECK (severity IN ('critical', 'high', 'medium', 'low'));

ALTER TABLE incidents
    ADD COLUMN assignee_user_id UUID REFERENCES users(id) ON DELETE SET NULL;

CREATE INDEX incidents_assignee_status_idx ON incidents (assignee_user_id, status);

INSERT INTO schema_migrations (version) VALUES ('0008_incident_triage');

COMMIT;
