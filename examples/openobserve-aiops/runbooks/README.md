# Legacy runbook catalog example

`catalog.json` shows the pre-Skills custom runbook format for migration reference. The current Holmes version in this repository loads troubleshooting guides from `SKILL.md` files configured through `custom_skill_paths`; it does not consume `custom_runbook_catalogs`.

The active order-service guides are the inventory, database-schema-mismatch, and release-regression Skills under [`../skills/`](../skills/). Holmes loads them through `custom_skill_paths`; do not configure this legacy catalog as the active integration.
