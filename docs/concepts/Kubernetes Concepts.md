# Kubernetes Concepts

> Back to [[Homelab Learning Map]] · See also [[Platform Concepts]] · Decisions in [[README|ADR index]]

First real entries here — Phase 12 Part B was the first time this project looked *inside* Kubernetes itself, past just Nodes.

## Helm

**Helm is a package manager for Kubernetes**, the same relationship `apt` has to `.deb` files. The problem it solves: a single "application" (here, the Tailscale Operator) is never one YAML file — it's a Namespace, a Deployment, RBAC objects, CRDs, and a Secret, all needing to be created, upgraded, and removed together as one unit.

**A chart is the package**: `Chart.yaml` (metadata), `values.yaml` (defaults), `templates/` (Kubernetes manifests written as Go templates referencing those values — the same *idea* as Ansible's Jinja2 `template` module, rendering a final config from a template + values, just scaled up to a whole set of interconnected objects instead of one file). A **chart repository** (`helm repo add`) is an HTTP index of available charts, same relationship an apt repo has to `.deb` files.

**`helm upgrade --install` renders, then applies.** It layers overrides on defaults (CLI `--set-string` beats `--values <file>` beats the chart's own defaults), feeds the result through the template engine to produce final, literal YAML (inspectable in isolation via `helm template`, no cluster contact), then applies it to the cluster like `kubectl apply -f` would for many objects at once. It records what it did as a **release** — stored as a `Secret` inside the cluster itself — which is what makes `helm upgrade` a real diff against known state rather than a blind re-apply, and `helm uninstall` able to cleanly remove every object a release created. Visible directly: `sh.helm.release.v1.tailscale-operator.v1` and `.v2` appeared as real Secrets after we fixed a bad OAuth client and re-ran `helm upgrade` — one Secret per revision (`k8s/tailscale/operator-values.yaml`, ADR-0025).

**Release name vs. chart reference are two different things that can look identical.** `helm upgrade --install tailscale-operator tailscale/tailscale-operator` — the first `tailscale-operator` is the release name *we chose*; the second is `<repo-alias>/<chart-name>`. They match here only because that's the sensible name to pick, not because they're the same field.

**`--wait` blocks until resources report ready, not just accepted** — same "submission isn't completion" lesson as Ansible's `wait_for`, Helm's version of it.

**Helm repos are local to the machine's CLI, not synced anywhere.** `helm repo add`/`helm repo update` write to that machine's own local cache (`~/.cache/helm`, `~/.config/helm`) — not to the cluster, and not to any other machine that happens to also run `helm`. Running the repo-add/update steps from one machine (a MacBook, over the tailnet) and the actual `helm install` from a different one (this Linux workstation, which has `k8s/traefik/values.yaml` on disk) failed in an uninformative way: no error, just no pods, no Service, an empty `helm list -n traefik` — because the second machine's Helm had simply never heard of the `traefik/traefik` chart reference it was asked to resolve. Fixed by running `helm repo add`/`update` again, locally, on whichever machine actually runs the install.

**A chart's values schema isn't stable across chart versions, even between routine releases.** The Traefik chart moved `ports.websecure.tls` to `ports.websecure.http.tls` between versions — strict enough that `helm install` refused outright (`additional properties 'tls' not allowed`) — and separately moved `service.type` to `service.spec.type`, a change the schema does *not* enforce, so the old key was silently ignored and the Service defaulted to the chart's own `LoadBalancer` default with zero error, only visible by actually reading `kubectl get svc`'s `TYPE` column rather than trusting the exit code. `helm show values <repo>/<chart>` prints the live, current schema straight from the chart — worth diffing a repo's committed values file against it before trusting that file still means what it meant when it was written (ADR-0027, `k8s/traefik/values.yaml`).

**Helm deliberately does not manage CRD upgrades or deletions.** CRDs ship in a chart's separate `crds/` directory (not `templates/`); `helm install` applies them the very first time, but `helm upgrade`/`helm uninstall` never touch them again on purpose — a routine chart change silently rewriting or deleting a cluster-wide schema is considered too dangerous to automate. The convention this leads to (Traefik included): manage CRDs explicitly, separately from the chart's own lifecycle. `helm show crds <chart>` extracts just that `crds/` content, with zero cluster contact — no install, no templating of anything else in the chart — ready to be piped into `kubectl apply` on its own.

**Server-side apply exists because client-side apply has a hard size ceiling.** Client-side `kubectl apply` (the default) stores the entire last-applied config in an annotation on the object, capped at roughly 256KB — Traefik's CRDs, with their full embedded OpenAPI schemas, are large enough to blow past that and fail outright. `--server-side` instead sends the object to the API server and lets *it* compute the diff and track field ownership per "manager" (the tool that last set that field) — no local annotation, no size limit.

**`--force-conflicts` matters the moment the same apply command runs a second time.** Server-side apply refuses to silently overwrite a field some other manager currently owns. A first install rarely trips this, but re-running the identical `helm show crds | kubectl apply --server-side` command later — e.g. after a chart upgrade changes a CRD field — would otherwise demand manual conflict resolution. `--force-conflicts` declares that this command is always the intended source of truth for these objects, so it should simply take ownership of whatever it specifies.

## Core objects: Namespace, Deployment, ServiceAccount

**A Namespace is a naming/isolation boundary within one cluster** — `kubectl create namespace tailscale` before the Helm install exists so this chart's objects don't collide with `traefik`'s or `cert-manager`'s later. Some charts create their own namespace (`--create-namespace`); this one expects it pre-created.

**A Deployment manages a pod running the actual application** — here, the Operator's own controller process. Nodes were the only Kubernetes object this project had touched directly before this; a Deployment is the first "workload" object.

**A ServiceAccount is an identity *for a pod*, not a human** — every pod runs *as* some ServiceAccount, and RBAC bound to that ServiceAccount determines what its own process can do when it calls back into the API server (which the Operator does constantly, via `client-go`, watching Services/Ingresses/its own CRDs). Confirmed via `kubectl describe pod`: `Service Account: operator`.

## CRDs and the Operator pattern

**A CRD (Custom Resource Definition) extends the Kubernetes API with a new object kind.** Kubernetes isn't a fixed, closed set of types — anyone can define a new one, and the API server will then let you create/list/watch instances of it exactly like built-in types (`Pod`, `Node`). The Tailscale chart installs CRDs like `Connector` and `ProxyClass` — new vocabulary the cluster now understands.

**The Operator pattern = CRD(s) (or existing resources) + a controller that continuously reconciles real-world state to match declared state.** The Operator's pod *watches* the API for things it cares about (Services requesting `loadBalancerClass: tailscale`, its own CRD instances) and *reacts* by creating/managing real tailnet devices. This watch-and-react loop is the general pattern — cert-manager's `Certificate` objects will work identically later.

## Kubernetes's own permission system: RBAC

**Authentication and authorization are strictly separate systems.** Proven concretely: a second device (a MacBook) got a *real* `403 Forbidden` response from the actual API server — meaning authentication (the proxy correctly identified the caller as a specific Tailscale login) had already fully succeeded; only authorization (does that identity have any permissions) failed. Two independent failure modes: one is a connectivity/identity problem, the other is a policy problem — never confuse which one you're debugging.

**Four RBAC object kinds, two axes.** `Role`/`ClusterRoleBinding` define *what can be done*; `RoleBinding`/`ClusterRoleBinding` grant it *to someone*. The namespace-scoped pair (`Role`, `RoleBinding`) only applies within one namespace; the cluster-scoped pair applies everywhere, and is *required* — not just preferred — for non-namespaced resources like `Node`. `kubectl get nodes` structurally cannot be granted via a `RoleBinding`, no matter what it references, because `Node` doesn't belong to any namespace to bind within (ADR-0026).

**`cluster-admin` is a built-in `ClusterRole`, not something this project defined** — ships with every standard Kubernetes install, `*` verbs on `*` resources in `*` API groups, and is the same role k3s's own `system:masters` group (and therefore the original admin `kubeconfig` from Phase 4–6) is already bound to.

**Only `ServiceAccount` is a real object among the three RBAC subject kinds.** `User` and `Group` aren't Kubernetes resources at all — no `kubectl get users` exists — they're just strings asserted by whatever authentication method produced them, which RBAC trusts without further verification. Here, that string arrives via `Impersonate-User: harshupadhayay906@gmail.com`, set by the Tailscale proxy — standard Kubernetes user impersonation, nothing Tailscale-specific about the mechanism itself. The trust boundary is entirely at the authentication step (Tailscale's own device/login verification), outside Kubernetes proper (`k8s/tailscale/api-server-rbac.yaml`, ADR-0026).

**`roleRef` is immutable after a binding is created.** To point a binding at a different role, delete and recreate it — the API rejects in-place edits to that field.

## Debugging: the standard triage flow

**`kubectl get pods` → `describe` → `logs`/`logs --previous`, in that order, each answering a different question.** Watched live against a real crash-looping pod:
- **`get pods`** shows *that* something's wrong: `READY 0/1`, `STATUS: CrashLoopBackOff`, a climbing `RESTARTS` count.
- **`describe pod`** gives *history and context* — the `Events` list (a timeline of scheduling/pulling/starting/backing-off, oldest to newest — usually the first thing worth reading top to bottom), exit codes, mounted volumes, env vars. It told us the container reached `Started` and died 2 seconds later with `Exit Code: 1` — narrowing *where*, not yet *why*.
- **`logs` / `logs --previous`** hold the actual reason, in the container's own stdout/stderr. `--previous` matters specifically once a container has already restarted: the *current* instance's logs might just be an uninformative fresh startup, while `--previous` preserves what the crashed instance actually said. This is where the real, specific, actionable error (`"creating operator authkey: ... (403)"`) was found — `describe` alone never would have surfaced it (ADR-0025).

**`CrashLoopBackOff` is a specific, meaningful state**, not a generic "broken." It means the container has crashed *repeatedly*, and Kubernetes is deliberately waiting with exponential backoff before retrying — distinct from `Error` (crashed once, not yet backing off), `Pending` (not scheduled/starting), or `ContainerCreating` (still pulling/creating).

## Multi-client cluster access via a proxy

**A kubeconfig doesn't have to contain any real Kubernetes credential.** Tested directly: `tailscale configure kubeconfig <name>` on a machine with zero prior kubeconfig created the entire file from scratch — and the resulting `user.token` field was the literal string `"unused"`. All real authentication happens one layer down, at the network level (the caller's Tailscale/WireGuard identity) — `kubectl` doesn't present a Kubernetes-native credential at all; by the time a request arrives at the proxy, the caller's identity is already established.
