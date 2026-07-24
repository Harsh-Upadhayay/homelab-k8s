# INC-YYYY-NNN: Short incident title

## Incident metadata

| Field | Value |
| --- | --- |
| Date | YYYY-MM-DD |
| Severity | SEV-N |
| Status | Investigating / Mitigated / Resolved / Closed |
| Systems | Affected systems |
| Start | ISO 8601 timestamp, including time zone |
| End | ISO 8601 timestamp, including time zone |
| Duration | User-impact duration |
| Detection | How the incident was first detected |
| Data impact | Confirmed loss, suspected risk, or how no loss was verified |

## Executive summary

In one short paragraph: what happened, what users experienced, the technical cause, and how service
was restored.

## Impact

Describe user-visible and platform impact, affected scope, degraded redundancy, and what was not
affected. Do not describe potential impact as though it actually occurred.

## Detection

Record the first signal, why it was or was not actionable, and which signal would have detected the
failure sooner or more clearly.

## Timeline

Use one time zone consistently and identify it. Mark reconstructed or approximate times.

| Time | Event |
| --- | --- |
| HH:MM | Observed event, decision, or remediation |

## Technical root cause

Explain the causal chain. Include the lowest-level confirmed failure and why it produced the
user-visible symptom.

## Contributing factors

List conditions that made the incident possible, harder to detect, or slower to recover. These are
system and process properties, not assignments of personal blame.

## Resolution and recovery

Describe the actions that restored service and the verification that established recovery. Include
commands only when they are safe, reusable, and essential to understanding the response.

## What went well

- Safeguards, evidence, or decisions that limited impact or accelerated recovery.

## What did not go well

- Missing safeguards, confusing signals, or recovery friction.

## Where we got lucky

- Conditions that limited impact but should not be relied upon as controls.

## Corrective and preventive actions

| Priority | Action | Owner | Status | Completion evidence |
| --- | --- | --- | --- | --- |
| P0/P1/P2 | Concrete, testable action | Owner | Open / In progress / Done / Deferred | Test, file, dashboard, or runbook evidence |

## Lessons and review questions

Summarize the reusable operational lessons. Add questions that should be answered during the
learning review, especially where the incident exposes Kubernetes, Linux, storage, networking, or
automation mechanics worth practising.

## Evidence

List relevant Kubernetes events, logs, object names, commits, dashboards, or linked runbooks.
Redact secrets and avoid pasting large unfiltered logs.
