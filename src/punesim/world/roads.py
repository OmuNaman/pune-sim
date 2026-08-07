"""Walking on the actual streets, instead of through walls.

V0 measured every trip as haversine distance x 1.4, with a note that V3 would
replace it and that nothing upstream may depend on how travel time is computed.
This is that replacement: the pinned OSM ways become a graph, homes and places
snap to it, and a walk is the shortest path along real lanes.

The detour factor was not a bad guess — in a peth's grid it is close on average
— but it is wrong in exactly the places that matter, where the river, the
railway or a wada block force a long way round. Two homes 200m apart across
Mutha are a twenty-minute walk, and the sim used to send people straight over
the water in four.

Cost: the shortest-path work is done from the *places*, not the homes. Walking
is symmetric, so one sweep from each of 438 places yields every home-to-place
distance — 438 searches instead of 7,008.
"""

import heapq
import math
from array import array
from pathlib import Path

import orjson

from .block import haversine_m

# Ways people can walk along. Motorways are excluded, and so is everything with
# no `highway` tag at all.
_WALKABLE = {
    "residential", "service", "footway", "tertiary", "trunk", "living_street",
    "primary", "secondary", "path", "pedestrian", "steps", "unclassified",
    "track", "primary_link", "secondary_link", "tertiary_link", "road",
}
_COORD_DP = 7  # OSM exports repeat shared vertices exactly; round only for float noise
SNAP_CELL_DEG = 0.002  # ~200 m; the grid bucket used to find a nearby node fast
MAX_SNAP_M = 400.0  # further than this from any way and a building is unreachable


class RoadGraph:
    """An undirected walking graph, plus distances from every place to every node."""

    def __init__(self, nodes: list[tuple[float, float]], adj: list[list[tuple[int, float]]]):
        self.nodes = nodes
        self.adj = adj
        self._grid: dict[tuple[int, int], list[int]] = {}
        for i, (lat, lon) in enumerate(nodes):
            self._grid.setdefault(self._cell(lat, lon), []).append(i)
        self._from_place: dict[str, array] = {}
        self._snap: dict[str, tuple[int, float] | None] = {}

    @staticmethod
    def _cell(lat: float, lon: float) -> tuple[int, int]:
        return (int(lat / SNAP_CELL_DEG), int(lon / SNAP_CELL_DEG))

    def nearest_node(self, lat: float, lon: float) -> tuple[int, float] | None:
        """(node, metres to it), searching outward by grid ring so a lone
        building on the edge of the extract still finds a lane."""
        ci, cj = self._cell(lat, lon)
        for ring in range(4):
            best, best_d = None, MAX_SNAP_M
            for i in range(ci - ring, ci + ring + 1):
                for j in range(cj - ring, cj + ring + 1):
                    for n in self._grid.get((i, j), ()):
                        d = haversine_m(lat, lon, *self.nodes[n])
                        if d < best_d:
                            best, best_d = n, d
            if best is not None:
                return best, best_d
        return None

    def _dijkstra(self, source: int) -> array:
        # array('d') rather than a list: one table per place x 8k nodes, and a
        # Python float object costs ~32 bytes against 8. At 438 places that is
        # 112 MB of boxed floats versus 28 MB of doubles.
        dist = array("d", [math.inf]) * len(self.nodes)
        dist[source] = 0.0
        pq = [(0.0, source)]
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist[u]:
                continue
            for v, w in self.adj[u]:
                nd = d + w
                if nd < dist[v]:
                    dist[v] = nd
                    heapq.heappush(pq, (nd, v))
        return dist

    def prepare(self, places: dict[str, tuple[float, float]]) -> None:
        """One sweep per place. Everything else reads off these."""
        for pid, (lat, lon) in places.items():
            hit = self.nearest_node(lat, lon)
            self._snap[pid] = hit
            if hit is not None:
                self._from_place[pid] = self._dijkstra(hit[0])

    def snap(self, key: str, lat: float, lon: float) -> tuple[int, float] | None:
        hit = self._snap.get(key, ...)
        if hit is ...:
            hit = self._snap[key] = self.nearest_node(lat, lon)
        return hit

    def metres(self, a_key: str, a: tuple[float, float],
               b_key: str, b: tuple[float, float]) -> float | None:
        """Walking distance along streets, or None when the graph cannot say.

        None is a real answer — a building the extract never connected, or two
        sides of a boundary with no way between them — and the caller falls
        back to the straight line rather than pretending the trip is impossible.
        """
        for key, (lat, lon), other_key, other in ((a_key, a, b_key, b), (b_key, b, a_key, a)):
            table = self._from_place.get(key)
            if table is None:
                continue
            hit = self.snap(other_key, *other)
            if hit is None:
                return None
            node, walk_to_node = hit
            d = table[node]
            if math.isinf(d):
                return None
            # both ends still have to cover the gap from door to lane
            own = self._snap.get(key)
            return d + walk_to_node + (own[1] if own else 0.0)
        return None

    @classmethod
    def load(cls, roads_path: str | Path) -> "RoadGraph":
        data = orjson.loads(Path(roads_path).read_bytes())
        index: dict[tuple[float, float], int] = {}
        nodes: list[tuple[float, float]] = []
        adj: list[list[tuple[int, float]]] = []

        def node_of(lon: float, lat: float) -> int:
            key = (round(lat, _COORD_DP), round(lon, _COORD_DP))
            got = index.get(key)
            if got is None:
                got = index[key] = len(nodes)
                nodes.append(key)
                adj.append([])
            return got

        for feat in data["features"]:
            if feat.get("properties", {}).get("highway") not in _WALKABLE:
                continue
            coords = feat.get("geometry", {}).get("coordinates") or []
            prev = None
            for lon, lat in coords:
                cur = node_of(lon, lat)
                if prev is not None and prev != cur:
                    w = haversine_m(*nodes[prev], *nodes[cur])
                    adj[prev].append((cur, w))
                    adj[cur].append((prev, w))
                prev = cur
        return cls(nodes, adj)
