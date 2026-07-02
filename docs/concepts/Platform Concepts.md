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
