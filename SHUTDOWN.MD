## Shutdown procedure:

#### 1. Shutdown nested environment:
  1. ssh holuser@manager

  2. Preview first (no changes are made):

     python3 /vpodrepo/2027-labs/2704/Shutdown/Shutdown.py --dry-run

  3. Run the shutdown:

     python3 /vpodrepo/2027-labs/2704/Shutdown/Shutdown.py

     `/vpodrepo/2027-labs/2704` is where labstartup clones this repo on the
     Manager (branch dev on a Holodeck dev pod, main in production).

Wait until complete (about 90 minutes, most of it the vSAN elevator).

This lab's `Shutdown/Shutdown.py` is a wrapper around the core
`~/hol/Shutdown/Shutdown.py`. It first stops the Aria Automation 8.x services
on auto-01b with `/opt/scripts/deploy.sh --shutdown` (usually 10-20 minutes),
then runs the core shutdown with the same options. Do not run the core
`python3 Shutdown/Shutdown.py` in `~/hol` directly: it only shuts down the
Aria Automation guest OS and hard-powers it off after 5 minutes.

Options are the core ones (`--dry-run`, `--quick`, `--no-hosts`, `--phase`,
`--phases`, `--help`) plus `--skip-aria-prep` to leave out the Aria step.
Settings: config.ini `[SHUTDOWN]` vra_prep_hosts and vra_prep_timeout.
Logs: `~/hol/shutdown-aria.log` (Aria step) and `~/hol/shutdown.log` (core).

The core reads `/tmp/config.ini`. Make sure it is this lab's config.ini
(vPod_SKU = ATE-2704) before shutting down.

#### 2. Shutdown router as follows:

  1. On the router, login as root and run the /root/shutdown.sh script -This was written today to perform a graceful shutdown of the kubernetes stuff because the way the holorouter was given to us, it just killed things at shutdown instead of graceful.

     Do this before shutting down the Manager: the router has an NFS mount
     from the Manager.

#### 3. Shutdown manager

#### 4. Shutdown console
