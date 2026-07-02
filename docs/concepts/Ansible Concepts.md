# Ansible Concepts

> Back to [[Homelab Learning Map]] · See also [[Platform Concepts]]

No agents, no daemons on the target — Ansible SSHs in, runs small Python-backed modules, and each one reports whether it changed anything. Everything below is organization on top of that one idea.

## Inventory and connection

**Inventory groups and hosts.** A group (`[proxmox_hosts]`) is an arbitrary label you invent; each line under it is an *inventory hostname* — a name Ansible uses to refer to the host in output and logs. That name never has to resolve to anything (`ansible/inventory.ini`).

**`ansible_host` is the real connection target.** It's the value actually handed to SSH. Conflating the inventory hostname with `ansible_host` is a trap if the label only resolves via something outside the repo (e.g. a personal `~/.ssh/config` alias) — we hit exactly this when `pve-dell` only worked on one machine. Fixed by pointing `ansible_host` at the real IP directly, so the inventory is portable across machines (`ansible/inventory.ini`).

**Group-scoped connection vars.** `[proxmox_hosts:vars]` sets `ansible_user` and `ansible_ssh_private_key_file` for every host in that group — the same information you'd pass to `ssh -i <key> user@host` manually, just declared once for the whole group instead of typed per connection (`ansible/inventory.ini`).

**`group_vars/<groupname>.yml` auto-loading.** Ansible automatically loads `group_vars/proxmox_hosts.yml` for any host in the `proxmox_hosts` group — no explicit include, the filename *is* the wiring. That's where `debian_codename: "trixie"` lives, used later via Jinja2 templating (`ansible/group_vars/proxmox_hosts.yml`).

**`ansible.cfg` defaults.** Sets the default inventory file, roles path, and privilege-escalation behavior so you don't pass `-i inventory.ini` on every invocation (`ansible/ansible.cfg`).

## Playbooks, plays, and roles

**A playbook is a list of plays.** Each play maps a group of hosts (`hosts:`) to work to do (`roles:` or `tasks:`). `ansible/proxmox-host.yml` is a one-play playbook targeting `proxmox_hosts`.

**`become` / privilege escalation.** Global default is `become = True` (sudo) in `ansible.cfg`, but a play can override it — we set `become: false` for the Proxmox host play since `ansible_user` is already `root`; there's nothing to escalate to (`ansible/proxmox-host.yml`).

**Roles are a directory convention, not special syntax.** `roles/<name>/tasks/main.yml` is the entry point Ansible looks for automatically when a playbook lists that role — no explicit file path needed (`ansible/roles/proxmox_host/`).

**`import_tasks` splits one role into multiple files.** `main.yml` pulls in `repos.yml` and `tailscale.yml` as if pasted inline. Combined with `tags:` on the import, this is what lets `--tags repos` skip the other file's tasks entirely — not just "don't run them," but "never even template them" (`ansible/roles/proxmox_host/tasks/main.yml`).

## Tasks, modules, and idempotency

**A task is a module plus its arguments.** The module (`ansible.builtin.copy`, `ansible.builtin.apt`, `ansible.builtin.systemd`, `ansible.builtin.get_url`) does the work; idempotency lives inside the module's own logic, not in the YAML you write. `copy` compares the target file's current content against what you declared and only writes on a real difference (`ansible/roles/proxmox_host/tasks/repos.yml`).

**Declarative file management via `copy` + inline `content:`.** Instead of scripting "edit this file," you declare the file's entire desired content and let the module diff it — this is how the pve-enterprise/no-subscription `.sources` files are managed (`ansible/roles/proxmox_host/tasks/repos.yml`).

**Package-state modules check before acting.** `ansible.builtin.apt` with `upgrade: full` inspects installed vs. available versions and only acts on the delta — rerun it on an up-to-date system and it reports no change, every time.

**Tags select which parts of a role run.** `tags: [repos]` / `tags: [tailscale]` on the two `import_tasks` calls let `--tags repos` or `--tags tailscale` run independently, with no cross-dependency — critical here since one path needs a runtime secret (a Tailscale auth key) and the other shouldn't ever require one (`ansible/roles/proxmox_host/tasks/main.yml`).

## Making non-idempotent commands safe

**`ansible.builtin.command` has no built-in idempotency.** Unlike `copy`/`apt`, a raw command module has no idea whether it changed anything — left alone it reports `changed` on every run, forever. Idempotency has to be hand-rolled around it (`ansible/roles/proxmox_host/tasks/tailscale.yml`).

**`register` captures a task's output into a variable**, exactly like assigning a return value — used to capture `tailscale status --json` for inspection by a later task (`ansible/roles/proxmox_host/tasks/tailscale.yml`).

**`changed_when` / `failed_when` override a module's default reporting.** The status-check task sets both to `false` — it's a pure probe, never a real "change" or "failure," even if `tailscale` isn't installed yet and the command exits non-zero (`ansible/roles/proxmox_host/tasks/tailscale.yml`).

**`when:` conditionals + Jinja2 filters gate a task's execution.** `(ts_status.stdout | from_json).BackendState != "Running"` parses JSON output with the `from_json` filter and reads a field from it, so `tailscale up` only actually runs if the host isn't already connected — this is what makes an inherently non-idempotent `command` safe to rerun (`ansible/roles/proxmox_host/tasks/tailscale.yml`).

**`no_log: true` suppresses a task's output from logs entirely.** Used on the `tailscale up --authkey=...` task so the secret never appears in plaintext in the terminal or any saved log (`ansible/roles/proxmox_host/tasks/tailscale.yml`).

## Execution and safety habits

**`--check --diff` is a dry run.** `--check` reports what a module *would* do without doing it; `--diff` shows before/after content for file-based tasks like `copy`. Not perfect — `command`-based tasks can't meaningfully dry-run — but free insurance for anything file-based. Run before the first real apply against a host (`ansible-playbook proxmox-host.yml --tags repos --check --diff`).

**`--extra-vars` passes runtime secrets without writing them to disk.** Same pattern used for the k3s join token: `ansible-playbook proxmox-host.yml --tags tailscale --extra-vars "tailscale_auth_key=..."` — the value lives only in that one invocation, never in a tracked file.

**`ansible <group> -m ping` is a connectivity smoke test**, independent of any playbook — confirms SSH auth and that Python exists on the remote end before trusting a real run.

**`ansible-galaxy collection install -r requirements.yml`** installs modules that aren't in `ansible.builtin` (here, `community.general` and `ansible.posix`, used by the k3s roles) — separate from the `ansible` package itself, which is why it's its own step (`ansible/requirements.yml`).
