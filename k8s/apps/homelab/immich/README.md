# Immich GitOps handoff

Immich currently runs from temporary migration-runner objects. The 350 GiB
library PVC and 10 GiB PostgreSQL PVC are the authoritative migrated state and
must never be deleted during adoption.

The first Argo CD sync is manual because the temporary `immich-server` and
`immich-machine-learning` Deployments use selectors that differ from the chart's
immutable selectors. At the adoption checkpoint:

1. Verify `immich-library` and `immich-postgres` are `Bound`, and both Longhorn
   volumes are healthy.
2. Scale the temporary server and machine-learning Deployments to zero.
3. Delete only their Deployments and Services. Do not delete the namespace,
   Secret, PVCs, PVs, Longhorn volumes, or PostgreSQL workload.
4. Sync this Application. Wait for PostgreSQL, Valkey, machine learning, and the
   server to become Ready.
5. Delete the now-unused temporary `immich-redis` Deployment and Service only
   after chart-managed `immich-valkey` is Ready.
6. Validate login, timeline, representative photo/video reads, uploads, database
   counts, and Immich's integrity report through `immich.in.neovara.uk`.
7. Only after acceptance, enable automated prune/self-heal on the Application.

Chart `0.13.1` normally runs Immich v3.0.0. `values.yaml` deliberately overrides
the images to the already-validated v2.7.3. PostgreSQL first moves to Immich's
official compatibility image containing pgvecto.rs, pgvector, and VectorChord;
the database extension transition must complete on v2 before any v3 image bump.
