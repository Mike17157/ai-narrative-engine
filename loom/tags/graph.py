"""Tag similarity graph — a navigable co-occurrence network over real Danbooru tags.

Nodes = high-frequency general tags (the catalog); weighted edges = how strongly two tags co-occur
across real character bundles (`cosine = co / sqrt(df_a * df_b)`). Retrieval is **navigation**:
personalized PageRank seeded at a draft's tags, hub-corrected (divided by the baseline PageRank) so
it surfaces tags SPECIFICALLY compatible with the draft rather than global hubs ('1girl', 'long
hair'). Results are bucketed by facet for the image-prompt composers.

Two **similarity filters** keep the connections meaningful:
  * an UPPER cosine bound drops near-alias edges that always co-occur ('weasel ears' == 'weasel
    tail', 'firefighter' == 'firefighter jacket') — redundant, not informative;
  * within a facet, edges between same-head-noun VARIANTS ('black skirt' <-> 'red skirt') are skipped
    so navigation explores complementary slots instead of colour variants of one garment.

Built once and pickled to loom/data/tag_graph.pickle (rebuilt lazily if missing). networkx-backed.
"""
from __future__ import annotations

import csv
import pickle
import re
from collections import Counter, defaultdict
from math import sqrt
from pathlib import Path
from threading import Lock

import networkx as nx

from . import facets as F
from .booru import _DATA as _TAGS_CSV
from .cooccur import get_cooccur

_CACHE = Path(__file__).resolve().parent.parent / "data" / "tag_graph.pickle"

MIN_POSTS = 200       # node floor: general tags with >= this many posts
MIN_COCOUNT = 2       # edge floor: a pair must co-occur in >= this many bundles
MAX_COSINE = 0.65     # similarity filter: drop edges at/above this (near-aliases)
ALPHA = 0.4           # PageRank damping — low so it stays near the seeds (local, themed)
TOP_NEIGHBORS = 80    # cap neighbours per node (densest hubs pruned to their strongest links)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _load_catalog(min_posts: int = MIN_POSTS) -> dict[str, int]:
    """The node vocabulary: general (category-0) tags with >= min_posts, name -> post_count."""
    out: dict[str, int] = {}
    if not _TAGS_CSV.exists():
        return out
    with _TAGS_CSV.open(encoding="utf-8", newline="") as fh:
        for r in csv.reader(fh):
            if len(r) < 3 or not r[0]:
                continue
            try:
                cat, cnt = int(r[1] or 0), int(r[2] or 0)
            except ValueError:
                continue
            if cat == 0 and cnt >= min_posts:
                out[_norm(r[0].replace("_", " "))] = cnt
    return out


def build_graph(min_posts: int = MIN_POSTS) -> nx.Graph:
    """Build the weighted, similarity-filtered tag graph and compute its baseline PageRank."""
    catalog = _load_catalog(min_posts)
    ix = get_cooccur()
    co: dict[str, Counter] = defaultdict(Counter)
    df: Counter = Counter()
    for tagset, _sc in ix.rows:
        ts = [t for t in tagset if t in catalog]
        for t in ts:
            df[t] += 1
        for i in range(len(ts)):
            for j in range(i + 1, len(ts)):
                a, b = ts[i], ts[j]
                co[a][b] += 1
                co[b][a] += 1

    G = nx.Graph()
    for t, cnt in catalog.items():
        G.add_node(t, post_count=cnt,
                   app_facet=F.facet_of(t, "appearance"),
                   clo_facet=F.facet_of(t, "clothing"))
    for a, nbrs in co.items():
        # keep this node's strongest links only (prune dense hubs)
        ranked = sorted(((b, c) for b, c in nbrs.items() if c >= MIN_COCOUNT),
                        key=lambda bc: -bc[1] / sqrt((df[a] or 1) * (df[bc[0]] or 1)))[:TOP_NEIGHBORS]
        for b, c in ranked:
            if a >= b:
                continue  # undirected — add each pair once
            w = c / sqrt((df[a] or 1) * (df[b] or 1))
            if w >= MAX_COSINE:
                continue  # similarity filter: near-alias / always-together edge
            # variant filter: skip same-head-noun variants within the same facet
            if (F.same_variant(a, b)
                    and (G.nodes[a]["clo_facet"] == G.nodes[b]["clo_facet"]
                         or G.nodes[a]["app_facet"] == G.nodes[b]["app_facet"])):
                continue
            G.add_edge(a, b, weight=w)

    # drop isolated nodes (no surviving edges) — they can't be navigated to
    G.remove_nodes_from([n for n in list(G.nodes) if G.degree(n) == 0])
    base = nx.pagerank(G, weight="weight", alpha=ALPHA)
    nx.set_node_attributes(G, base, "base_pr")
    return G


class TagGraph:
    """Loaded-once navigator over the tag similarity graph."""

    def __init__(self, G: nx.Graph):
        self.G = G

    @property
    def ready(self) -> bool:
        return self.G.number_of_nodes() > 0

    def navigate(self, seeds: list[str]) -> dict[str, float]:
        """Hub-corrected personalized PageRank scores for tags compatible with `seeds`.
        score = personalized_pr / baseline_pr (kept only where it rose above baseline)."""
        present = [_norm(s) for s in seeds if _norm(s) in self.G]
        if not present:
            return {}
        ppr = nx.pagerank(self.G, personalization={s: 1.0 for s in present},
                          weight="weight", alpha=ALPHA)
        seen = set(present)
        out: dict[str, float] = {}
        for n, p in ppr.items():
            if n in seen:
                continue
            base = self.G.nodes[n].get("base_pr") or 1e-12
            if p > base:
                out[n] = p / base
        return out

    def palette(self, draft: list[str], kind: str = "appearance",
                per_facet: int = 24) -> dict[str, list[str]]:
        """Faceted palette of compatible tags for the draft — the composer's pass-2 menu. Same
        shape as CooccurIndex.faceted_palette. Empty if no draft tag is in the graph (caller
        falls back)."""
        scored = self.navigate(draft)
        if not scored:
            return {}
        attr = "app_facet" if kind == "appearance" else "clo_facet"
        order = [f for f, _ in (F.FACETS_APPEARANCE if kind == "appearance" else F.FACETS_CLOTHING)]
        buckets: dict[str, list[str]] = {f: [] for f in order}
        heads: dict[str, set] = {f: set() for f in order}
        for t, _s in sorted(scored.items(), key=lambda kv: -kv[1]):
            f = self.G.nodes[t].get(attr)
            if not f or len(buckets[f]) >= per_facet:
                continue
            h = F.head_noun(t)
            if h in heads[f] and sum(1 for x in buckets[f] if F.head_noun(x) == h) >= 3:
                continue  # at most 3 variants of one item per facet (diversity)
            buckets[f].append(t)
            heads[f].add(h)
        return {f: v for f, v in buckets.items() if v}

    def related(self, seeds: list[str], kind: str = "clothing", per_facet: int = 24) -> dict:
        """Inspection helper for the debug endpoint."""
        return self.palette(seeds, kind, per_facet)


_graph: TagGraph | None = None
_lock = Lock()


def get_graph() -> TagGraph:
    """Process-wide singleton. Unpickles the cached graph if present, else builds + caches it."""
    global _graph
    if _graph is None:
        with _lock:
            if _graph is None:
                G = None
                if _CACHE.exists():
                    try:
                        with _CACHE.open("rb") as fh:
                            G = pickle.load(fh)
                    except Exception:  # noqa: BLE001 — corrupt cache: rebuild
                        G = None
                if G is None:
                    G = build_graph()
                    try:
                        _CACHE.parent.mkdir(parents=True, exist_ok=True)
                        with _CACHE.open("wb") as fh:
                            pickle.dump(G, fh)
                    except Exception:  # noqa: BLE001 — non-fatal if we can't write the cache
                        pass
                _graph = TagGraph(G)
    return _graph
