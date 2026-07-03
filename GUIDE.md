# Homelab Kubernetes Platform — Build Guide

End-to-end setup: Proxmox → Terraform-provisioned VMs → Ansible-configured k3s → Traefik → cert-manager → Cloudflare Tunnel → Tailscale. Deferred by design: GitOps (Argo CD), monitoring, logging, secrets management (SOPS/age, later External Secrets/Vault), and the full backup strategy — each is a clean follow-on once this foundation is solid.

**Companion files referenced throughout live alongside this guide:**
```
homelab-k8s/
├── GUIDE.md                 ← this file
├── terraform/                provisions the two VMs on Proxmox
├── ansible/                  configures OS + installs k3s, reproducibly
└── k8s/                      manifests applied after the cluster is up
    ├── traefik/
    ├── cert-manager/
    ├── cloudflared/
    ├── tailscale/
    └── example-app/
```

Versions pinned at the time of writing (verify current before you install — links included):
| Component | Version pinned in this guide |
|---|---|
| Proxmox VE | 9.2 (Debian 13 "Trixie") |
| Ubuntu Server | 26.04 LTS "Resolute Raccoon" (5yr support to 2031) |
| Terraform provider `bpg/proxmox` | `~> 0.111` |
| k3s | `v1.36.2+k3s1` |
| Traefik Helm chart | `41.x` |
| cert-manager | `v1.20.3` |
| cloudflared | check `cloudflare/cloudflared` tags on Docker Hub before pulling |
| Tailscale Kubernetes Operator | latest stable from `pkgs.tailscale.com/helmcharts` |

---

## 0. Prerequisites

**Hardware assumption** (adjust sizing to your actual box, the one hard constraint doesn't move): 8+ cores, 32–64GB RAM, and — non-negotiable — **NVMe storage for the control-plane VM**. etcd is fsync-latency sensitive; if the control-plane VM's disk ever lands on spinning storage or a contended pool, expect leader-election flakiness and latency alerts.

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
ssh-keygen -t ed25519 -C "homelab-admin"
ssh-copy-id root@<proxmox-ip>
```

**Verify your storage pool is actually on NVMe** — Datacenter → Storage, check which physical device backs `local-lvm` (or whatever pool you'll target). This is the constraint that matters most in the whole build — confirm it now, not after etcd is already unhappy. Left as a manual, one-time check against the live host rather than scripted.

**Everything else — disabling the enterprise repo nag and running `apt full-upgrade`  — is codified** in `ansible/roles/proxmox_host/`, once the SSH key step above has landed:
```bash
cd ansible
ansible-playbook proxmox-host.yml --tags repos
```
This is a separate playbook from `site.yml` on purpose — it targets the hypervisor itself (`[proxmox_hosts]` in `inventory.ini`), not the k3s cluster, so it has its own blast radius and its own rerun cadence. It's idempotent: rerun it any time (e.g. after a Proxmox reinstall) to land in the same state.

The role also has a `tailscale` tag for joining the host to your tailnet (Phase 12 Part A) — see that phase below for why it's a separate tag rather than running by default: it needs a fresh auth key, which routine repo/apt housekeeping shouldn't require.

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

Verify it shows up as a template (grey/cube icon) in the web UI under your node. That's the only manual VM-building step in this whole guide — everything from here is code.

---

## Phase 3 — Terraform: provision the two VMs

Files: `terraform/`. This is the infra-as-code layer — think of it as the CloudFormation/Terraform equivalent you already know from AWS, just targeting Proxmox's API instead. `bpg/proxmox` is the actively-maintained community provider (the older `telmate/proxmox` is effectively legacy at this point) — it's what clones VMs from the template you just built rather than reinstalling an OS every time.

**Create a dedicated API token** (don't use the root password — a scoped token is the equivalent of an IAM role vs. root credentials):
```bash
# on the Proxmox host
pveum user add terraform@pve
pveum aclmod / -user terraform@pve -role PVEVMAdmin
pveum user token add terraform@pve tf --privsep 0
# copy the printed token value — it's shown exactly once
```

**Set up and apply:**
```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars with your real endpoint, token, SSH key, IPs

terraform init
terraform plan    # review — should show 3 resources to add
terraform apply
```

After a couple of minutes you'll have three running VMs with static IPs, cloud-init-provisioned SSH access, and qemu-guest-agent reporting status back to Proxmox. Both workers also carry a second, empty 250GB data disk (`scsi1`) — reserved for distributed storage (Longhorn, a later phase; see ADR-0021), formatted and mounted by Ansible in Phase 6, not by Terraform. Verify:
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

- **`common`** (Phase 4, runs on both nodes) — the real k3s prerequisites, nothing more: swap off (Kubernetes requires this), `br_netfilter` + `overlay` kernel modules loaded and persisted, the two sysctl flags that let bridged traffic hit iptables rules, hostname set to match inventory. This is deliberately *not* general server hardening — that's a separate concern from "what does k3s actually need to boot."

- **`k3s_server`** (Phase 5, `k3s-server-1` only) — writes `/etc/rancher/k3s/config.yaml` from the template, which is where every earlier design decision becomes a real flag:
  - `cluster-init: true` → embedded etcd, not SQLite — the pivotal call from the design phase
  - `secrets-encryption: true` → Kubernetes Secrets encrypted at rest in etcd, not just base64
  - `node-taint: node-role.kubernetes.io/control-plane:NoSchedule` → app pods physically cannot land here
  - `disable: [traefik, servicelb]` → k3s ships both by default; we're bringing our own Traefik (ClusterIP, no LoadBalancer needed given the tunnel), so both are switched off to avoid collisions
  - Flannel and kube-proxy are left on defaults — no flags — which *is* the decision: simplest CNI first, Cilium deferred as a deliberate later project
  - `etcd-snapshot-schedule-cron` + `etcd-snapshot-retention` → local hourly snapshots from day one. Off-box shipping to S3 is part of the deferred backup phase, but there's no reason to wait to start taking *local* snapshots — it costs nothing and gives you something to test a restore against immediately.

  After install, the role fetches `/etc/rancher/k3s/k3s.yaml` back to your workstation as `kubeconfig` and rewrites the server URL from `127.0.0.1` to the real LAN IP (the file defaults to localhost, which only works *on* the node itself — an easy trap if you skip this step and then wonder why `kubectl` from your laptop can't connect).

- **`k3s_agent`** (Phase 6, both workers) — formats and mounts the dedicated data disk (`/dev/sdb`, ext4, label `k3s-data`, mounted at `/var/lib/longhorn` — mounted by label since `/dev/sdX` names can reorder across boots), then points at the server's real IP with the shared token and joins as an agent. The mount is preparation for the distributed-storage phase (ADR-0021); the format task is a no-op on any disk that already has a filesystem, so reruns never eat data.

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
  --values k8s/traefik/values.yaml
```

(`k8s/traefik/dashboard-service.yaml` gets applied in Phase 12 — it depends on the Tailscale operator existing first, since it's claimed by `loadBalancerClass: tailscale`.)

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
helm repo add jetstack https://charts.jetstack.io
helm repo update

kubectl create namespace cert-manager
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.20.3/cert-manager.crds.yaml

helm install cert-manager jetstack/cert-manager \
  --namespace cert-manager \
  --version v1.20.3
```

**Create a Cloudflare API token** — Cloudflare dashboard → My Profile → API Tokens → Create Token → use the "Edit zone DNS" template, scoped to your specific domain. Copy the token value (shown once).

**Create the secret cert-manager will use** (created imperatively, not committed to Git in any form — secrets management is a deliberately deferred follow-on, see the "What's deferred" section):
```bash
kubectl create secret generic cloudflare-api-token \
  --namespace cert-manager \
  --from-literal=api-token='<paste your Cloudflare API token>'
```

**Apply the ClusterIssuers** (edit the placeholder emails in the file first):
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

**Configure the one route this tunnel needs** — under the tunnel's Public Hostname tab:
```
Hostname: *.neovara.uk  (or list each subdomain individually — your call)
Service:  http://traefik.traefik.svc.cluster.local:8000
```

This is the whole trick: the tunnel has exactly **one** route, pointed at Traefik's ClusterIP Service — not at individual apps. Traefik does the per-app `Host:` routing from here on via IngressRoute objects (Phase 14). Adding a tenth app later means adding an IngressRoute, never touching the tunnel config again.

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

Back in the dashboard, the tunnel should now show **Healthy**.

**Why the NetworkPolicy isn't optional here.** Cloudflare's own quickstart deploys cloudflared with zero network restriction — by default it can resolve and reach *every* Service in *every* namespace via standard service discovery, because nothing stops it. That makes the one public-facing component in your entire cluster a pivot point: if a vulnerability in whatever app you're exposing ever got exploited, an attacker landing in that request path could otherwise walk straight to your databases or your Traefik dashboard through the tunnel pod's own network reach. `networkpolicy.yaml` locks cloudflared down to exactly Traefik on port 8000, plus DNS, plus Cloudflare's own edge — nothing else. This is also a nice confirmation of something from the design phase: NetworkPolicy enforcement works here even without Cilium, because k3s's bundled netpol controller enforces it on plain Flannel too.

---

## Phase 12 — Tailscale

Files: `k8s/tailscale/`. This is the admin front door — private, never public, covering the Kubernetes API, in-cluster dashboards, and the Proxmox UI. It's genuinely **two separate mechanisms**, not one, because those targets don't live in the same place:

| Target | Lives where | Reached via |
|---|---|---|
| Proxmox web UI, node SSH | on the hypervisor itself | tailscaled installed directly on the Proxmox host |
| Kubernetes API (`kubectl`) | the k3s control plane | the Operator's built-in API server proxy — no separate route needed |
| In-cluster dashboards (Traefik now; Grafana/Argo CD later) | inside the cluster | the Operator claiming a Service via `loadBalancerClass: tailscale` |

**Part A — tailscaled on the Proxmox host** (covers Proxmox UI + node-level access). This is codified as the `tailscale` tag in `ansible/roles/proxmox_host/` (same role as Phase 1's repo housekeeping, kept as a separate tag since this needs a fresh auth key and that shouldn't run by default) — independent of the k3s cluster, so it can be done any time after Phase 1.

Generate a one-time-use auth key first (Tailscale admin console → Settings → Keys → Generate auth key — no need for reusable or ephemeral, this host is permanent), then:
```bash
cd ansible
ansible-playbook proxmox-host.yml --tags tailscale --extra-vars "tailscale_auth_key=<paste the auth key>"
```
Same pattern as the k3s join token in Phase 4–6: passed at runtime via `--extra-vars`, never written to a file in this repo. The role installs `tailscale` from its official apt repo, enables `tailscaled`, and runs `tailscale up` non-interactively — idempotent, so rerunning it is a no-op once the host is already joined.

The Proxmox UI is now reachable at `https://<tailscale-ip-or-magicdns-name>:8006` from any device on your tailnet — never from the public internet.

**Part B — the Tailscale Kubernetes Operator** (covers the API server + in-cluster dashboards):

In the Tailscale admin console → Access Controls, add to the policy file's `tagOwners` section:
```json
"tagOwners": {
  "tag:k8s-operator": [],
  "tag:k8s": ["tag:k8s-operator"]
}
```

Create an OAuth client (Settings → OAuth clients) scoped to write `devices:core` — copy the client ID and secret.

```bash
helm repo add tailscale https://pkgs.tailscale.com/helmcharts
helm repo update

kubectl create namespace tailscale

helm upgrade --install tailscale-operator tailscale/tailscale-operator \
  --namespace tailscale \
  --set-string oauth.clientId="<your OAuth client ID>" \
  --set-string oauth.clientSecret="<your OAuth client secret>" \
  --values k8s/tailscale/operator-values.yaml \
  --wait

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

**Traefik dashboard over Tailscale** — this is what `k8s/traefik/dashboard-service.yaml` was for:
```bash
kubectl apply -f k8s/traefik/dashboard-service.yaml
kubectl get svc -n traefik traefik-dashboard
# EXTERNAL-IP will show a tailnet IP once the operator's claimed it — give it a minute
```
Reach it from any tailnet device at the MagicDNS name shown in the Tailscale admin console under Machines. This exact `type: LoadBalancer` + `loadBalancerClass: tailscale` pattern is the one you'll reuse for Grafana and Argo CD's dashboard once those land in a later phase — same two lines on any Service, every time.

---

## Phase 13 — End-to-end validation

Files: `k8s/example-app/`. Everything up to this point is infrastructure — this is the first proof that a request can actually travel the whole path: browser → Cloudflare → tunnel → Traefik → Service → pod.

```bash
kubectl apply -f k8s/example-app/deployment.yaml
kubectl apply -f k8s/example-app/service.yaml

# edit ingressroute.yaml with your real domain first
kubectl apply -f k8s/example-app/ingressroute.yaml
```

**Add the DNS record** in the Cloudflare dashboard: `whoami.neovara.uk` → CNAME → `<your-tunnel-id>.cfargotunnel.com` (or let the dashboard's "Add route" flow under the tunnel's Public Hostname tab do this for you — it manages the DNS record automatically when you add a route there instead of relying on the wildcard from Phase 11).

**Test the public path:**
```bash
curl https://whoami.neovara.uk
# should return whoami's output: Hostname, IP, headers — confirms the FULL
# chain: Cloudflare DNS -> tunnel -> cloudflared pod -> Traefik -> IngressRoute
# match -> Service -> one of the 2 whoami pods
```

**Test the internal path** (over Tailscale, confirming the admin plane independently of the public one):
```bash
# from a device on your tailnet
tailscale status   # confirm you're connected
kubectl get pods -n default -l app=whoami   # via the API server proxy from Phase 12
```

If the public curl works and `kubectl` works over the tailnet, every layer built in this guide is doing its job correctly — CNI, Service/ClusterIP, kube-proxy, CoreDNS, Traefik's IngressRoute matching, the tunnel's outbound connection, and the operator's API server proxy, all in one working request path.

Once confirmed, `kubectl delete -f k8s/example-app/` — this was scaffolding, not a real app.

---

## What's deferred, and why that's fine

Everything below was explicitly scoped out of this build — not forgotten, staged:

- **GitOps (Argo CD)** — once added, this is what turns "manual `kubectl apply`" into "commit and it reconciles itself," and it's also what will manage Traefik/cert-manager/cloudflared themselves going forward instead of the imperative `helm install`/`kubectl apply` commands used to bootstrap them here.
- **Monitoring** (kube-prometheus-stack) and **logging** (Loki + Grafana Alloy) — both slot in additively; nothing in this guide blocks them.
- **Secrets management (SOPS + age, later External Secrets Operator + Vault)** — for now, Kubernetes Secrets are created imperatively with `kubectl create secret` and never committed to Git in any form, encrypted or otherwise. SOPS+age is the natural first step when this gets automated (commit ciphertext to Git, decrypt-and-apply manually), with ESO/Vault as the later upgrade once real rotation and a secrets backend are wanted.
- **Full backup strategy** (Velero, off-box etcd snapshots via `--etcd-s3-*`, Proxmox VM backups) — local etcd snapshots are already running from Phase 5; shipping them off-box and adding Velero + Proxmox Backup Server is the next layer, not a redo.
- **Cilium** — deliberately deferred; recall this is the one *non-additive* item on this whole list. Flannel → Cilium isn't an upgrade, it's a rebuild (pod networking can't be live-migrated between CNIs). Treat it as its own dedicated project later — and a good real rebuild-from-Git/DR drill when you get there.
- **HA control plane** (`k3s-server-2`, `k3s-server-3`) and **more workers** — both are additive. A second/third server joins the existing embedded-etcd cluster for real quorum; a second worker just needs a new Terraform resource block and an Ansible inventory entry. This is exactly the path the embedded-etcd choice back in the design phase was making room for.

---

## Troubleshooting — sharp edges you're likely to actually hit

- **etcd complaining about disk latency** — if you ever see leader-election flakiness or slow-apply warnings, the first thing to check is whether `k3s-server-1`'s disk is genuinely on NVMe. This is the one hardware constraint in the whole design that isn't negotiable.
- **`kubectl` from your laptop hangs or refuses to connect** — almost always the kubeconfig still points at `127.0.0.1:6443` instead of the server's real IP. The Ansible role handles this automatically, but if you ever grab a fresh kubeconfig manually, remember to fix that line.
- **Traefik shows `EXTERNAL-IP: <none>` on the main Service** — that's correct, not broken. It's ClusterIP by design; there is no LoadBalancer in this topology.
- **A Service can't reach an otherwise-healthy pod** — check `targetPort` against what the container is actually listening on. `containerPort` is documentation only; nothing in the traffic path enforces it matches.
- **cert-manager's Certificate stays stuck in `False` / pending** — check `kubectl describe certificate <name> -n <ns>` and `kubectl get challenges -A`; almost always either the Cloudflare API token's permissions are wrong (needs Zone:DNS:Edit on the right zone) or the `email` fields in the ClusterIssuer don't match the Cloudflare account that issued the token.
- **cloudflared shows `Registered tunnel connection` but curl still 404s** — check the tunnel's Public Hostname route actually points at `http://traefik.traefik.svc.cluster.local:8000` and that an IngressRoute exists matching the `Host()` you're testing; a tunnel with no matching IngressRoute is a very plausible-looking dead end.
- **Ansible reruns fail on `k3s_token`** — this is deliberate (the `assert` task in both roles). Reusing the exact same token across runs is what makes the playbook idempotent instead of accidentally re-bootstrapping a new cluster identity.

---

## Repo structure recap

```
homelab-k8s/
├── GUIDE.md
├── .gitignore
├── terraform/
│   ├── versions.tf / provider.tf / variables.tf / main.tf / outputs.tf
│   └── terraform.tfvars.example
├── ansible/
│   ├── ansible.cfg / inventory.ini / site.yml / proxmox-host.yml / requirements.yml
│   ├── group_vars/{all.yml, proxmox_hosts.yml}
│   └── roles/{common, k3s_server, k3s_agent, proxmox_host/tasks/{repos,tailscale}.yml}
└── k8s/
    ├── traefik/          values.yaml, dashboard-service.yaml
    ├── cert-manager/      cluster-issuer.yaml, internal-wildcard-certificate.yaml
    ├── cloudflared/       namespace.yaml, deployment.yaml, networkpolicy.yaml
    ├── tailscale/         operator-values.yaml
    └── example-app/       deployment.yaml, service.yaml, ingressroute.yaml
```

Push this to Git now, before you forget — everything except `terraform.tfvars` and `kubeconfig` (both already gitignored) is meant to live there.
