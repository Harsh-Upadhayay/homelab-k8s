# The devbox image (ADR-0059, issue #65).
#
# WHY THIS EXISTS: before it, tmux/ripgrep/kubectl/tailscaled were installed by hand
# into ~/.local/bin on the workspace PVC, because apt writes to /usr — the container's
# EPHEMERAL layer — and only $HOME survives a restart. That was a workaround for not
# having a build pipeline, not a design. Software belongs in the image; $HOME should
# hold user state only. Rebuilding the image is now the way to change tooling, and a
# recreated PVC no longer means reinstalling the world.
#
# Debian (glibc), NOT Alpine (musl): VS Code Remote-SSH's server is dynamically
# linked against glibc and will not run on musl, which would break the primary use
# case for this whole milestone (ADR-0064).
#
# Build from the devbox, where the source already is:
#   buildctl --addr tcp://buildkit.workbench.svc:80 build \
#     --frontend dockerfile.v0 \
#     --local context=/workspace/homelab-k8s/k8s/workbench/devbox-image \
#     --local dockerfile=/workspace/homelab-k8s/k8s/workbench/devbox-image \
#     --output type=image,name=registry.workbench.svc.cluster.local/devbox:dev,push=true,registry.insecure=true
FROM mcr.microsoft.com/devcontainers/base:bookworm

# Versions pinned deliberately, per the repo-wide rule — these are the versions
# proven working in M1/M6, not whatever is newest today. Bump consciously.
ARG TAILSCALE_VERSION=1.98.10
ARG BUILDKIT_VERSION=0.18.2

USER root

# tmux           — sessions must outlive the SSH connection (issue #59)
# ripgrep        — the search tool the workflow assumes
# less/tree/htop — basic interactive ergonomics over SSH
# iproute2/dnsutils/netcat — network debugging from inside the cluster
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tmux \
        ripgrep \
        less \
        tree \
        htop \
        iproute2 \
        dnsutils \
        netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

# tailscaled + tailscale. Static binaries from the official tarball rather than the
# apt repo: no repo key to manage, and the version is pinned exactly.
#
# NOTE these land in /usr/local/bin, so the devbox Deployment's command and
# livenessProbe must reference that path rather than the old /home/vscode/.local/bin.
RUN curl -fsSL "https://pkgs.tailscale.com/stable/tailscale_${TAILSCALE_VERSION}_amd64.tgz" -o /tmp/ts.tgz \
    && tar xzf /tmp/ts.tgz -C /tmp \
    && mv "/tmp/tailscale_${TAILSCALE_VERSION}_amd64/tailscaled" /usr/local/bin/ \
    && mv "/tmp/tailscale_${TAILSCALE_VERSION}_amd64/tailscale" /usr/local/bin/ \
    && chmod +x /usr/local/bin/tailscaled /usr/local/bin/tailscale \
    && rm -rf /tmp/ts.tgz "/tmp/tailscale_${TAILSCALE_VERSION}_amd64"

# buildctl — the client for the in-cluster buildkitd, so the devbox can rebuild THIS
# image. Only the client; the daemon runs as its own Deployment.
RUN curl -fsSL "https://github.com/moby/buildkit/releases/download/v${BUILDKIT_VERSION}/buildkit-v${BUILDKIT_VERSION}.linux-amd64.tar.gz" -o /tmp/bk.tgz \
    && tar xzf /tmp/bk.tgz -C /tmp bin/buildctl \
    && mv /tmp/bin/buildctl /usr/local/bin/ \
    && chmod +x /usr/local/bin/buildctl \
    && rm -rf /tmp/bk.tgz /tmp/bin

# kubectl, pinned to the cluster's minor version. The devbox holds namespace-admin on
# `workbench` only (ADR-0065), so this is scoped by RBAC rather than by omission.
ARG KUBECTL_VERSION=v1.36.2
RUN curl -fsSL "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" -o /usr/local/bin/kubectl \
    && chmod +x /usr/local/bin/kubectl

# mise — one binary replacing nvm/pyenv/gvm, so ONE image serves all projects.
# Deliberately installed to /usr/local/bin while its DATA dir stays under $HOME on
# the PVC: the tool is software (image), the installed runtimes are user state
# (volume). Adding a language version therefore never needs a rebuild.
RUN curl -fsSL https://mise.run | MISE_INSTALL_PATH=/usr/local/bin/mise sh

# Neovim. Debian bookworm ships 0.7 (2022) in apt, which is too old for most modern
# plugin managers, so this takes the upstream static tarball instead.
ARG NVIM_VERSION=v0.11.3
RUN curl -fsSL "https://github.com/neovim/neovim/releases/download/${NVIM_VERSION}/nvim-linux-x86_64.tar.gz" -o /tmp/nvim.tgz \
    && tar xzf /tmp/nvim.tgz -C /opt \
    && ln -sf /opt/nvim-linux-x86_64/bin/nvim /usr/local/bin/nvim \
    && rm -f /tmp/nvim.tgz

# ---------------------------------------------------------------------------
# WHAT BELONGS IN THIS IMAGE — the test is "does the pod need it to boot?", not
# "is it software?".
#
# Everything above this line is either boot-critical or a cheap apt package:
#
#   tailscaled  — the livenessProbe execs it. Missing => the pod crash-loops. This
#                 is the exact failure mode the runbook documents from the
#                 hand-installed era.
#   buildctl    — chicken-and-egg: without it you cannot rebuild the image that
#                 would restore it.
#   mise, kubectl, nvim, tmux, ripgrep, git — cheap, and no simpler runtime path.
#
# The AI coding CLIs (claude, codex, agy) are deliberately NOT here. They are pure
# user tools: nothing in the pod lifecycle touches them, and $HOME is on the
# workspace PVC, so a one-time install into ~/.local/bin persists across restarts
# exactly like a baked binary would.
#
# For Claude Code, installing to $HOME is strictly BETTER than baking it in. Its
# native installer manages versions under ~/.local/share/claude/versions/ and
# auto-updates in the background; on the PVC those updates persist. Baked into the
# image, updates would land in the container's ephemeral layer and be re-downloaded
# on every restart — which is why an image-baked install has to set
# DISABLE_AUTOUPDATER=1 and give up auto-updates entirely.
#
# Keeping them out of the image also removes the only reason this image needed
# system-wide Node: `claude` is a native binary (npm is just a delivery mechanism),
# and `codex` can use a mise-managed Node.
#
# First-run setup, from inside the devbox (see docs/Workbench Runbook.md):
#
#   curl -fsSL https://claude.ai/install.sh | bash        # → ~/.local/bin/claude
#   npm config set prefix ~/.local                        # BEFORE installing codex
#   mise use -g node@22 && npm install -g @openai/codex
#
# The `npm config set prefix` line is load-bearing: npm globals default to living
# INSIDE the active Node install directory, so a later `mise use -g node@24` moves
# that directory and `codex` silently disappears. Pointing the prefix at ~/.local
# decouples it from the Node version.
# ---------------------------------------------------------------------------

USER vscode
