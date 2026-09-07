---
name: Bug report
about: Something doesn't work as described
title: ''
labels: bug
---

## What happened

<!-- Include the exact command and its output. Redact hostnames if you like, but keep the shape. -->

```
$ cc-peer ...
```

## What you expected

## Environment

- cc-peer version: <!-- cc-peer --version -->
- Claude Code version on **both** ends: <!-- claude --version -->
- OS on both ends: <!-- e.g. macOS 26 (arm64) → Ubuntu 24.04 (arm64) -->

## Discovery output

<!-- This resolves most issues. It shows stale records and sessions with no inbox. -->

```
$ cc-peer list --host <target> --all
```

## Checked

- [ ] `ssh <host> true` succeeds on its own
- [ ] The target session appears in `list --all` (if not, it may have bound no socket)
- [ ] The message wasn't simply held for approval — a session in `--dangerously-skip-permissions`
      mode holds every incoming message unless `crossSessionInbound` is `accept`
