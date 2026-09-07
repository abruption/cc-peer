#!/bin/sh
# cc-peer installer.
#
#   ./install.sh                      install here
#   ./install.sh --host web-01        install on a remote machine over SSH
#   ./install.sh --host a --host b    ...on several
#   ./install.sh --uninstall          remove it
#
# Installs cc_peer.py and its Claude Code skill into ~/.claude/skills/cc-peer,
# and links ~/.local/bin/cc-peer. Nothing else is touched.
#
# Remote installs push the files over the SSH connection itself, so the remote
# machine needs no internet access — which matters, since air-gapped hosts are
# one of the reasons this tool exists.

set -eu

RAW="https://raw.githubusercontent.com/abruption/cc-peer/main"
SKILL_DIR="$HOME/.claude/skills/cc-peer"
BIN_DIR="$HOME/.local/bin"
HOSTS=""
UNINSTALL=0

die() { echo "install.sh: $*" >&2; exit 1; }

usage() {
    sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --host) [ $# -ge 2 ] || die "--host needs a value"; HOSTS="$HOSTS $2"; shift 2 ;;
        --host=*) HOSTS="$HOSTS ${1#--host=}"; shift ;;
        --uninstall) UNINSTALL=1; shift ;;
        -h|--help) usage 0 ;;
        *) die "unknown argument: $1 (try --help)" ;;
    esac
done

# --------------------------------------------------------------------------
# Local install
# --------------------------------------------------------------------------

fetch() {
    # fetch <url> <dest>
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$1" -o "$2"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "$2" "$1"
    else
        die "need curl or wget to download $1"
    fi
}

install_here() {
    command -v python3 >/dev/null 2>&1 || die "python3 not found"

    mkdir -p "$SKILL_DIR" "$BIN_DIR"

    # Prefer files sitting next to this script (a clone, or a remote push);
    # fall back to downloading them.
    src_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd 2>/dev/null) || src_dir=""
    if [ -n "$src_dir" ] && [ -f "$src_dir/cc_peer.py" ]; then
        cp "$src_dir/cc_peer.py" "$SKILL_DIR/cc_peer.py"
        if [ -f "$src_dir/skills/cc-peer/SKILL.md" ]; then
            cp "$src_dir/skills/cc-peer/SKILL.md" "$SKILL_DIR/SKILL.md"
        elif [ -f "$src_dir/SKILL.md" ]; then
            cp "$src_dir/SKILL.md" "$SKILL_DIR/SKILL.md"
        fi
    fi
    [ -f "$SKILL_DIR/cc_peer.py" ] || fetch "$RAW/cc_peer.py" "$SKILL_DIR/cc_peer.py"
    [ -f "$SKILL_DIR/SKILL.md" ] || fetch "$RAW/skills/cc-peer/SKILL.md" "$SKILL_DIR/SKILL.md"

    chmod +x "$SKILL_DIR/cc_peer.py"
    ln -sf "$SKILL_DIR/cc_peer.py" "$BIN_DIR/cc-peer"

    version=$(python3 "$SKILL_DIR/cc_peer.py" --version 2>/dev/null || echo "unknown")
    echo "installed $version on $(hostname)"
    echo "  command: $BIN_DIR/cc-peer"
    echo "  skill:   $SKILL_DIR/SKILL.md"

    case ":${PATH}:" in
        *":$BIN_DIR:"*) ;;
        *) echo "  note: $BIN_DIR is not on PATH; add it or call the script directly" ;;
    esac
}

uninstall_here() {
    [ -L "$BIN_DIR/cc-peer" ] && rm -f "$BIN_DIR/cc-peer"
    rm -rf "$SKILL_DIR"
    echo "removed cc-peer from $(hostname)"
}

# --------------------------------------------------------------------------
# Remote install — ship the files over the SSH connection, no internet needed
# --------------------------------------------------------------------------

remote_run() {
    host=$1
    if [ "$UNINSTALL" -eq 1 ]; then
        ssh "$host" 'rm -f "$HOME/.local/bin/cc-peer"; rm -rf "$HOME/.claude/skills/cc-peer"; echo "removed cc-peer from $(hostname)"'
        return
    fi

    src_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
    py="$src_dir/cc_peer.py"
    skill="$src_dir/skills/cc-peer/SKILL.md"
    [ -f "$py" ] || die "cc_peer.py not found next to this script; run from a clone"
    [ -f "$skill" ] || die "skills/cc-peer/SKILL.md not found next to this script"

    {
        echo 'set -eu'
        echo 'command -v python3 >/dev/null 2>&1 || { echo "python3 not found on $(hostname)" >&2; exit 1; }'
        echo 'mkdir -p "$HOME/.claude/skills/cc-peer" "$HOME/.local/bin"'
        echo 'base64 -d > "$HOME/.claude/skills/cc-peer/cc_peer.py" <<'"'"'CC_PEER_PY'"'"''
        base64 < "$py"
        echo 'CC_PEER_PY'
        echo 'base64 -d > "$HOME/.claude/skills/cc-peer/SKILL.md" <<'"'"'CC_PEER_SKILL'"'"''
        base64 < "$skill"
        echo 'CC_PEER_SKILL'
        echo 'chmod +x "$HOME/.claude/skills/cc-peer/cc_peer.py"'
        echo 'ln -sf "$HOME/.claude/skills/cc-peer/cc_peer.py" "$HOME/.local/bin/cc-peer"'
        echo 'echo "installed $(python3 "$HOME/.claude/skills/cc-peer/cc_peer.py" --version) on $(hostname)"'
    } | ssh "$host" sh
}

# --------------------------------------------------------------------------

if [ -n "$HOSTS" ]; then
    status=0
    set -f            # no globbing: --host '*' must not expand
    # shellcheck disable=SC2086 # deliberate split: HOSTS is a list we built
    for host in $HOSTS; do
        remote_run "$host" || { echo "install.sh: failed on $host" >&2; status=1; }
    done
    set +f
    exit "$status"
fi

if [ "$UNINSTALL" -eq 1 ]; then
    uninstall_here
else
    install_here
fi
