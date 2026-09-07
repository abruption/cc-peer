<!-- PR title should follow Conventional Commits, e.g. "fix: read the socket path from the session record" -->

## Summary
<!-- What does this change and why? -->

Closes #<!-- issue number -->

## Type
- [ ] feat
- [ ] fix
- [ ] docs
- [ ] chore / refactor / test / perf

## Checklist
- [ ] `python3 cc_peer.py list` works locally
- [ ] `python3 cc_peer.py list --host <a real host>` works over SSH
- [ ] `--dry-run` resolves a target without posting anything
- [ ] Commits follow Conventional Commits
- [ ] Docs updated if behavior changed (README / SKILL.md / CLAUDE.md)
- [ ] No secrets, tokens, or session keys in the diff

## Verified against
<!-- Claude Code version and OS you tested on, e.g. "v2.1.263, macOS 26 + Ubuntu 24.04 arm64" -->

## Notes
<!-- Tradeoffs, follow-ups, manual test steps reviewers should know about. -->
