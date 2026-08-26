---
name: automative
description: >-
  Improvement loop for anything a command can score: code speed, ML training configs, prompts
  against an eval, trading strategy parameters against a backtest, drafts against a rubric. Use it
  when the user wants to optimize, iterate on, hill climb, or keep improving something with a
  measurable metric, or mentions AUTOMATIVE.md or automative. The automative CLI owns scores, keep
  or discard decisions, git, budgets, and hooks. You own hypotheses and edits.
user-invocable: true
allowed-tools: Bash(automative:*), Bash(git log:*), Bash(git diff:*), Bash(git show:*), Read, Edit, Write, Grep, Glob
metadata:
  author: Manav Pandey
  version: "0.1.0"
license: MIT
---

# Automative

This skill is a thin loader. The loop you follow is a versioned protocol pinned per project. Read it
through the CLI so the right version is used and its integrity is checked.

1. Run `automative session brief`.
   - Exit 4: no run yet. If `AUTOMATIVE.md` exists and is committed, run `automative run start`.
     Otherwise run `/automative:init` with the human. Never write `verify`, `scope`, `protected`,
     or budgets yourself.
   - Exit 3: the run is done. Run `automative run end`, report the summary, stop.
   - Exit 5: paused. Tell the human, stop.
   - Exit 0: active. Continue.
2. The brief's `Protocol` line names the pinned `SKILL.md`. Read that file once per session (again
   after a context reset) and follow it exactly. Its `references/` load when you need them.
3. Loop: one change inside `scope`, then `automative try -m "..." --hypothesis "..."`, then read
   the verdict, then learn sparingly, then repeat. Never compute, guess, or announce a score
   yourself. Never ask whether to continue. The harness ends the run. To build on an earlier attempt
   instead of the best (a crash worth fixing, a near miss worth combining), run
   `automative checkout N` first; `automative tree` shows the attempts. If the contract has a
   `heldout` command, the verdict says only pass or fail for it. Paths under `sealed` and the
   held-out command itself are off limits; the hooks refuse them and every call is recorded.
4. Write everything a person will read later (commit messages, hypotheses, strategies, summaries)
   in plain English: one idea per sentence, name the actor, give the number, no filler, no em
   dashes, no claims of significance. The protocol's `references/writing.md` has the rules.

If `automative` is not on PATH, tell the human to run
`uv tool install git+https://github.com/MVPandey/Automative` (or `uv tool install .` from the plugin
directory).
