# T11 — original artifact timestamp and content provenance

T11 adds to T1–T10 and modifies none of their files. It asks which extraction and
file-handling operations preserve or change the timestamps and metadata of the
original PNG, and which candidate sequences remain compatible with the
historical state of

    /mnt/data/gh_demo/screenshots/01_github.com_01.png   0:1001 0644, 209,306 bytes,
    SHA-256 dd0ff1aa…f61f51, device 27, inode 262311, mount ID 52
    parent /mnt/data/gh_demo                              0:1001 2755

The historical mtime and ctime have not been established. T11 searches the
repository for them and does not invent them, so every timestamp comparison
against history is INCONCLUSIVE.

## Original artifact

| Item | Value |
|---|---|
| Repository | `3v8lmortyl-max/playwright-browser` |
| Workflow run | `45136043351` |
| Artifact | `14799922102` — `agent-framework-pypi-documentary` |
| ZIP SHA-256 | `cafc11da650cb8d2be6564eadef1830e8f9b5cfd12541d24b85dd038676b2f65` |
| Member | `screenshots/01_github.com_01.png` (mode 0100644, 198,259 compressed, CRC32 0e9614e3) |
| PNG SHA-256 | `dd0ff1aa6371838a1bd9d941b4b43a1596a01974c202a6967416f20916f61f51` (209,306 bytes) |

The ZIP is downloaded via `GET /repos/{repo}/actions/artifacts/14799922102/zip` with
`GITHUB_TOKEN` (workflow permissions: `contents: read`, `actions: read`). The token
is sent as an unredirected header, so it never goes to the blob-storage redirect,
and it is removed from the environment before any other process starts.

Verification order:
1. Check the archive SHA-256.
2. Run the binary ZIP inspection.
3. Verify the member's SHA-256, size, CRC32, mode and compressed size, and confirm
   there are no ownership extra fields.

If the artifact is unavailable, expired or mismatched, T11 stops, writes a
STOPPED report and exits 5. It never falls back to a substitute.

`--self-test` only validates the harness where the artifact cannot be fetched.
It uses a stand-in archive and labels every output SELF_TEST.

## Experiments

Each scenario runs in a fresh `/tmp/archbound-t11-*/<scenario>` directory. The
grid is 15 operations × 3 destination profiles (T9: A `0:1001 2755`, B
`1000:1001 2755`, C `1000:1000 0755`) × 2 identities (root; unprivileged uid/gid
1000), with umask 022 and TZ=UTC unless stated otherwise.

| Group | Operations |
|---|---|
| A. Archive extraction | `01_unzip`; `01tz_unzip_alt_tz` (TZ=Asia/Kolkata); `02_zipfile_extract`; `03_zipfile_extractall` (members=[member]) |
| B. Copy | `04_cp`; `05_cp_p` (source 1001:1001); `05g_cp_p_src_0_1001`; `06_copyfile`; `07_copy2`; `08_stream` (ZIP member → destination) |
| C. Atomic replacement | `09_atomic_samedir`; `10_atomic_otherdir`; `10g_atomic_otherdir_setgid1001`; `11a`/`11b` (same as 09/10, over an existing uid-1000 file) |

The copy source is the verified member, written to `src/` with a sentinel mtime
of `2001-02-03T04:05:06.123456789Z`, so that nanosecond-exact preservation can be
recognised.

## Instrumentation

- Every step, setup included, runs under:
  `strace -f -tt -y -s 64 -e trace=%file,%creds,fchown,fchmod,utimensat,futimesat,copy_file_range,sendfile,ioctl,umask,write,close,fsync,fdatasync`
  This covers `openat`, `mkdirat`, `utimensat`/`futimens`, `fchownat`, `fchmodat`,
  `renameat`/`renameat2`, `linkat` and `copy_file_range`.
- After every step the whole scenario tree is snapshotted: uid, gid, mode,
  device, inode, nlink, size, SHA-256, atime/mtime/ctime in nanoseconds, and
  birth time (statx via `stat %W/%w`).
- Snapshot hashing uses `O_NOATIME`.
- Recorded alongside: identity probes (EUID/EGID/groups/umask), filesystem and
  mount info (`statfs`, `findmnt`, `/proc/self/mountinfo`), and decoding of the
  historical device number.

## Classification

- **REPRODUCED** (per scenario): matches UID, GID, mode, size, SHA-256 and the
  parent's UID/GID/mode.
- **CONSISTENT** (per operation): reproduced in at least one configuration, but
  not uniquely identifiable.
- **RULED_OUT**: conflicts with an established attribute, or the operation is not
  permitted.
- **INCONCLUSIVE**: the needed historical metadata (timestamps) is unavailable, or
  there is no verifiable output.

The report keeps four things apart:
1. What the original ZIP proves.
2. What the experiments reproduce.
3. What the historical metadata supports.
4. What remains unknown without platform-side telemetry.

It does not identify the actual ChatGPT extraction mechanism. Note that ctime
cannot be restored by ordinary timestamp-preservation operations.

## Files and outputs

- `t11_timestamp_provenance.py` — the unprivileged orchestrator.
- `t11_privileged_helper.py` with `t11_privileged_wrapper.sh` — the only sudo'd
  command.
- `.github/workflows/archive-boundary-t11.yml` — the workflow.

The evidence artifact `archive-boundary-t11-evidence-<run_id>` contains:
- `provenance_report.md` / `.json`
- `original_zip_manifest.json`, `original_png_verification.json`
- `artifact_acquisition.json`, `historical_metadata_search.json`, `filesystem_info.json`
- `results.json`
- per scenario, in `scenarios/<id>/`: `result.json`, `steps.json`,
  `snapshot_before.json`, `snapshot_after.json` and `traces/NN.strace`

Run locally with the original artifact:
`python3 t11_timestamp_provenance.py --out ./evidence-out-t11 --base /tmp --artifact-zip <zip>`
