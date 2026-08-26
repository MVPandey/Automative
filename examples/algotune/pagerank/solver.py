"""The file the agent edits. It starts as the reference from task.py, so the speedup starts at 1.0.

Keep the class name and the method signature. Return the same structure task.is_solution expects.
"""

import task


class Solver:
    def solve(self, problem):
        return task.reference(problem)
