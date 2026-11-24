#!/usr/bin/env python3
"""Runs one extractor as root and proves the effective identity."""
from __future__ import annotations
import argparse
import os
import subprocess
import sys
import zipfile

p = argparse.ArgumentParser()
p.add_argument("--extractor", choices=("unzip", "zipfile"), required=True)
p.add_argument("--archive", required=True)
p.add_argument("--dest", required=True)
a = p.parse_args()

print(f"ROOT_HELPER_EUID={os.geteuid()} EGID={os.getegid()}", flush=True)
if os.geteuid() != 0:
    raise SystemExit(77)

if a.extractor == "unzip":
    result = subprocess.run(["unzip", "-o", a.archive, "-d", a.dest])
    raise SystemExit(result.returncode)

with zipfile.ZipFile(a.archive) as zf:
    zf.extractall(a.dest)
print("ZIPFILE_EXTRACTALL_COMPLETED", flush=True)
