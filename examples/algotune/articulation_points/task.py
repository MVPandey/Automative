"""Articulation points. Adapted from AlgoTune's articulation_points task (MIT)."""

import random


def generate_problem(n, random_seed=1):
    rng = random.Random(random_seed)
    num_nodes = max(2, n)
    edges = [[u, v] for u in range(num_nodes) for v in range(u + 1, num_nodes) if rng.random() < 0.3]
    if not edges:
        edges.append([0, 1])
    return {"num_nodes": num_nodes, "edges": edges}


def _components_without(n, adjacency, removed):
    seen = [False] * n
    seen[removed] = True
    count = 0
    for start in range(n):
        if seen[start]:
            continue
        count += 1
        seen[start] = True
        stack = [start]
        while stack:
            node = stack.pop()
            for nxt in adjacency[node]:
                if not seen[nxt]:
                    seen[nxt] = True
                    stack.append(nxt)
    return count


def _components(n, adjacency):
    seen = [False] * n
    count = 0
    for start in range(n):
        if seen[start]:
            continue
        count += 1
        seen[start] = True
        stack = [start]
        while stack:
            node = stack.pop()
            for nxt in adjacency[node]:
                if not seen[nxt]:
                    seen[nxt] = True
                    stack.append(nxt)
    return count


def reference(problem):
    """Remove each node in turn and see whether the component count goes up."""
    n = problem["num_nodes"]
    adjacency = [[] for _ in range(n)]
    for u, v in problem["edges"]:
        adjacency[u].append(v)
        adjacency[v].append(u)
    whole = _components(n, adjacency)
    points = []
    for node in range(n):
        if _components_without(n, adjacency, node) > whole:
            points.append(node)
    return {"articulation_points": points}


def _tarjan(n, edges):
    adjacency = [[] for _ in range(n)]
    for u, v in edges:
        adjacency[u].append(v)
        adjacency[v].append(u)
    disc = [-1] * n
    low = [0] * n
    points = set()
    timer = 0
    for root in range(n):
        if disc[root] != -1:
            continue
        disc[root] = low[root] = timer
        timer += 1
        root_children = 0
        stack = [(root, -1, iter(adjacency[root]))]
        while stack:
            node, parent, it = stack[-1]
            advanced = False
            for nxt in it:
                if nxt == parent:
                    continue
                if disc[nxt] == -1:
                    disc[nxt] = low[nxt] = timer
                    timer += 1
                    if node == root:
                        root_children += 1
                    stack.append((nxt, node, iter(adjacency[nxt])))
                    advanced = True
                    break
                low[node] = min(low[node], disc[nxt])
            if advanced:
                continue
            stack.pop()
            if stack:
                pnode = stack[-1][0]
                low[pnode] = min(low[pnode], low[node])
                if pnode != root and low[node] >= disc[pnode]:
                    points.add(pnode)
        if root_children > 1:
            points.add(root)
    return sorted(points)


def is_solution(problem, solution):
    if not isinstance(solution, dict) or "articulation_points" not in solution:
        return False
    return list(solution["articulation_points"]) == _tarjan(problem["num_nodes"], problem["edges"])
