from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping


@dataclass(frozen=True)
class WalletNode:
    address: str
    label: str = ""
    entity_type: str = "unknown"


@dataclass(frozen=True)
class WalletEdge:
    source: str
    destination: str
    amount_usd: float
    tx_hash: str = ""


@dataclass(frozen=True)
class WalletGraphReport:
    wallet_count: int
    edge_count: int
    clusters: tuple[tuple[str, ...], ...]
    exchange_flow_usd: float
    coordinated_wallets: tuple[str, ...]
    reasons: tuple[str, ...] = ()


class WalletGraphIntelligence:
    def analyze(self, nodes: Iterable[WalletNode], edges: Iterable[WalletEdge]) -> WalletGraphReport:
        node_rows = tuple(nodes)
        edge_rows = tuple(edges)
        neighbors: dict[str, set[str]] = {}
        labels = {node.address: node.entity_type for node in node_rows}
        for edge in edge_rows:
            neighbors.setdefault(edge.source, set()).add(edge.destination)
            neighbors.setdefault(edge.destination, set()).add(edge.source)
        clusters = _clusters(neighbors)
        exchange_flow = sum(
            float(edge.amount_usd)
            for edge in edge_rows
            if labels.get(edge.destination) == "exchange" or labels.get(edge.source) == "exchange"
        )
        coordinated = tuple(sorted(address for address, linked in neighbors.items() if len(linked) >= 3))
        reasons = (f"cluster_count={len(clusters)}", f"exchange_flow_usd={exchange_flow:.2f}", f"coordinated_wallets={len(coordinated)}")
        return WalletGraphReport(len(node_rows), len(edge_rows), clusters, exchange_flow, coordinated, reasons)


def _clusters(neighbors: Mapping[str, set[str]]) -> tuple[tuple[str, ...], ...]:
    visited: set[str] = set()
    out: list[tuple[str, ...]] = []
    for address in sorted(neighbors):
        if address in visited:
            continue
        stack = [address]
        cluster: set[str] = set()
        while stack:
            current = stack.pop()
            if current in cluster:
                continue
            cluster.add(current)
            stack.extend(neighbors.get(current, ()))
        visited.update(cluster)
        if len(cluster) > 1:
            out.append(tuple(sorted(cluster)))
    return tuple(out)
