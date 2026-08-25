# Modes

Modes only change how you think. The harness treats every try the same way.

- `draft`: a new approach from the root. Use it for the first two or three tries of a run and after
  a plateau. Larger structural changes are fine here, but still one change per try.
- `improve`: refine the current best with one change whose effect you can measure directly. This is
  the default after a keep.
- `debug`: repair a crash or a metric error from the previous try while keeping its approach. At
  most two repairs per idea, then drop the idea.

Pass the mode with `--mode`. It is recorded in the ledger so the report can show which mode produced
the keeps.
