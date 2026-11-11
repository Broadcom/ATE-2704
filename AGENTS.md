# Project guidance

Use `/Users/ac004843/Documents/repos/HOLFY27-MGR-DOCS` as the reference for EPC Lab repository conventions. If this checkout is on another machine, locate the corresponding documentation checkout before relying on that path.

## Checkpoints

At the start of each session, if `.checkpoints/*.md` exists, load `~/.codex/skills/checkpoint/SKILL.md` (or `$CODEX_HOME/skills/checkpoint/SKILL.md` when configured) and follow its resume procedure. Compare the saved state with the current repository and ask before continuing unless the user has already authorized resuming that task.

Use the checkpoint skill when the user requests a checkpoint, indicates they are stopping work, asks to resume a checkpoint, or requests clearing checkpoints. If the skill is unavailable, report that and preserve existing notes.
