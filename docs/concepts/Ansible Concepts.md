# Ansible Concepts

> Back to [[Homelab Learning Map]] · See also [[Platform Concepts]] · Decisions in [[README|ADR index]]

No agents, no daemons on the target — Ansible SSHs in, runs small Python-backed modules, and each one reports whether it changed anything. Everything below is organization on top of that one idea.

## Inventory and connection

**Inventory groups and hosts.** A group (`[proxmox_hosts]`) is an arbitrary label you invent; each line under it is an *inventory hostname* — a name Ansible uses to refer to the host in output and logs. That name never has to resolve to anything (`ansible/inventory.ini`).

**`ansible_host` is the real connection target.** It's the value actually handed to SSH. Conflating the inventory hostname with `ansible_host` is a trap if the label only resolves via something outside the repo (e.g. a personal `~/.ssh/config` alias) — we hit exactly this when `pve-dell` only worked on one machine. The normal inventory now uses portable Tailscale MagicDNS names and records LAN IP fallbacks for bootstrap/recovery (`ansible/inventory.ini`).

**Group-scoped connection vars.** `[proxmox_hosts:vars]` sets `ansible_user` and `ansible_ssh_private_key_file` for every host in that group — the same information you'd pass to `ssh -i <key> user@host` manually, just declared once for the whole group instead of typed per connection (`ansible/inventory.ini`).

**`group_vars/<groupname>.yml` auto-loading.** Ansible automatically loads `group_vars/proxmox_hosts.yml` for any host in the `proxmox_hosts` group — no explicit include, the filename *is* the wiring. That's where `debian_codename: "trixie"` lives, used later via Jinja2 templating (`ansible/group_vars/proxmox_hosts.yml`).

**`ansible.cfg` defaults.** Sets the default inventory file, roles path, and privilege-escalation behavior so you don't pass `-i inventory.ini` on every invocation (`ansible/ansible.cfg`).

## Playbooks, plays, and roles

**A playbook is a list of plays.** Each play maps a group of hosts (`hosts:`) to work to do (`roles:` or `tasks:`). `ansible/proxmox.yml` runs ASRock safeguards, Dell safeguards, and then common host/cluster policy as three ordered plays.

**`become` / privilege escalation.** Global default is `become = True` (sudo) in `ansible.cfg`, but a play can override it — every Proxmox play sets `become: false` because `ansible_user` is already `root`; there's nothing to escalate to (`ansible/proxmox.yml`).

**Roles are a directory convention, not special syntax.** `roles/<name>/tasks/main.yml` is the entry point Ansible looks for automatically when a playbook lists that role — no explicit file path needed (`ansible/roles/proxmox_host/`).

**Split a role only where the split preserves a real lifecycle boundary.** `proxmox_host` keeps repository configuration and disruptive package maintenance in separate task files because maintenance is explicit-only and fail-closed. Tailscale is a separate reusable role because it applies to both Debian hypervisors and Ubuntu k3s guests. The one-task hostname mapping stays inline in `proxmox_cluster/tasks/main.yml`; a separate file would add navigation without cohesion.

## Tasks, modules, and idempotency

**A task is a module plus its arguments.** The module (`ansible.builtin.copy`, `ansible.builtin.apt`, `ansible.builtin.systemd`, `ansible.builtin.get_url`) does the work; idempotency lives inside the module's own logic, not in the YAML you write. `copy` compares the target file's current content against what you declared and only writes on a real difference (`ansible/roles/proxmox_host/tasks/repositories.yml`).

**Declarative file management via `copy` + inline `content:`.** Instead of scripting "edit this file," you declare the file's entire desired content and let the module diff it — this is how the Proxmox `.sources` files are managed (`ansible/roles/proxmox_host/tasks/repositories.yml`).

**Package-state modules check before acting.** `ansible.builtin.apt` with `upgrade: full` inspects installed vs. available versions and only acts on the delta — rerun it on an up-to-date system and it reports no change, every time.

**Tags select lifecycle operations, and `never` makes opt-in intent real.** `tailscale`, `terraform-api`, `maintenance-upgrade`, and `e1000e-aspm` all carry `never`, so a bare playbook cannot request a secret, reveal a one-time token, upgrade packages, or stage a reboot. Safe configuration still runs by default.

## Making non-idempotent commands safe

**`ansible.builtin.command` has no built-in idempotency.** Unlike `copy`/`apt`, a raw command module has no idea whether it changed anything — left alone it reports `changed` on every run, forever. Idempotency has to be hand-rolled around it (`ansible/roles/tailscale_host/tasks/main.yml`).

**`register` captures a task's output into a variable**, exactly like assigning a return value — used to capture `tailscale status --json` for inspection by a later task (`ansible/roles/tailscale_host/tasks/main.yml`).

**`changed_when` / `failed_when` override a module's default reporting.** The status-check task is a pure probe, never a real change or failure, even if the current connection state is unhealthy (`ansible/roles/tailscale_host/tasks/main.yml`).

**`when:` conditionals + Jinja2 filters gate a task's execution.** `(ts_status.stdout | from_json).BackendState != "Running"` parses JSON output with the `from_json` filter and reads a field from it, so `tailscale up` only actually runs if the host isn't already connected — this is what makes an inherently non-idempotent `command` safe to rerun (`ansible/roles/tailscale_host/tasks/main.yml`).

**`no_log: true` suppresses a task's output from logs entirely.** Used on the `tailscale up --authkey=...` task so the secret never appears in plaintext in the terminal or any saved log (`ansible/roles/tailscale_host/tasks/main.yml`).

## Execution and safety habits

**`--check --diff` is a dry run.** `--check` reports what a module *would* do without doing it; `--diff` shows before/after content for file-based tasks like `copy`. Not perfect — command probes explicitly use `check_mode: false` when later assertions need live state — but useful for file-based changes (`ansible-playbook proxmox.yml --tags repositories --check --diff`).

**Live read probes can still run during check mode.** `check_mode: false` on `pveum`, `pvecm`, kernel, and Tailscale status commands does not authorize a mutation; it means dry-run execution may read the real state required by later assertions. Their `changed_when: false` keeps the report honest.

**`--extra-vars` passes runtime secrets without writing them to disk.** Same pattern used for the k3s join token: `ansible-playbook proxmox.yml --tags tailscale --extra-vars "tailscale_auth_key=..."` — the value lives only in that one invocation, never in a tracked file.

**`ansible <group> -m ping` is a connectivity smoke test**, independent of any playbook — confirms SSH auth and that Python exists on the remote end before trusting a real run.

**`ansible-galaxy collection install -r requirements.yml`** installs modules that aren't in `ansible.builtin` (here, `community.general` and `ansible.posix`, used by the k3s roles) — separate from the `ansible` package itself, which is why it's its own step (`ansible/requirements.yml`).

## Templates, handlers, and service lifecycle (Phase 4–6)

**`ansible.builtin.template` renders a separate `.j2` file with full Jinja2 control flow**, not just `{{ variable }}` substitution — unlike `copy` + inline `content:` (used for the simpler Proxmox `.sources` files), `template` supports real loops: `{% for san in k3s_tls_sans %} - "{{ san }}" {% endfor %}` generates one YAML list item per entry in a list variable. Reach for `template` the moment a file needs a loop or conditional, not just static values dropped in (`ansible/roles/k3s_server/templates/config.yaml.j2`).

**Handlers run once, at the end of the play, only if notified by a `changed` task.** `notify: restart k3s` on the config-write task queues the `restart k3s` handler; it doesn't fire immediately, and it won't fire at all if the config task reports no change. Two guarantees make this safe to reason about: only a `changed` task can trigger a notify (no needless restarts on routine reruns), and a handler fires *at most once per play* no matter how many tasks notify it. `listen:` decouples further — a task can notify a topic string, and multiple differently-named handlers can each `listen:` to that same topic, so one notify fans out to several.

Walking the actual task order shows why the *timing* (end-of-play, not inline) matters. Fresh install: config-write is `changed` → queues the restart → install/enable/wait/fetch all run → the queued restart fires at the end. On a rerun where only `k3s_tls_sans` changes, the handler restart is what applies the new config to the live process. An unconditional restart task would instead blip etcd on every run (`ansible/roles/k3s_server/handlers/main.yml`, ADR-0008/ADR-0010).

**Version convergence needs semantic state, not file existence.** Merely checking that `/usr/local/bin/k3s` exists would permanently skip future pinned upgrades. The server and agent roles read `k3s --version`, compare it with `k3s_version`, and run the installer only when missing or mismatched. The same read/compare/change pattern is used for Tailscale connection state and Proxmox ACLs.

**`environment:` passes env vars to one task's execution**, without modifying the script being run — the structured equivalent of `INSTALL_K3S_EXEC=server /tmp/install-k3s.sh` in a shell, scoped only to that task, never persisted on the remote system or leaked to other tasks. `install-k3s.sh` reads its config from env vars rather than CLI flags — a common pattern for scripts distributed via `curl | sh`, since it lets behavior be configured without touching the piped script's content. Settable at play/block level too, not just per-task, when several tasks need the same vars (`ansible/roles/k3s_server/tasks/main.yml`).

**`wait_for` polls for an observable readiness signal, because "the command returned" isn't "the service is ready."** `install-k3s.sh` runs `systemctl start` and exits the instant the process is *forked* — it doesn't wait for etcd bootstrap, TLS cert generation, or the API server actually accepting requests, all of which take real time afterward. Two different signals are used here because they mean different things: `port: 6443` only proves *a process is listening* (weak — could be alive but not yet functional); `path: /var/lib/rancher/k3s/server/node-token` existing means the *entire* internal bootstrap sequence completed, since that file is one of the last things k3s generates. Checking the weaker signal first, then the stronger one, avoids the race of fetching a kubeconfig that doesn't exist yet or is mid-write. General principle: any time automation crosses an async boundary (start a background service, kick off something that outlives the triggering command), an explicit readiness check is required before depending on it — "the command returned" is never sufficient on its own (`ansible/roles/k3s_server/tasks/main.yml`).

**`ansible.builtin.fetch` pulls a file from the remote host back to the control machine** — the mirror of `copy`/`template`, which push local content out. `flat: true` skips `fetch`'s default per-hostname subdirectory nesting (useful when fetching the same path from many hosts at once, to avoid collisions; unnecessary noise with exactly one server here). The reason this copy has to happen at all: `kubectl` is a pure network client — it talks to the API server over HTTPS, with no requirement to run *on* a cluster node — so managing the cluster from a workstation (rather than SSHing into the server for every command) is the normal way Kubernetes is used, not a quirk of this repo. Left un-fetched, the kubeconfig also stays root-only (`0600`) on the server, unusable without `sudo` even there (`ansible/roles/k3s_server/tasks/main.yml`).

**`delegate_to: localhost` runs one task's actions on the control machine, regardless of the play's actual target.** Rewriting the just-fetched kubeconfig's server URL has to happen on the workstation, not on `k3s-server-1` — even though every other task in this same play targets the server, and the file at that path means something different (or doesn't meaningfully exist) there. `become: false` follows for a practical reason, not just neatness: the play's inherited default is `become: true`, and leaving that on while delegating to `localhost` would make Ansible try to `sudo` on *your own workstation* — unnecessary, since you already own the file `fetch` just wrote, and it could even interactively prompt for your own password, breaking a supposedly non-interactive run (`ansible/roles/k3s_server/tasks/main.yml`).

**`ansible.builtin.replace` is regex find-and-replace *within* file content**, distinct from `lineinfile` (whole-line match/replace, used in the `k3s_node` role to comment out swap in `/etc/fstab`). The kubeconfig line is `server: https://127.0.0.1:6443`; only the address needs to change. `replace` preserves that minimal diff (`ansible/roles/k3s_server/tasks/main.yml`).

**`ansible.builtin.assert` stops the whole play immediately with a custom message**, unlike `when:` (which silently skips just one task and lets the play continue as if nothing happened). The alternative worth contrasting against: guarding every downstream task with `when: k3s_token is defined` instead of asserting up front. Forget to pass the token under that design, and every task would report "skipping" — the playbook would finish with **exit code 0**, i.e. success, having done nothing at all. A silent no-op "success" is a worse failure mode than a loud crash; it's exactly the kind of bug that costs an hour later wondering why the cluster still isn't up despite green output. `fail_msg:` is what makes the loud version actually useful — the real message here doesn't just say the token is missing, it gives the exact command to fix it plus the operational caveat to reuse the same value on every rerun. Decision rule: `when:` for a legitimate branch in the logic; `assert` for a precondition where nothing downstream could possibly be correct if it's not met (`ansible/roles/k3s_server/tasks/main.yml`).

**`--extra-vars` sits at the top of Ansible's variable precedence chain**, above `group_vars`, host_vars, and role defaults — it always wins, no exceptions. That's exactly the property wanted for `k3s_token`, which doesn't appear in *any* `group_vars` file, not even as a placeholder: if someone had added `k3s_token: "changeme"` there "just so it's not undefined," every fresh clone of this public repo would silently bootstrap with the same, GitHub-visible join secret unless someone remembered to override it. Because the variable exists nowhere except the command line, there's no low-precedence fallback it could ever silently inherit — combined with the `assert` above, either a real token is supplied explicitly or the playbook refuses to proceed; no silently-insecure third option. Contrast `k3s_version`, which *does* have a checked-in default (it isn't a secret) — `--extra-vars` there is an escape hatch for a one-off override, not the only way the variable can ever get a value. Same mechanism, two different purposes depending on whether the variable is a secret (`ansible/group_vars/all.yml`).

## Splitting changes by blast radius, and templating a live fix into a unit (Phase: e1000e)

**Split changes with different *disruption profiles* into separate task files with separate tags, so each can be run at the moment its disruption is acceptable.** The Dell e1000e fix has a live, non-disruptive offload half and a GRUB/reboot ASPM half. They remain `e1000e_offload.yml` (default-safe tag `e1000e`) and `e1000e_aspm.yml` (explicit-only tag `e1000e-aspm`).

**Make a non-idempotent command idempotent by reading state first and gating the change on it.** `ethtool -K` always exits 0 and prints nothing, so a bare `command` task reports `changed` every run. The fix is a `command: ethtool -k <iface>` with `changed_when: false` + `register`, then the `ethtool -K` task carries `when: "'...-offload: on' in register.stdout"` — it only runs (and only reports changed) when something is actually still on. Same shape as the general "make non-idempotent commands safe" move above, applied to a host tweak.

**Persist a live fix by templating a systemd oneshot, not by editing a managed config file.** The offload change has to survive reboot, but editing `/etc/network/interfaces` with `lineinfile` risks being silently dropped when Proxmox's GUI regenerates that file. A `copy`-ed `/etc/systemd/system/e1000e-offload.service` (`Type=oneshot`, `RemainAfterExit=yes`, `After=network-online.target`, `WantedBy=multi-user.target`) is self-contained and survives. The enable task uses the systemd module with `state: "{{ 'restarted' if unit_file.changed else 'started' }}"` — re-run the unit only if the template changed, otherwise a no-op start.

**Idempotent kernel-cmdline edit: `lineinfile` with `backrefs: yes`, guarded by a pre-read so a re-run doesn't append twice.** Appending `pcie_aspm=off` to `GRUB_CMDLINE_LINUX_DEFAULT="quiet"` wants to produce `"quiet pcie_aspm=off"` once and then never change. The regexp `^(GRUB_CMDLINE_LINUX_DEFAULT=")(.*)(")$` with `backrefs: yes` and `line: \1\2 pcie_aspm=off\3` does the append-in-place — but on a second run it would match again and append again. The guard is a preceding `command: grep ... GRUB_CMDLINE` with `when: "'pcie_aspm' not in grep.stdout"`, so the edit is skipped entirely once the value is present. Pair it with a `notify`/`register`-gated `update-grub` so the bootloader only regenerates when the line actually changed.
