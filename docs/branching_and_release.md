# Branching and Release Workflow

This repository uses a lightweight Git Flow pattern:

- `main` is always releasable.
- All code, docs, and metadata changes start from a short-lived branch.
- Use `feature/<topic>` for additive work.
- Use `fix/<topic>` for normal bug fixes.
- Use `hotfix/<topic>` for urgent production fixes.
- Merge to `main` through a pull request after CI passes.
- Cut GitHub releases only from `main`.
- Version-only release bump commits may be made directly on `main` after the
  candidate code has already passed CI.

Do not use a long-lived `develop` branch unless the project starts batching larger
features that need integration before release.

## Normal Change Flow

1. Create a branch from current `main`.
2. Make a focused commit.
3. Run the relevant local verification.
4. Push the branch.
5. Open a pull request against `main`.
6. Merge only after CI is green.

## Release Flow

1. Confirm `main` is clean and up to date.
2. Confirm the candidate code was merged through a pull request with green CI.
3. Bump `custom_components/solem_blip/manifest.json` and `pyproject.toml`
   directly on `main`.
4. Run release sanity checks:
   - `git diff --check`
   - validate `custom_components/solem_blip/manifest.json` as JSON
   - optionally run `python -m compileall -q custom_components`
5. Commit and push the version-only release bump directly to `main`.
6. Create a GitHub release tag such as `v1.2.21`.

Do not open a pull request for a version-only release bump. The release bump
does not need to rerun the full CI suite when the candidate code already passed
CI before merge.

CI detects version-only release bumps on pushes to `main`: when only
`custom_components/solem_blip/manifest.json` and `pyproject.toml` changed, and
the only changed lines are version fields, full validation jobs are skipped.
Only the lightweight Python/JSON sanity job runs.

If the release commit includes anything beyond version metadata, treat it as a
normal change: branch, test, open a pull request, and merge only after CI passes.

## Pre-release Flow

Use pre-releases when a change needs live Home Assistant validation before it is
declared stable.

1. Merge the candidate change to `main` after CI passes.
2. Bump the pre-release version directly on `main`, such as `1.2.22b1` for a
   `v1.2.22-beta.1` tag.
3. Run the release sanity checks from the release flow.
4. Commit and push the version-only pre-release bump directly to `main`.
5. Create an immutable GitHub pre-release tag from `main`, such as
   `v1.2.22-rc.1` or `v1.2.22-beta.1`.
6. Install the pre-release on live Home Assistant through HACS or manual update.
7. Run the live validation checklist against real BL-IP devices.
8. If validation passes, bump from the pre-release version to the stable version
   directly on `main`, run release sanity checks, push, and create the stable
   tag, such as `v1.2.22`.
9. If validation fails, fix through a new branch and pull request, then cut the
   next pre-release tag, such as `v1.2.22-rc.2`.

Use `beta.N` when behavior may still change after live testing. Use `rc.N` when
the candidate is intended to become the final stable build unless validation
finds a blocker.

Do not retag or mutate an existing pre-release to promote it. Leave the
pre-release tag intact and create the stable tag from the validated commit.

## Hotfix Flow

1. Create `hotfix/<topic>` from `main`.
2. Keep the fix narrow.
3. Run focused tests plus full verification.
4. Open a pull request and merge after CI passes.
5. Create the patch release from merged `main`.
