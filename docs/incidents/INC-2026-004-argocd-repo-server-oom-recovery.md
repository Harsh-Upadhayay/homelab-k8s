# INC-2026-004: Argo CD repository server OOM during recovery reconciliation

## Incident metadata

| Field | Value |
| --- | --- |
| Date | 2026-07-25 JST |
| Severity | SEV-3 |
| Status | Mitigated; recovery validation in progress |
| Systems | Argo CD, Helm-based applications, monitoring, Loki |
| Start | 2026-07-25 09:59 JST |
| End | In progress |
| Duration | In progress |
| Detection | Argo CD applications reported `Unknown` and sync operations failed while restoring GitOps |
| Data impact | No application data loss; Loki's intentionally discarded history had not yet been recreated |

## Executive summary

When the Argo CD application controller was restored after planned storage maintenance, it
reconciled several large Helm applications concurrently. The repository server exceeded its
256 MiB memory limit and was repeatedly OOM-killed. Manifest generation timed out, leaving Loki
absent and multiple Applications in `Unknown` state even though already-running workloads
continued. The immediate mitigation increased the Git-managed repository-server request to
128 MiB and limit to 1 GiB, then retried reconciliation.

## Impact

- Argo CD could not reliably render or reconcile Helm applications.
- Loki's replacement StatefulSet and Longhorn PVC were not created on the first sync attempt.
- Application status for Loki, Alloy, Immich, and kube-prometheus-stack was temporarily unknown.
- Existing application data and all Longhorn replicas remained intact.

## Detection

The first signal was `ComparisonError` with `DeadlineExceeded` or connection refused from
`argocd-repo-server`. Pod status then confirmed 13 restarts and a last termination reason of
`OOMKilled` with exit code 137. The configured memory limit was 256 MiB.

## Timeline

Times are JST on 2026-07-25.

| Time | Event |
| --- | --- |
| 09:57 | PR #52 merged and the Argo CD application controller was restored. |
| 09:59 | Hard refresh and sync operations began across applications stopped for maintenance. |
| 10:00 | Repository server began concurrent Helm chart pulls and manifest generation. |
| 10:01 | Application operations failed with repository-server timeouts and connection refusals. |
| 10:02 | Pod evidence confirmed repeated exit-137 OOM kills at the 256 MiB limit. |
| 10:02 | Recovery changed the repository-server request to 128 MiB and limit to 1 GiB. |

## Technical root cause

The repository server had a 256 MiB memory limit. Restoring the application controller caused
multiple uncached Helm sources, including Loki, Alloy, kube-prometheus-stack, and Immich, to be
rendered close together. That transient working set exceeded the container limit. Kubernetes
terminated the process with `OOMKilled`; in-flight gRPC manifest-generation calls then surfaced as
timeouts or connection refusals, and the next restart repeated the same workload.

## Contributing factors

- The repository-server limit had not been sized for full-cluster cold-cache reconciliation.
- Several large Helm applications were refreshed simultaneously.
- The application controller had been intentionally stopped, concentrating deferred work at
  restart.
- Loki required a full recreation rather than an ordinary no-change comparison.

## Resolution and recovery

1. Confirmed the repo-server endpoint and pod were selected correctly.
2. Distinguished network symptoms from process failure using container last-state evidence.
3. Raised the repository-server request to 128 MiB and limit to 1 GiB in Git and live state.
4. Retried failed Application comparisons and syncs after the larger pod became Ready.
5. Verified Loki recreation and application health before closing the maintenance window.

## What went well

- Kubernetes retained an exact `OOMKilled` termination reason and exit code.
- Existing application workloads continued while GitOps reconciliation was impaired.
- The resource limit was declaratively managed and could be corrected without changing chart
  versions.

## What did not go well

- Argo CD's own observability dependency was being restored at the same time as the failure.
- Application-level `Unknown` initially resembled a repository connectivity problem.
- No alert or capacity test covered a cold-cache, full-application reconciliation.

## Where we got lucky

- The failure happened during an accepted maintenance window.
- No secret generation or destructive prune completed before the repo server restarted.
- All persistent application data was already protected by healthy Longhorn replicas.

## Corrective and preventive actions

| Priority | Action | Owner | Status | Completion evidence |
| --- | --- | --- | --- | --- |
| P0 | Raise repo-server memory request to 128 MiB and limit to 1 GiB | Repository owner | Done | `k8s/argocd/values.yaml` and Ready replacement pod |
| P0 | Retry failed reconciliation and recreate Loki on Longhorn | Repository owner | In progress | All Applications Synced/Healthy and Loki PVC Bound |
| P1 | Alert on Argo CD component restarts and OOM kills | Repository owner | Open ([#53](https://github.com/Harsh-Upadhayay/homelab-k8s/issues/53)) | Tested Prometheus alert |
| P2 | Exercise a cold-cache full reconciliation after future Argo CD resource changes | Repository owner | Open ([#53](https://github.com/Harsh-Upadhayay/homelab-k8s/issues/53)) | Runbook evidence with stable memory and zero restarts |

## Lessons and review questions

- A GitOps controller must be sized for recovery bursts, not only steady-state comparisons.
- `connection refused` from a ClusterIP can be a downstream symptom of a repeatedly restarting
  selected pod; inspect last termination state before diagnosing the network.
- Should large Helm applications be refreshed in controlled batches after extended controller
  downtime?

## Evidence

- `argocd-repo-server` restart count: 13.
- Last termination: `OOMKilled`, exit code 137.
- Previous limit/request: 256 MiB/64 MiB.
- Failed operations reported `DeadlineExceeded` and connection refused to
  `argocd-repo-server:8081`.
- Repo-server logs showed concurrent uncached Helm pulls immediately before termination.
