# VCF.py - ATE-2704 wrapper around the core VCF startup module
# Version 1.0 - 2026-11-15
#
# Runs right after ESXi.py, when every ESXi host answers but before any VCF
# VM is powered on. It:
#   1. finds out which 9.1.1 upgrades this lab has had (Startup/ate2704stage.py,
#      direct ESXi connections - vCenter is not running yet),
#   2. adjusts the startup and shutdown settings for that stage, in memory
#      (lsf.config, used by the rest of this startup) and in /tmp/config.ini
#      (used by the shutdown), keeping the original as /tmp/config.ini.base,
#   3. runs the core ~/hol/Startup/VCF.py unchanged.
#
# A lab that has not been upgraded gets no changes. If detection fails, the
# baseline config.ini is used, as before this wrapper existed.
#
# Standalone (on the Manager): python3 VCF.py --standalone --dry-run
#   detects the stage and shows the changes, then runs the core VCF.py dry run.

import importlib.util
import os
import sys

MODULE_NAME = 'VCF'
CORE = '/home/holuser/hol/Startup/VCF.py'
HERE = os.path.dirname(os.path.abspath(__file__))


def _stage_module():
    spec = importlib.util.spec_from_file_location('ate2704stage', os.path.join(HERE, 'ate2704stage.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def prepare(lsf, dry_run=False):
    """Detect the stage and apply the overlay. Never raises: failures fall back to the baseline."""
    try:
        stg = _stage_module()
        lsf.write_output('ATE-2704: detecting the lab upgrade stage (direct ESXi connections)...')
        stage = stg.detect(lsf)
        if not stage['ok'] and stage['errors']:
            lsf.write_output('ATE-2704: retrying stage detection in 60 s (' + '; '.join(stage['errors'])[:300] + ')')
            lsf.labstartup_sleep(60)
            stage = stg.detect(lsf)
        lsf.write_output('ATE-2704 stage: ' + stg.summary(stage))
        changes = stg.apply_overlay(lsf.config, stage, lsf.write_output)
        for c in changes:
            lsf.write_output('ATE-2704 setting: ' + c)
        if not changes:
            lsf.write_output('ATE-2704: no setting changes - baseline config.ini applies')
        if not dry_run:
            stg.save(stage, changes)
            if changes:
                stg.write_config(lsf.config, lsf.configini)
                lsf.write_output(f'ATE-2704: wrote the adjusted settings to {lsf.configini} '
                                 f'(original kept as {stg.BASE_COPY})')
        return stage
    except Exception as e:
        lsf.write_output(f'ATE-2704: stage detection failed ({e}) - using the baseline config.ini')
        return None


def main(lsf=None, standalone=False, dry_run=False):
    if lsf is None:
        sys.path.insert(0, '/home/holuser/hol')
        import lsfunctions as lsf
        if not standalone:
            lsf.init(router=False)
    prepare(lsf, dry_run=dry_run)
    spec = importlib.util.spec_from_file_location('core_VCF', CORE)
    core = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(core)
    return core.main(lsf=lsf, standalone=standalone, dry_run=dry_run)


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description='ATE-2704 VCF startup wrapper')
    p.add_argument('--standalone', action='store_true')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--stage-only', action='store_true', help='detect and show the changes only, do not run the core VCF.py')
    a = p.parse_args()
    sys.path.insert(0, '/home/holuser/hol')
    import lsfunctions as _lsf
    _lsf.init(router=False)
    if a.stage_only:
        prepare(_lsf, dry_run=True)
        sys.exit(0)
    sys.exit(0 if main(lsf=_lsf, standalone=a.standalone, dry_run=a.dry_run) is not False else 1)
