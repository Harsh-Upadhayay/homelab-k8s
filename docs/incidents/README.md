# Incident reviews

This directory is the platform's append-only learning log for operational incidents and meaningful
near misses. It follows the blameless post-incident review style used by Site Reliability
Engineering teams: explain how the system behaved, why the available safeguards were insufficient,
how service was restored, and what will reduce recurrence. The purpose is learning, not fault.

Incident reviews are different from the other documentation:

- an **ADR** records why an architecture or policy was chosen;
- a **runbook** records how to perform or recover an operation;
- a **troubleshooting note** records reusable diagnostic knowledge;
- an **incident review** reconstructs one real event, its impact, evidence, and follow-up work.

## When to open an incident

Create a review when any of the following occurs:

- a user-facing service becomes unexpectedly unavailable or loses data;
- a recovery or migration crosses a data-loss boundary or requires manual intervention;
- automation produces an unintended infrastructure change;
- a control-plane, storage, network, security, backup, or restore safeguard fails;
- a near miss exposes a failure mode worth rehearsing, even when there is no impact.

Small, already-understood command mistakes do not need a review unless they reveal a systemic gap.

## Ritual

1. Stabilize the system and preserve evidence. Do not delay recovery to write the report.
2. Create `INC-YYYY-NNN-short-title.md` from [TEMPLATE.md](./TEMPLATE.md).
3. Separate observed facts from hypotheses. Use timestamps and exact error messages where useful,
   but never record credentials or other secrets.
4. State impact and data integrity explicitly. Do not infer "no data loss" from service recovery
   alone; record how it was verified.
5. Identify a root cause, contributing factors, and detection/recovery gaps. Avoid assigning blame
   to a person.
6. Give every corrective action an owner, status, and completion condition. Do not mark proposed
   work as complete.
7. Link the report from this index and from the affected runbook.
8. Revisit open actions before repeating the same class of operation.

## Severity

Severity reflects actual impact, not how alarming the symptoms looked.

| Level | Meaning in this homelab |
| --- | --- |
| SEV-1 | Irrecoverable data loss, major security compromise, or loss of the cluster/control plane with no tested recovery |
| SEV-2 | Extended outage of a critical service, control plane, or storage system; significant recovery risk; no confirmed irreversible loss |
| SEV-3 | Limited or short user-facing outage, failed migration step, or degraded redundancy requiring intervention |
| SEV-4 | Near miss or internal fault with no user-visible impact, retained because it teaches a reusable lesson |

## Index

| ID | Date | Severity | System | Summary | Status |
| --- | --- | --- | --- | --- | --- |
| [INC-2026-001](./INC-2026-001-longhorn-stale-mount-namespace.md) | 2026-07-24 | SEV-3 | Immich / Longhorn | Late storage mount left the instance-manager unable to see its engine binary | Resolved; prevention open |
