# T10 — extraction provenance reconstruction

Separate from T1–T9 (none of their files are modified). T10 runs 30 synthetic
scenarios to find which sequences of operations can produce the historical file

    /mnt/data/gh_demo/screenshots/01_github.com_01.png   0:1001  mode 0644
    /mnt/data/gh_demo                                     0:1001  mode 2755

The file came from a ZIP whose PNG entry has mode `0100644` and carries no
UID/GID extra fields. The historical values (including PNG SHA-256 `dd0ff1…` and
ZIP SHA-256 `cafc11…`) are reference data only. Every experiment uses synthetic
files inside a disposable `/tmp/archbound-t10-*` lab that mirrors the
`mnt/data/gh_demo` layout. Nothing reads or claims access to any platform's
filesystem or processes.

| Question | Scenarios |
|---|---|
| 1. Root download → root extraction | S1a–S1e (setgid vs plain parent, unzip vs zipfile, umask 022 vs 002) |
| 2. Root download → unprivileged materialization + extraction | S2a–S2b |
| 3. Unprivileged download → privileged extraction | S3a–S3c |
| 4. Existing setgid destination vs newly created nested dirs | S4a–S4e (explicit 2755, inherited, chgrp without setgid, archive directory entry) |
| 5. Direct copy vs ZIP extraction | S5a–S5f (`cp`, `cp -p`, `install`, `shutil.copy2`, redirection) |
| 6. chown / rename / copy / atomic replacement | S6a–S6i |

Each step runs as root (uid 0) or as an unprivileged identity (uid 1001, gid 1001)
under `strace -f -tt -y -e trace=%file,%desc,%creds,umask`. After every step the
lab is snapshotted (uid, gid, mode, setgid, inode, size, mtime, ctime, SHA-256),
which gives a per-step ownership-transition log.

Files:
- `extraction_provenance.py` — unprivileged orchestrator: builds the synthetic
  ZIPs, collects evidence, writes the report.
- `extraction_provenance_helper.py` — scenario runner. It is the only command run
  via sudo, and it drops privileges for the unprivileged steps.
- `extraction_provenance_wrapper.sh` — narrow sudo wrapper.
- `.github/workflows/archive-boundary-t10.yml` — CI (`workflow_dispatch` + push on
  `provenance-test`, `contents: read`, no secrets, evidence uploaded always).

Output (`evidence-out-t10/`):
- `provenance_report.md` / `.json`, in four sections: experimentally reproduced
  mechanisms, mechanisms consistent with the evidence, mechanisms ruled out, and
  unknowns that need platform-side telemetry. It also contains a candidate
  chronology and a step-by-step ownership log.
- `scenarios/<id>/scenario.json` plus `traces/NN.strace` for every step.
- `results.json` and `origin/` (the synthetic inputs).

Run locally: `python3 extraction_provenance.py --out ./evidence-out-t10 --base /tmp`
