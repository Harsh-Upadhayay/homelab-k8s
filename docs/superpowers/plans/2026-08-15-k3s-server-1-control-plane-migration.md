# k3s-server-1 Control-Plane Migration to pve-asrock Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the existing `k3s-server-1` VM (VMID 100 — the sole k3s control-plane node, embedded etcd) from `pve-dell` to `pve-asrock`'s internal motherboard SSD, preserving its identity (VMID, disk contents, IP, API endpoint) with no control-plane replacement, and leaving real free-space headroom on the destination.

**Architecture:** Both hosts are members of one Proxmox cluster (`neovara`, ADR-0049), so this is an in-cluster VM relocation (`qm migrate --with-local-disks`), not a cross-cluster export/import. The VM is stopped for the move (offline migration) rather than live-migrated, because it is a single-replica etcd store — a clean stop/copy/start is far easier to reason about and verify than a live-migrated etcd process. Terraform's `server_node_name` is reconciled to the new placement only *after* the physical move, and only via a path that a `terraform plan` confirms will not destroy/recreate the VM.

**Tech Stack:** Proxmox VE (`qm`, LVM-thin), k3s (embedded etcd, `k3s etcd-snapshot`), Terraform (`bpg/proxmox` provider ~> 0.111), kubectl, Argo CD, Longhorn.

**Spec:** `docs/migrations/immich.md` § "Control-plane placement and eventual `pve-dell` retirement" (the runbook section that defines this move's prerequisites and acceptance bar); `CLAUDE.md` § "Decisions already made" (the control-plane-placement bullet, ADR-0049 topology, and the hard constraints below).

## Global Constraints

- Never touch the laptop's internal NVMe (`pve-dell`'s `nvme0n1`) for any reason — not applicable to this move directly, but if any step touches `pve-dell` storage, confirm it's the external USB SSD (`sda`), never `nvme0n1`.
- Do not upgrade, reinstall, or modify `pve-asrock`'s Proxmox kernel or its `proxmox-default-kernel` / `proxmox-kernel-7.0*` holds under any circumstance, including as a side effect of a capacity fix.
- The VM must survive this migration as the **same** entity: same VMID (100), same disk contents (embedded etcd data, secrets-encryption keys, k3s identity), same IP (`192.168.1.21`), same Tailscale machine identity. No destroy/recreate at any layer (Proxmox or Terraform).
- Do not add a QDevice, a second/third etcd server, or any other HA member as a side effect of this move. This migration keeps `k3s-server-1` as the sole server. Real 3-node HA is a separate, future project.
- A `terraform plan` must show **zero** destroy/recreate actions on `k3s_server_1` before any `terraform apply` in this plan runs. If a plan step ever proposes replacement, stop and use the state-reconciliation fallback in Task 8 instead of applying.
- Never overcommit `pve-asrock`'s RAM (same spirit as ADR-0020's rule for `pve-dell`). `k3s-server-1` keeps its existing 4096MB — its own real usage (~2.2GiB) leaves too little slack to safely cut further. Any headroom needed for the move comes out of `k3s-worker-3`, which has ~19GiB of idle/reclaimable RAM to spare.
- **All repository changes for this migration live on git branch `feat/k3s-server-1-asrock-migration` only.** Do not merge or push to `main` at any point during execution — the user reviews the full diff and merges it themselves at the end, not incrementally. This constraint applies to git-tracked changes only; the live Proxmox/LVM/Terraform-apply operations themselves are real infrastructure changes that happen regardless of git branch (Terraform state isn't branch-scoped) — the branch isolates the *code review*, not the infrastructure timing.
- If Argo CD's reconciliation interferes with the migration window (e.g. fighting a transient state during the control-plane restart), it's acceptable to pause it (`kubectl scale deploy -n argocd argocd-application-controller --replicas=0`, or equivalent) for the duration and resume it after Task 9 verification passes. Prefer not to if the migration proceeds cleanly without it.

---

## Current State (verified 2026-08-15 — re-verify in Task 1, don't trust these blindly on execution day)

- `k3s-server-1`: VMID 100 on `pve-dell`, 4 cores / 4096MB RAM, `scsi0` disk 60G virtual on `local-lvm`, `discard=ignore`, IP `192.168.1.21`, tags `control-plane;k3s;terraform-managed`, `onboot=1`.
- Real filesystem usage inside the VM: 9.0GiB / 58G (16%). Embedded-etcd db: 1.1GB, with 24 hourly auto-snapshots already retained at `/var/lib/rancher/k3s/server/db/snapshots/` (not off-box — off-box etcd shipping is explicitly deferred platform-wide, so this VM's local disk is the only copy of its own snapshots too).
- `pve-dell`'s LVM view of `vm-100-disk-0`: 63.47% of 60G (~38GiB) — this figure is inflated by `discard=ignore` never reclaiming host-side thin blocks for data the guest has since deleted. It is not the real live-data size.
- `pve-asrock`'s `pve` volume group (on `/dev/sda3`, the 118.24GiB internal motherboard SSD): **14.75 GiB free at the VG level**, not just the thin pool. The `data` thin-pool LV is 53.93GiB, already 72.5% consumed by `k3s-worker-3`'s own disk, which itself sits at 97.7% of its 40G thin allocation.
- **Why 14.75GiB free is not good enough on its own:** even after shrinking `k3s-server-1`'s real footprint to ~10GiB, landing it in a pool with only 14.75GiB free leaves ~4–5GiB of headroom for a node meant to run indefinitely (etcd db growth, k3s upgrades, OS patching, logs). That's too tight to accept as the end state — Task 2 fixes this at the source (extend the pool) rather than by squeezing the transfer size alone.

---

## Task 1: Re-verify current state and take a pre-flight snapshot

**Systems:** `pve-dell`, `pve-asrock`, `k3s-server-1` (192.168.1.21)

**Produces:** Confirmed, current-as-of-execution values for every number in "Current State" above. If any value has drifted meaningfully (e.g., VG free space is now lower because something else grew), stop and re-plan Task 2's sizing before continuing.

- [ ] **Step 1: Confirm cluster is healthy before touching anything**

Run: `kubectl get nodes -o wide && kubectl get applications -n argocd`
Expected: all 3 nodes `Ready`; all Argo CD apps `Synced`/`Healthy`. If not, stop — do not start a control-plane migration against a degraded cluster.

- [ ] **Step 2: Re-check pve-asrock's real free VG space**

Run: `ssh -i ~/.ssh/proxmox_ed25519 root@192.168.1.51 "vgs pve; lvs -o lv_name,lv_size,data_percent --select 'lv_name=~vm-103|lv_name=~^data$'"`
Expected: `VFree` for VG `pve` is close to `14.75g` (re-confirm the exact figure — Task 2 uses `+100%FREE` so it self-corrects for small drift, but a large drop means something unexpected consumed space and needs investigating first).

- [ ] **Step 3: Re-check k3s-server-1's real disk usage**

Run: `ssh -i ~/.ssh/id_ed25519 harsh@192.168.1.21 "df -h /; sudo du -sh /var/lib/rancher/k3s/server/db"`
Expected: root filesystem usage close to `9.0G`/16%; etcd db close to `1.1G`.

- [ ] **Step 4: Confirm current VM config matches assumptions**

Run: `ssh -i ~/.ssh/proxmox_ed25519 root@pve-dell "qm config 100"`
Expected: `scsi0` line shows `discard=ignore`, `size=60G`, on `local-lvm`; `onboot: 1`; tags include `control-plane;k3s;terraform-managed`.

- [ ] **Step 5: No commit** — this task is read-only verification, nothing to commit.

---

## Task 2: Give the destination real headroom — extend pve-asrock's thin pool

**Systems:** `pve-asrock`

**Consumes:** Task 1's confirmation that `pve` VG free space is still meaningfully close to 14.75GiB, and that `pve-asrock`'s swap is still genuinely unused (re-check — Task 1 doesn't currently capture this, so verify inline below before shrinking it).
**Produces:** `pve-asrock`'s `data` thin-pool LV grown to consume all free VG space — both the original ~14.75GiB that was already unallocated, and ~6GiB reclaimed from shrinking swap — for a total pool around ~74.7GiB, giving genuine long-term headroom for both `k3s-worker-3` and the incoming `k3s-server-1`, not just enough to fit one copy operation.

This is non-disruptive — LVM supports online thin-pool extension, and swap can be safely turned off/resized/back-on live as long as it's confirmed unused first (no VM downtime required for any of this). Do this independently of, and before, the migration maintenance window.

- [ ] **Step 1: Re-confirm swap is still unused right before touching it**

Run: `ssh -i ~/.ssh/proxmox_ed25519 root@192.168.1.51 "free -h; swapon --show"`
Expected: `Swap: 8.0Gi 0B 8.0Gi` (used still `0B`). If used is nonzero, stop — something changed since the original check and swap is no longer safe to touch without investigating first.

- [ ] **Step 2: Turn swap off**

Run: `ssh -i ~/.ssh/proxmox_ed25519 root@192.168.1.51 "swapoff -a"`
Expected: command returns with no output/error; `swapon --show` afterward prints nothing (no active swap).

- [ ] **Step 3: Shrink the swap logical volume from 8G to 2G**

Run: `ssh -i ~/.ssh/proxmox_ed25519 root@192.168.1.51 "lvreduce -L 2G /dev/pve/swap"`
Expected: `Size of logical volume pve/swap changed from 8.00 GiB to 2.00 GiB.` A small, non-zero swap is kept rather than removed entirely — some emergency headroom is still worth having, and this frees the ~6GiB that was sitting idle rather than the whole thing.

- [ ] **Step 4: Recreate the swap signature and re-enable it**

Run: `ssh -i ~/.ssh/proxmox_ed25519 root@192.168.1.51 "mkswap /dev/pve/swap && swapon -a"`
Expected: `mkswap` prints a new UUID for the resized volume; `swapon --show` afterward shows `/dev/dm-0 partition 2G 0B -1` (or similar, sized at 2G). No `/etc/fstab` edit is needed — Proxmox's fstab references `/dev/pve/swap` by stable device-mapper path, not by UUID, so the regenerated UUID doesn't break anything.

- [ ] **Step 5: Extend the thin pool into all now-remaining free VG space**

Run: `ssh -i ~/.ssh/proxmox_ed25519 root@192.168.1.51 "lvextend -l +100%FREE pve/data"`
Expected: output confirms the `data` logical volume was resized, new size approximately `74.7 GiB` (exact figure depends on Task 1's re-measured free space plus the ~6GiB just reclaimed from swap — accept whatever `lvextend` reports, don't force the number above).

- [ ] **Step 6: Verify the extension landed and VG is now fully allocated**

Run: `ssh -i ~/.ssh/proxmox_ed25519 root@192.168.1.51 "vgs pve; lvs pve/data"`
Expected: `VFree` for `pve` is now ~0 (a few MiB of unavoidable slack is fine); `pve/data` size is ~74.7G.

- [ ] **Step 7: Confirm Proxmox's storage view agrees**

Run: `ssh -i ~/.ssh/proxmox_ed25519 root@192.168.1.51 "pvesm status"`
Expected: `local-lvm` row's `Total (KiB)` now reflects the larger pool (~78,000,000 KiB range, up from 56,545,280).

- [ ] **Step 8: No commit** — this is a live infrastructure change with no corresponding repo file; it will be referenced from the final documentation task.

---

## Task 3: Reclaim stale thin-pool blocks on the source disk (discard + fstrim)

**Systems:** `pve-dell`, `k3s-server-1` (192.168.1.21)

**Consumes:** Nothing from Task 2 — this can run in parallel with it, but is written second since it's the source-side half of the capacity story.
**Produces:** `vm-100-disk-0`'s real LVM-thin `Data%` drops from ~63% toward something close to its true ~16–20% live usage, so a subsequent block-level copy transfers only real data.

This step causes one brief control-plane restart (the guest OS must reboot once for the virtio-scsi controller to renegotiate discard support with the guest kernel). Expect ~1–2 minutes of API unavailability. This is acceptable for a single-server cluster during a planned maintenance window; do not run this during active use.

- [ ] **Step 1: Flip discard on for scsi0, preserving every other existing disk option**

Run: `ssh -i ~/.ssh/proxmox_ed25519 root@pve-dell "qm set 100 --scsi0 local-lvm:vm-100-disk-0,aio=io_uring,backup=1,cache=writeback,discard=on,iothread=0,replicate=1,size=60G,ssd=0"`
Expected: `update VM 100: -scsi0 ...` confirmation line, no errors.

- [ ] **Step 2: Reboot the guest once so the new discard feature is visible to the OS**

Run: `ssh -i ~/.ssh/proxmox_ed25519 root@pve-dell "qm reboot 100"` then wait ~60s and confirm it's back: `ssh -i ~/.ssh/id_ed25519 harsh@192.168.1.21 "uptime"`
Expected: SSH succeeds again within ~90 seconds; `uptime` shows a fresh boot time.

- [ ] **Step 3: Verify the guest now sees discard support**

Run: `ssh -i ~/.ssh/id_ed25519 harsh@192.168.1.21 "cat /sys/block/sda/queue/discard_max_bytes"`
Expected: a nonzero value (was `0` before the discard flag was enabled).

- [ ] **Step 4: Run fstrim inside the guest to reclaim deleted blocks**

Run: `ssh -i ~/.ssh/id_ed25519 harsh@192.168.1.21 "sudo fstrim -av"`
Expected: output line like `/: X GiB (Y bytes) trimmed`, where Y is a large positive number (multiple GiB) — this is the actual proof the stale blocks existed and are now being released to the host thin pool.

- [ ] **Step 5: Confirm the host-side thin allocation actually shrank**

Run: `ssh -i ~/.ssh/proxmox_ed25519 root@pve-dell "lvs -o lv_name,lv_size,data_percent --select 'lv_name=~vm-100-disk'"`
Expected: `Data%` for `vm-100-disk-0` has dropped substantially from `63.47` — should now be roughly in the 20–30% range (not necessarily identical to the guest's 16% filesystem usage, because thin-pool blocks are reclaimed in fixed chunk sizes larger than individual files, but it should be a clear, large drop, not a rounding-error change).

- [ ] **Step 6: No commit** — infrastructure-only change, nothing in the repo to commit yet.

---

## Task 4: Take a fresh, verified etcd snapshot immediately before the move

**Systems:** `k3s-server-1` (192.168.1.21)

**Consumes:** Nothing new — the automatic hourly snapshots already exist; this step adds one taken right before the migration window, as the actual rollback artifact for this specific operation.
**Produces:** A named, on-demand snapshot the executor can point to if anything about the migration goes wrong, plus written confirmation of what "rollback" means for this specific migration (below).

**Rollback path for this migration, stated explicitly (this satisfies the runbook's "tested rollback path" requirement without a full disaster-recovery drill, which is out of scope for a routine VM relocation):** this is an *offline* migration — the VM is cleanly shut down before any disk data moves, and `qm migrate` does not delete the source disk until the transfer to the destination is confirmed complete. If the migration fails or hangs partway, the source VM and its disk on `pve-dell` are untouched and the VM can simply be started again on `pve-dell` with `qm start 100` — no etcd data is ever at risk during a failed *transfer*. The on-demand snapshot below exists as the second line of defense: if the destination disk becomes corrupted *after* a successful copy (e.g., a bad `pve-asrock` write), `k3s server --cluster-reset --cluster-reset-restore-path=<snapshot>` restores etcd from this exact point in time.

- [ ] **Step 1: Take a named on-demand snapshot**

Run: `ssh -i ~/.ssh/id_ed25519 harsh@192.168.1.21 "sudo k3s etcd-snapshot save --name pre-asrock-migration"`
Expected: command completes and prints the snapshot's path/name, e.g. `.../pre-asrock-migration-<timestamp>`.

- [ ] **Step 2: Verify it's listed and has a sane, nonzero size**

Run: `ssh -i ~/.ssh/id_ed25519 harsh@192.168.1.21 "sudo k3s etcd-snapshot list | grep pre-asrock-migration"`
Expected: one row, size in the same ~28–30MB ballpark as the existing hourly snapshots (a 0-byte or missing entry means the snapshot failed — stop and investigate before proceeding).

- [ ] **Step 3: No commit** — the snapshot lives on the VM's own disk (which is exactly what's being migrated, so it travels with the move automatically).

---

## Task 5: Shrink k3s-worker-3's RAM to create host headroom on pve-asrock

**Systems:** `pve-asrock`, `k3s-worker-3` (192.168.1.24)
**Files:** Modify `terraform/proxmox/terraform.tfvars` (`workers.k3s-worker-3.memory`)

**Consumes:** Confirmed real usage on both sides: `k3s-server-1` uses ~2.2GiB of its existing 4096MB (too little slack to cut further — stays unchanged) and `k3s-worker-3` uses only ~7.5GiB of its 28,672MB (19GiB idle) — there's plenty to give up here instead.
**Produces:** `pve-asrock` host RAM demand drops from 28,672MB (worker-3 alone, already exceeding what's left once `server-1`'s 4096MB is added) to 24,576MB + 4096MB = 28,672MB total — leaving ~3.3GiB of host reserve once `server-1` lands in Task 6, instead of a negative one.

This is done as a live hot-unplug memory resize (the `bpg/proxmox`-managed VM does not use `hotplug` ballooning here, so this requires a VM reboot to take effect cleanly — schedule it as part of this same maintenance window, not during active use of dependent workloads on `k3s-worker-3`).

- [ ] **Step 1: Confirm current usage one more time immediately before the change**

Run: `ssh -i ~/.ssh/id_ed25519 harsh@192.168.1.24 "free -h"`
Expected: `used` close to `7.5Gi`, confirming nothing has grown enough to make a 4GB cut unsafe since it was last checked.

- [ ] **Step 2: Reduce the live VM's memory allocation on pve-asrock**

Run: `ssh -i ~/.ssh/proxmox_ed25519 root@192.168.1.51 "qm set 103 --memory 24576"`
Expected: `update VM 103: -memory 24576` confirmation, no errors.

- [ ] **Step 3: Reboot k3s-worker-3 so the new memory ceiling takes effect**

Run: `ssh -i ~/.ssh/proxmox_ed25519 root@192.168.1.51 "qm reboot 103"` then wait ~45s and confirm: `ssh -i ~/.ssh/id_ed25519 harsh@192.168.1.24 "uptime"`
Expected: SSH succeeds again within ~90 seconds; `uptime` shows a fresh boot time.

- [ ] **Step 4: Confirm the node rejoined the cluster healthy at the new size**

Run: `kubectl get node k3s-worker-3 -o wide && kubectl describe node k3s-worker-3 | grep -A5 Allocatable`
Expected: `STATUS Ready`; allocatable memory reflects the new ~24GiB ceiling, not the old ~28GiB.

- [ ] **Step 5: Confirm host RAM math now works before proceeding to the actual control-plane migration**

Run: `ssh -i ~/.ssh/proxmox_ed25519 root@192.168.1.51 "free -h"`
Expected: `free`/`available` has grown by roughly 4GiB compared to Task 1's baseline reading — this is the headroom Task 6 will consume when `k3s-server-1` (4096MB) lands here.

- [ ] **Step 6: Update Terraform so this doesn't drift back on the next apply**

Edit `terraform/proxmox/terraform.tfvars`, in the `k3s-worker-3` block: change `memory = 28672` to `memory = 24576`.

- [ ] **Step 7: Commit on the migration branch**

```bash
git add terraform/proxmox/terraform.tfvars
git commit -m "chore(terraform): shrink k3s-worker-3 memory 28672->24576MB

Frees host RAM headroom on pve-asrock ahead of the k3s-server-1
control-plane migration. worker-3's real usage is ~7.5GiB against
its old 28GiB allocation, so this leaves it ~16.5GiB of slack while
giving the host the reserve it needs to avoid overcommitting once
the control-plane VM (4096MB, unchanged) lands here."
```

---

## Task 6: Cleanly stop k3s-server-1 and migrate it to pve-asrock

**Systems:** `pve-dell`, `pve-asrock`, `k3s-server-1`

**Consumes:** Task 2's extended pool (destination has real headroom) and Task 3's shrunk source disk (transfer size is now close to real usage, not the stale 38GiB figure).
**Produces:** `k3s-server-1` (VMID 100) running on `pve-asrock`, disk relocated to `pve-asrock`'s `local-lvm`, same VMID/IP/disk contents, `pve-dell` no longer holds this VM.

- [ ] **Step 1: Gracefully stop the k3s server process before shutting down the VM**

Run: `ssh -i ~/.ssh/id_ed25519 harsh@192.168.1.21 "sudo systemctl stop k3s"`
Expected: command returns with no error; `sudo systemctl status k3s` shows `inactive (dead)`.

- [ ] **Step 2: Shut down the VM cleanly**

Run: `ssh -i ~/.ssh/proxmox_ed25519 root@pve-dell "qm shutdown 100 --timeout 60"`
Expected: `qm list` on `pve-dell` shows VMID 100 as `stopped`.

- [ ] **Step 3: Migrate the VM to pve-asrock, bringing its local disk along**

Run: `ssh -i ~/.ssh/proxmox_ed25519 root@pve-dell "qm migrate 100 pve-asrock --with-local-disks --targetstorage local-lvm"`
Expected: command streams progress and finishes with `migration finished successfully`. This can take a few minutes even at the reduced ~10GiB size over a LAN link — do not interrupt it.

- [ ] **Step 4: Confirm the VM now lives on pve-asrock**

Run: `ssh -i ~/.ssh/proxmox_ed25519 root@192.168.1.51 "qm list"`
Expected: VMID 100 (`k3s-server-1`) now appears here, `stopped`.

- [ ] **Step 5: Confirm it's gone from pve-dell**

Run: `ssh -i ~/.ssh/proxmox_ed25519 root@pve-dell "qm list"`
Expected: VMID 100 no longer listed (only `k3s-worker-1` and the template remain).

- [ ] **Step 6: Start the VM on its new host**

Run: `ssh -i ~/.ssh/proxmox_ed25519 root@192.168.1.51 "qm start 100"`
Expected: command returns cleanly; `qm status 100` on `pve-asrock` reports `running`.

- [ ] **Step 7: Confirm k3s came back up and the API is reachable at the same IP**

Run: (wait ~30s, then) `kubectl get nodes -o wide`
Expected: all 3 nodes `Ready`, `k3s-server-1`'s `INTERNAL-IP` still shows `192.168.1.21` — the IP is guest-level config baked into the disk, unaffected by which physical host runs the VM.

- [ ] **Step 8: No commit** — this is a live Proxmox operation; Terraform state reconciliation happens in Task 8, after Task 7's decision gate.

---

## Task 7: Determine Terraform's reaction before touching state (decision gate)

**Systems:** local workstation (Terraform working directory `terraform/proxmox/`)

**Consumes:** Task 6's completed physical migration (the VM already lives on `pve-asrock` — Terraform state still says `pve-dell`).
**Produces:** A concrete, observed answer to "does changing `server_node_name` make the `bpg/proxmox` provider propose an in-place update, or a destroy/recreate?" — this determines which branch of Task 8 to execute. This is a required review checkpoint per the runbook: **"A valid plan must show no control-plane replacement."**

- [ ] **Step 1: Update the tfvars value to match the new physical placement**

Edit `terraform/proxmox/terraform.tfvars` line 16: change `server_node_name = "pve-dell"` to `server_node_name = "pve-asrock"`.

- [ ] **Step 2: Run a plan and read the proposed action type carefully — do not apply yet**

Run: `cd terraform/proxmox && terraform plan`
Expected output is one of two shapes:
  - **In-place update:** the plan shows `~ update in-place` for `proxmox_virtual_environment_vm.k3s_server_1` with only `node_name` changing (`~ node_name = "pve-dell" -> "pve-asrock"`), or even `0 to add, 0 to change, 0 to destroy` if the provider treats this as informational/already-reconciled.
  - **Forced replacement:** the plan shows `-/+ destroy and then create replacement` for the same resource, with `node_name` listed as a `# forces replacement` field.

- [ ] **Step 3: Record which shape was observed**

This is the decision gate — write down (in the terminal output, or a scratch note) which of the two shapes appeared. Task 8 has one branch for each; use the one matching what was actually observed. Do not proceed to `terraform apply` from this task under any circumstance — Task 8 handles the actual state change, deliberately, in the correct branch.

- [ ] **Step 4: No commit yet** — `terraform.tfvars` is already edited on disk (needed for the `plan` to run) but stays uncommitted until Task 8 confirms which path was taken and completes it successfully.

---

## Task 8: Reconcile Terraform state (branch per Task 7's observed result)

**Systems:** local workstation (Terraform working directory `terraform/proxmox/`)

**Consumes:** Task 7's recorded decision (in-place update vs. forced replacement).
**Produces:** `terraform plan` reports zero pending changes for `k3s_server_1`, with state correctly reflecting `pve-asrock` as its `node_name` — and the VM was never destroyed or recreated by Terraform, regardless of which branch ran.

### Branch A — Task 7 observed an in-place update (or zero changes)

- [ ] **Step A1: Apply the in-place update**

Run: `cd terraform/proxmox && terraform apply`
Expected: plan reprinted matches Task 7's Step 2 output exactly (no drift since then); type `yes`; apply completes with `Apply complete! Resources: 0 added, 1 changed, 0 destroyed.` (or `0 changed` if it was already a no-op plan).

- [ ] **Step A2: Confirm zero drift remains**

Run: `terraform plan`
Expected: `No changes. Your infrastructure matches the configuration.`

- [ ] **Step A3: Commit the tfvars change**

```bash
git add terraform/proxmox/terraform.tfvars
git commit -m "chore(terraform): move k3s-server-1 control plane to pve-asrock

Physical VM migrated via qm migrate --with-local-disks; provider
applied the node_name change in place, no control-plane replacement."
```

### Branch B — Task 7 observed a forced replacement

- [ ] **Step B1: Revert the tfvars edit so state and config agree on the OLD value for a moment**

Run: `cd terraform/proxmox && git checkout -- terraform.tfvars` (this restores `server_node_name = "pve-dell"` in the file only — Terraform state still (correctly) doesn't know about the physical move yet either).

- [ ] **Step B2: Remove the stale resource from state without touching real infrastructure**

Run: `terraform state rm proxmox_virtual_environment_vm.k3s_server_1`
Expected: `Removed proxmox_virtual_environment_vm.k3s_server_1` confirmation. This only edits Terraform's bookkeeping — the actual VM on `pve-asrock` is completely unaffected by this command.

- [ ] **Step B3: Re-apply the tfvars edit now that state has no opinion about node_name**

Edit `terraform/proxmox/terraform.tfvars` line 16 again: `server_node_name = "pve-dell"` → `server_node_name = "pve-asrock"`.

- [ ] **Step B4: Re-import the existing VM at its new location**

Run: `terraform import proxmox_virtual_environment_vm.k3s_server_1 pve-asrock/100`
Expected: `Import successful!` — this attaches Terraform's state to the VM that already exists at VMID 100 on `pve-asrock`; it does not create anything.

- [ ] **Step B5: Confirm zero drift**

Run: `terraform plan`
Expected: `No changes. Your infrastructure matches the configuration.` If any diff appears (e.g., in disk options Task 3 changed, like `discard=on`), reconcile the Terraform config (`main.tf`'s `disk` block) to match reality rather than letting a future `apply` silently flip it back to `discard=ignore` — the config is the source of truth going forward, so if `discard=on` is being kept permanently, add it to the resource block now.

- [ ] **Step B6: Commit**

```bash
git add terraform/proxmox/terraform.tfvars terraform/proxmox/main.tf
git commit -m "chore(terraform): reconcile k3s-server-1 state after manual move to pve-asrock

Provider forces replacement on node_name change, so the physical
qm migrate was done out-of-band and state was re-imported at the
new location rather than letting Terraform destroy/recreate the
control-plane VM."
```

---

## Task 9: Full post-move verification

**Systems:** local workstation, cluster-wide

**Consumes:** Task 6 (VM running on `pve-asrock`) and Task 8 (Terraform reconciled).
**Produces:** Explicit, checked confirmation across every system the runbook requires before this migration counts as done: etcd health, kubectl access, all nodes, Argo CD, Longhorn, DNS, Traefik, and both a public and a private application path.

- [ ] **Step 1: etcd health**

Run: `ssh -i ~/.ssh/id_ed25519 harsh@192.168.1.21 "sudo k3s etcd-snapshot list | tail -5"`
Expected: the pre-migration snapshot from Task 4 is present, and hourly auto-snapshots have resumed on schedule (a new one within the last hour if enough time has passed since the restart).

- [ ] **Step 2: kubectl access from this workstation**

Run: `kubectl get nodes -o wide`
Expected: all 3 nodes `Ready`; `k3s-server-1` still shows `INTERNAL-IP 192.168.1.21`.

- [ ] **Step 3: Argo CD application health**

Run: `kubectl get applications -n argocd`
Expected: all applications `Synced` / `Healthy` (same full list as before the migration — `alloy`, `argo-rollouts`, `argocd`, `audiobookshelf`, `cert-manager`, `cloudflared`, `external-secrets`, `immich`, `kiroku`, `kiroku-governance`, `kube-prometheus-stack`, `loki`, `longhorn`, `nextcloud`, `root`, `tailscale`, `traefik`, `workbench`).

- [ ] **Step 4: Longhorn volume health**

Run: `kubectl get volumes.longhorn.io -n longhorn-system`
Expected: every volume `attached` / `healthy`, same as pre-migration (the control-plane move doesn't touch Longhorn replicas, but this confirms the brief control-plane blip in Task 6 didn't destabilize anything).

- [ ] **Step 5: CoreDNS is answering**

Run: `kubectl run -it --rm dns-check --image=busybox:1.36 --restart=Never -- nslookup nextcloud.nextcloud.svc.cluster.local`
Expected: resolves to a ClusterIP, no timeout.

- [ ] **Step 6: cloudflared tunnel is up**

Run: `kubectl get pods -n cloudflare` and `kubectl logs -n cloudflare -l app=cloudflared --tail=20`
Expected: pod `Running`, recent log lines show active connections to Cloudflare's edge, no repeated connection-error loops.

- [ ] **Step 7: Public application path (Traefik + cloudflared)**

Run: `curl -sI https://nextcloud.neovara.uk | head -5`
Expected: `HTTP/2 200` (or a Nextcloud redirect like `302`), not a connection error or 5xx.

- [ ] **Step 8: Private application path (Tailscale + Traefik internal)**

Run: `curl -sI https://immich.in.neovara.uk | head -5` (from a machine on the tailnet)
Expected: a valid HTTP response (200/302), not a connection timeout.

- [ ] **Step 9: No commit** — this task is verification only.

---

## Task 10: Update documentation to reflect the new topology

**Files:**
- Modify: `CLAUDE.md` (architecture diagram + the "Preferred control-plane placement" bullet)
- Modify: `ROADMAP.md` (close out issue #49)
- Modify: `CHANGELOG.md` (add an entry)
- Modify: `docs/migrations/immich.md` (mark the "Control-plane placement" runbook section as completed, with the date and actual outcome)

**Consumes:** Task 9's full green verification.
**Produces:** The repo's documentation matches reality — `pve-dell` is no longer where the control plane lives, and issue #49 is closed rather than left open against completed work.

- [ ] **Step 1: Update CLAUDE.md's architecture diagram and control-plane bullet**

In `CLAUDE.md`, move `k3s-server-1` from the `pve-dell` block to the `pve-asrock` block in the ASCII architecture diagram, and update the "Preferred control-plane placement after Immich recovery" bullet under "Decisions already made" to state the move is complete (with date), rather than still "preferred"/future-tense. Keep the note about needing real 3-node HA later and about Longhorn-replica-driven Dell-outage continuity being a separate, still-open concern.

- [ ] **Step 2: Close out issue #49 in ROADMAP.md**

In `ROADMAP.md`, update the line currently reading "The existing control-plane VM move remains #49" to reflect completion, matching the style of the adjacent completed roadmap bullets (e.g., how the `k3s-worker-3` addition bullet above it is written).

- [ ] **Step 3: Add a CHANGELOG entry**

Add a dated `CHANGELOG.md` entry summarizing: `k3s-server-1` moved from `pve-dell` to `pve-asrock`'s internal SSD via offline `qm migrate --with-local-disks`, preserving VMID/IP/etcd data; `pve-asrock`'s `local-lvm` thin pool was extended to consume all free VG space first to provide real headroom; discard was enabled and the source disk trimmed pre-move to shrink the real transfer size; Terraform state was reconciled via [in-place update / import — use whichever branch Task 8 actually took].

- [ ] **Step 4: Mark the immich.md runbook section as completed**

In `docs/migrations/immich.md`, under "Control-plane placement and eventual `pve-dell` retirement", add a note (don't delete the original planning text — it documents the reasoning) stating the move completed on this date, referencing this plan file, and confirming which of its 4 listed prerequisites were satisfied and how.

- [ ] **Step 5: Commit the documentation update**

```bash
git add CLAUDE.md ROADMAP.md CHANGELOG.md docs/migrations/immich.md
git commit -m "docs: record k3s-server-1 control-plane move to pve-asrock (closes #49)"
```

---

## Risks / Explicitly Out of Scope

- **`k3s-worker-3`'s own disk is at 97.7% of its 40G thin allocation** on the same `pve-asrock` pool this plan extends. This plan does not touch it. Task 2's pool extension buys it some slack too (same shared pool), but if it keeps growing it will need its own capacity fix — flag as a follow-up, don't fold it into this migration.
- **This migration does not, by itself, give Dell-outage application continuity.** The control plane moving to `pve-asrock` only means the *API* survives a `pve-dell` failure. Longhorn replicas for critical PVCs (e.g., Immich/Nextcloud databases) still need healthy copies on `pve-asrock`/`k3s-worker-3` before that claim can be made — this is explicitly called out as separate follow-on work in the source runbook and is not part of this plan.
- **This plan does not create real etcd HA.** `k3s-server-1` remains the sole server. A genuine 3-node embedded-etcd control plane is deliberately deferred (see `CLAUDE.md`'s "Explicitly deferred" section).
- **Off-box etcd backup shipping remains deferred.** The Task 4 snapshot and the existing hourly snapshots all live on the same disk being migrated — they protect against a bad migration, not against losing that disk entirely later. That's a pre-existing, accepted gap this plan doesn't change.

---

## Self-Review Notes

- **Spec coverage:** all 4 runbook prerequisites are covered — real capacity inspection (Task 1/2), verified snapshot + rollback path (Task 4), RAM headroom (Task 5), reviewed migration plan preserving the VM (Tasks 6–8, with an explicit no-replacement gate), full post-move verification (Task 9). Documentation currency (a `CLAUDE.md` requirement: "don't silently change" decisions — so once changed, the doc must say so) is covered by Task 10.
- **No placeholders:** every step has a concrete command and an expected, checkable output; Task 7/8's decision gate is fully specified on both branches rather than "handle appropriately."
- **Type/name consistency:** VMID 100, IP `192.168.1.21`, resource name `proxmox_virtual_environment_vm.k3s_server_1`, tfvars key `server_node_name`, and storage id `local-lvm` are used identically across every task that references them.
