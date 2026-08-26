"""PageRank. Adapted from AlgoTune's pagerank task (MIT)."""

import random

ALPHA = 0.85
TOL = 1e-10
MAX_ITER = 1000


def generate_problem(n, random_seed=1):
    rng = random.Random(random_seed)
    if n <= 0:
        return {"adjacency_list": []}
    p = min(1.0, min(max(1, n - 1), 8) / (n - 1)) if n > 1 else 0.0
    adjacency = []
    for i in range(n):
        adjacency.append(sorted(j for j in range(n) if j != i and rng.random() < p))
    return {"adjacency_list": adjacency}


def reference(problem):
    """Power iteration over a dense transition matrix."""
    adjacency = problem["adjacency_list"]
    n = len(adjacency)
    if n == 0:
        return {"pagerank_scores": []}
    if n == 1:
        return {"pagerank_scores": [1.0]}
    matrix = [[0.0] * n for _ in range(n)]
    for i, outgoing in enumerate(adjacency):
        if outgoing:
            share = 1.0 / len(outgoing)
            for j in outgoing:
                matrix[j][i] = share
        else:
            share = 1.0 / n
            for j in range(n):
                matrix[j][i] = share
    rank = [1.0 / n] * n
    teleport = (1.0 - ALPHA) / n
    for _ in range(MAX_ITER):
        new = []
        for j in range(n):
            total = 0.0
            row = matrix[j]
            for i in range(n):
                total += row[i] * rank[i]
            new.append(ALPHA * total + teleport)
        change = sum(abs(a - b) for a, b in zip(new, rank))
        rank = new
        if change < TOL:
            break
    return {"pagerank_scores": rank}


def _exact(adjacency):
    n = len(adjacency)
    rank = [1.0 / n] * n
    teleport = (1.0 - ALPHA) / n
    dangling = [i for i, out in enumerate(adjacency) if not out]
    for _ in range(MAX_ITER):
        new = [teleport] * n
        dangling_mass = sum(rank[i] for i in dangling) / n
        for i, outgoing in enumerate(adjacency):
            if outgoing:
                share = ALPHA * rank[i] / len(outgoing)
                for j in outgoing:
                    new[j] += share
        if dangling:
            bump = ALPHA * dangling_mass
            new = [x + bump for x in new]
        change = sum(abs(a - b) for a, b in zip(new, rank))
        rank = new
        if change < TOL:
            break
    return rank


def is_solution(problem, solution):
    if not isinstance(solution, dict) or "pagerank_scores" not in solution:
        return False
    adjacency = problem["adjacency_list"]
    n = len(adjacency)
    scores = solution["pagerank_scores"]
    if len(scores) != n:
        return False
    if n == 0:
        return True
    try:
        scores = [float(s) for s in scores]
    except (TypeError, ValueError):
        return False
    if any(s < 0 or s != s or s in (float("inf"), float("-inf")) for s in scores):
        return False
    if abs(sum(scores) - 1.0) > 1e-6:
        return False
    expected = [1.0] if n == 1 else _exact(adjacency)
    return max(abs(a - b) for a, b in zip(scores, expected)) < 1e-6
