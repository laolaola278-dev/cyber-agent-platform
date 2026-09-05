#!/usr/bin/env python
"""Assert the coverage-matrix discipline: no unaccounted verification gap.

CI gate for the blind-spot governance rollout
(`docs/quality/ci-blindspot-governance.md`, P0 "gating" item). The matrix
`docs/quality/coverage-matrix.md` makes "what is NOT verified" auditable:
every promised capability row must account for each verification layer.
The gate fails when a row registers a verification gap that is neither:

  * excused  — the cell is an unadorned ``❌`` with no inline reason, or
  * anchored — the cell carries a 🚫 / 盲区 / 缓解 / known limitation /
    设计 marker so the gap is an *intentionally registered* limitation.

Exit codes:
    0  matrix is compliant (every gap is accounted for)
    1  at least one unaccounted gap — CI must fail

Run (from repo root, no third-party deps — stdlib only, mirrors the
inline chart-defaults assertion precedent in ci.yml `packaging` job):

    python scripts/quality/assert_coverage_matrix.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MATRIX_PATH = REPO_ROOT / "docs" / "quality" / "coverage-matrix.md"

# Verification-layer columns (index 1..5 in the table, before 状态).
VERIFICATION_LAYERS = ["单测", "集成", "真实网络", "soak", "认证"]

# A cell that registers a gap must carry one of these anchors to be
# considered an *accounted-for* (registered) limitation. Anything else
# that starts with ❌ — a bare "❌" or "❌ 需要真实传感器" style text with
# no 盲区/缓解/known-limitation framing — is an unaccounted blind spot.
ACCOUNTABILITY_ANCHORS = (
    "🚫",
    "盲区",
    "缓解",
    "known limitation",
    "known-limitation",
    "设计性拒绝",
    "设计如此",
    "锚定拒绝",
)

TABLE_ROW_RE = re.compile(r"^\|\s*(?P<capability>.+?)\s*\|")
CELL_RE = re.compile(r"❌")


def iter_matrix_rows(text: str):
    """Yield (capability, cells) for every capability row of the matrix.

    Capability rows are the non-separator lines of the markdown table.
    ``cells`` excludes the leading capability cell; the trailing 状态
    cell is kept as ``cells[-1]`` (layer cells are ``cells[:-1]``).
    """
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        if re.match(r"^\|[\s:\-|]+\|$", stripped):
            continue  # separator row like |---|---|
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 2:
            continue
        capability = cells[0]
        if capability in {"承诺能力", "能力"}:
            continue  # header row
        yield capability, cells[1:]


def find_unaccounted_gaps(text: str) -> list[tuple[str, str, str]]:
    """Return (capability, layer, cell) triples for unaccounted ❌ cells.

    A ❌ cell is *unaccounted* when none of the accountability anchors
    appear anywhere in the cell text. Layer cells are cells[:-1]; the
    trailing 状态 cell is authoritative and never gated here (its role
    is summary, not per-layer evidence).
    """
    gaps: list[tuple[str, str, str]] = []
    for capability, cells in iter_matrix_rows(text):
        layer_cells = cells[:-1] if len(cells) > 1 else cells
        for layer, cell in zip(VERIFICATION_LAYERS, layer_cells):
            if CELL_RE.search(cell) and not any(
                anchor in cell for anchor in ACCOUNTABILITY_ANCHORS
            ):
                gaps.append((capability, layer, cell))
    return gaps


def main() -> int:
    if not MATRIX_PATH.is_file():
        print(f"FAIL: coverage matrix not found at {MATRIX_PATH}")
        print("      docs/quality/coverage-matrix.md is a required release artifact")
        return 1

    text = MATRIX_PATH.read_text(encoding="utf-8")
    gaps = find_unaccounted_gaps(text)

    if gaps:
        print(
            "FAIL: coverage matrix has unaccounted verification gaps "
            f"({len(gaps)} cell{'s' if len(gaps) != 1 else ''}):"
        )
        for capability, layer, cell in gaps:
            print(f"  - {capability} :: {layer} :: {cell!r}")
        print()
        print("Every ❌ cell must carry an accountability anchor")
        print("(盲区 / 缓解 / 🚫 / known limitation / 设计性拒绝 / 锚定拒绝)")
        print("so the gap is a registered, reviewable limitation — otherwise it")
        print("is an open blind spot and the release cannot claim it as covered.")
        return 1

    rows = sum(1 for _ in iter_matrix_rows(text))
    print(
        f"coverage matrix OK: {rows} capability rows, every verification gap "
        "is accounted for (registered limitation or anchored rejection)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
