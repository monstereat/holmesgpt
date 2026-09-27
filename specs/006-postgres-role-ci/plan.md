# Plan: PostgreSQL role verifier CI gate

Add role template/bootstrap/verifier paths to the existing AIOps workflow filter. Add an isolated PostgreSQL 16 verifier step alongside existing backup and monitoring checks. Run the same verifier locally, parse the workflow YAML, and record that hosted workflow execution and branch protection remain separate checks.
