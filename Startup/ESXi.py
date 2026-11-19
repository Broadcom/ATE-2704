# ESXi.py - ATE-2704 wrapper around the core ESXi startup module
# Version 1.0 - 2026-11-19
#
# Only does something on the Region B recovery template: a copy of a lab at
# the end of Lab 3 with the Region A vCenter and hosts deleted, for students
# who move on to Labs 4 and 5. That template's Manager has the marker file
# /home/holuser/ate2704-no-region-a.
#
# With the marker, before the core ESXi.py checks the hosts, it removes the
# Region A vCenter, hosts, datastore, cluster, test VMs and their pings, ports
# and URLs (Startup/ate2704stage.py strip_region_a) from lsf.config, used by
# the rest of this startup, and from /tmp/config.ini, used by the shutdown
# (original kept as /tmp/config.ini.base). Without the marker it changes
# nothing. Either way it then runs the core ~/hol/Startup/ESXi.py unchanged.
#
# Standalone (on the Manager): python3 ESXi.py --standalone --dry-run

import importlib.util
import os
import sys

MODULE_NAME = 'ESXi'
CORE = '/home/holuser/hol/Startup/ESXi.py'
HERE = os.path.dirname(os.path.abspath(__file__))


def _stage_module():
    spec = importlib.util.spec_from_file_location('ate2704stage', os.path.join(HERE, 'ate2704stage.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def remove_region_a(lsf, dry_run=False):
    """Region B recovery template: drop Region A from the settings. Never raises."""
    try:
        stg = _stage_module()
        if not stg.region_a_removed():
            return
        lsf.write_output(f'ATE-2704: {stg.REGION_A_MARKER} found - Region A vCenter and hosts are not part of '
                         'this lab (Region B recovery template); removing them from the settings')
        changes = stg.strip_region_a(lsf.config, lsf.write_output)
        for c in changes:
            lsf.write_output('ATE-2704 setting: ' + c)
        if changes and not dry_run:
            stg.write_config(lsf.config, lsf.configini)
            lsf.write_output(f'ATE-2704: wrote the adjusted settings to {lsf.configini} '
                             f'(original kept as {stg.BASE_COPY})')
    except Exception as e:
        lsf.write_output(f'ATE-2704: could not remove Region A from the settings ({e})')


def main(lsf=None, standalone=False, dry_run=False):
    if lsf is None:
        sys.path.insert(0, '/home/holuser/hol')
        import lsfunctions as lsf
        if not standalone:
            lsf.init(router=False)
    remove_region_a(lsf, dry_run=dry_run)
    spec = importlib.util.spec_from_file_location('core_ESXi', CORE)
    core = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(core)
    return core.main(lsf=lsf, standalone=standalone, dry_run=dry_run)


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description='ATE-2704 ESXi startup wrapper')
    p.add_argument('--standalone', action='store_true')
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    sys.path.insert(0, '/home/holuser/hol')
    import lsfunctions as _lsf
    _lsf.init(router=False)
    sys.exit(0 if main(lsf=_lsf, standalone=a.standalone, dry_run=a.dry_run) is not False else 1)
