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

## Hotfix Flow

1. Create `hotfix/<topic>` from `main`.
2. Keep the fix narrow.
3. Run focused tests plus full verification.
4. Open a pull request and merge after CI passes.
5. Create the patch release from merged `main`.
