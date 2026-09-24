"""A2.2 Stage 12: publication needs two authorities, and each one is executed here.

A hypothetical tag at the candidate is the moment two independent questions get asked: was this
commit *certified* (F-33's authority, resolved from the certification workflows' artifacts), and
were these images *produced* by the producer this repository pins (A2.2's authority, read from the
records the build jobs wrote). Each has its own inline gate in `release.yml`, each is executed
below by compiling and running that gate's own code -- the method F-21 established -- and neither
is asked to answer the other's question.

What makes this a dry run of the *composition* rather than two unrelated tests is the graph:
`publish-release` needs both jobs, so a failure in either one skips the publication. Reimplementing
"both must pass" in a test would prove the reimplementation, so these tests assert the three things
that are actually load-bearing:

* the certification gate PASSes while the producer gate refuses (the negative dry-run the approval
  asks for: one `producer_alignment` flipped to MISMATCH, and the publication stops anyway);
* the producer gate PASSes while the certification gate refuses (so the pair cannot be reduced to
  the new authority, which would silently drop F-33's protection);
* `publish-release` -- and `release-chart`, whose asset points at the images -- needs both jobs, so
  the composition is enforced by the graph and not by prose in this file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from tests.test_release_image_completeness import (
    RELEASE_YML,
    _exec_release_gate,
    _record,
)
from tests.test_release_publication_gate import (
    CHAIN_TAG,
    RELEASE_JOBS,
    SHA_TAG,
    FakeActionsApi,
    _exec_gate,
    _green_runs,
    _runs_payload,
)

FIVE = ["cap-backend", "cap-frontend", "cap-sandbox-http", "cap-sandbox-browser",
        "cap-egress-proxy"]
CERT_JOB = "verify-certification"
PRODUCER_JOB = "release-image-completeness"
PUBLISH_JOB = "publish-release"
CHART_JOB = "release-chart"


def _release_doc() -> dict:
    return yaml.safe_load(RELEASE_YML.read_text("utf-8"))


def _five(**mutations) -> list[dict]:
    records = []
    for image in FIVE:
        record = _record(image)
        if image in mutations:
            mutations[image](record)
        records.append(record)
    return records


def _flip_alignment(record: dict) -> dict:
    """One field, one cause: the producer of this image is not the producer we pinned."""
    record["producer"]["producer_alignment"]["verdict"] = "MISMATCH"
    record["producer"]["comparison"]["workflow_vs_observed"]["status"] = "MISMATCH"
    record["producer"]["producer_alignment"]["components"]["workflow_vs_observed"] = "MISMATCH"
    return record


def _workdir(tmp_path: Path, name: str) -> Path:
    """A directory of its own per gate: both helpers `chdir`, and neither should see the other's
    uploaded records."""
    path = tmp_path / name
    path.mkdir(parents=True, exist_ok=True)
    return path


# -- the dry run at a candidate that earned both authorities ------------------------------

def test_a_candidate_with_both_authorities_would_publish(tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """Green certification for the tagged commit, five CONFORMING producer records: both pass."""
    runs, jobs = _green_runs(SHA_TAG)
    api = FakeActionsApi(runs=runs, jobs=jobs, chain=CHAIN_TAG)
    cert_code, cert = _exec_gate(_workdir(tmp_path, "cert"), monkeypatch, api)
    assert cert_code == 0, cert and cert["failures"]
    assert cert["verdict"] == "PASS" and cert["tag_sha"] == SHA_TAG

    prod_code, prod = _exec_release_gate(_workdir(tmp_path, "producer"), monkeypatch, _five(),
                                         sha=SHA_TAG)
    assert prod_code == 0, prod and prod["failures"]
    assert prod["verdict"] == "PASS"
    assert {name: verdict["state"] for name, verdict in prod["producer_verdicts"].items()} \
        == {image: "CONFORMING" for image in FIVE}
    # The two artifacts describe the same hypothetical release, and the producer gate names the
    # commit it scored: the run identity it checked is this run's, at this commit.
    assert prod["commit"] == SHA_TAG


def test_the_producer_gate_refuses_even_when_certification_is_green(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The approval's negative dry-run: one MISMATCH alignment, and publication stops.

    Certification stays PASS through the whole thing, which is the point -- F-33's authority cannot
    see the producer, and before A2.2 there was nothing else to see it with.
    """
    runs, jobs = _green_runs(SHA_TAG)
    api = FakeActionsApi(runs=runs, jobs=jobs, chain=CHAIN_TAG)
    cert_code, cert = _exec_gate(_workdir(tmp_path, "cert"), monkeypatch, api)
    assert cert_code == 0 and cert["verdict"] == "PASS"

    def break_it(record: dict) -> None:
        _flip_alignment(record)

    prod_code, prod = _exec_release_gate(_workdir(tmp_path, "producer"), monkeypatch,
                                         _five(**{"cap-sandbox-browser": break_it}), sha=SHA_TAG)
    assert prod_code == 1, prod and prod["verdict"]
    assert prod["verdict"] == "FAIL"
    assert prod["producer_verdicts"]["cap-sandbox-browser"]["state"] == "MISMATCH"
    assert prod["producer_summary"]["MISMATCH"] == ["cap-sandbox-browser"]
    # The other four stay conforming: one bad producer is a refusal about the set, not a
    # re-scoring of the images that were fine.
    assert all(prod["producer_verdicts"][name]["state"] == "CONFORMING"
               for name in FIVE if name != "cap-sandbox-browser")
    refusal = json.dumps(prod["failures"])
    assert "producer_alignment disagrees" in refusal, refusal


def test_certification_still_refuses_when_the_producer_is_conforming(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The other direction, because a batch that adds an authority must not subtract one.

    Nothing in the release graph may now publish on producer evidence alone: the certification
    gate is asked the same question it was asked before A2.2, and an unreferenced candidate still
    fails there even with five perfect producer records.
    """
    prod_code, prod = _exec_release_gate(_workdir(tmp_path, "producer"), monkeypatch, _five(),
                                         sha=SHA_TAG)
    assert prod_code == 0 and prod["verdict"] == "PASS"

    noise = [(f"{i:040d}", 900 + i, "success") for i in range(100)]
    cert_code, cert = _exec_gate(
        _workdir(tmp_path, "cert"), monkeypatch,
        FakeActionsApi(runs={workflow: _runs_payload(noise) for workflow in RELEASE_JOBS},
                       jobs={}, chain=CHAIN_TAG))
    assert cert_code == 1, cert and cert["verdict"]
    assert cert["verdict"] == "FAIL"
    assert cert["tag_sha"] == SHA_TAG
    assert all("dispatch certification on this commit" in reason
               for reason in cert["failures"]), cert["failures"]


# -- the composition is the graph, not a sentence in this file ---------------------------

def test_publish_and_chart_each_need_both_authorities() -> None:
    """`needs:` is what makes the pair a gate; the tests above only run the two halves."""
    doc = _release_doc()
    publish = doc["jobs"][PUBLISH_JOB]["needs"]
    publish = publish if isinstance(publish, list) else list(publish)
    assert CERT_JOB in publish and PRODUCER_JOB in publish, publish
    chart = doc["jobs"][CHART_JOB]["needs"]
    chart = chart if isinstance(chart, list) else list(chart)
    assert CERT_JOB in chart and PRODUCER_JOB in chart, (
        "the chart asset ships the image digests, so it waits for both authorities too")


def test_the_two_authorities_are_answered_by_different_jobs() -> None:
    """Neither gate was folded into the other, and neither reads the other's artifact.

    If `verify-certification` grew a producer read, or the completeness gate grew a certification
    resolve, one red job would start meaning two different things -- and the diagnosis split that
    A2.2 Stage 6 insists on inside the producer gate would be lost between the jobs.
    """
    doc = _release_doc()
    cert_steps = json.dumps(doc["jobs"][CERT_JOB]["steps"])
    producer_steps = json.dumps(doc["jobs"][PRODUCER_JOB]["steps"])
    assert "producer-evidence" not in cert_steps, (
        "the certification gate now reads producer evidence: two authorities in one verdict")
    assert "release-certification-gate" not in producer_steps, (
        "the completeness gate now reads certification evidence")
    assert "install_controlled_buildx.py" not in cert_steps, (
        "the certification job does not build images and must not install a producer")


def test_nothing_in_the_release_graph_creates_a_tag() -> None:
    """A2.2 changes how an image is built; the tag that starts it stays a human action."""
    text = RELEASE_YML.read_text("utf-8")
    assert "git tag" not in text, "release.yml now creates a tag itself"
    assert "create_auto_tag_release" not in text, "a release-creation API call appeared"
    doc = _release_doc()
    # YAML resolves the bare `on:` key to the boolean True, which is how this file has to be read.
    triggers = doc.get("on") or doc.get(True) or {}
    assert "push" in triggers and triggers["push"].get("tags"), (
        "the release workflow must stay tag-triggered: a push-to-main trigger would publish a "
        "release on every candidate, which is the sealed-release constraint this batch works under"
    )
    assert not triggers["push"].get("branches"), "the release workflow must not fire on branches"
