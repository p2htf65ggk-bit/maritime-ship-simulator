# Source provenance

Repository history starts with the clean 2.3.0 source snapshot. No earlier
private Git history, operator configuration, provider credentials, downloaded
chart library or equipment-specific integration guide was imported.

## Initial baseline

- Initial commit: `5db1be6` (the root commit on `main`).
- Historical import archive SHA256:
  `08022d55d74e54714bfbde4d2fecd4955db315cb34cb69c62241f8ced430e834`.
  This identifies the import, **not** the later final release ZIP.
- All 252 entries in `SOURCE_SHA256SUMS` were verified before the initial commit.
- `DEVELOPER_SOURCE_MANIFEST.json` describes the curated export and its scope.

To verify the historical import's `SOURCE_SHA256SUMS`, use an isolated worktree
of the initial commit, not your working branch:

```bash
git worktree add --detach ../maritime-source-baseline 5db1be6
cd ../maritime-source-baseline
shasum -a 256 -c SOURCE_SHA256SUMS
```

On Linux, `sha256sum -c SOURCE_SHA256SUMS` is also suitable.

The final release source ZIP contains a freshly generated `SOURCE_SHA256SUMS`
for that archive and `RELEASE_SNAPSHOT.json` with its exact source commit.
Verify the ZIP against the final release asset hashes, then verify the contained
file manifest. The historical import hash above must not be used for that ZIP.

## Repository presentation

Subsequent repository-preparation commits add the generic English PDF/Word
guide, a control-panel screenshot, README navigation and issue templates.
Later preparation also adds a four-image gallery, first-start/handoff guides,
complete dependency notices, library-source/replacement information and native
license validation. These packaging changes do not change simulator logic.
See the commit diff for the exact documentation changes. The guide screenshot
shows the 2.3.0 controls; unchanged UI screenshots inside the guide may originate
from an earlier version, as explained in the guide.

Native packages remain subject to the release licensing and platform validation
checks in [RELEASE_LICENSING.md](RELEASE_LICENSING.md) and
[VALIDATION.md](VALIDATION.md). A prepared draft does not mean binaries are
approved or published.
