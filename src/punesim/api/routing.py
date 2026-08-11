"""The actual line somebody walked, for drawing.

The engine already routes on real streets — `Block.walk_seconds` asks
`RoadGraph.metres`, which reads a precomputed Dijkstra table per place. But that
table holds distances only (`roads.py:72` returns `dist`), which is all a travel
time needs and not enough to draw with. So the map lerped a straight line
between endpoints and people visibly walked through buildings, in a simulation
whose whole point is that the geography is real.

This adds the one thing missing: a Dijkstra that keeps predecessors, so a path
can be reconstructed. It lives here rather than in `world/roads.py` because the
engine has no use for it and that module's memory budget is deliberate — 438
distance tables at 8k nodes is already 28 MB, and predecessor arrays would
double it for a feature only the viewer wants.
"""

import heapq
import math
from array import array

from ..world.block import Block

CACHE_MAX = 512  # paths are small and repeat constantly as you follow somebody


def _path_dijkstra(graph, source: int, target: int) -> list[int] | None:
    """Node path source→target, or None if they are not connected.

    Stops as soon as the target is settled — a walk across four peths settles a
    fraction of the graph, so this is much cheaper than the full sweep
    `prepare()` does per place.
    """
    n = len(graph.nodes)
    dist = array("d", [math.inf]) * n
    prev = array("i", [-1]) * n
    dist[source] = 0.0
    pq = [(0.0, source)]
    while pq:
        d, u = heapq.heappop(pq)
        if u == target:
            break
        if d > dist[u]:
            continue
        for v, w in graph.adj[u]:
            nd = d + w
            if nd < dist[v]:
                dist[v] = nd
                prev[v] = u
                heapq.heappush(pq, (nd, v))
    if math.isinf(dist[target]):
        return None
    out, cur = [], target
    while cur != -1:
        out.append(cur)
        if cur == source:
            break
        cur = prev[cur]
    out.reverse()
    return out if out and out[0] == source else None


_cache: dict[tuple[str, str, str], list[list[float]] | None] = {}


def walk_path(block: Block, from_id: str, to_id: str) -> list[list[float]] | None:
    """[[lat, lon], ...] along the streets, or None when the graph cannot say.

    None is a real answer, not a failure — a building the extract never
    connected to a lane, or two sides of a boundary with no way between them.
    `RoadGraph.metres` returns None for the same cases and the engine falls back
    to a straight line; the caller here should do the same rather than pretend
    the trip is impossible.
    """
    graph = block.roads
    if graph is None:
        return None
    key = (block.name, from_id, to_id)
    if key in _cache:
        return _cache[key]
    a, b = block.get(from_id), block.get(to_id)
    if a is None or b is None:
        return None
    sa = graph.snap(from_id, a.lat, a.lon)
    sb = graph.snap(to_id, b.lat, b.lon)
    if sa is None or sb is None:
        _cache[key] = None
        return None
    nodes = _path_dijkstra(graph, sa[0], sb[0])
    if nodes is None:
        _cache[key] = None
        return None
    # The door-to-lane gaps at both ends are real walking too, so the drawn line
    # starts at the building and not at the kerb it snapped to.
    line = [[a.lat, a.lon]] + [[graph.nodes[i][0], graph.nodes[i][1]] for i in nodes] \
        + [[b.lat, b.lon]]
    if len(_cache) > CACHE_MAX:
        _cache.clear()
    _cache[key] = line
    return line
