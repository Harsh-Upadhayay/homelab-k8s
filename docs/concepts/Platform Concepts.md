# Platform Concepts

> Back to [[Homelab Learning Map]] · See also [[Ansible Concepts]] · Decisions in [[README|ADR index]]

Linux, networking, and infra ideas that don't belong to one specific tool — the "any other concept" bucket.

## SSH

**`~/.ssh/config` `Host` aliases are local, not portable.** A `Host pve-dell` block with `HostName`/`User`/`IdentityFile` lets *you* type `ssh pve-dell` from *your* machine — but that alias lives entirely in your dotfiles, not in DNS or the repo. Using it as `ansible_host` in a committed inventory file makes the automation silently depend on one person's local config; anyone else (or CI, or a future you on a new laptop) would fail to resolve the host. The fix: commit the real, resolvable value (an IP here) to the inventory, and let personal SSH aliases stay personal (ADR-0007, `ansible/inventory.ini`).

**`IdentitiesOnly yes` restricts which keys SSH will offer**, but includes both the config's `IdentityFile` *and* any key passed explicitly via `-i`/`ansible_ssh_private_key_file` — both are "explicitly configured," not just the config file's. Worth knowing before assuming a mismatched key will hard-fail rather than just also getting tried.

## Package management

**deb822 `.sources` format vs. legacy `.list`.** Debian 13 (Trixie) / Proxmox VE 9 ship apt sources as deb822 stanzas (`Types:`/`URIs:`/`Suites:`/`Components:`/`Signed-By:`/`Enabled:`) rather than the older one-line `deb <url> <suite> <components>` format. The `Enabled: false` field is what actually disables a repo — the file doesn't need to be deleted or commented out (`ansible/roles/proxmox_host/tasks/repos.yml`, confirmed by inspecting `/etc/apt/sources.list.d/*.sources` on `pve-dell` directly over SSH).

**A repo needs both a source file and a trusted signing key.** Adding `pkgs.tailscale.com`'s apt repo means fetching its GPG keyring (`get_url` → `/usr/share/keyrings/tailscale-archive-keyring.gpg`) *and* pointing the `.sources` file's `Signed-By:` at that exact file — apt won't trust packages from a repo whose key it doesn't have, regardless of the source line being correct (`ansible/roles/proxmox_host/tasks/tailscale.yml`).

## Mesh VPNs (Tailscale)

**A tailnet is a private mesh network, not a hub-and-spoke VPN.** Each device gets a stable IP in the `100.64.0.0/10` (CGNAT) range and can reach every other authorized device directly (NAT traversal via WireGuard, brokered by Tailscale's coordination servers) — no central VPN server to run, no port to forward on the router. Confirmed hands-on: `pve-dell` got `100.111.162.64` and was immediately reachable from other tailnet devices, with zero firewall/router changes (ADR-0018, `tailscale status` on `pve-dell`).

**Auth keys are single-use onboarding credentials, not ongoing credentials.** A `tskey-auth-...` key authenticates a device *once*, at `tailscale up` time; after that the device has its own persistent node identity and the key can be discarded. That's why it's safe to pass via `--extra-vars` at runtime and never store — same treatment as any other bootstrap-only secret in this repo (the k3s join token is the other example).

**Why this beats exposing a service directly**, even to "just the LAN": a tailnet device is only reachable by other *authenticated* devices on that same tailnet — compare to opening a port on the router (reachable by the whole internet) or even a LAN-only service (reachable by anything that gets onto that LAN, e.g. a compromised IoT device). This is the reasoning behind using Tailscale for the Proxmox UI/SSH and, later, `kubectl`/dashboards, instead of any LAN-exposed alternative.

**OAuth client scopes are narrower than they sound, and can't be edited after creation.** The Kubernetes Operator needs three separate scopes together — Devices Core, Auth Keys, and General/Services — not just the one ("Devices Core") that sounds sufficient for "manage devices." Confirmed the hard way: a client with only Devices Core produced a 403 specifically on *auth key creation*, since the Operator creates auth keys at runtime for itself and for every proxy pod it later spins up. Tailscale doesn't allow adding a missing scope to an existing client — only generating a new one (ADR-0025).

**Tailscale's admin console restructured OAuth client creation** — it's no longer under a "Settings → OAuth clients" page; it now lives under **Trust Credentials → Credential → OAuth**. Worth knowing that admin-console navigation is not a stable API — verify current paths against live docs/search rather than trusting a prior session's memory of the UI.

**On macOS, the Homebrew *formula* (`brew install tailscale`) is not the same as the *cask*.** The plain formula installs bare open-source `tailscaled` binaries without macOS's network extension integration — Tailscale's own docs call it "advanced users only." Full functionality needs either the Standalone `.pkg` from tailscale.com, the Mac App Store app, or `brew install --cask tailscale-app` (the cask, not the formula) — and never mix the Standalone and App Store variants on one machine, they conflict.

**A kubeconfig's `user` block doesn't have to contain a real credential.** Tested directly on a machine with zero prior kubeconfig: `tailscale configure kubeconfig <operator-hostname>` created the entire file from scratch, with `user.token` set to the literal string `"unused"`. The real authentication is the caller's Tailscale network identity, established before the request ever reaches the proxy — the kubeconfig only needs to say *where* to send requests, not *prove who's sending them* (see [[Kubernetes Concepts]] for the authorization half of this).

**Quad-100 patches a public CNAME chain with its own MagicDNS state — no public record needed.** The plan for `*.in.neovara.uk` assumed the CNAME target (`traefik-internal.egret-pence.ts.net`) needed to be publicly resolvable, via Tailscale publishing machine names in public ts.net DNS when the tailnet's HTTPS-certificates feature is enabled. Empirically that public record is **NXDOMAIN** — never published, even with HTTPS certs on and well past the documented ~10-minute propagation — yet on-tailnet resolution works anyway, by a better mechanism: a tailnet device's resolver is Tailscale's `100.100.100.100` (quad-100); it forwards the `whoami.in.neovara.uk` query upstream, gets back a CNAME whose target is a MagicDNS name *quad-100 itself owns*, and fills in the A record from its own live state. Verified from both sides: on-tailnet, curl resolved to `100.79.208.52` and got a 200; off-tailnet, `curl: (6) Could not resolve host` (`dig @1.1.1.1` shows the CNAME in the answer but overall NXDOMAIN — the public chain dies at the unpublished ts.net hop). Two consequences: (1) **resolve == reach** — the set of clients that can resolve an internal name equals the set that can reach its IP; off-tailnet the names are invisible, not merely unreachable, and no CGNAT IP leaks in practice. (2) It's more **self-healing than a hardcoded A record**: Tailscale 100.x IPs are stable per device *registration* (not per connection), but deleting/recreating an operator-managed Service registers a *new* device with a new IP — an A record would go silently stale on every rebuild, while quad-100 always knows the current IP, provided the hostname stays pinned via `tailscale.com/hostname` *and* the stale device is deleted in the admin console (else the new one comes up suffixed `traefik-internal-1` and the CNAME breaks). Caveat, recorded not fixed: a tailnet device that bypasses Tailscale DNS (custom resolver) can't resolve `*.in` names — acceptable for a single operator with default clients (ADR-0029).

**Two front doors, one Traefik — and the headers fingerprint which door a request used.** The same whoami pod is served through both access paths: publicly via cloudflared (`whoami.neovara.uk` — TLS terminated at Cloudflare's edge with the `*.neovara.uk` Google Trust Services cert, so Traefik sees plaintext and the app sees `X-Forwarded-Proto: http`, `X-Forwarded-Port: 80`), and internally via the Tailscale proxy (`whoami.in.neovara.uk` — TLS terminated by Traefik itself with the cert-manager `*.in.neovara.uk` wildcard, so the app sees `X-Forwarded-Proto: https`, `X-Forwarded-Port: 443`). Same pod, two front doors, cleanly distinguishable at the app by those headers — verified in one session with both curls returning 200 (`k8s/traefik/tailscale-service.yaml` vs `k8s/cloudflared/`, ADR-0018/ADR-0029, GUIDE.md Phase 12 Part C).

**The two complete request paths, hop by hop — every hop labeled with its mechanism.** Both verified live; each path uses *both* load-balancing mechanisms in sequence (the classic kernel path to reach Traefik, then Traefik's direct endpoint dialing to reach the app — see [[Kubernetes Concepts]]).

*Public* (`https://whoami.neovara.uk`):
```
client → public DNS (proxied CNAME → Cloudflare anycast IPs)
       → Cloudflare edge  [TLS TERMINATED HERE — *.neovara.uk Universal SSL cert]
       → tunnel (edge → cloudflared's held-open OUTBOUND connection; no inbound port anywhere)
       → cloudflared pod → dials traefik.traefik.svc.cluster.local:80
             [CoreDNS → ClusterIP; node-kernel DNAT :80→pod:8000 — L4, per-connection]
       → Traefik "web" entrypoint → router (web, Host whoami.neovara.uk)
       → reads whoami's EndpointSlices → dials a pod IP :80 directly  [L7, per-request]
       → whoami pod  (sees X-Forwarded-Proto: http, Port: 80, Cf-Connecting-Ip)
```

*Internal* (`https://whoami.in.neovara.uk`):
```
tailnet client → quad-100 resolver (CNAME *.in.neovara.uk → traefik-internal.<tailnet>.ts.net,
                 A record patched from live MagicDNS state — resolves ONLY on-tailnet)
       → WireGuard to the operator's proxy pod (the traefik-internal tailnet device, 100.x)
       → proxy forwards to Service traefik-internal:443
             [node-kernel DNAT :443→pod:8443 — L4, per-connection]
       → Traefik "websecure" entrypoint  [TLS TERMINATED HERE — *.in.neovara.uk
             Let's Encrypt wildcard via the default TLSStore]
       → router (websecure, Host whoami.in.neovara.uk)
       → reads whoami's EndpointSlices → dials a pod IP :80 directly  [L7, per-request]
       → whoami pod  (sees X-Forwarded-Proto: https, Port: 443)
```

The symmetry to remember: each door has exactly one component holding an outbound-only link to its network (cloudflared → Cloudflare's edge; the ts-proxy → the tailnet), both converge on the same Traefik routing table two coordinates apart (entrypoint × Host), and the app is identical and unaware behind both (ADR-0016/0018/0028/0029).

## Proxmox templates and cloud-init

**A template is inert, not running.** After `qm template <id>`, the VM can never be started again — no vCPU/RAM allocated, no QEMU process. It's just a disk plus a config file with a `template: 1` flag, sitting on storage purely as a clone source (GUIDE.md Phase 2, confirmed on `pve-dell`: the Start button disappears and `qm start 9000` fails).

**Cloud images are pre-installed disks built for unattended customization**, not interactive installers — the entire reason cloud-init exists. Every major cloud provider uses the same image format for the same reason: you can't click through an installer for a VM spun up via API (GUIDE.md Phase 2).

**`virt-customize` edits a disk image offline, before any VM ever boots from it.** Needed because two changes (installing `qemu-guest-agent`, truncating `/etc/machine-id`) have to land *before* the image is cloned — editing a running VM afterward would mean repeating the change on every clone instead of once, on the shared source (GUIDE.md Phase 2).

**Resetting `/etc/machine-id` before cloning avoids duplicate identity across every clone.** A cloud image customized with a real machine-id already generated would hand that same ID to every VM cloned from it — colliding DHCP leases and confusing systemd/journald/D-Bus, all of which assume the ID is unique per install. Truncating it to empty makes each clone regenerate its own on first boot (GUIDE.md Phase 2).

**`qemu-guest-agent` needs two independent halves to actually work.** The daemon must be installed *inside* the guest (not present by default — added via `virt-customize`), and Proxmox must separately be told to expect it (`agent: enabled=1` on the VM config). Either half alone does nothing; Proxmox won't show live guest IP/status until both are true — confirmed via `qm config 9000` showing `agent: enabled=1` (GUIDE.md Phase 2).

**A template's disk size is not a clone's disk size.** The template holds whatever the raw cloud image actually is — confirmed at 3.50GB here (`lvs` on `pve-dell`) — and stays that size forever. Each clone independently resizes up to its own real target (`disk.size` in `terraform/main.tf` — 60GB/150GB) at clone time; the *partition* grows immediately, but the filesystem inside only expands to fill it once cloud-init's growpart/resizefs modules run on that clone's first boot.

**Full clone vs. linked clone is a real tradeoff, not just a flag.** `full = true` (used here) gives every clone its own independent disk copy — costs more storage, but means the template can later be deleted or rebuilt without any risk to VMs already cloned from it. A linked clone would save space via copy-on-write, at the cost of tying every clone's lifetime to the template disk's continued existence (`terraform/main.tf`).

**The cloud-init drive is a separate, synthetic volume — not a copy of anything.** Confirmed with real numbers on `pve-dell`: the OS disk is 3.50GB, the cloud-init volume is 4.00MB (`lvs`) — nearly 900x smaller, so it physically cannot contain a copy of the OS disk. `qm set --ide2 local-lvm:cloudinit` has no `import-from`; the `cloudinit` keyword tells Proxmox to manufacture a brand-new, empty volume from scratch, with zero relationship to the downloaded cloud image file.

**"Cloud-init" means two different things, easy to conflate.** (1) The *program* — systemd services already installed and enabled inside the guest OS, present by default in a cloud image (contrast `qemu-guest-agent`, which isn't). (2) The *seed drive* — external configuration data that program reads at boot. The image ships with (1) built in; Proxmox generates (2) separately, from the VM's own config, with no relationship to the OS disk's contents.

**Proxmox's cloud-init keys are a simplified layer over cloud-init's own format.** `ipconfig0`, `sshkeys`, `ciuser`, `nameserver` live directly on the VM's Proxmox config and get translated into the real NoCloud `user-data`/`meta-data` format inside the generated seed volume. This is a subset of cloud-init's full, provider-agnostic spec (which also covers `packages:`, `runcmd:`, etc.) — the escape hatch for the full spec is `cicustom` (a hand-authored snippet file), not used in this repo.

**Cloud-init values are set per-clone, never on the template.** The template's own cloud-init volume has no `ipconfig0`/`sshkeys`/`ciuser` set — confirmed via `qm config 9000` — because those get set later by Terraform's `initialization` block on each *clone's* own config. Only then does Proxmox regenerate that clone's seed volume from them, right before it boots (`terraform/main.tf`).

**LVM-thin volumes vs. directory-based files.** On `local-lvm`, both the OS disk and the cloud-init drive are separate logical volumes (`base-9000-disk-0`, `vm-9000-cloudinit`), not flat files on a filesystem you could `ls` — contrast with directory-backed storage, where the same cloud-init drive would materialize as an actual `.iso` file you could inspect directly.

## Proxmox access control (pveum)

**VM privileges and storage privileges are two entirely separate buckets.** `pveum role list` shows `PVEVMAdmin` carries `VM.Clone`, `VM.Config.*`, `VM.PowerMgmt`, etc. — zero `Datastore.*` privileges. `PVEDatastoreUser` carries exactly `Datastore.AllocateSpace` + `Datastore.Audit`. A role that sounds comprehensive ("VM *Admin*") doesn't imply it covers a cross-cutting resource like storage — cloning a VM needs both buckets granted, since cloning has to allocate space on the target datastore as part of the operation. Caught the hard way: `terraform apply` failed on all three VMs with `HTTP 403 - Datastore.AllocateSpace` despite the token already having `PVEVMAdmin` at `/` (ADR-0023).

**ACLs are scoped by path, and grants at different paths are additive.** `pveum aclmod / -user X -role A` and `pveum aclmod /storage/local-lvm -user X -role B` are two independent grants that both apply — Proxmox doesn't require one "complete" role per user. This makes least-privilege scoping natural: grant broad VM operations at `/`, but narrow storage operations to only the specific pool actually used, rather than reaching for `PVEDatastoreAdmin` (which also grants `Datastore.Allocate`/`Datastore.AllocateTemplate` — creating storage and uploading templates, neither of which Terraform does here).

**Network attach is a *third* separate privilege bucket: `SDN.*`.** Attaching a VM's NIC to a bridge needs `SDN.Use`, checked against an implicit path `/sdn/zones/<zone>/<bridge>` — even when no SDN zones/vnets are actually configured (`pvesh get /cluster/sdn/zones` returning `[]`). Proxmox VE evidently evaluates every bridge attachment through the SDN permission model regardless of whether SDN is meaningfully "in use." One VM-clone operation (with cloud-init networking) therefore spans all three buckets — VM, Datastore, SDN — discoverable only by hitting each 403 in sequence and cross-checking `pveum role list` (ADR-0023, ADR-0024).

## Cloudflare Tunnel and edge TLS

**A Cloudflare Tunnel is an *outbound* reverse tunnel — nothing inbound is ever opened.** `cloudflared` runs inside the cluster and dials *out* to Cloudflare's edge, holding the connection open; public traffic then flows edge → down that connection → cluster. No router port-forward, no exposed home IP, works behind CGNAT (outbound always works). Same outbound-only posture as Tailscale, but for the *public* audience (ADR-0016, GUIDE.md Phase 11). Confirmed end to end: `https://whoami.neovara.uk` → internet → edge → tunnel → cloudflared → Traefik:80 returned the *identical* 404 to hitting Traefik directly in-cluster.

**Remotely-managed (token) vs. locally-managed (config file).** We use remotely-managed: the tunnel and its routing live in the Cloudflare dashboard, and the connector pulls config at runtime from a single `TUNNEL_TOKEN` (injected from a Secret) — the Deployment holds no routing config at all. cloudflared reaches the edge on port **7844** (UDP for QUIC by default, TCP for the http2 fallback — the NetworkPolicy must allow *both*); 443 is optional (updates/PQ). Healthy signs: ~4× `Registered tunnel connection` in the logs (four edge datacenters = redundancy) and the dashboard flipping **Healthy** — but "Healthy" only means the connector linked to the edge; it says nothing about DNS or TLS being correct (both bit us next).

**Cloudflare's free Universal SSL covers only the root + ONE wildcard level.** The edge cert's SAN is literally `neovara.uk, *.neovara.uk` — so `whoami.neovara.uk` (first-level) gets a valid cert, but `whoami.k8s.neovara.uk` (two levels) gets **none**: its TLS handshake dies at the edge with `sslv3 alert handshake failure`, *before* the tunnel is ever consulted. Covering a second-level wildcard (`*.k8s.neovara.uk`) needs the paid Advanced Certificate Manager. This is the "deep-subdomain Cloudflare Tunnel trap" — a nested scheme that looks clean silently breaks free public TLS (ADR-0028). Internal `*.in.neovara.uk` is *unaffected*: it uses cert-manager's *Let's Encrypt* cert over Tailscale, and Let's Encrypt does issue multi-level wildcards — Cloudflare's edge isn't in that path.

**Remotely-managed tunnel config isn't instantly consistent — cloudflared polls config from the API, and dashboard edits can silently not propagate.** Evidence from Phase 13: the tunnel's ingress JSON (visible in `kubectl logs -n cloudflare | grep 'Updated to new configuration'`) showed the stale `k8s.neovara.uk` hostname long after the dashboard read `whoami.neovara.uk` — every request for `whoami.neovara.uk` hit cloudflared's catch-all `http_status:404`. Fix: **delete and re-create** the public hostname rather than editing it; a fresh create reliably forces a new config version. The Tunnel "Healthy" status only means cloudflared's *connection* to the edge is live — it says nothing about whether the config it holds is current. Same lesson from two domains now: don't trust a green status ping to imply correctness; verify expected state against live output (`kubectl logs` / `curl`).

**A more specific DNS record overrides a wildcard — the basis for app-by-app migration.** A legacy homelab owns `*.neovara.uk → old router`; adding a *specific* `whoami.neovara.uk → tunnel` (proxied CNAME) beats the wildcard for just that name, cutting one app to k8s while everything else still hits the old box — reversible by deleting the record. Two things that surprised: proxied *wildcards* are now available on **all** plans (no longer Enterprise-only — verified against live docs), and adding a **specific** public hostname to a tunnel **auto-creates** its DNS record, while a **wildcard** public hostname does **not** (you hand-create the `*` record). End-state, once the legacy wildcard retires: a single `*.neovara.uk` tunnel route (first-level → free Universal SSL), after which a new public app needs only an `IngressRoute` — zero Cloudflare/DNS change per app (ADR-0028).
