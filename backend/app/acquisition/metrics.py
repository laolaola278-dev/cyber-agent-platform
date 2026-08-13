"""Phase 28.4 -- low-cardinality production metrics for acquisition/worker.

Reuses the platform's in-process Prometheus exposition style (see
``app/observability/metrics.py``). All labels are fixed enumerations -- never
run_id / worker_id / sandbox ids (high cardinality) and never secrets/tokens.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock

_DURATION_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0)


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _labels(values: dict[str, str]) -> str:
    if not values:
        return ""
    body = ",".join(
        f'{name}="{_escape(value)}"' for name, value in sorted(values.items())
    )
    return "{" + body + "}"


@dataclass(slots=True)
class AcquisitionMetrics:
    """Bounded metric set for the acquisition durable-execution pipeline."""

    _counters: dict[tuple[str, frozenset[tuple[str, str]]], int] = field(
        default_factory=dict
    )
    _gauges: dict[str, float] = field(default_factory=dict)
    _duration_count: dict[tuple[str, frozenset[tuple[str, str]]], int] = field(
        default_factory=dict
    )
    _duration_sum: dict[tuple[str, frozenset[tuple[str, str]]], float] = field(
        default_factory=dict
    )
    _duration_buckets: dict[
        tuple[str, frozenset[tuple[str, str]], float], int
    ] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    # -- counters ------------------------------------------------------------
    def inc(
        self, name: str, labels: dict[str, str] | None = None, amount: int = 1
    ) -> None:
        key = (name, frozenset((labels or {}).items()))
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + amount

    # -- gauges --------------------------------------------------------------
    def set_gauge(self, name: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = float(value)

    # -- durations (summary-style count + sum + buckets) ---------------------
    def observe_duration(
        self,
        name: str,
        seconds: float,
        labels: dict[str, str] | None = None,
    ) -> None:
        lkey = frozenset((labels or {}).items())
        with self._lock:
            self._duration_count[(name, lkey)] = self._duration_count.get(
                (name, lkey), 0
            ) + 1
            self._duration_sum[(name, lkey)] = (
                self._duration_sum.get((name, lkey), 0.0) + seconds
            )
            for bucket in _DURATION_BUCKETS:
                if seconds <= bucket:
                    self._duration_buckets[(name, lkey, bucket)] = (
                        self._duration_buckets.get((name, lkey, bucket), 0) + 1
                    )

    # -- render --------------------------------------------------------------
    def render(self) -> str:
        with self._lock:
            counters = dict(self._counters)
            gauges = dict(self._gauges)
            dc = dict(self._duration_count)
            ds = dict(self._duration_sum)
            db = dict(self._duration_buckets)

        lines: list[str] = []
        for (name, labels), value in sorted(counters.items()):
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name}{_labels(dict(labels))} {value}")
        for name, value in sorted(gauges.items()):
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name} {value}")
        for (name, labels), count in sorted(dc.items()):
            label_dict = dict(labels)
            lines.append(f"# TYPE {name} summary")
            lines.append(
                f'{name}_count{_labels({**label_dict, "quantile": ""})}'.replace(
                    'quantile=""', ""
                )
                + f" {count}"
            )
            lines.append(
                f'{name}_sum{_labels(label_dict) if label_dict else ""} {ds[(name, labels)]:.6f}'
            )
        for (name, labels, bucket), count in sorted(db.items()):
            lines.append(
                f'{name}_bucket{_labels({**dict(labels), "le": str(bucket)})} {count}'
            )
            lines.append(
                f'{name}_bucket{_labels({**dict(labels), "le": "+Inf"})}'
                f" {count}"
            )
        return "\n".join(lines) + "\n"
