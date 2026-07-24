# Homelab Kubernetes Platform — Build Guide

End-to-end foundation setup: Proxmox → Terraform-provisioned VMs → Ansible-configured k3s → Traefik → cert-manager → Cloudflare Tunnel → Tailscale. GitOps (Argo CD), Longhorn, monitoring, and logging were originally follow-on phases and are now live; their current state is summarized in `ROADMAP.md` and recorded in the v2.0 ADRs. Secrets management (SOPS/age, later External Secrets/Vault) and the full backup strategy remain deferred.

**Companion files referenced throughout live alongside this guide:**
```
homelab-k8s/
├── GUIDE.md                 ← this file
├── terraform/                provisions the current three VMs on Proxmox
├── ansible/                  configures OS + installs k3s, reproducibly
└── k8s/                      GitOps-managed platform and application manifests
    ├── argocd/
    ├── traefik/
    ├── cert-manager/
    ├── cloudflared/
    ├── tailscale/
    ├── longhorn/
    ├── monitoring/
    ├── argo-rollouts/
    ├── apps/
    └── example-app/
```

Versions pinned at the time of writing (verify current before you install — links included):
| Component | Version pinned in this guide |
|---|---|
| Proxmox VE | 9.2 (Debian 13 "Trixie") |
| Ubuntu Server | 26.04 LTS "Resolute Raccoon" (5yr support to 2031) |
| Terraform provider `bpg/proxmox` | `~> 0.111` |
| k3s | `v1.36.2+k3s1` |
| Traefik Helm chart | `41.0.1` |
| cert-manager | `v1.20.3` |
| cloudflared | `2026.6.1` |
| Tailscale Kubernetes Operator | Helm chart `1.98.4` |

---

## 0. Prerequisites

**Hardware assumption** (adjust sizing to your actual box): 8+ cores, 32–64GB RAM, and the fastest storage you can give the control-plane VM — etcd is fsync-latency sensitive, so internal NVMe is the ideal. **This deployment's reality (ADR-0022): `pve-dell` is a laptop whose internal NVMe holds Windows and personal data and is strictly off-limits — everything, etcd included, runs from an external 1TB USB SSD.** That's a conscious tradeoff: USB bridges add fsync latency headroom risk, so if etcd ever shows leader-election flakiness, the disk is the first suspect (see Troubleshooting), and the long-term fix is dedicated physical nodes, not that NVMe.

**On your local workstation** (the machine you'll run Terraform/Ansible/kubectl from — does not need to be the Proxmox host itself):
```bash
# Terraform (or OpenTofu, drop-in compatible, fully open-source — either works)
# macOS: brew install terraform
# Linux: see https://developer.hashicorp.com/terraform/install

# Ansible
# macOS: brew install ansible
# Linux (Debian/Ubuntu): sudo apt install ansible

# kubectl + helm
# macOS: brew install kubectl helm
# Linux: see https://kubernetes.io/docs/tasks/tools/ and https://helm.sh/docs/intro/install/
```

**Accounts needed:** a Cloudflare account with your domain's nameservers already pointed at Cloudflare, and a Tailscale account (free tier is fine for a homelab tailnet).

**A note on placeholders:** this guide uses `neovara.uk` as the example domain, `192.168.1.0/24` as the example LAN, and `.21`/`.22` as example host IPs throughout — matching values used earlier in this conversation. Swap in your own before running anything.

---

## Phase 1 — Proxmox host

If Proxmox isn't installed yet: download the current ISO from `proxmox.com/en/downloads`, verify the SHA256, write it to a USB drive (Rufus in **DD mode** on Windows, `dd`/Etcher on Linux/macOS — UNetbootin does not work with this ISO), boot from it, and run through the graphical installer. Target disk gets wiped entirely, so double-check you're pointing at the right one. Set a static IP for the management interface during install; you'll SSH into this box constantly.

After first boot, the web UI is at `https://<proxmox-ip>:8006`. Log in as `root`.

**One thing that has to happen manually, before anything else can be automated:** enable SSH key access to the Proxmox host itself (Terraform's provider needs SSH for some operations like file uploads, and Ansible needs it to run at all — this is the one bootstrap step that can't automate itself into existence):
```bash
# from your workstation, generate a key if you don't have one
ssh-keygen -t ed25519 -f ~/.ssh/proxmox_ed25519 -C "homelab-proxmox-admin"
ssh-copy-id -i ~/.ssh/proxmox_ed25519.pub root@<proxmox-ip>
```

**Verify which physical device backs your storage pool** — `pvs` and `lsblk` on the host, or Datacenter → Storage in the UI. Confirm two things now, not after etcd is already unhappy: (1) `local-lvm` sits entirely on the intended disk (here: the external SSD, `/dev/sda3` as the only LVM PV), and (2) the off-limits internal NVMe appears in **no** volume group and nowhere in `/etc/pve/storage.cfg` (ADR-0022). Left as a manual, one-time check against the live host rather than scripted.

**Normal host configuration is codified** once the SSH key step above has
landed. A default run applies hardware safeguards first, configures the APT
repositories, and maintains stable LAN hostname mappings. It deliberately does
not run package upgrades, Tailscale enrollment, or API-token creation:

```bash
cd ansible
ansible-playbook proxmox.yml
```

Package maintenance is a separate, explicit operation. The upgrade fails
closed unless every hardware-specific package hold is already present:

```bash
ansible-playbook proxmox.yml --tags maintenance-upgrade
```

This is separate from `site.yml` because it targets the hypervisors, not the
k3s guests. Fresh-install bootstrap, protected-disk preflights, Proxmox cluster
create/join, and ASRock's installed-first-boot NIC repair remain tracked in
GitHub issues #43, #45, #46, and #47; a successful default play must not be
mistaken for zero-manual-step disaster recovery.

---

## Phase 2 — Build the Ubuntu cloud-init template

Terraform **clones** VMs from a template — it does not run the OS installer. So the one manual, one-time step is building that template. Run this **on the Proxmox host** (SSH in, or use the Shell in the web UI).

```bash
# Install the tool that lets us inject packages into the cloud image before first boot
apt update && apt install -y libguestfs-tools

# Grab the current Ubuntu 26.04 LTS cloud image
cd /var/lib/vz/template/iso
wget https://cloud-images.ubuntu.com/resolute/current/resolute-server-cloudimg-amd64.img

# Bake in qemu-guest-agent so Proxmox can see the VM's IP/status once it's running
virt-customize -a resolute-server-cloudimg-amd64.img --install qemu-guest-agent
# Reset the machine-id so every clone doesn't share the same one (breaks DHCP leases / systemd otherwise)
virt-customize -a resolute-server-cloudimg-amd64.img --run-command "truncate -s 0 /etc/machine-id"

# Create the template VM shell (ID 9000 is a common convention for templates — pick any unused ID)
qm create 9000 \
  --name "ubuntu-2604-cloudinit-template" \
  --numa 0 --ostype l26 \
  --cpu cputype=host --cores 2 --sockets 1 --memory 2048 \
  --net0 virtio,bridge=vmbr0

# Import the customized image as this VM's boot disk
qm set 9000 --scsihw virtio-scsi-pci \
  --scsi0 local-lvm:0,import-from=/var/lib/vz/template/iso/resolute-server-cloudimg-amd64.img

# Attach a cloud-init drive (this is what lets Terraform inject hostname/IP/SSH keys per clone)
qm set 9000 --ide2 local-lvm:cloudinit
qm set 9000 --boot order=scsi0
qm set 9000 --serial0 socket --vga serial0
qm set 9000 --agent enabled=1

# Convert to a template — from this point it can only be cloned, not booted directly
qm template 9000
```

Verify it shows up as a template (grey/cube icon) in the web UI under your
node. Automating this template lifecycle and multi-node VM placement is tracked
in GitHub issue #44; until then, this remains a manual rebuild step.

---

## Phase 3 — Terraform: provision the three VMs

Files: `terraform/`. This is the infra-as-code layer — think of it as the CloudFormation/Terraform equivalent you already know from AWS, just targeting Proxmox's API instead. `bpg/proxmox` is the actively-maintained community provider (the older `telmate/proxmox` is effectively legacy at this point) — it's what clones VMs from the template you just built rather than reinstalling an OS every time.

**Create a dedicated API token** (don't use the root password — a scoped token is the equivalent of an IAM role vs. root credentials). Automated by the cluster-scoped `proxmox_cluster` role's `terraform-api` tag — see below — but the equivalent by hand:
```bash
# on the Proxmox host
pveum user add terraform@pve
pveum aclmod / -user terraform@pve -role PVEVMAdmin
# PVEVMAdmin alone is NOT enough — it covers VM.Clone/Config/PowerMgmt but
# has zero Datastore.* privileges, and cloning has to allocate space on the
# target storage. Without this second grant, terraform apply fails with
# "HTTP 403 - Permission check failed (/storage/<pool>, Datastore.AllocateSpace)".
pveum aclmod /storage/local-lvm -user terraform@pve -role PVEDatastoreUser
# A third bucket: attaching a VM's NIC to a bridge needs SDN.Use, even for a
# plain Linux bridge with zero SDN zones/vnets actually configured — Proxmox
# wraps every bridge in an implicit zone ("localnetwork") for this check.
# Without it: "HTTP 403 - Permission check failed (/sdn/zones/localnetwork/<bridge>, SDN.Use)".
pveum aclmod /sdn/zones/localnetwork/vmbr0 -user terraform@pve -role PVESDNUser
pveum user token add terraform@pve tf --privsep 0
# copy the printed token value — it's shown exactly once
```

Or run it via Ansible after the `neovara` cluster is quorate. The role delegates
to the declared cluster seed and independently self-heals all three ACLs:

```bash
cd ansible
ansible-playbook proxmox.yml --tags terraform-api
```

**Set up and apply:**
```bash
cd terraform
# terraform.tfvars is committed (it holds no secrets — endpoint, node, IPs,
# specs). Edit it for your environment if you're not rebuilding this exact host.
# The one secret, the Proxmox API token, is passed at runtime via env var —
# never written to a file (same pattern as K3S_TOKEN, ADR-0003):
export PROXMOX_VE_API_TOKEN='terraform@pve!tf=<the token from the step above>'

terraform init
terraform plan    # review — should show 3 resources to add
terraform apply
```

After a couple of minutes you'll have three running VMs with static IPs, cloud-init-provisioned SSH access, and qemu-guest-agent reporting status back to Proxmox. Both workers also carry a second, empty 280GB data disk (`scsi1`) — reserved for distributed storage (Longhorn, a later phase; see ADR-0021), formatted and mounted by Ansible in Phase 6, not by Terraform. Total declared: 740G of the 816G thin pool (91%) — the remaining ~9% is deliberate headroom so the pool can never silently fill underneath its guests. Verify:
```bash
ssh harsh@192.168.1.21 "hostname && ip a"
ssh harsh@192.168.1.22 "hostname && ip a"
ssh harsh@192.168.1.23 "hostname && ip a"
```

**Why Terraform here and Ansible next, not one or the other:** provisioning (does this VM exist, with this CPU/disk/IP) is a different problem from configuration (what's installed and running inside it). Terraform is declarative infra state — rerun `apply` and it converges, doesn't reinstall. Ansible is imperative configuration over SSH — it's what actually puts k3s on the box. Using the right tool for each half is also just less to fight with than forcing one tool to do both jobs.

---

## Phase 4–6 — Ansible: configure the OS and install k3s

Files: `ansible/`. This is the configuration-management layer — SSH-based, idempotent, rerunnable. Everything here is a custom minimal role rather than a pulled-in third-party playbook (the official `k3s-io/k3s-ansible` exists and is fine, but writing it yourself means every flag is something you chose and can explain, which matches the actual goal here).

**Install the required Ansible collections once:**
```bash
cd ansible
ansible-galaxy collection install -r requirements.yml
```

**Set the cluster join token** (an Ansible/k3s-level secret — different scope from Kubernetes Secrets entirely, so it's handled separately and never committed):
```bash
export K3S_TOKEN=$(openssl rand -hex 32)
echo "$K3S_TOKEN" > ~/.k3s-homelab-token   # save it somewhere durable — you'll need the SAME value on every future run
```

**Run the full playbook:**
```bash
ansible-playbook site.yml --extra-vars "k3s_token=${K3S_TOKEN}"
```

**What each phase actually does, mapped to the roles:**

- **`k3s_node`** (Phase 4, runs on all k3s nodes) — the real k3s prerequisites, nothing more: swap off (Kubernetes requires this), `br_netfilter` + `overlay` kernel modules loaded and persisted, the two sysctl flags that let bridged traffic hit iptables rules, hostname set to match inventory. This is deliberately *not* general server hardening — that's a separate concern from "what does k3s actually need to boot."

- **`k3s_server`** (Phase 5, `k3s-server-1` only) — writes `/etc/rancher/k3s/config.yaml` from the template, which is where every earlier design decision becomes a real flag:
  - `cluster-init: true` → embedded etcd, not SQLite — the pivotal call from the design phase
  - `secrets-encryption: true` → Kubernetes Secrets encrypted at rest in etcd, not just base64
  - `node-taint: node-role.kubernetes.io/control-plane:NoSchedule` → app pods physically cannot land here
  - `disable: [traefik, servicelb]` → k3s ships both by default; we're bringing our own Traefik (ClusterIP, no LoadBalancer needed given the tunnel), so both are switched off to avoid collisions
  - Flannel and kube-proxy are left on defaults — no flags — which *is* the decision: simplest CNI first, Cilium deferred as a deliberate later project
  - `etcd-snapshot-schedule-cron` + `etcd-snapshot-retention` → local hourly snapshots from day one. Off-box shipping to S3 is part of the deferred backup phase, but there's no reason to wait to start taking *local* snapshots — it costs nothing and gives you something to test a restore against immediately.

  After install, the role fetches `/etc/rancher/k3s/k3s.yaml` back to your workstation as `kubeconfig` and rewrites the server URL from `127.0.0.1` to the real LAN IP (the file defaults to localhost, which only works *on* the node itself — an easy trap if you skip this step and then wonder why `kubectl` from your laptop can't connect).

- **`longhorn_node`** (Phase 6, both workers) — owns the explicitly declared worker data device: ext4 formatting, the `/var/lib/longhorn` mount, and iSCSI/NFS prerequisites. Keeping this out of `k3s_agent` prevents Kubernetes membership logic from hiding storage mutation.

- **`k3s_agent`** (Phase 6, both workers) — writes the agent config, converges the pinned k3s version, and joins the workers to the server with the shared token.

**One honest tradeoff in `ansible.cfg`:** `host_key_checking = False`. Convenient for a homelab where you're rebuilding VMs often (no stale host-key prompts blocking automation), but it does mean Ansible won't warn you if a host's SSH key ever unexpectedly changes. Fine here; flip it back on if this repo ever manages anything less trusted than your own LAN.

---

## Phase 7 — Verify the cluster

```bash
export KUBECONFIG=$(pwd)/kubeconfig   # from the ansible/ directory
kubectl get nodes -o wide
```

Expect:
```
NAME            STATUS   ROLES                  AGE   VERSION
k3s-server-1    Ready    control-plane,master   2m    v1.36.2+k3s1
k3s-worker-1    Ready    <none>                 1m    v1.36.2+k3s1
k3s-worker-2    Ready    <none>                 1m    v1.36.2+k3s1
```

**Confirm the taint actually landed:**
```bash
kubectl describe node k3s-server-1 | grep Taints
# Taints:  node-role.kubernetes.io/control-plane:NoSchedule
```

**Confirm embedded etcd is really running** (not SQLite):
```bash
ssh harsh@192.168.1.21 "sudo k3s etcd-snapshot ls"
# should list snapshots once the first hourly cron fires — or take one manually right now:
ssh harsh@192.168.1.21 "sudo k3s etcd-snapshot save --name day1-check"
ssh harsh@192.168.1.21 "sudo k3s etcd-snapshot ls"
```

Seeing a snapshot land is the confirmation that `--cluster-init` actually did what it was supposed to — this is also your first real look at the etcd operations you specifically wanted hands-on experience with. Poke around further if you're curious:
```bash
ssh harsh@192.168.1.21 "sudo ETCDCTL_API=3 etcdctl \
  --endpoints https://127.0.0.1:2379 \
  --cacert /var/lib/rancher/k3s/server/tls/etcd/server-ca.crt \
  --cert /var/lib/rancher/k3s/server/tls/etcd/client.crt \
  --key /var/lib/rancher/k3s/server/tls/etcd/client.key \
  endpoint status --write-out=table"
```

**Rebuild story, since this is the whole point of the Terraform+Ansible split:** `terraform destroy && terraform apply` followed by `ansible-playbook site.yml --extra-vars "k3s_token=${K3S_TOKEN}"` (same token) gets you back to this exact point, from nothing, without touching the Proxmox UI. That's what "minimal manual intervention" actually buys you — and it's the same muscle you'll use for restore drills later.

---

## Phase 8 — Install Helm

```bash
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
helm version
```

(If your network policy is stricter about curl-pipe-bash, grab the release tarball directly from `github.com/helm/helm/releases` instead — same result.)

---

## Phase 9 — Traefik

Files: `k8s/traefik/`. This is the ingress layer — the thing that finally reads the HTTP `Host:` header and decides which app a request is for, which is the one job NodePort/LoadBalancer alone can never do.

```bash
helm repo add traefik https://traefik.github.io/charts
helm repo update

kubectl create namespace traefik

# Traefik's CRDs (IngressRoute, Middleware, etc.) ship separately from the chart
helm show crds traefik/traefik | kubectl apply --server-side --force-conflicts -f -

helm install traefik traefik/traefik \
  --namespace traefik \
  --version 41.0.1 \
  --values k8s/traefik/values.yaml
```

The internal front-door Service and dashboard IngressRoute live under `k8s/traefik/manifests/` and
are introduced in Phase 12 after the Tailscale Operator exists.

**Pin the chart version.** The chart's values schema isn't stable across releases — this exact install failed once against an unpinned `traefik/traefik` because the schema had moved `ports.websecure.tls` to `ports.websecure.http.tls`, and separately, silently defaulted `service.type` to `LoadBalancer` because `service.spec.type` had replaced it without erroring (ADR-0027). Before trusting this values file against a newer chart version, diff it against `helm show values traefik/traefik --version <new-version>` first.

Verify:
```bash
kubectl get pods -n traefik
kubectl get svc -n traefik
# traefik service should show TYPE=ClusterIP — no EXTERNAL-IP, and that's correct, not broken
```

**Why IngressRoute over Gateway API for this build:** Gateway API is genuinely where the ecosystem is heading, but it requires installing a separate set of CRDs and has more moving parts to learn at once. IngressRoute is Traefik's native CRD, ships with the chart, and is simpler to reason about while you're still building the mental model for everything else. Migrating later is a config change, not a rebuild — nothing here blocks it.

---

## Phase 10 — cert-manager + Cloudflare DNS-01

Files: `k8s/cert-manager/`. TLS certificate automation. In this design cert-manager is deliberately **not on the public path** — Cloudflare terminates TLS at its edge for anything reached through the tunnel, so a public request already arrives over HTTPS before it hits your cluster at all. What cert-manager is actually for here is **internal/Tailscale-only services**: it lets things like a future Grafana or Argo CD dashboard get a real, non-self-signed certificate even though they're never publicly reachable — DNS-01 proves domain ownership via a TXT record rather than an HTTP request, so it works regardless of public reachability.

**Install:**
```bash
kubectl create namespace cert-manager

helm install cert-manager oci://quay.io/jetstack/charts/cert-manager \
  --namespace cert-manager \
  --version v1.20.3 \
  --set crds.enabled=true
```

(cert-manager's own docs now recommend the OCI chart with `crds.enabled=true` over the older pattern of a separate `kubectl apply` for CRDs against a Helm HTTP repo — `installCRDs` is deprecated in favor of it. Verified live against `oci://quay.io/jetstack/charts/cert-manager` at v1.20.3, which is still the current stable release.)

**Create a Cloudflare API token** — Cloudflare dashboard → My Profile → API Tokens → Create Token → use the "Edit zone DNS" template, scoped to your specific domain. Copy the token value (shown once).

**Create the secret cert-manager will use** (created imperatively, not committed to Git in any form — secrets management is a deliberately deferred follow-on, see the "What's deferred" section):
```bash
kubectl create secret generic cloudflare-api-token \
  --namespace cert-manager \
  --from-literal=api-token='<paste your Cloudflare API token>'
```

**Apply the ClusterIssuers** (edit the placeholder contact email in the file first — Let's Encrypt uses it for expiry notices; not needed under the `cloudflare:` block since API Token auth, unlike API Key auth, doesn't require it):
```bash
kubectl apply -f k8s/cert-manager/cluster-issuer.yaml
kubectl get clusterissuer
# both should show READY=True within a few seconds
```

**Test with the staging issuer first** — real certs from staging aren't browser-trusted, but confirming the DNS-01 challenge actually completes costs nothing against staging's near-unlimited rate limit, versus prod's genuinely limited one:
```bash
kubectl apply -f k8s/cert-manager/internal-wildcard-certificate.yaml
# edit the file first: swap letsencrypt-prod -> letsencrypt-staging, and your real domain

kubectl describe certificate internal-wildcard-tls -n traefik
# watch for "Certificate issued successfully"
```

Once that's clean, flip the `issuerRef` back to `letsencrypt-prod` and re-apply for the real certificate.

---

## Phase 11 — Cloudflare Tunnel

Files: `k8s/cloudflared/`. This is the external front door — the mechanism that gets a public request from the internet to Traefik with no inbound port ever opened on your router, no exposed home IP, and (unlike a direct port-forward) works regardless of whether your ISP's IPv4 setup would even allow forwarding 80/443 in the first place.

**Create the tunnel** (Cloudflare Zero Trust dashboard → Networking → Tunnels → Create a tunnel → choose "Cloudflared" as the connector type). Name it something like `homelab`. On the install-command screen, don't run the suggested command — you just need the **token** it shows you (a long string); the Kubernetes Deployment will use that instead of installing cloudflared on a host.

**Add a public hostname per app** — under the tunnel's Public Hostname tab, add a **specific first-level** hostname (not a wildcard — see the SSL note below):
```
Hostname: whoami.neovara.uk
Service:  http://traefik.traefik.svc.cluster.local:80
```
(Target the **Service port 80**, not the container's `8000`. The Traefik Service exposes `80 → targetPort 8000`; cloudflared dials the Service's ClusterIP on 80, and kube-proxy forwards it to the pod's 8000. Connecting to the ClusterIP on 8000 hits nothing — verified: `:80` → 404-from-Traefik, `:8000` → connection refused.) Adding a *specific* hostname here also makes Cloudflare **auto-create its DNS record** — no manual DNS step.

**Why specific first-level names, and NOT a nested `*.k8s.neovara.uk` wildcard:** two constraints stack up. (1) A legacy homelab already owns the `*.neovara.uk` wildcard → old router, so the tunnel can't reuse that exact name. (2) Cloudflare's **free Universal SSL only covers the root + one wildcard level** (`neovara.uk`, `*.neovara.uk`) — a two-level name like `whoami.k8s.neovara.uk` gets **no edge certificate**, so its TLS handshake fails before the tunnel is ever consulted (covering `*.k8s.neovara.uk` needs the paid Advanced Certificate Manager). The free path that satisfies both: give each public app a **specific first-level** name (`whoami.neovara.uk`, `app.neovara.uk`). Universal SSL's `*.neovara.uk` cert covers it, and because a *specific* record beats the legacy wildcard, that one name routes to the cluster while everything else still flows to the old router — clean, reversible, app-by-app migration. (Internal services are unaffected: `*.in.neovara.uk` over Tailscale uses cert-manager's own Let's Encrypt cert, which *can* do multi-level wildcards since Cloudflare's edge isn't in that path.)

The routing trick still holds: every public hostname points at the **same** Traefik Service (`:80`), never at individual apps. Traefik does the per-app `Host:` routing via IngressRoute objects. Adding app #2 means: one new public hostname on the tunnel + one new IngressRoute — the cloudflared Deployment itself never changes.

**End-state (after migration):** once every workload is on k8s and the legacy `*.neovara.uk → old router` wildcard is retired, replace the per-app public hostnames with a **single `*.neovara.uk` tunnel route** (first-level, so still covered by free Universal SSL) plus one proxied `*.neovara.uk` DNS record. From then on a new public app needs **only** an IngressRoute — no Cloudflare or DNS change ever again. Until then, the per-app hostname step is the price of coexisting with the legacy wildcard.

**Apply the manifests:**
```bash
kubectl apply -f k8s/cloudflared/namespace.yaml

kubectl create secret generic cloudflared-tunnel-token \
  --namespace cloudflare \
  --from-literal=token='<paste the tunnel token from the dashboard>'

kubectl apply -f k8s/cloudflared/deployment.yaml
kubectl apply -f k8s/cloudflared/networkpolicy.yaml
```

Verify:
```bash
kubectl get pods -n cloudflare
kubectl logs -n cloudflare deployment/cloudflared
# look for "Registered tunnel connection" — that's the outbound QUIC connection to Cloudflare's edge
```

Back in the dashboard, the tunnel should now show **Healthy** (this only means the connector linked to the edge — it says nothing about DNS/TLS yet).

**Prove the public path end-to-end:**
```bash
curl -I https://whoami.neovara.uk
# HTTP 404 (from Traefik) = SUCCESS — the request reached Traefik, which has no
# IngressRoute for this Host yet (that's Phase 13's whoami app). The 404 body is
# Traefik's; cross-check it matches an in-cluster hit to traefik.traefik.svc:80.
# A 502/530/1033 Cloudflare error instead = the tunnel isn't reaching the origin.
```
If `curl` fails with `Could not resolve host`, the DNS record is missing (a *specific* tunnel hostname auto-creates it; a *wildcard* one does not). If it fails with an SSL `handshake failure`, the name is deeper than one level and free Universal SSL doesn't cover it — use a first-level name (ADR-0028).

**Why the NetworkPolicy isn't optional here.** Cloudflare's own quickstart deploys cloudflared with zero network restriction — by default it can resolve and reach *every* Service in *every* namespace via standard service discovery, because nothing stops it. That makes the one public-facing component in your entire cluster a pivot point: if a vulnerability in whatever app you're exposing ever got exploited, an attacker landing in that request path could otherwise walk straight to your databases or your Traefik dashboard through the tunnel pod's own network reach. `networkpolicy.yaml` locks cloudflared down to exactly Traefik on port 8000, plus DNS, plus Cloudflare's own edge — nothing else. This is also a nice confirmation of something from the design phase: NetworkPolicy enforcement works here even without Cilium, because k3s's bundled netpol controller enforces it on plain Flannel too.

---

## Phase 12 — Tailscale

Files: `k8s/tailscale/`. This is the admin front door — private, never public, covering the Kubernetes API, in-cluster dashboards, and the Proxmox UI. It's genuinely **two separate mechanisms**, not one, because those targets don't live in the same place:

| Target | Lives where | Reached via |
|---|---|---|
| Proxmox web UI, hypervisor SSH | on the hypervisor itself | tailscaled installed directly on the Proxmox host |
| k3s node SSH | on each k3s VM | tailscaled installed directly on each node (Part A, extended) |
| Kubernetes API (`kubectl`) | the k3s control plane | the Operator's built-in API server proxy — no separate route needed |
| In-cluster dashboards (Traefik now; Grafana/Argo CD later) | inside the cluster | the Operator claiming a Service via `loadBalancerClass: tailscale` |

**Part A — tailscaled on the Proxmox hosts** (covers Proxmox UI + node-level access). The shared `tailscale_host` role is explicit-only because it needs a runtime auth key; a default Proxmox run never attempts enrollment.

Generate a reusable, non-ephemeral auth key (one play enrolls two permanent
hosts; a one-time key would be consumed by the first), then:

```bash
cd ansible
ansible-playbook proxmox.yml --tags tailscale --extra-vars "tailscale_auth_key=<paste the reusable key>"
```
Same pattern as the k3s join token in Phase 4–6: passed at runtime via `--extra-vars`, never written to a file in this repo. The role installs `tailscale` from its official apt repo, enables `tailscaled`, and runs `tailscale up` non-interactively — idempotent, so rerunning it is a no-op once the host is already joined.

The Proxmox UI is now reachable at `https://<tailscale-ip-or-magicdns-name>:8006` from any device on your tailnet — never from the public internet.

**Part A (extended) — tailscaled on the k3s nodes themselves.** The same
`tailscale_host` role runs against the k3s guests with Ubuntu repository
variables. It is tagged `[never, tailscale]`, so a bare `site.yml` run never
requires an auth key.

Generate a **reusable** auth key this time (one run joins all three nodes, so a one-time-use key would fail after the first) — still **not** ephemeral, since these VMs are permanent and an ephemeral node gets pruned from the tailnet shortly after it goes offline (a reboot could then cost you access). For a fresh rebuild, temporarily select the commented LAN targets in inventory; the normal committed targets use MagicDNS and therefore assume Tailscale is already connected (see #43):
```bash
cd ansible
ansible-playbook site.yml --tags tailscale --extra-vars "tailscale_auth_key=<paste a reusable auth key>"
```
Confirm all three joined, then note their tailnet IPs:
```bash
ansible k3s_cluster -m command -a "tailscale ip -4"
```

**SSH into a node over the tailnet.** Nothing about auth changes — the nodes still trust exactly the key cloud-init baked in at clone time (Phase 3: `terraform.tfvars`'s `ssh_public_key`, the `homelab-admin`/`id_ed25519` pair, for user `harsh`). You just target the tailnet address instead of the LAN IP:
```bash
ssh -i ~/.ssh/id_ed25519 harsh@k3s-server-1.<your-tailnet>.ts.net   # or the 100.x IP
```

**Two distinct SSH identities — don't cross them.** The host and the nodes were provisioned by different mechanisms, so they trust different keys. Using the Proxmox key against a node (or vice versa) fails with `Permission denied (publickey)` — that's expected, not a misconfiguration:

| Target | User | Private key | How its public key got there |
|---|---|---|---|
| Proxmox host (`pve-dell`) | `root` | `~/.ssh/proxmox_ed25519` | manual `ssh-copy-id` (Phase 1 bootstrap) |
| k3s nodes | `harsh` | `~/.ssh/id_ed25519` | Terraform cloud-init at clone time (Phase 3) |

Both key *pairs* live only where you generated them. If you're migrating off a LAN workstation onto another machine, copy the private keys across first (e.g. `scp` them over the tailnet under whatever names you like — only the private half is needed to connect, since the public key is embedded in it) or you'll lock yourself out the moment the old box is gone.

**Part B — the Tailscale Kubernetes Operator** (covers the API server + in-cluster dashboards):

In the Tailscale admin console → Access Controls, add to the policy file's `tagOwners` section:
```json
"tagOwners": {
  "tag:k8s-operator": [],
  "tag:k8s": ["tag:k8s-operator"]
}
```

Create an OAuth client (Settings → OAuth clients) with **Devices Core**, **Auth Keys**, and
**Services** Read+Write scopes, tagged `tag:k8s-operator` (ADR-0025). Copy the client ID and secret.

```bash
helm repo add tailscale https://pkgs.tailscale.com/helmcharts
helm repo update

kubectl create namespace tailscale

# Runtime credentials stay outside Git and Helm values. The chart consumes this
# existing Secret when oauth.* is omitted from k8s/tailscale/values.yaml.
kubectl -n tailscale create secret generic operator-oauth \
  --from-literal=client_id="<your OAuth client ID>" \
  --from-literal=client_secret="<your OAuth client secret>"

helm upgrade --install tailscale-operator tailscale/tailscale-operator \
  --namespace tailscale \
  --version 1.98.4 \
  --values k8s/tailscale/values.yaml \
  --wait

kubectl apply -f k8s/tailscale/manifests/api-server-rbac.yaml
kubectl apply -f k8s/tailscale/manifests/proxyclass-monitoring.yaml

kubectl get pods -n tailscale
# operator pod should reach Running; check the Tailscale admin console —
# a new device tagged k8s-operator should appear in your tailnet
```

**Kubernetes API over Tailscale** — no extra manifests needed, `apiServerProxyConfig.mode: "true"` in the values file already turned this on:
```bash
tailscale configure kubeconfig <operator-magicdns-name>
# find the exact name on the Machines page of the Tailscale admin console
kubectl get nodes   # now works over the tailnet, from any device, no public API exposure
```

**Traefik dashboard over Tailscale** uses the shared internal Traefik front door from Part C, not a
separate LoadBalancer Service. Once that front door exists, the dashboard's IngressRoute exposes
`api@internal` at `https://traefik.in.neovara.uk`:

```bash
kubectl apply -f k8s/traefik/manifests/ingressroute-dashboard.yaml
```

**Part C — Internal apps over Tailscale (`*.in.neovara.uk`)** — this is where the Phase 10 wildcard cert finally goes into service, and it completes the cluster half of ADR-0018. The goal: any internal app gets a real HTTPS name like `whoami.in.neovara.uk`, reachable from tailnet devices only, with a browser-trusted cert and no per-app cert or DNS work. Three manifests do it (see ADR-0029 for the full decision):

```bash
kubectl apply -f k8s/traefik/manifests/tailscale-service.yaml  # internal front door
kubectl apply -f k8s/traefik/manifests/tlsstore.yaml           # wildcard cert as Traefik's default
kubectl apply -f k8s/example-app/ingressroute-internal.yaml   # first internal route
kubectl get svc -n traefik traefik-internal
# EXTERNAL-IP fills in with the Tailscale IP + MagicDNS name (~30s) once the operator claims it
```

- `tailscale-service.yaml` is a *third* Service pointed at the same Traefik pods — the same `loadBalancerClass: tailscale` pattern as the dashboard above, but exposing only `websecure` (443 → the pod's 8443). The operator's proxy pod joins the tailnet as its own device, `traefik-internal` — the internal counterpart of what cloudflared is for the public path.
- `tlsstore.yaml` exists because an IngressRoute's `tls.secretName` only works from the route's *own* namespace, and the cert lives in `traefik` while app routes live in app namespaces. The one cluster-wide `TLSStore` named `default` hands the wildcard to any route that enables TLS with an empty `tls: {}` — one setting covers every current and future `*.in` app.
- `ingressroute-internal.yaml` is whoami's second front door (`websecure` entrypoint, `Host(\`whoami.in.neovara.uk\`)`, `tls: {}`) — the public twin `ingressroute.yaml` is untouched.

Then one manual, one-time DNS step in Cloudflare (DNS-only / grey-cloud, **not** proxied — Cloudflare must never sit in this path):

```
*.in.neovara.uk  CNAME  traefik-internal.<your-tailnet>.ts.net
```

Counterintuitively, that CNAME target does *not* need to exist in public DNS — and in practice it doesn't (the public ts.net record is NXDOMAIN). It works anyway because a tailnet device resolves through Tailscale's quad-100 resolver, which forwards the query upstream, gets back a CNAME pointing at a MagicDNS name it *itself* owns, and fills in the A record from its own live state. That's better than a hardcoded A record: quad-100 always knows the proxy's current IP (see [[Platform Concepts]] and ADR-0029).

**Verify** from a tailnet device: `curl -v https://whoami.in.neovara.uk` — expect resolution to the proxy's `100.x` IP, `subject: CN=*.in.neovara.uk` with "SSL certificate verify ok" (no `-k`), and a 200 with `X-Forwarded-Proto: https`. Then the negative test: disconnect Tailscale and curl again — it must fail with `Could not resolve host`. Off-tailnet, internal names aren't just unreachable, they're *invisible*.

**Rebuild note:** if the `traefik-internal` Service (or the cluster) is ever recreated, delete the stale `traefik-internal` device in the Tailscale admin console first — otherwise the new proxy joins name-suffixed (`traefik-internal-1`) and the CNAME breaks. The `tailscale.com/hostname` annotation keeps the name (and therefore DNS) valid across rebuilds; the new device's new IP heals automatically via quad-100.

---

## Phase 13 — End-to-end validation

Files: `k8s/example-app/`. Everything up to this point is infrastructure — this is the first proof that a request can actually travel the whole path: browser → Cloudflare → tunnel → Traefik → Service → pod. The original validation used a throwaway Deployment. The current directory was later converted into an Argo Rollouts blue/green exercise (ADR-0047), so the following small imperative Deployment/Service recreates the foundation-era test without requiring the Rollouts CRDs.

**Deploy:**
```bash
kubectl create deployment whoami --image=traefik/whoami:v1.11.0 --replicas=2
kubectl expose deployment whoami --name=whoami-stable --port=80 --target-port=80
kubectl apply -f k8s/example-app/ingressroute.yaml
kubectl apply -f k8s/example-app/ingressroute-internal.yaml
```

**Add the tunnel public hostname** — Cloudflare Zero Trust → Networks → Tunnels → homelab → Public Hostname:
```
whoami.neovara.uk → http://traefik.traefik.svc.cluster.local:80
```
(This auto-creates the proxied DNS CNAME. Verify it took by checking cloudflared's log: `kubectl logs -n cloudflare deployment/cloudflared | grep 'Updated to new configuration'` — the ingress config JSON should list `whoami.neovara.uk`. If it doesn't, delete and re-create the hostname rather than editing it; cloudflared config can get stuck on partial edits.)

**Test the public path:**
```bash
curl https://whoami.neovara.uk
# → 200 with whoami's header-echo: Hostname, IP, RemoteAddr, X-Forwarded-For,
#   Cf-Connecting-Ip, Cf-Ray — confirms the FULL chain: Cloudflare DNS → edge
#   (TLS terminated) → tunnel → cloudflared → traefik ClusterIP:80 → kube-proxy
#   DNAT → Traefik → IngressRoute Host(`whoami.neovara.uk`) → whoami Service
#   → load-balanced across the 2 replicas
```
Hit it several times to see the `Hostname:` alternating between pods — proof of kube-proxy load-balancing.

**Test the private path** (over Tailscale, from the Mac — confirms the admin plane independently):
```bash
kubectl get pods -n default -l app=whoami
```
Both work = every layer in the build is doing its job: Flannel/CNI, kube-proxy/Service, CoreDNS, Traefik IngressRoute, cloudflared tunnel, and the Tailscale Operator API proxy.

After validating the foundation-era test above, remove only the imperative resources it created:

```bash
kubectl delete deployment whoami
kubectl delete service whoami-stable
kubectl delete -f k8s/example-app/ingressroute.yaml
kubectl delete -f k8s/example-app/ingressroute-internal.yaml
```

The directory was later repurposed as the Argo Rollouts blue/green exercise (ADR-0047), so do not
delete the whole current directory from the cluster unless deliberately retiring that exercise.

---

## Follow-on status

This guide ends at the validated foundation. Several originally deferred layers have since landed:

- **GitOps (Argo CD)** is live and manages the platform and migrated applications through the app-of-apps pattern. Runtime Secrets remain imperative and outside Git.
- **Longhorn**, **monitoring** (kube-prometheus-stack), and **logging** (Loki + Grafana Alloy) are live. See `ROADMAP.md`, `docs/adr/v2.0 - Operability.md`, and `k8s/` for the tested configuration.

The following remain deliberately deferred:

- **Secrets management (SOPS + age, later External Secrets Operator + Vault)** — for now, Kubernetes Secrets are created imperatively with `kubectl create secret` and never committed to Git in any form, encrypted or otherwise. SOPS+age is the natural first step when this gets automated (commit ciphertext to Git, decrypt-and-apply manually), with ESO/Vault as the later upgrade once real rotation and a secrets backend are wanted.
- **Full backup strategy** (Velero, off-box etcd snapshots via `--etcd-s3-*`, Proxmox VM backups) — local etcd snapshots are already running from Phase 5; shipping them off-box and adding Velero + Proxmox Backup Server is the next layer, not a redo.
- **Cilium** — deliberately deferred; recall this is the one *non-additive* item on this whole list. Flannel → Cilium isn't an upgrade, it's a rebuild (pod networking can't be live-migrated between CNIs). Treat it as its own dedicated project later — and a good real rebuild-from-Git/DR drill when you get there.
- **HA control plane** (`k3s-server-2`, `k3s-server-3`) and **more workers** — both are additive. A second/third server joins the existing embedded-etcd cluster for real quorum; each additional worker needs a Terraform resource and an Ansible inventory entry. The Immich recovery runbook explicitly plans one such worker on a second Proxmox host; it is not implemented in the current code yet.

**Accepted physical-host expansion (ADR-0049):** the second Proxmox host is not standalone. Immediately after installing the workstation—and before creating any guest—create the Proxmox cluster on `pve-dell` and join the empty workstation. Keep one Terraform provider/token and select VM placement by `node_name`. The temporary two-node stage accepts read-only Proxmox configuration when either member is missing; a third physical node is planned one to two months later. The complete, data-safe order is in `docs/migrations/immich.md`. After Immich recovery and a real 119GiB SSD capacity check, the preferred placement change is moving the existing single `k3s-server-1` VM to the workstation. Do not add only one more k3s server: two embedded-etcd members still require both; real HA needs three.

---

## Troubleshooting — sharp edges you're likely to actually hit

- **etcd complaining about disk latency** — leader-election flakiness or slow-apply warnings point at the disk first. On this deployment that's a *known* risk, not a surprise: everything runs from a USB-attached external SSD because the internal NVMe is off-limits (ADR-0022). Check `dmesg` on the host for USB resets, and confirm the SSD's power/control is still pinned `on` (the `uas` driver does this by default). If it becomes chronic, the fix is a dedicated physical node for the control plane — never the internal NVMe.
- **`kubectl` from your laptop hangs or refuses to connect** — almost always the kubeconfig still points at `127.0.0.1:6443` instead of the server's real IP. The Ansible role handles this automatically, but if you ever grab a fresh kubeconfig manually, remember to fix that line.
- **Traefik shows `EXTERNAL-IP: <none>` on the main Service** — that's correct, not broken. It's ClusterIP by design; there is no LoadBalancer in this topology.
- **A Service can't reach an otherwise-healthy pod** — check `targetPort` against what the container is actually listening on. `containerPort` is documentation only; nothing in the traffic path enforces it matches.
- **cert-manager's Certificate stays stuck in `False` / pending** — check `kubectl describe certificate <name> -n <ns>` and `kubectl get challenges -A`; almost always either the Cloudflare API token's permissions are wrong (needs Zone:DNS:Edit on the right zone) or the `email` fields in the ClusterIssuer don't match the Cloudflare account that issued the token.
- **cloudflared shows `Registered tunnel connection` but curl still 404s** — (1) check the tunnel's ingress config actually lists your hostname: `kubectl logs -n cloudflare deployment/cloudflared | grep 'Updated to new configuration'` — a missing hostname in the JSON means the dashboard config never saved (delete and re-create, don't edit). (2) Verify the origin reaches Traefik: from inside the cluster, `curl -H 'Host: <your-host>' http://traefik.traefik.svc.cluster.local:80` — if this works but the tunnel doesn't, the tunnel's dashboard config is wrong; if this also 404s, the IngressRoute might not exist or might not match the `Host()` header. The host curl from step 2 returning 200 with whoami body means the problem is strictly tunnel-side: delete and re-add the public hostname from scratch rather than editing it.
- **Ansible reruns fail on `k3s_token`** — this is deliberate (the `assert` task in both roles). Reusing the exact same token across runs is what makes the playbook idempotent instead of accidentally re-bootstrapping a new cluster identity.

---

## Repo structure recap

```
homelab-k8s/
├── GUIDE.md
├── .gitignore
├── terraform/
│   ├── versions.tf / provider.tf / variables.tf / main.tf / outputs.tf
│   └── terraform.tfvars   (committed — no secrets; API token comes from $PROXMOX_VE_API_TOKEN)
├── ansible/
│   ├── ansible.cfg / inventory.ini / site.yml / proxmox.yml / requirements.yml
│   ├── group_vars/
│   └── roles/
│       ├── k3s_{node,server,agent}/ and longhorn_node/
│       └── proxmox_{host,cluster,hw_asrock,hw_dell}/ and tailscale_host/
└── k8s/
    ├── argocd/            root app, child Applications, and AppProjects
    ├── traefik/           chart values + companion manifests
    ├── cert-manager/      chart values + issuer/certificate manifests
    ├── cloudflared/       Deployment, NetworkPolicy, metrics
    ├── tailscale/         chart values + companion manifests
    ├── longhorn/          chart values + StorageClasses/UI route
    ├── monitoring/        Prometheus/Grafana, Loki, and Alloy values/manifests
    ├── argo-rollouts/     controller values + dashboard route
    ├── apps/              homelab workloads and personal-app governance
    └── example-app/       blue/green Rollout exercise
```

Push this to Git now, before you forget — everything except `kubeconfig` and Terraform's local state/lock (all gitignored) is meant to live there. `terraform.tfvars` **is** committed: it holds no secrets now that the API token comes from `$PROXMOX_VE_API_TOKEN`.
