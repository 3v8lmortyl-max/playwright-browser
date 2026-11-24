# T12 — Historical runtime telemetry recovery: report

**Final assessment: INSUFFICIENT HISTORICAL EVIDENCE.** Multiple sequences remain. T12 also found that the source archive itself is not unique.

Evidence classes used throughout this report:
- **[HIST]**: recovered historical evidence, with a source reference.
- **[USER]**: user-provided historical values that T12 could not re-verify.
- **[EXP]**: experimental results from T9–T11.
- **[INF]**: inference.
- **[UNK]**: unknown.

## 1. What was searched

The full list of 19 sources is in `evidence_inventory.json`, and every query is in `search_log.json`. In summary:

- **Git history:** all 57 commits on every ref, all commit messages, and the unreachable objects. Nothing historical was found.
- **GitHub search:** code search returned 0 hits, though it covers the default branch only. Issue and pull-request search returned 0.
- **Original run 45136043351 / job 137562438892:** step metadata and the job log.
- **Retained artifacts:** all 41 in the repository were listed and the 25 non-experimental ones were scanned. A read-only job (run 45327222852) read their central directories, `capture_manifest.json`, and the SHA-256 of `screenshots/01_github.com_01.png`.
- **This container:** no `/mnt/data` and no `/proc/325`. Its namespace is `mnt:[4026531832]`, a different runtime. The attachment and upload directories are empty.
- **The historical ChatGPT runtime is inaccessible.** That covers `/mnt/data` on device 27, PID 325 and `mnt:[4026532181]`. No stat, statx, ls, mount, mountinfo, process or audit output from it exists in any reachable source.
- **Not searched:** Gmail and Google Drive. They are outside the authorised source list and hold personal data. They are the only remaining place where an exported ChatGPT transcript could plausibly exist.

## 2. Recovered historical evidence (all upstream: the original GitHub runner, not `/mnt/data`)

| ID | Fact | Confidence |
|---|---|---|
| R01 | Original job timing: `screenshot.py` started 08:07:54.077Z, capture ended 08:08:14Z, render ran 08:08:14–08:08:38Z, upload ran 08:08:38.8–08:08:41.5Z | CONFIRMED [HIST] |
| R02 | Every `CAPTURED` log line carries 08:08:14.742Z (buffered stdout), so the log has no per-file capture time | CONFIRMED [HIST] |
| R03 | Full central directory of 14799922102: 32 entries, DOS times following pipeline order. The PNG is **08:07:56**; the other screenshots run 08:07:58 to 08:08:14, the stills 08:08:16 to 08:08:20, and the mp4 08:08:38 | CONFIRMED [HIST] |
| R04 | `capture_manifest.json` records `captured_at` 08:08:14.741451Z, and its own DOS time is 08:08:14 | CONFIRMED [HIST] |
| R05 | The DOS times are each file's mtime on the runner, in **UTC**, at 2-s resolution, not packaging time. The PNG was written on the runner in [08:07:56, 08:07:58) UTC | STRONGLY SUPPORTED [INF from R01+R03+R04] |
| R06 | The manifest's PNG record: SHA-256 `dd0ff1…`, 209,306 bytes, 1080×1920, from `https://github.com/advisories/GHSA-764m-3ppc-5m73`, scroll 0, HTTP 200 | CONFIRMED [HIST] |
| **R07** | **A second retained artifact, 14797203454** (run 45130379294, main@b16f992, created 07:22:35Z, digest `sha256:ed2067…ddb3`), **contains a byte-identical `screenshots/01_github.com_01.png`** (SHA-256 `dd0ff1…`). Its DOS time is **07:21:58** | CONFIRMED [HIST] |
| R08 | No other retained non-experimental artifact contains the member. Deleted or expired artifacts cannot be checked | CONFIRMED [HIST] |

**Correction to a prior premise.** The established facts attribute the historical PNG to artifact 14799922102. The PNG hash alone does not support that attribution, because two archives contain the identical member. The attribution holds only if the original identification rested on independent historical evidence, such as a recorded download of that artifact ID or archive hash. T12 found no such record.

## 3. Candidate matrix

| Candidate | Supporting evidence | Contradicting evidence | Status |
|---|---|---|---|
| C1 Root extraction into a setgid GID-1001 directory | [EXP] T9, T10 S1a/S1c/S3a/S3c/S4a/S4b, T11 01–03 reproduce the signature | none | CONSISTENT |
| C2 Root copy into a setgid directory | [EXP] T10 S5a/d/e/f, T11 04/06/07/08 | none | CONSISTENT |
| C3 Unprivileged extraction + root chown | [EXP] T10 S6a/S6b. [USER] a UID-1000 process (PID 325) existed, but nothing links it to the file | none | CONSISTENT |
| C4 Temp file + rename | [EXP] T10 S6e, T11 09/10g/11a | none for the setgid-temp or chown variants | CONSISTENT |
| C5 Root platform materialisation service | none recovered | none | UNKNOWN |
| Source archive | 14799922102 and 14797203454 both hold identical bytes | none | both CONSISTENT; others UNKNOWN |

These variants are ruled out because the established attributes contradict them:
- Unprivileged creation with no later chown.
- Direct creation by PID 325 (UID 1000) under ordinary credentials. Caveat: its capabilities and namespace mapping are unknown.
- Root creation in a non-setgid parent with no group change.
- `cp -p` from a source not owned 0:1001.
- Rename of a temp file made in a non-setgid directory with no chown.
- Archive-borne ownership.
- umask-002 extraction that created gh_demo, which would give 2775.

## 4. Answers

1. **Did we recover the historical PNG's mtime?** **No — UNKNOWN.** No reachable record holds it. Upstream, only the runner-side mtime is STRONGLY SUPPORTED: [08:07:56, 08:07:58) UTC, or 07:21:58 for the second artifact. That is not the `/mnt/data` mtime.
2. **Did we recover its ctime or birth time?** **No — UNKNOWN.**
3. **Did we recover the historical `screenshots/` directory metadata?** **No — UNKNOWN.** No UID/GID, mode, setgid bit, timestamps or creation order were found.
4. **Did we establish how gh_demo got 0:1001 2755?** **No — UNKNOWN.** Inheritance from a setgid `/mnt/data` and an explicit chgrp/chmod are both CONSISTENT [EXP, T10 S1a vs S4a]. The metadata of `/mnt/data` was not recovered.
5. **Did we identify the process that created or replaced the PNG?** **No — UNKNOWN.** PID 325 [USER] runs as UID 1000. It cannot have created the UID-0 file under ordinary credentials, but nothing ties any other process to the path.
6. **Did we identify the actual extraction or copy operation?** **No — UNKNOWN.** Whether any extraction happened at all is not established; a direct copy or service write fits equally.
7. **Which candidate sequences can now be eliminated?** **No new top-level candidate is eliminated.** C1–C4 remain CONSISTENT and C5 UNKNOWN. Only the sub-variants listed in §3 are ruled out, all by attributes established before T12. T12 also **re-opens the source attribution**: two archives fit.
8. **Is a unique historical sequence established?** **No.**
9. **What exact missing evidence prevents a definitive conclusion?**
   - The historical PNG's `statx` output (mtime, ctime and birth time in nanoseconds). This is decisive:
     - mtime = 2026-11-23T08:07:56Z with 0 ns (or that instant shifted by the extractor's TZ offset) means an mtime-restoring extraction (unzip-like) from 14799922102, or a preserving copy of such a file.
     - mtime = 07:21:58Z with 0 ns means the same from 14797203454.
     - A nanosecond mtime equal to ctime means a single new write (zipfile, cp, copyfile, stream).
     - mtime earlier than ctime with no DOS match means a rename, chown or other later inode change.
   - `statx` of `screenshots/` and `gh_demo/`, and `ls -ln` / `stat` of `/mnt/data`.
   - The process lineage of whatever wrote the path (parent of PID 325, any root helper or connector), with credentials, capabilities and argv.
   - Mount provenance: `/proc/<pid>/mountinfo` for mount 52, the filesystem type and options (including `grpid`), overlay or FUSE layers, and `/proc/<pid>/uid_map` and `gid_map`.
   - A download or materialisation record naming the artifact ID or archive SHA-256, which would settle 14799922102 versus 14797203454.
   - File-event telemetry (audit, fanotify or inotify) for create, rename and chown on the path.

   All of these live only in the historical platform runtime, which is inaccessible here and whose file no longer exists.

## 5. Conclusion

**INSUFFICIENT HISTORICAL EVIDENCE.** The surviving records establish where the bytes came from upstream: a Playwright capture of the GitHub advisory page, written on a GitHub runner at about 08:07:56Z or 07:21:58Z and packaged into two artifacts. They establish nothing about how the file was materialised in `/mnt/data`. Four mechanism families remain CONSISTENT, the source archive is ambiguous between two retained artifacts, and none of the telemetry that could discriminate between them could be reached.

This investigation does not identify the actual ChatGPT extraction mechanism. It claims no access to that platform's internals.
