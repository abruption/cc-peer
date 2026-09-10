# Publishing the transition

The GitHub repository is `abruption/session-peer`. Keep its history and issues;
do not archive it or recreate `abruption/cc-peer`, which would replace redirects.

## PyPI setup (project owner)

Configure a **pending Trusted Publisher** for the new `session-peer` project:

| Field | Value |
| --- | --- |
| PyPI project | `session-peer` |
| GitHub owner | `abruption` |
| GitHub repository | `session-peer` |
| Workflow filename | `publish.yml` |
| GitHub environment | `pypi` |

Update the existing `cc-peer` project's Trusted Publisher to the same GitHub
owner/repository/workflow/environment. Its final release still uses distribution
name `cc-peer`, from the maintenance branch. A GitHub rename does not automatically
update PyPI's publisher configuration. GitHub administrator access alone does not
provide access to these PyPI owner settings.

References: [pending publishers](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/),
[rename failures](https://docs.pypi.org/trusted-publishers/troubleshooting/).
A pending publisher does not reserve a package name. Recheck name availability
before publishing; do not publish an empty placeholder to reserve it.

## Publication order

1. Confirm all PR checks and distribution installation tests pass on the release
   commit. Preserve the standalone frozen `cc_peer.py` in every new release tag;
   old updaters fetch that exact path. It must stay out of the new wheel/sdist.
2. Publish the prepared **session-peer v0.6.0** GitHub release from the verified
   main commit. Publishing triggers `publish.yml`; a draft does not.
3. Check the Actions publish result and install `session-peer==0.6.0` from PyPI in
   a fresh environment. Confirm version, local listing and safe dry-run. Do not
   treat submission as acknowledgement or bypass a receiving agent's quota.
4. Publish **cc-peer v0.5.1** from `maintenance/cc-peer`, explicitly with
   `latest=false`. Its tagged pyproject names the old distribution and uses the
   old standalone code. Check its PyPI upload and migration notice.
5. Confirm GitHub's latest release is still v0.6.0 and the old GitHub URLs redirect.
   Only then archive the **PyPI cc-peer project** in its owner settings. Keep its
   existing distributions downloadable; do not delete or yank them for migration.
6. Complete the transition checklist in #48. #45–#47 remain future work.

If OIDC setup, publication, or installation verification fails, leave the old
PyPI project unarchived and report the failed stage. Do not replace credentials
with repository secrets or republish a used version. Package-managed installs
upgrade with their own manager; standalone installs use the self-updater.

## Verification limits for this transition

134 unit/integration tests passed locally and CI exercised Python 3.9/3.13 on
macOS/Linux and Python 3.13 on Windows. Wheel/sdist build and isolated installation,
shellcheck, and standalone install/reinstall/uninstall coexistence were exercised.
The actual v0.5.0 updater was tested against the frozen compatibility file.

On two macOS machines, Codex CLI 0.154.0 discovery, dry-run, and actual queue
submission worked; queued payloads matched the originals. These were pending
submissions, not acknowledged responses. Claude local/SSH inbox writes succeeded,
but its weekly quota was exhausted: receiving turns, responses, and the separately
requested ontology document update were not verified. Do not run further live
Claude consumption tests until that limit is restored.
