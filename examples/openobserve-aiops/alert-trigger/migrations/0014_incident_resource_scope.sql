ALTER TABLE incidents
    ADD COLUMN IF NOT EXISTS resource TEXT NOT NULL DEFAULT 'order-service';

ALTER TABLE incidents
    ADD CONSTRAINT incidents_resource_format
    CHECK (resource ~ '^[a-z0-9][a-z0-9._:-]{0,99}$');

CREATE INDEX IF NOT EXISTS incidents_resource_created_idx
    ON incidents (resource, created_at DESC, id DESC);
