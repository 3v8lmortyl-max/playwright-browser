# Archive extraction privilege-boundary test suite

A reproducible harness that measures whether an archive extractor confines its
writes to the extraction root when fed hostile ZIP archives (the "Zip-Slip"
family of attacks). It is a **defensive** test: it detects and evidences
boundary escapes; it does not weaponize them. Every attempted escape is
constructed to stay inside a disposable lab directory, and a post-run guard
asserts nothing left that disposable directory.

## Threat model

The attacker controls the **contents of an archive**. The victim extracts it,
sometimes as root. The question each test answers: does the extractor write only
inside the chosen extraction root, or can the archive steer a write outside it?

The orchestrator and archive generation run as an **unprivileged** user. Only
the extraction step runs as root, through a single narrowly scoped `sudo`
command. Extracting untrusted archives as root is exactly the dangerous pattern
this suite characterizes.

## Components

| File | Role | Privilege |
|------|------|-----------|
| `hostile_zip.py` | Builds the hostile archives. Has a safety gate that refuses to emit any archive whose worst-case resolved target would leave the lab. | unprivileged |
| `run_boundary_suite.py` | Orchestrator: builds labs, generates archives, snapshots before/after, drives extraction, classifies, collects evidence, enforces the confinement guard. | unprivileged |
| `privileged_extract_wrapper.sh` | The **only** command invoked via `sudo`. Runs `strace` around the helper; does nothing else. | root (via sudo) |
| `privileged_extract.py` | Performs exactly one extraction with one allowlisted extractor into one lab; records effective UID/GID and extractor identity. Enforces extractor allowlist and destination confinement. | root (via sudo) |
| `sudoers.d/archive-boundary.example` | How to lock `sudo` down to just the wrapper on a hardened/self-hosted runner. | — |

## Extractors tested

- **`unzip`** — system Info-ZIP `unzip`.
- **`python-zipfile-extractall`** — Python standard library `zipfile.ZipFile.extractall`.
- **`python-zipfile-naive-control`** — an **intentionally unsafe** positive control
  reproducing the classic manual-join anti-pattern (no sanitization, creates
  symlinks, writes through them). It exists only to prove the harness can detect
  a real escape. **Never copy it into production code.**

## Test cases

| ID | Attack |
|----|--------|
| T1 | `../` parent-directory path traversal |
| T2 | Absolute archive path |
| T3 | Nested path traversal (`sub/deeper/../../../outside/...`) |
| T4 | Symlink entry, then a file created *through* the symlink |
| T5 | Duplicate filenames / overwrite behavior |
| T6 | Hardlink-like / special-file (FIFO) metadata |
| T7 | Setuid/setgid permission metadata |
| T8 | Symlink / path collision (symlink then regular file, same name) |

All traversal targets and symlink destinations are constructed to resolve into
the test's own lab (`lab/` or `lab/outside/`) — never above `LAB_ROOT`.

## Isolation

For every (test × extractor) pair a fresh lab is created under an ephemeral temp
base (`$RUNNER_TEMP` in CI, else `/tmp`):

```
LAB_ROOT/                      (mktemp -d archbound-XXXX; removed after the run)
  _CANARY_DO_NOT_TOUCH         (hash-checked after the run)
  T1__unzip/
    input/      extract/       outside/       evidence/
  ...
```

- `extract/` is the extraction root (the boundary under test).
- `outside/` is a sibling escapes are aimed at — still inside the lab.
- A **SAFE** result = no archive-controlled write outside `extract/`.
- A **VULNERABLE** result = an archive-controlled write landed outside `extract/`
  (by construction still inside the disposable lab).
- **INCONCLUSIVE** = the vector is unsupported by that extractor, or
  instrumentation was insufficient.

Guards: the generator's per-entry safety gate, plus a post-run check that
`LAB_ROOT` gained no unexpected top-level entries and the canary is unchanged.
The orchestrator exits non-zero only if confinement is breached — not when a
VULNERABLE finding is (expectedly) produced.

## Evidence (per test)

- Extractor identity and effective UID/GID (`evidence/extract_meta.json`).
- Archive SHA-256 and per-entry metadata (`archive_entries.json`, `result.json`).
- Before/after filesystem snapshots (`snapshot_before.json`, `snapshot_after.json`)
  with type, permissions (incl. setuid/setgid/sticky), uid/gid, inode, size and
  SHA-256 of regular files, and symlink targets.
- `diff.json` of what changed.
- `strace -f -e trace=file` syscall trace when `strace` is available
  (`strace.log`).
- Extractor stdout/stderr and an explicit classification with reason.

## Run locally

Unprivileged user, with passwordless sudo limited to the wrapper (see
`sudoers.d/archive-boundary.example`):

```bash
cd security/archive-boundary
python3 run_boundary_suite.py --out ./evidence-out --base /tmp
```

Useful flags: `--tests T1 T4`, `--extractors unzip`, `--no-sudo` (skip the
privilege split — the escape measurement still runs), `--allow-root` (run the
orchestrator as root; not recommended, generation should be unprivileged).

## Run in CI

`.github/workflows/archive-boundary.yml` runs the suite on a fresh ephemeral
`ubuntu-latest` runner, triggered manually via **workflow_dispatch**, with
`permissions: contents: read` and no secrets. Evidence is uploaded as an
artifact even if a test fails.

## T9 — ownership provenance (separate module and workflow)

T9 is a distinct experiment, kept separate from the T1–T8 boundary tests so the
existing suite is untouched. It asks: a historical copy of
`screenshots/01_github.com_01.png` carried the on-disk ownership signature
**UID 0 : GID 1001 : mode 0644**. ZIP archives do **not** transport POSIX
uid/gid, so that signature cannot have come from the archive — can it be
reproduced by the extraction *environment*, and under which conditions?

Files: `ownership_provenance.py` (unprivileged orchestrator),
`provenance_extract_helper.py` (the only sudo'd command; sets up the destination
with an exact owner/group/mode and extracts as root or after dropping to an
unprivileged uid), `provenance_extract_wrapper.sh` (narrow sudo wrapper).

It extracts the entry into three destination profiles — A `0:1001` mode 2755
(setgid), B `1000:1001` mode 2755 (setgid), C `1000:1000` mode 0755 (no setgid)
— as root and as uid 1000, with both `unzip` and `zipfile.extractall`, recording
archive metadata, extractor identity, and the resulting file's ownership, mode,
inode, timestamps and SHA-256. Everything stays in disposable temp dirs; the
original artifact is never touched (it is a generated artifact, absent from the
repo, so a synthetic stand-in payload is used and the historical SHA-256 is
recorded only as the reference identity — ownership is independent of content).

Result: the signature is reproduced **only** when extraction runs **as root**
into a **setgid** directory whose group is **1001** (profiles A and B); the
directory's owner is irrelevant. Without setgid+group 1001 (profile C), root
extraction yields GID 0. Unprivileged extraction yields UID 1000, never 0. So
the necessary conditions are: (1) root (→ UID 0), (2) setgid destination of group
1001 (→ GID 1001 via inheritance), (3) mode 0644 (umask 022 / archive). This
characterizes POSIX mechanics only; it does not reconstruct any product's
internal extraction path.

Run locally: `python3 ownership_provenance.py --out ./evidence-out-t9 --base /tmp`.
CI: `.github/workflows/archive-boundary-t9.yml` (workflow_dispatch + push on
`provenance-test`), `permissions: contents: read`, no secrets, evidence uploaded
always.
