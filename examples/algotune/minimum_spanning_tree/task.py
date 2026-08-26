"""Minimum spanning tree. Adapted from AlgoTune's minimum_spanning_tree task (MIT)."""

import random


def generate_problem(n, random_seed=1):
    rng = random.Random(random_seed)
    num_nodes = max(2, n)
    edges = []
    for u in range(num_nodes):
        for v in range(u + 1, num_nodes):
            if rng.random() < 0.3:
                edges.append([u, v, round(rng.uniform(0.1, 10.0), 3)])
    if len(edges) < num_nodes - 1:
        edges = [[u, u + 1, round(rng.uniform(0.1, 10.0), 3)] for u in range(num_nodes - 1)]
    return {"num_nodes": num_nodes, "edges": edges}


def _connected_in(chosen, a, b):
    """Whether a and b are connected by the edges chosen so far (a fresh BFS every time)."""
    seen = {a}
    frontier = [a]
    while frontier:
        node = frontier.pop(0)
        if node == b:
            return True
        for u, v, _ in chosen:
            if u == node and v not in seen:
                seen.add(v)
                frontier.append(v)
            elif v == node and u not in seen:
                seen.add(u)
                frontier.append(u)
    return b in seen


def reference(problem):
    """Kruskal with a breadth first search per edge instead of union-find."""
    chosen = []
    for u, v, w in sorted(problem["edges"], key=lambda e: e[2]):
        if not _connected_in(chosen, u, v):
            chosen.append([min(u, v), max(u, v), w])
        if len(chosen) == problem["num_nodes"] - 1:
            break
    chosen.sort(key=lambda e: (e[0], e[1]))
    return {"mst_edges": chosen}


def _mst_weight(problem):
    parent = list(range(problem["num_nodes"]))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    total, count = 0.0, 0
    for u, v, w in sorted(problem["edges"], key=lambda e: e[2]):
        ru, rv = find(u), find(v)
        if ru != rv:
            parent[ru] = rv
            total += w
            count += 1
    return total, count


def is_solution(problem, solution):
    if not isinstance(solution, dict) or "mst_edges" not in solution:
        return False
    n = problem["num_nodes"]
    weights = {}
    for u, v, w in problem["edges"]:
        weights[(min(u, v), max(u, v))] = w
    edges = solution["mst_edges"]
    expected_total, expected_count = _mst_weight(problem)
    if len(edges) != expected_count:
        return False
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            x = parent[x]
        return x

    total = 0.0
    previous = None
    for edge in edges:
        if len(edge) != 3:
            return False
        u, v, w = edge
        if not (0 <= u < v < n) or weights.get((u, v)) != w:
            return False
        if previous is not None and (u, v) < previous:
            return False
        previous = (u, v)
        ru, rv = find(u), find(v)
        if ru == rv:
            return False
        parent[ru] = rv
        total += w
    return abs(total - expected_total) < 1e-6
