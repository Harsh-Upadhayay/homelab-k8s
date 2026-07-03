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
