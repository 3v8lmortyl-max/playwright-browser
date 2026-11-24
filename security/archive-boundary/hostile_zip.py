#!/usr/bin/env python3
"""Generate hostile ZIP archives for the archive-extraction boundary suite.

This module is run by the *unprivileged* orchestrator user. It never extracts
anything and never runs with elevated privileges.

Design / safety invariant
--------------------------
Every crafted entry is constructed so that its worst-case resolved target stays
*inside the per-test lab directory* (``lab/`` or ``lab/outside/``). It must never
reach ``LAB_ROOT`` (the parent of all labs) or anywhere above it. Before writing
an archive we recompute the worst-case resolved target for each entry and
``assert`` it is confined to ``lab``. A coding mistake therefore fails loudly at
generation time instead of ever producing an archive that could escape the
disposable sandbox.

The archives intentionally model classic extraction-boundary attacks (the
"Zip-Slip" family): parent-directory traversal, absolute paths, nested
traversal, symlink-then-write-through, duplicate names, special-file and
setuid/setgid metadata, and symlink/path collision. They exist only to measure
whether an extractor confines writes to its extraction root.
"""

from __future__ import annotations

import os
import stat
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# Unix file-type + permission modes, shifted into ZIP "external attributes".
S_IFREG = 0o100000
S_IFLNK = 0o120000
S_IFIFO = 0o010000

TEST_IDS = ("T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8")

TEST_TITLES = {
    "T1": "Parent-directory ('../') path traversal",
    "T2": "Absolute archive path",
    "T3": "Nested path traversal",
    "T4": "Symlink entry followed by a file created through the symlink",
    "T5": "Duplicate filenames / overwrite behavior",
    "T6": "Hardlink-like / special-file (FIFO) metadata",
    "T7": "Setuid/setgid permission metadata",
    "T8": "Symlink / path collision (symlink then regular file, same name)",
}

# Vectors that cannot apply to a given extractor are reported INCONCLUSIVE by the
# orchestrator rather than SAFE. The Python stdlib ``zipfile`` never creates
# symlinks (it writes the link target as regular file content), so the symlink
# vectors are not applicable to it.
VECTOR_REQUIRES_SYMLINK = {"T4", "T8"}


@dataclass
class Entry:
    """One archive member and how its worst-case target is computed."""

    name: str
    data: bytes
    mode: int  # full st_mode-style value (type bits | perm bits)
    # Returns the set of absolute filesystem paths this entry could create a
    # write at, given the extraction destination directory.
    worst_case: Callable[[Path], list[Path]] = field(repr=False)
    note: str = ""


def _zi(name: str, mode: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.create_system = 3  # Unix: makes `external_attr` mode bits authoritative
    info.external_attr = (mode & 0xFFFF) << 16
    # Mark directory entries so extractors treat them as directories.
    if stat.S_ISDIR(mode):
        info.external_attr |= 0x10
    return info


def _norm(dest: Path, name: str) -> Path:
    """Resolve ``name`` the way a naive ``join(dest, name)`` extractor would.

    Mirrors ``os.path.join`` semantics: an absolute ``name`` discards ``dest``.
    """
    return Path(os.path.normpath(os.path.join(str(dest), name)))


def build_entries(lab: Path, test_id: str, tag: str) -> list[Entry]:
    """Return the ordered entries for ``test_id``.

    ``lab`` is the per-test lab directory; ``dest`` (the extraction root) is
    ``lab/extract``. ``tag`` is a short unique marker baked into filenames so
    evidence is unambiguous.
    """
    lab = lab.resolve()
    dest = lab / "extract"
    outside = lab / "outside"

    def wc_join(name: str):
        return lambda d: [_norm(d, name)]

    if test_id == "T1":
        # Single "../" climbs from the extraction root into the lab directory.
        name = f"../T1_escape_{tag}"
        return [
            Entry(name, b"T1 parent-traversal payload\n", S_IFREG | 0o644,
                  wc_join(name), "single ../ out of extraction root")
        ]

    if test_id == "T2":
        # Absolute path aimed *into the lab* (lab/outside). If the extractor
        # honors absolute paths the file lands outside the extraction root.
        abs_name = str(outside / f"T2_abs_{tag}")
        return [
            Entry(abs_name, b"T2 absolute-path payload\n", S_IFREG | 0o644,
                  lambda d, n=abs_name: [Path(n)], "absolute path into lab/outside")
        ]

    if test_id == "T3":
        # Nested traversal: a subdir prefix then climb back out to lab/outside.
        name = f"sub/deeper/../../../outside/T3_nested_{tag}"
        return [
            Entry(name, b"T3 nested-traversal payload\n", S_IFREG | 0o644,
                  wc_join(name), "nested ../../.. into lab/outside")
        ]

    if test_id == "T4":
        # Symlink pointing at lab/outside, then a file written *through* it.
        link = f"T4_link_{tag}"
        through = f"{link}/T4_through_{tag}"
        target = "../outside"  # relative to extract/ -> lab/outside
        return [
            Entry(link, target.encode(), S_IFLNK | 0o777,
                  lambda d: [outside], "symlink -> ../outside"),
            Entry(through, b"T4 write-through-symlink payload\n", S_IFREG | 0o644,
                  lambda d: [outside / f"T4_through_{tag}", _norm(d, through)],
                  "file created through the symlink"),
        ]

    if test_id == "T5":
        # Two members with the same name, different content, both in-root.
        name = f"T5_dup_{tag}"
        return [
            Entry(name, b"T5 FIRST copy\n", S_IFREG | 0o644, wc_join(name),
                  "first duplicate"),
            Entry(name, b"T5 SECOND copy (overwrite?)\n", S_IFREG | 0o644,
                  wc_join(name), "second duplicate, same name"),
        ]

    if test_id == "T6":
        # FIFO / special-file metadata (ZIP has no true hardlinks; a FIFO mode
        # exercises the "special file" branch). Stays in-root.
        name = f"T6_special_{tag}"
        return [
            Entry(name, b"", S_IFIFO | 0o644, wc_join(name),
                  "entry carrying S_IFIFO mode bits"),
        ]

    if test_id == "T7":
        # Setuid+setgid regular file, in-root. Dangerous if honored while
        # extracting as root, even though it is not a boundary escape.
        name = f"T7_setuid_{tag}"
        mode = S_IFREG | stat.S_ISUID | stat.S_ISGID | 0o755  # 0o106755
        return [
            Entry(name, b"T7 setuid/setgid payload\n", mode, wc_join(name),
                  "mode 0o6755 (setuid+setgid)"),
        ]

    if test_id == "T8":
        # Symlink and a regular file share one name. Does the extractor replace
        # the symlink, or write the file through it into lab/outside?
        name = f"T8_col_{tag}"
        target = "../outside"
        return [
            Entry(name, target.encode(), S_IFLNK | 0o777,
                  lambda d: [outside], "symlink -> ../outside (first)"),
            Entry(name, b"T8 regular-file content (second)\n", S_IFREG | 0o644,
                  lambda d: [outside / name, _norm(d, name)],
                  "regular file, same name (second)"),
        ]

    raise ValueError(f"unknown test id: {test_id!r}")


def _assert_confined(lab: Path, dest: Path, entries: list[Entry]) -> None:
    """Fail loudly unless every worst-case target stays inside ``lab``."""
    lab = lab.resolve()
    lab_prefix = str(lab) + os.sep
    for e in entries:
        for target in e.worst_case(dest):
            t = Path(os.path.normpath(str(target)))
            ok = str(t) == str(lab) or str(t).startswith(lab_prefix)
            if not ok:
                raise AssertionError(
                    f"SAFETY GATE: entry {e.name!r} worst-case target {t} "
                    f"is not confined to lab {lab}"
                )


def build(lab: Path, test_id: str, tag: str) -> Path:
    """Build the archive for ``test_id`` and return its path (``lab/input/payload.zip``).

    Raises if any entry could resolve outside the lab (safety gate).
    """
    lab = Path(lab).resolve()
    dest = lab / "extract"
    entries = build_entries(lab, test_id, tag)
    _assert_confined(lab, dest, entries)

    input_dir = lab / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    zip_path = input_dir / "payload.zip"

    # ZIP_STORED keeps bytes verbatim and avoids any surprise from compression.
    # Duplicate-name entries (T5/T8) are deliberate; silence the stdlib warning.
    import warnings

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as zf:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Duplicate name:")
            for e in entries:
                zf.writestr(_zi(e.name, e.mode), e.data)

    os.chmod(zip_path, 0o644)
    return zip_path


if __name__ == "__main__":
    # Dump one sample archive per test into a scratch dir for manual inspection.
    import argparse
    import tempfile

    ap = argparse.ArgumentParser(description="Emit sample hostile archives.")
    ap.add_argument("--out", default=None, help="output dir (default: a temp dir)")
    args = ap.parse_args()

    out = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="hostile-samples-"))
    for tid in TEST_IDS:
        lab = out / f"{tid}__sample"
        (lab / "extract").mkdir(parents=True, exist_ok=True)
        (lab / "outside").mkdir(parents=True, exist_ok=True)
        z = build(lab, tid, "sample")
        print(f"{tid}: {z}")
        with zipfile.ZipFile(z) as zf:
            for info in zf.infolist():
                mode = (info.external_attr >> 16) & 0xFFFF
                print(f"    {info.filename!r:50s} mode={mode:06o} size={info.file_size}")
