# Ansible Concepts

> Back to [[Homelab Learning Map]] · See also [[Platform Concepts]] · Decisions in [[README|ADR index]]

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

**`import_tasks` splits one role into multiple files.** `main.yml` pulls in `repos.yml` and `tailscale.yml` as if pasted inline. Combined with `tags:` on the import, this is what lets `--tags repos` skip the other file's tasks entirely — not just "don't run them," but "never even template them." This is the mechanism that made ADR-0006 possible — one role covering two concerns without losing the ability to run either independently (`ansible/roles/proxmox_host/tasks/main.yml`).

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

**A skipped `command` task in check mode still returns `stdout: ''`** — defined, but empty, not undefined. This broke a `default('[]')` fallback that only meant to guard against a genuinely-unset variable: `''` isn't undefined, so the filter chain got `'' | from_json` and blew up on invalid JSON. Fix: `default('[]', true)` — the second argument makes `default()` also substitute on any falsy value (empty string, `None`, `0`), not just true "undefined." A one-token difference between "works" and "crashes only in `--check` mode, never in a real run" (`ansible/roles/proxmox_host/tasks/terraform_token.yml`, ADR-0023).

**`--extra-vars` passes runtime secrets without writing them to disk.** Same pattern used for the k3s join token: `ansible-playbook proxmox-host.yml --tags tailscale --extra-vars "tailscale_auth_key=..."` — the value lives only in that one invocation, never in a tracked file.

**`ansible <group> -m ping` is a connectivity smoke test**, independent of any playbook — confirms SSH auth and that Python exists on the remote end before trusting a real run.

**`ansible-galaxy collection install -r requirements.yml`** installs modules that aren't in `ansible.builtin` (here, `community.general` and `ansible.posix`, used by the k3s roles) — separate from the `ansible` package itself, which is why it's its own step (`ansible/requirements.yml`).

## Templates, handlers, and service lifecycle (Phase 4–6)

**`ansible.builtin.template` renders a separate `.j2` file with full Jinja2 control flow**, not just `{{ variable }}` substitution — unlike `copy` + inline `content:` (used for the simpler Proxmox `.sources` files), `template` supports real loops: `{% for san in k3s_tls_sans %} - "{{ san }}" {% endfor %}` generates one YAML list item per entry in a list variable. Reach for `template` the moment a file needs a loop or conditional, not just static values dropped in (`ansible/roles/k3s_server/templates/config.yaml.j2`).

**Handlers run once, at the end of the play, only if notified by a `changed` task.** `notify: restart k3s` on the config-write task queues the `restart k3s` handler; it doesn't fire immediately, and it won't fire at all if the config task reports no change. This is what makes the same declaration correct in two different situations: on a fresh install, the config task is `changed` but k3s isn't installed yet — harmless, since the handler waits until the play's end, by which point k3s is already running from an explicit task. On a later rerun that only tweaks `k3s_tls_sans`, the same notify now does real work: restarting an already-running k3s so it picks up new cert SANs (`ansible/roles/k3s_server/handlers/main.yml`, ADR-0008/ADR-0010).

**`creates:`/`removes:` is the built-in version of hand-rolled command idempotency.** Where `tailscale.yml` needed a manual `register` + `set_fact` + `when:` chain (because "already done" meant parsing JSON status), `args: {creates: /usr/local/bin/k3s}` on the k3s install command says the same thing declaratively: skip entirely if that file already exists. Reach for `creates`/`removes` whenever "already done" is expressible as a file's existence; fall back to the manual pattern only when it isn't (`ansible/roles/k3s_server/tasks/main.yml`).

**`environment:` passes env vars to one task's execution**, without modifying the script being run. `INSTALL_K3S_VERSION`/`INSTALL_K3S_EXEC` configure `install-k3s.sh` (written, like many install scripts, to read its config from the environment) purely through the task definition (`ansible/roles/k3s_server/tasks/main.yml`).

**`wait_for` polls for an observable readiness signal, because "the command returned" isn't "the service is ready."** `install-k3s.sh` starts a systemd unit and exits immediately — but etcd bootstrap, cert generation, and API server startup all take real time afterward. Two different signals are used because they mean different things: `port: 6443` means the API server process is listening; `path: /var/lib/rancher/k3s/server/node-token` existing means cluster bootstrap fully completed (that file is one of the last things generated). Fetching the kubeconfig before either of these would be true is racy — it might not exist yet, or be mid-write (`ansible/roles/k3s_server/tasks/main.yml`).

**`ansible.builtin.fetch` pulls a file from the remote host back to the control machine** — the mirror of `copy`/`template`, which push local content out. `flat: true` skips `fetch`'s default per-hostname subdirectory nesting (useful when fetching from many hosts at once; unnecessary noise with exactly one server) (`ansible/roles/k3s_server/tasks/main.yml`).

**`delegate_to: localhost` runs one task's actions on the control machine, regardless of the play's actual target.** Rewriting the just-fetched kubeconfig's server URL has to happen on the workstation, not on `k3s-server-1` — even though every other task in this same play targets the server. `become: false` follows naturally: the local user doesn't need sudo to edit its own files (`ansible/roles/k3s_server/tasks/main.yml`).

**`ansible.builtin.replace` is regex find-and-replace *within* file content**, distinct from `lineinfile` (whole-line match/replace). Used to swap `127.0.0.1` for the server's real IP inside the fetched kubeconfig — k3s's own generated config always defaults to localhost, correct from its own perspective at generation time, wrong for reading from anywhere else (`ansible/roles/k3s_server/tasks/main.yml`).

**`ansible.builtin.assert` stops the whole play immediately with a custom message**, unlike `when:` (which silently skips just one task). Used to fail fast if `k3s_token` wasn't supplied, rather than letting a missing token fail confusingly deep inside the k3s install process — or worse, silently bootstrap with an empty join secret (`ansible/roles/k3s_server/tasks/main.yml`).

**`--extra-vars` sits at the top of Ansible's variable precedence chain**, above `group_vars`, host_vars, and role defaults. That's exactly the property wanted for `k3s_token` — deliberately absent from every `group_vars` file, so it must be supplied fresh at the command line and can never be accidentally baked into a committed file. Same reasoning as the Tailscale auth key (`ansible/group_vars/all.yml`).
