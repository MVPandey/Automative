"""All-pairs shortest paths. Adapted from AlgoTune's shortest_path_dijkstra task (MIT)."""

import random


def generate_problem(n, random_seed=1):
    rng = random.Random(random_seed)
    weight = {}
    for u in range(n):
        for v in range(u + 1, n):
            if rng.random() < 0.2:
                w = rng.randint(1, 100)
                weight[(u, v)] = w
                weight[(v, u)] = w
    data, indices, indptr = [], [], [0]
    for u in range(n):
        for v in range(n):
            if (u, v) in weight:
                data.append(float(weight[(u, v)]))
                indices.append(v)
        indptr.append(len(data))
    return {"data": data, "indices": indices, "indptr": indptr, "shape": [n, n]}


def _adjacency(problem):
    n = problem["shape"][0]
    data, indices, indptr = problem["data"], problem["indices"], problem["indptr"]
    return [[(indices[k], data[k]) for k in range(indptr[u], indptr[u + 1])] for u in range(n)]


def reference(problem):
    """Dijkstra from every source, picking the next vertex with a linear scan."""
    n = problem["shape"][0]
    adjacency = _adjacency(problem)
    rows = []
    for source in range(n):
        dist = [None] * n
        dist[source] = 0.0
        done = [False] * n
        for _ in range(n):
            best, best_d = -1, None
            for v in range(n):
                if not done[v] and dist[v] is not None and (best_d is None or dist[v] < best_d):
                    best, best_d = v, dist[v]
            if best == -1:
                break
            done[best] = True
            for v, w in adjacency[best]:
                nd = best_d + w
                if dist[v] is None or nd < dist[v]:
                    dist[v] = nd
        rows.append(dist)
    return {"distance_matrix": rows}


def _heap_all_pairs(problem):
    import heapq

    n = problem["shape"][0]
    adjacency = _adjacency(problem)
    rows = []
    for source in range(n):
        dist = [None] * n
        dist[source] = 0.0
        heap = [(0.0, source)]
        while heap:
            d, u = heapq.heappop(heap)
            if d > dist[u]:
                continue
            for v, w in adjacency[u]:
                nd = d + w
                if dist[v] is None or nd < dist[v]:
                    dist[v] = nd
                    heapq.heappush(heap, (nd, v))
        rows.append(dist)
    return rows


def is_solution(problem, solution):
    if not isinstance(solution, dict) or "distance_matrix" not in solution:
        return False
    n = problem["shape"][0]
    rows = solution["distance_matrix"]
    if len(rows) != n or any(len(r) != n for r in rows):
        return False
    expected = _heap_all_pairs(problem)
    for r, e in zip(rows, expected):
        for a, b in zip(r, e):
            if a is None or b is None:
                if not (a is None and b is None):
                    return False
            elif abs(float(a) - b) > 1e-9:
                return False
    return True
