# Homelab Learning Map

Entry point for the revision notes on this build. Written as we go — a section only exists once we've actually run the thing, not before.

Unlike a from-scratch project, this repo already has a decision record: `claude.md` (settled architecture decisions) and `GUIDE.md` (the phase-by-phase build, with the *why* inline). These notes don't duplicate that — they're the layer underneath it: the Ansible/Terraform/Kubernetes/Linux *mechanics* that make each phase's commands actually work. Read `GUIDE.md` for what we're building and why; read these for how the tool itself thinks.

## Start here

- [[Ansible Concepts]] — inventory, playbooks, roles, modules, idempotency
- [[Terraform Concepts]] — providers, resources, state (populated from Phase 3 onward)
- [[Kubernetes Concepts]] — manifests, Helm, CRDs (populated from Phase 9 onward)
- [[Platform Concepts]] — Linux, networking, and infra ideas that don't belong to one tool (Proxmox, systemd, mesh VPNs, DNS-01, etc.)

## Conventions

- **One file per domain**, not one file per concept — same reasoning as the ratelimiter project's concept docs: many small files becomes unreadable, one themed file per tool stays skimmable.
- Each entry: **bold concept name**, a condensed explanation of the mechanism, then a citation of where in this repo it's actually exercised — a `GUIDE.md` phase and/or a file path, e.g. `(GUIDE.md Phase 1 / Phase 12 Part A, ansible/roles/proxmox_host/)`.
- **Adding a concept**: append it to the relevant theme section in the matching file. Only add it once you've actually run the code that exercises it — this is a record of what you've done, not a preview of what's coming.
- Cross-reference with `[[wikilinks]]`; this map is the hub.
