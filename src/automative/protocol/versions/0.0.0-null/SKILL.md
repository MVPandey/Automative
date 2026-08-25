# Automative protocol 0.0.0-null (baseline loop)

This is the bare modify, verify, keep loop with no guidance. It is the control arm that every real
protocol has to beat on held out benchmark tasks.

1. Run `automative session brief`. If it reports no run, run `automative run start`.
2. Edit files inside `scope`.
3. Run `automative try -m "<what changed>" --hypothesis "<why>"`.
4. Repeat until the brief reports the run is done. Then run `automative run end`.
