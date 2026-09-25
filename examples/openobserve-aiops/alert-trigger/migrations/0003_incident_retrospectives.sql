BEGIN;

CREATE TABLE IF NOT EXISTS incident_retrospectives (
    incident_id UUID PRIMARY KEY REFERENCES incidents(id) ON DELETE CASCADE,
    impact TEXT NOT NULL DEFAULT '',
    root_cause TEXT NOT NULL DEFAULT '',
    resolution TEXT NOT NULL DEFAULT '',
    action_items JSONB NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(action_items) = 'array'),
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'reviewed')),
    updated_by UUID NOT NULL REFERENCES users(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_by UUID REFERENCES users(id),
    reviewed_at TIMESTAMPTZ,
    CHECK ((status = 'reviewed') = (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL))
);

COMMIT;
