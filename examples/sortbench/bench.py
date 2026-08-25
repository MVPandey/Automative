"""Verifier: prints the median wall-clock milliseconds of dedupe_and_sort on a seeded input."""

import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / 'src'))

from slowsort import dedupe_and_sort  # noqa: E402

RNG = random.Random(1234)
DATA = [RNG.randrange(0, 900) for _ in range(4000)]
REPS = 5


def main() -> None:
    timings: list[float] = []
    for _ in range(REPS):
        start = time.perf_counter()
        dedupe_and_sort(list(DATA))
        timings.append((time.perf_counter() - start) * 1000.0)
    print(f'{statistics.median(timings):.3f}')


if __name__ == '__main__':
    main()
