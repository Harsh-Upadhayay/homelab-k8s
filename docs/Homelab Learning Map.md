# Homelab Learning Map

Entry point for the revision notes on this build.

The notes are organized into two kinds of document:

- **ADRs** (`adr/`) — architecture decisions, one log per release milestone (v0.1 … v2.0), in standard Status/Context/Decision/Consequences format. Each log opens with a short narrative, so reading one top-to-bottom tells the story of that milestone.
- **Concepts** (`concepts/`) — the Ansible/Terraform/Kubernetes/platform mechanics the decisions rely on, condensed into themed prose. Read the ADRs for the *why*; read Concepts for the *how*.

This is a second, formal decision record alongside `claude.md` (settled decisions, optimized for fast AI context loading) and `GUIDE.md` (the phase-by-phase build, with reasoning inline). The ADRs are the durable, numbered version of the same decisions — `claude.md` and `GUIDE.md` stay as the fast-reading front door; this is where a decision's full Context/Consequences and any later reversal actually lives.

## Start here

- [[README|ADR index]] — every decision by number, with status, in one table
- [[Ansible Concepts]] — inventory, playbooks, roles, modules, idempotency
- [[Terraform Concepts]] — providers, resources, state (populated from Phase 3 onward)
- [[Kubernetes Concepts]] — manifests, Helm, CRDs (populated from Phase 9 onward)
- [[Platform Concepts]] — Linux, networking, and infra ideas that don't belong to one tool

## Decision logs by milestone

- [[v0.1 - Foundation]] — Terraform/Ansible split, version pinning, secrets deferred, Proxmox host automation (ADR-0001–0007)
- [[v0.2 - Cluster Bootstrap]] — embedded etcd, secrets-encryption, control-plane taint, Flannel/Cilium (ADR-0008–0012)
- [[v0.3 - Ingress and TLS]] — Traefik ClusterIP, IngressRoute vs. Gateway API, cert-manager scope (ADR-0013–0015)
- [[v0.4 - Public and Private Access]] — Cloudflare Tunnel routing, cloudflared NetworkPolicy, Tailscale's two mechanisms (ADR-0016–0018)

## Conventions

- **One ADR log per milestone**, not one file per decision — same reasoning as the ratelimiter project's version logs: many small files becomes unreadable, one themed file per milestone stays skimmable. A new log starts only when a new milestone actually produces a decision.
- **One Concepts file per domain** (Ansible/Terraform/Kubernetes/Platform), each entry a bold concept name, a condensed explanation, then a citation of where it's exercised in this repo (a `GUIDE.md` phase, an ADR number, and/or a file path).
- **Adding a decision**: append an `## ADR-00NN — Title` section (Status/Context/Decision/Consequences) to the current milestone's log, and add a row to `adr/README.md`. To reverse a past decision, add a **new** ADR and set the old one's status to `Superseded by 00NN` — never edit a decision away.
- **Adding a concept**: append it to the relevant theme section in the matching Concepts file. Only add it once the code that exercises it has actually run — Concepts is a record of what you've done, not a preview of what's coming. ADRs are the exception: a decision counts once it's committed to code, even before that code has actually run against a live host.
- Cross-reference everything with `[[wikilinks]]`; this map is the hub.
