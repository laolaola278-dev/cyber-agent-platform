"""Low-cardinality in-process Prometheus exposition for CAP control-plane requests."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from threading import Lock

_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _labels(values: dict[str, str]) -> str:
    if not values:
        return ""
    body = ",".join(f'{name}="{_escape(value)}"' for name, value in sorted(values.items()))
    return "{" + body + "}"


@dataclass(slots=True)
class MetricsRegistry:
    """Bounded HTTP metric registry; route templates prevent identifier cardinality."""

    _requests: dict[tuple[str, str, str], int] = field(default_factory=lambda: defaultdict(int))
    _duration_count: dict[tuple[str, str], int] = field(default_factory=lambda: defaultdict(int))
    _duration_sum: dict[tuple[str, str], float] = field(default_factory=lambda: defaultdict(float))
    _duration_buckets: dict[tuple[str, str, float], int] = field(
        default_factory=lambda: defaultdict(int)
    )
    _business_gauges: dict[str, float] = field(default_factory=dict)
    _in_progress: int = 0
    _lock: Lock = field(default_factory=Lock)

    def begin(self) -> None:
        with self._lock:
            self._in_progress += 1

    def observe(self, method: str, route: str, status_code: int, duration: float) -> None:
        method = method.upper()
        status_class = f"{status_code // 100}xx"
        with self._lock:
            self._in_progress = max(0, self._in_progress - 1)
            self._requests[(method, route, status_class)] += 1
            self._duration_count[(method, route)] += 1
            self._duration_sum[(method, route)] += duration
            for bucket in _BUCKETS:
                if duration <= bucket:
                    self._duration_buckets[(method, route, bucket)] += 1

    def set_business_gauges(self, values: dict[str, float]) -> None:
        allowed = {
            "cap_execution_count",
            "cap_execution_duration_seconds",
            "cap_worker_utilization_ratio",
            "cap_queue_depth",
            "cap_plugin_success_ratio",
            "cap_approval_latency_seconds",
            "cap_playbook_success_ratio",
        }
        if set(values) - allowed:
            raise ValueError("Unsupported business metric")
        with self._lock:
            self._business_gauges = dict(values)

    def render(self) -> str:
        with self._lock:
            requests = dict(self._requests)
            counts = dict(self._duration_count)
            sums = dict(self._duration_sum)
            buckets = dict(self._duration_buckets)
            business_gauges = dict(self._business_gauges)
            in_progress = self._in_progress

        lines = [
            "# HELP cap_http_requests_total Total CAP HTTP requests.",
            "# TYPE cap_http_requests_total counter",
        ]
        for (method, route, status_class), value in sorted(requests.items()):
            lines.append(
                "cap_http_requests_total"
                + _labels({"method": method, "route": route, "status_class": status_class})
                + f" {value}"
            )
        lines.extend(
            [
                "# HELP cap_http_request_duration_seconds CAP HTTP request duration.",
                "# TYPE cap_http_request_duration_seconds histogram",
            ]
        )
        for method, route in sorted(counts):
            base_labels = {"method": method, "route": route}
            for bucket in _BUCKETS:
                lines.append(
                    "cap_http_request_duration_seconds_bucket"
                    + _labels({**base_labels, "le": str(bucket)})
                    + f" {buckets.get((method, route, bucket), 0)}"
                )
            lines.append(
                "cap_http_request_duration_seconds_bucket"
                + _labels({**base_labels, "le": "+Inf"})
                + f" {counts[(method, route)]}"
            )
            lines.append(
                "cap_http_request_duration_seconds_count"
                + _labels(base_labels)
                + f" {counts[(method, route)]}"
            )
            lines.append(
                "cap_http_request_duration_seconds_sum"
                + _labels(base_labels)
                + f" {sums[(method, route)]:.9f}"
            )
        for name, value in sorted(business_gauges.items()):
            lines.extend(
                [
                    f"# HELP {name} CAP platform aggregate metric.",
                    f"# TYPE {name} gauge",
                    f"{name} {value}",
                ]
            )
        lines.extend(
            [
                "# HELP cap_http_requests_in_progress Current CAP HTTP requests.",
                "# TYPE cap_http_requests_in_progress gauge",
                f"cap_http_requests_in_progress {in_progress}",
                "# HELP cap_info CAP build information.",
                "# TYPE cap_info gauge",
                'cap_info{service="cyber-agent-platform"} 1',
            ]
        )
        return "\n".join(lines) + "\n"
