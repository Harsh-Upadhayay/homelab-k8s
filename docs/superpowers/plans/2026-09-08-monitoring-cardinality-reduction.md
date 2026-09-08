# Plan — Cut monitoring footprint (Prometheus cardinality + Grafana dashboards)

## Context

`k3s-worker-1` has been overloaded for days. Root cause: Prometheus holds
**335,825 head series** inside a **2Gi** `memory.max`. The head does not fit, so
mmap'd TSDB chunk pages are evicted and immediately re-faulted
(`workingset_refault_file` = 1.07 billion, `pgscan_direct` = 1.09 billion).

Prometheus's PVC is a **Longhorn** volume, so every refault becomes an iSCSI
round-trip: the Longhorn engine serving
`pvc-4aeb6dfe-8a93-48bd-9711-98cefee03cc8` is now the node's top CPU consumer
(64% CPU, 44 cumulative hours). Grafana showed the same failure at 512Mi with
its bleve index (21.3M refaults). Everything else on the node — notably the
`workbench/devbox` pod the user works in — is starved behind this.

Owner requirements (explicit):
- **Do not increase CPU/RAM.** Reduce it.
- Rarely looks at dashboards; does not want or understand detailed metrics.
- **Does** want long-term graphs for server status / health / load / connections.

## Measured evidence

Series per scrape job:

| job | series | note |
|---|---|---|
| kubelet | 92,626 | cAdvisor bulk |
| kube-scheduler | 69,474 | **duplicate** — k3s single process |
| kube-controller-manager | 69,459 | **duplicate** — k3s single process |
| apiserver | 49,159 | ~all histogram buckets |
| kube-state-metrics | 14,957 | keep |

`_bucket` series = 200,143 = **60% of head** (top-40 metric names alone).

All of `apiserver` (:6443), `kube-controller-manager` (:10257),
`kube-scheduler` (:10259) and `kube-etcd` (:2381) target `192.168.1.21`. On k3s
these are ONE process sharing ONE metrics registry — ports 10257/10259 re-serve
the same `apiserver_*` and `etcd_*` metrics already collected under `apiserver`.
138,933 series (41% of the database) are literal duplicates.

## Global Constraints

- **Single file for all edits:** `k8s/monitoring/values-kube-prometheus-stack.yaml`.
  No other file is modified by any task.
- **Comment in the repo's house style.** Every block gets a comment explaining
  the *reasoning*, not the *what* — matching the existing comments in this file
  (see the `kubeControllerManager` block at ~line 325). Future-you must be able
  to see WHY and not naively re-enable it.
- The existing `kubeControllerManager` / `kubeScheduler` / `kubeEtcd` `endpoints`
  blocks were deliberate work for issue #25. Do **not** delete them — set
  `enabled: false` and leave the endpoint config plus a comment recording that
  it was correct work, superseded by the discovery that k3s serves one registry.
- Never bump any `limits` or `requests` upward. This whole change exists to
  reduce them.
- Do not touch `k8s/monitoring/values-loki.yaml` or `values-alloy.yaml`.
- YAML must remain valid and Helm-renderable. Verify with a parser, not by eye.
- Do not run `helm`, `kubectl apply`, or `argocd sync`. Commit only — the
  controller handles rollout and validation.

## Task 1 — Stop collecting duplicate and unused series

Edit `k8s/monitoring/values-kube-prometheus-stack.yaml`.

**1a. Disable the duplicate control-plane jobs.** In the existing
`kubeControllerManager:` and `kubeScheduler:` blocks (~lines 330 and 338), add
`enabled: false` as the first key of each, keeping the existing `endpoints:` and
`serviceMonitor:` keys in place beneath it. Add a comment above BOTH blocks
explaining: k3s runs apiserver/scheduler/controller-manager in a single process
sharing one metrics registry, so :10257 and :10259 re-serve the same
`apiserver_*`/`etcd_*` series already scraped from :6443 — 138,933 duplicate
series, 41% of the database, for zero extra information. Note the endpoint
config is retained because a future non-k3s or HA control plane would need it.

Leave `kubeEtcd:` **enabled** — :2381 is a genuinely separate etcd listener.

**1b. Drop histogram buckets.** Histogram `_bucket` series exist only to compute
percentiles, which this platform's owner does not use; `_sum` and `_count`
survive, so rates and averages still work. Add to `kubeApiServer`, `kubelet`, and
`kubeEtcd`:

```yaml
kubeApiServer:
  serviceMonitor:
    metricRelabelings:
      - action: drop
        sourceLabels: [__name__]
        regex: ".*_bucket"

kubeEtcd:
  # (keep the existing endpoints block)
  serviceMonitor:
    metricRelabelings:
      - action: drop
        sourceLabels: [__name__]
        regex: ".*_bucket"
```

**1c. Trim cAdvisor and kubelet.** `kubelet` is the largest remaining job
(92,626 series). Keep CPU / memory / network / filesystem per container; drop
the rest.

```yaml
kubelet:
  serviceMonitor:
    cAdvisorMetricRelabelings:
      - action: drop
        sourceLabels: [__name__]
        regex: "container_(tasks_state|memory_failures_total|blkio_device_usage_total|memory_(mapped_file|swap|failcnt)|file_descriptors|threads.*|ulimits_soft|sockets|processes|last_seen|start_time_seconds|spec_.*)"
      - action: drop
        sourceLabels: [__name__]
        regex: ".*_bucket"
    metricRelabelings:
      - action: drop
        sourceLabels: [__name__]
        regex: ".*_bucket"
```

**1d. Disable rule groups that depend on dropped buckets.** kube-prometheus-stack
ships alerting rules using `histogram_quantile()` over the series 1b removes, and
recording/alerting rules for the scrape jobs 1a disables. Left enabled they
silently never fire or evaluate against nothing. Add a `defaultRules:` block
(create it if absent) with a comment stating each is disabled *because its input
series are no longer collected*:

```yaml
defaultRules:
  rules:
    kubeApiserverBurnrate: false
    kubeApiserverHistogram: false
    kubeApiserverSlos: false
    kubeControllerManager: false
    kubeSchedulerAlerting: false
    kubeSchedulerRecording: false
    etcd: false
```

**Verify:** parse the file with `python3 -c "import yaml,sys; yaml.safe_load(open(...))"`
and confirm it loads; then assert with a script that
`kubeControllerManager.enabled is False`, `kubeScheduler.enabled is False`,
`kubeEtcd` has no `enabled: false`, and each of the four `metricRelabelings` /
`cAdvisorMetricRelabelings` lists is present and non-empty. Paste the command
and its real output into the report.

## Task 2 — Prune dashboards, shrink limits, extend retention

Edit the same file.

**2a. Prune Grafana dashboards.** 28 default dashboards are loaded; they are the
direct cause of Grafana's oversized bleve search index. Most are for components
Task 1 stops collecting (`apiserver`, `scheduler`, `controller-manager`, `etcd`,
`proxy`), for platforms that do not exist here (`nodes-aix`, `nodes-darwin`), or
for a multi-cluster setup that does not exist (`k8s-resources-multicluster`).

In the `grafana:` block set `defaultDashboardsEnabled: false`, with a comment
explaining the index cost and that most defaults would render empty panels after
Task 1 anyway.

Then, in the EXISTING `grafana.dashboards` structure that already provides
`gnetId: 24178` (~line 303), add **Node Exporter Full** alongside it, following
the exact same key layout as the existing entry:

```yaml
      # gnetId 1860 "Node Exporter Full" — the one dashboard that covers the
      # owner's actual ask: per-server CPU, memory, disk, network and connection
      # counts, over time. Node-exporter series are cheap and untouched by the
      # Task 1 cardinality cuts, so this keeps working long-term.
      gnetId: 1860
      revision: 37
      datasource:
        - name: datasource
          value: prometheus
```

Match the surrounding indentation and key order exactly; do not restructure the
existing 24178 entry.

**2b. Reduce the Prometheus memory limit.** Find the Prometheus `resources`
block. Set `limits.memory: 1Gi` (down from `2Gi`). Leave `requests` alone.
Comment it with the sizing logic: ~65k head series after Task 1 at ~3-5KB/series
is ~200-320Mi of head, so 1Gi leaves real headroom rather than sitting just above
the working set — and record that a limit set *just above* the working set is
what produced this incident (silent thrash instead of a loud OOMKill).

**Do not change Grafana's 512Mi limit.** Pruning dashboards should stop its
thrash, but there is no measurement yet proving it survives a lower ceiling, and
shaving a limit on a guess is the exact mistake being fixed. Add a comment saying
so.

**2c. Extend retention.** Change `retention: 15d` (~line 40) to `retention: 90d`.
Comment the reasoning: series count drives RAM, series×time drives disk; cutting
series ~5x means 90 days now costs about what 15 days cost before. Fewer metrics
is what makes long-term graphs affordable.

**Verify:** re-parse the YAML; assert `grafana.defaultDashboardsEnabled is False`,
that both gnetId 24178 and 1860 are present, that the Prometheus memory limit
string is `1Gi`, that Grafana's is still `512Mi`, and that retention is `90d`.
Paste the command and its real output into the report.
