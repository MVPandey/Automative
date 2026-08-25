# When stuck

Signs: five discards in a row; tries since best at half the plateau patience; you are proposing
variations of one idea.

1. Reset your model of the problem. Reread the goal, the constraints, and every in scope file from
   top to bottom. Write three lines into `notes.md` about where the metric is actually decided.
2. Take inventory. From the ledger, list the kinds of change tried. For code: data structure,
   algorithm, caching, I/O. For models: architecture, optimizer, schedule, data. For prompts:
   framing, examples, constraints. For strategies: signals, sizing, risk limits. Name three kinds
   not yet tried.
3. Combine. Take two discards that each moved the metric the right way but not enough, and apply
   them together as one change.
4. Invert. Try the opposite of the last three attempts: bigger instead of smaller, remove instead of
   add.
5. Go structural. Switch to `draft` mode and change the approach, not a parameter.
6. Ask the catalogue. `automative strategy suggest -k 10`, then the `## Strategy hints` section of
   `AUTOMATIVE.md`.
7. Measure first. If you do not know where the metric is decided, spend one try adding only
   instrumentation inside scope. It will be discarded, but the log will tell you where to aim.
   Remove it in the next try.

If none of this produces a keep inside the plateau patience, the harness stops the run and flags it
for a human. That is the right outcome, not a failure on your part.
