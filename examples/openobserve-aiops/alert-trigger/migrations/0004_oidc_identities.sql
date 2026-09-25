BEGIN;

ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS oidc_issuer TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS oidc_subject TEXT;
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_oidc_identity_pair_check;
ALTER TABLE users ADD CONSTRAINT users_oidc_identity_pair_check
    CHECK ((oidc_issuer IS NULL) = (oidc_subject IS NULL));
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_oidc_identity_unique;
ALTER TABLE users ADD CONSTRAINT users_oidc_identity_unique UNIQUE (oidc_issuer, oidc_subject);

CREATE TABLE IF NOT EXISTS oidc_login_transactions (
    state_hash CHAR(64) PRIMARY KEY,
    code_verifier TEXT NOT NULL,
    nonce TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS oidc_login_transactions_expiry_idx ON oidc_login_transactions (expires_at);

INSERT INTO schema_migrations (version) VALUES ('0004_oidc_identities')
ON CONFLICT (version) DO NOTHING;

COMMIT;
