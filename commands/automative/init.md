---
name: automative:init
description: Write AUTOMATIVE.md for this project with the human (goal, metric, verify and guard commands, scope, protected files, budget).
argument-hint: "[what to optimize, in a sentence]"
---

Help the human write `AUTOMATIVE.md`. Use `AskUserQuestion` for anything you cannot read from the
repo. Collect these, in this order, in at most two rounds of questions:

1. The goal, one paragraph: what "better" means and why. Any domain: code, ML, prompts, trading,
   writing.
2. The metric: a name, a direction (lower or higher), and a verify command whose last stdout line
   is a bare number. If no such command exists, propose one and write it as a protected script.
   Rubric scores are allowed only through a protected judge script that is not this model session.
3. Guards: commands that must exit 0 for a keep (tests, type checks, constraint checkers).
4. Scope: the globs the agent may edit. Protected: the verifier, tests, data prep, lockfiles.
5. Budget: iterations (default 30; 0 means unbounded), minutes (120), plateau patience (8),
   repeats for noisy metrics, min_improvement (for example "2%").

Then run `automative init` with the matching flags (`--goal`, `--metric`, `--direction`,
`--verify`, `--guard`, `--scope`, `--protected`, `--iterations`, `--minutes`, `--repeats`,
`--min-improvement`, `--tag`). Fill in the prose sections of the generated file (Context,
Constraints, Strategy hints, Out of scope) in plain English. Run `automative doctor`. Ask the human
to commit before running `/automative`.

$ARGUMENTS
