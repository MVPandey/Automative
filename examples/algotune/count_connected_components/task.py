"""Count connected components. Adapted from AlgoTune's count_connected_components task (MIT)."""

import random


def _connected(size, rng):
    """Random undirected graph on `size` nodes, resampled until it is connected."""
    if size == 1:
        return []
    while True:
        edges = [(u, v) for u in range(size) for v in range(u + 1, size) if rng.random() < 0.2]
        if _count(size, edges) == 1:
            return edges


def _count(n, edges):
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for u, v in edges:
        ru, rv = find(u), find(v)
        if ru != rv:
            parent[ru] = rv
    return len({find(x) for x in range(n)})


def generate_problem(n, random_seed=1):
    rng = random.Random(random_seed)
    if n <= 1:
        return {"edges": [], "num_nodes": n}
    k = rng.randint(2, min(5, n))
    cuts = sorted(rng.sample(range(1, n), k - 1))
    sizes = [b - a for a, b in zip([0] + cuts, cuts + [n])]
    edges, base = [], 0
    for size in sizes:
        edges.extend((base + u, base + v) for u, v in _connected(size, rng))
        base += size
    perm = list(range(n))
    rng.shuffle(perm)
    edges = [(perm[u], perm[v]) for u, v in edges]
    rng.shuffle(edges)
    return {"edges": edges, "num_nodes": n}


def reference(problem):
    """Breadth first search from every unvisited node, rescanning the edge list for neighbours."""
    n = problem["num_nodes"]
    edges = problem["edges"]
    visited = [False] * n
    components = 0
    for start in range(n):
        if visited[start]:
            continue
        components += 1
        visited[start] = True
        queue = [start]
        while queue:
            node = queue.pop(0)
            for u, v in edges:
                if u == node and not visited[v]:
                    visited[v] = True
                    queue.append(v)
                elif v == node and not visited[u]:
                    visited[u] = True
                    queue.append(u)
    return {"number_connected_components": components}


def is_solution(problem, solution):
    if not isinstance(solution, dict) or "number_connected_components" not in solution:
        return False
    expected = _count(problem["num_nodes"], problem["edges"])
    return solution["number_connected_components"] == expected
