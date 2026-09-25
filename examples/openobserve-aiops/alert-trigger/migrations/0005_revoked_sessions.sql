BEGIN;

CREATE TABLE IF NOT EXISTS revoked_sessions (
    token_hash CHAR(64) PRIMARY KEY,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS revoked_sessions_expiry_idx ON revoked_sessions (expires_at);

INSERT INTO schema_migrations (version) VALUES ('0005_revoked_sessions')
ON CONFLICT (version) DO NOTHING;

COMMIT;
