---
name: automative:evolve
description: Propose a small edit to the pinned protocol from validated strategies, then benchmark it against the current version and the bare loop before a human promotes it.
argument-hint: "[--from VERSION] [--driver claude-p|manual]"
---

Start right away.

1. Run `automative evolve --propose $ARGUMENTS`. It prints the validated strategies, the current
   protocol, and the candidate directory it created.
2. Edit the candidate's `SKILL.md`, `references/*.md`, or `rules.toml` in place. At most three
   section level changes, at most 40 new lines each. Ground each change in ledger evidence and say
   so in the manifest's `rationale`. Write the new text the way `references/writing.md` says.
3. Run `automative protocol candidate validate <version>`. Fix it until it passes.
4. Run `automative evolve --bench <version> $ARGUMENTS`. This runs the benchmark stages headlessly
   (same budget, seeds, held out split, and a null protocol arm) and prints the gate result.
5. Report the result. You never promote. A human runs
   `automative protocol promote <version> --confirm`.
