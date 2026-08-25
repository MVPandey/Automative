"""Guard: behaviour must not change (stdlib unittest so the example needs no dependencies)."""

import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))

from slowsort import dedupe_and_sort  # noqa: E402


class DedupeAndSortTests(unittest.TestCase):
    def test_basic(self) -> None:
        self.assertEqual(dedupe_and_sort([3, 1, 3, 2, 1]), [1, 2, 3])

    def test_empty(self) -> None:
        self.assertEqual(dedupe_and_sort([]), [])

    def test_matches_reference(self) -> None:
        rng = random.Random(7)
        data = [rng.randrange(0, 50) for _ in range(500)]
        self.assertEqual(dedupe_and_sort(data), sorted(set(data)))

    def test_does_not_mutate_input(self) -> None:
        data = [5, 4, 4, 3]
        dedupe_and_sort(data)
        self.assertEqual(data, [5, 4, 4, 3])


if __name__ == '__main__':
    unittest.main()
