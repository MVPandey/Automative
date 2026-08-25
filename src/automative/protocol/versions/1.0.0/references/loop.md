# Loop details

## What `automative try` does, in order

1. Refuses if a try is pending, hooks are dead, the spec or a protected file changed, files outside
   scope are dirty, or there is no in scope diff.
2. Stages only in scope files and commits `automative(iN): <message>` with `Hypothesis:` and
   `Predict:` trailers.
3. Runs the verify command `repeats` times (median), then the guard commands, then the held out
   command if one is configured and the metric improved.
4. Decides. Keep only if the score improved by at least `min_improvement`, all guards pass, and the
   held out metric did not get worse. Ties and gains below the threshold are discards.
5. On anything but a keep, adds a revert commit. The failed diff stays in history and under
   `refs/automative/<run>/<iter>`. Appends the ledger row, writes a git note, updates the budget.

## Reading verdicts

- "improvement below min_improvement": the direction was right but the size was inside the noise.
  Look for a larger lever in the same area instead of repeating the tweak.
- "metric got worse": if the reason is clear, record the kind of change as a `fails` strategy.
- "guard command failed": your change broke behaviour the tests protect. Rework it. Do not weaken
  the tests.
- "last stdout line was not a bare number": the verify command printed something unexpected,
  usually a crash printed to stdout. Read the log.

## Noise

If `repeats` is 1 and the same idea flips between keep and discard, tell the human to raise
`repeats` or `min_improvement`. Do not "confirm" by rerunning the same change. The harness treats an
empty diff as a no-op.
