---
name: automative
description: Start or continue an Automative run in this project (a modify, verify, keep or discard loop).
argument-hint: "[--name SLUG] [--iterations N] [--minutes M]"
---

Start right away. Do not deliberate before reading the brief.

1. Run `automative session brief`. Act on its exit code as the `automative` skill describes:
   4 means `automative run start $ARGUMENTS` if `AUTOMATIVE.md` is committed, otherwise
   `/automative:init`; 3 means `automative run end` and stop; 5 means stop; 0 means continue.
2. Read the pinned protocol file named on the brief's `Protocol:` line and follow it until the
   harness reports the run is done.
3. Stream output live. Print one progress line every five iterations and nothing else between
   verdicts. Write in plain English.
