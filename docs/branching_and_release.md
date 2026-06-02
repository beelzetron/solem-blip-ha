# Branching and Release Workflow

This repository uses a lightweight Git Flow pattern:

- `main` is always releasable.
- All code, docs, and metadata changes start from a short-lived branch.
- Use `feature/<topic>` for additive work.
- Use `fix/<topic>` for normal bug fixes.
- Use `hotfix/<topic>` for urgent production fixes.
- Merge to `main` through a pull request after CI passes.
- Cut GitHub releases only from `main`.

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
2. Bump `custom_components/solem_blip/manifest.json` and `pyproject.toml`.
3. Run full verification in the UBI container.
4. Merge the release commit to `main`.
5. Create a GitHub release tag such as `v1.2.21`.

## Pre-release Flow

Use pre-releases when a change needs live Home Assistant validation before it is
declared stable.

1. Merge the candidate change to `main` after CI passes.
2. Create an immutable GitHub pre-release tag from `main`, such as
   `v1.2.22-rc.1` or `v1.2.22-beta.1`.
3. Install the pre-release on live Home Assistant through HACS or manual update.
4. Run the live validation checklist against real BL-IP devices.
5. If validation passes, create the stable release tag, such as `v1.2.22`, from
   the same commit.
6. If validation fails, fix through a new branch and pull request, then cut the
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
