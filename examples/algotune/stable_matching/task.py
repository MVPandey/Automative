"""Stable matching. Adapted from AlgoTune's stable_matching task (MIT)."""

import random


def generate_problem(n, random_seed=1):
    rng = random.Random(random_seed)
    indices = list(range(n))
    proposer_prefs = [rng.sample(indices, n) for _ in range(n)]
    receiver_prefs = [rng.sample(indices, n) for _ in range(n)]
    return {"proposer_prefs": proposer_prefs, "receiver_prefs": receiver_prefs}


def reference(problem):
    """Gale-Shapley with list.index rank lookups and pop(0)."""
    proposer_prefs = problem["proposer_prefs"]
    receiver_prefs = problem["receiver_prefs"]
    n = len(proposer_prefs)
    next_choice = [0] * n
    receiver_match = [None] * n
    free = list(range(n))
    while free:
        p = free.pop(0)
        r = proposer_prefs[p][next_choice[p]]
        next_choice[p] += 1
        current = receiver_match[r]
        if current is None:
            receiver_match[r] = p
        elif receiver_prefs[r].index(p) < receiver_prefs[r].index(current):
            receiver_match[r] = p
            free.append(current)
        else:
            free.append(p)
    matching = [0] * n
    for r, p in enumerate(receiver_match):
        matching[p] = r
    return {"matching": matching}


def is_solution(problem, solution):
    if not isinstance(solution, dict) or "matching" not in solution:
        return False
    proposer_prefs = problem["proposer_prefs"]
    receiver_prefs = problem["receiver_prefs"]
    n = len(proposer_prefs)
    matching = solution["matching"]
    if not isinstance(matching, list) or len(matching) != n:
        return False
    if len(set(matching)) != n or not all(isinstance(r, int) and 0 <= r < n for r in matching):
        return False
    receiver_to_proposer = [0] * n
    for p, r in enumerate(matching):
        receiver_to_proposer[r] = p
    receiver_rank = [[0] * n for _ in range(n)]
    for r, prefs in enumerate(receiver_prefs):
        for rank, p in enumerate(prefs):
            receiver_rank[r][p] = rank
    for p in range(n):
        prefs = proposer_prefs[p]
        for better in prefs[: prefs.index(matching[p])]:
            other = receiver_to_proposer[better]
            if receiver_rank[better][p] < receiver_rank[better][other]:
                return False
    return True
