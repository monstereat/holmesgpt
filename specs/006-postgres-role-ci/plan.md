# Plan: PostgreSQL role verifier CI gate

Add role template/bootstrap/verifier paths to the existing AIOps workflow filter. Run both the isolated PostgreSQL 16 role verifier and a local password-bootstrap verifier in a no-network PostgreSQL container, alongside existing backup and monitoring checks. Confirm both checks locally, parse the workflow YAML, and record that hosted workflow execution and branch protection remain separate checks.
