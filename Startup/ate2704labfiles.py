# ate2704labfiles.py - ATE-2704: keep the console's "~/Lab Files" in step with the repo
# Version 1.1 - 2026-11-16
#   1.1: also mirrors the repo's lab-build/ to the console's "~/Lab Build"
#        (build and instructor files, kept out of the students' folder).
#
# Why this exists
# ---------------
# The scripts and JSON files that students use from "~/Lab Files" on the
# console (the upgrade helper scripts, the installer JSON specs and the
# "Break-Fix Scripts" pack) used to live only in the template. Fixing one
# meant re-mastering the template. They now live in this repo, in lab-files/,
# and every lab startup copies them to the console. A fix pushed to the repo
# reaches every lab deployed after that, through the normal git pull at boot.
#
# What it does
# ------------
# lab-files/ mirrors "~/Lab Files": lab-files/a/b.sh -> "~/Lab Files/a/b.sh".
# For each file in lab-files/:
#   - copied if it is missing on the console or its content differs,
#   - the executable bit follows the repo file,
#   - unchanged files are left alone.
# Nothing is ever deleted: files on the console that are not in the repo
# (large or build-only files kept in the template, students' own files) stay.
# Python caches (__pycache__, *.pyc) are skipped.
#
# It runs on the Manager and writes to the console through the NFS mount that
# the core startup uses (/lmchol/home/holuser). It never raises: a failure is
# logged and the lab start carries on with the files already in the template.
#
# Called by Startup/VCF.py (early in every startup).
# Command line (on the Manager, as holuser):
#   python3 ate2704labfiles.py --dry-run    # show what would change
#   python3 ate2704labfiles.py              # sync now

import filecmp
import os
import shutil
import stat
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.normpath(os.path.join(HERE, '..', 'lab-files'))
DEST = '/lmchol/home/holuser/Lab Files'
# Every (repo folder, console folder) pair that is mirrored.
PAIRS = [(SRC, DEST),
         (os.path.normpath(os.path.join(HERE, '..', 'lab-build')), '/lmchol/home/holuser/Lab Build')]
SKIP_DIRS = {'__pycache__', '.git'}
SKIP_SUFFIXES = ('.pyc',)


def _wanted(path):
    return not path.endswith(SKIP_SUFFIXES) and os.path.basename(path) != '.DS_Store'


def sync(log=print, dry_run=False, src=SRC, dest=DEST):
    """Copy new or changed files from src to dest. Returns (copied, unchanged, errors)."""
    copied, unchanged, errors = [], 0, []
    if not os.path.isdir(src):
        log(f'ATE-2704 lab files: no {src} in the repo - nothing to copy')
        return copied, unchanged, errors
    if not os.path.isdir(os.path.dirname(dest)):
        log(f'ATE-2704 lab files: console home {os.path.dirname(dest)} not mounted - skipping')
        return copied, unchanged, ['console not mounted']
    for root, dirs, files in os.walk(src):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        rel_dir = os.path.relpath(root, src)
        for name in sorted(files):
            s = os.path.join(root, name)
            if not _wanted(s):
                continue
            rel = os.path.normpath(os.path.join(rel_dir, name))
            d = os.path.join(dest, rel)
            try:
                if os.path.isfile(d) and filecmp.cmp(s, d, shallow=False):
                    unchanged += 1
                    continue
                copied.append(rel)
                if dry_run:
                    continue
                os.makedirs(os.path.dirname(d), exist_ok=True)
                tmp = d + '.ate2704-new'
                shutil.copyfile(s, tmp)
                mode = 0o755 if os.stat(s).st_mode & stat.S_IXUSR else 0o644
                os.chmod(tmp, mode)
                os.replace(tmp, d)
            except OSError as e:
                errors.append(f'{rel}: {e}')
    verb = 'would copy' if dry_run else 'copied'
    log(f'ATE-2704 lab files: {verb} {len(copied)}, unchanged {unchanged}, errors {len(errors)} '
        f'({src} -> {dest})')
    for rel in copied:
        log(f'ATE-2704 lab files:   {verb}: {rel}')
    for e in errors:
        log(f'ATE-2704 lab files:   WARNING: {e}')
    return copied, unchanged, errors


def run(lsf=None, dry_run=False):
    """Startup entry point. Never raises."""
    log = lsf.write_output if lsf else print
    copied, unchanged, errors = [], 0, []
    for src, dest in PAIRS:
        try:
            c, u, e = sync(log, dry_run, src, dest)
            copied += c; unchanged += u; errors += e
        except Exception as e:
            log(f'ATE-2704 lab files: sync of {src} failed ({e}) - the template copies stay in place')
            errors.append(str(e))
    return copied, unchanged, errors


if __name__ == '__main__':
    run(dry_run='--dry-run' in sys.argv)
