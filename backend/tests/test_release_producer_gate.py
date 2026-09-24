"""A2.2 Stage 5/6/7: the publication gate reads the producer, and refuses in six words.

F-47 was filed over one sentence: the producer block in the release evidence was written by the
build job and read by nobody. This file is the reader. Every control here runs the *actual*
completeness gate -- the inline Python of `release.yml`, compiled and executed, the same method
F-21 established for the certification gate -- against records the recorder itself produced, and
asks two questions of each refusal: does publication stop, and does the word say *which* failure
it is.

The six words matter. A release engineer told "the gate returned false" cannot act; one told
`MISSING: cap-egress-proxy: the build job recorded no producer evidence at all` can. So the
controls below are written as pairs -- one for the block, one for the diagnosis -- and the
positive control at the top is what keeps them honest: a set of five conforming records passes,
which is what makes each refusal below a finding about the record rather than about the fixture.

The records are not sketches. `conforming_producer()` runs `record_build_producer.py` against the
stub machine from the A2.1 fixture, reading `release.yml`'s own `release-images` job, so a literal
that moves in that file moves the fixtures with it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_release_image_completeness import (
    GATE_RUN_ID,
    GATE_SHA,
    PROJECT_ROOT,
    RELEASE_BUILDS,
    _exec_release_gate,
    _record,
    conforming_producer,
)

FIVE = ["cap-backend", "cap-frontend", "cap-sandbox-http", "cap-sandbox-browser",
        "cap-egress-proxy"]
CONTRACT_PATH = PROJECT_ROOT / "scripts/release/producer_contract.json"


def _gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, records: list[dict],
          **kwargs) -> tuple[int, dict | None]:
    """Run the gate. The records are written as the release jobs would have uploaded them."""
    return _exec_release_gate(tmp_path, monkeypatch, records, **kwargs)


def _five(**mutations) -> list[dict]:
    records = []
    for image in FIVE:
        record = _record(image)
        if image in mutations:
            mutations[image](record)
        records.append(record)
    return records


def _mutate(record: dict, dotted: str, value) -> dict:
    """Change exactly one field of one record, so a verdict has one cause."""
    node = record["producer"]
    parts = dotted.split(".")
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value
    return record


def _statuses(record: dict, comparison: str, value: str) -> dict:
    block = record["producer"]["comparison"][comparison]
    block["status"] = value
    if value != "CONFORMING":
        # The alignment is built from the components; keep the record internally consistent so a
        # test of one word cannot be explained away by a record that contradicts itself.
        record["producer"]["producer_alignment"]["verdict"] = value
        record["producer"]["producer_alignment"]["components"][comparison] = value
    return record


def _failure_text(data: dict | None, code: int) -> str:
    assert code != 0 and data is not None, "the gate accepted what it must refuse"
    return json.dumps(data["failures"])


# -- the positive control: all five conforming, and it passes ------------------------------

def test_five_conforming_producer_records_pass_the_gate(tmp_path: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """Without this, every refusal below is only proof that the fixture is broken."""
    code, data = _gate(tmp_path, monkeypatch, _five())
    assert code == 0, data and data["failures"]
    assert data["verdict"] == "PASS"
    assert {name: verdict["state"] for name, verdict in data["producer_verdicts"].items()} \
        == {image: "CONFORMING" for image in FIVE}
    assert all(names == [] for names in data["producer_summary"].values()), data["producer_summary"]
    assert set(data["producer_summary"]) == {"UNKNOWN", "MISMATCH", "ERROR", "AMBIGUOUS",
                                             "MISSING"}
    assert data["producer_contract"] == json.loads(
        CONTRACT_PATH.read_text("utf-8"))["contract"]
    assert data["run_id"] == GATE_RUN_ID


def test_the_gate_reads_its_requirements_from_the_contract(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The contract is not documentation to the gate; it is the source of what it enforces.

    Remove the file and the gate says it does not know what it requires, and writes no verdict at
    all -- it does not fall back to "no requirements", which would make the gate a formality.
    """
    for broken in ("", "{ not json"):
        code, data = _gate(tmp_path, monkeypatch, _five(), contract_text=broken)
        assert code == 1 and data is None, (broken, code, data and data["failures"])
        assert not list((tmp_path / "outputs").glob("**/release-images-*.json"))


def test_a_contract_that_disagrees_with_the_gate_about_the_image_set_refuses(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two lists of the five is one too many, and neither wins quietly."""
    contract = json.loads(CONTRACT_PATH.read_text("utf-8"))
    contract["inherited_from_a2_1"]["producer"]["set_acceptance"]["expected_images"] = \
        ["cap-backend", "cap-frontend", "cap-sandbox-http", "cap-egress-proxy"]
    code, data = _gate(tmp_path, monkeypatch, _five(),
                       contract_text=json.dumps(contract))
    assert code == 1 and data is None


# -- MISSING: no producer evidence --------------------------------------------------------

def test_a_producer_that_was_never_recorded_blocks_as_missing(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The recorder's own failure path: `{"recorded": false}` is not an empty producer."""
    def break_it(record: dict) -> None:
        record["producer"] = {"recorded": False}
        record["producer_record_exit"] = 1
    code, data = _gate(tmp_path, monkeypatch, _five(**{"cap-egress-proxy": break_it}))
    text = _failure_text(data, code)
    assert "MISSING: cap-egress-proxy" in text, text
    assert data["producer_verdicts"]["cap-egress-proxy"]["state"] == "MISSING"
    assert data["producer_summary"]["MISSING"] == ["cap-egress-proxy"]


def test_a_record_with_no_producer_field_at_all_blocks_as_missing(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A record shaped like the pre-A2.2 evidence is refused, not accepted as "no objection"."""
    def break_it(record: dict) -> None:
        del record["producer"]
    code, data = _gate(tmp_path, monkeypatch, _five(**{"cap-backend": break_it}))
    assert data["producer_verdicts"]["cap-backend"]["state"] == "MISSING"
    assert "no producer evidence" in _failure_text(data, code)


# -- ERROR: the instrument, not the measurement -------------------------------------------

def test_an_unreadable_producer_blocks_as_error_not_as_mismatch(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A lost read is a broken instrument. Calling it a mismatch would blame the machine."""
    code, data = _gate(tmp_path, monkeypatch, _five(**{
        "cap-frontend": lambda record: _mutate(record, "contract_gaps",
                                               ["observed.controlled_buildx.version"])}))
    text = _failure_text(data, code)
    assert data["producer_verdicts"]["cap-frontend"]["state"] == "ERROR"
    assert "ERROR: cap-frontend: the producer instrument did not complete" in text, text
    assert "MISMATCH" not in text


def test_a_record_missing_a_field_the_contract_names_blocks_as_error(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`evidence_fields` is enforced field by field, and the refusal names the field."""
    code, data = _gate(tmp_path, monkeypatch, _five(**{
        "cap-backend": lambda record: _mutate(
            record, "observed.controlled_buildx",
            {"declared_path": "/tmp/cap-controlled-buildx/buildx", "version": "v0.37.1"})}))
    text = _failure_text(data, code)
    assert data["producer_verdicts"]["cap-backend"]["state"] == "ERROR"
    assert "observed.controlled_buildx.commit" in text, text


def test_a_build_that_did_not_succeed_blocks_as_error(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A perfect producer record of a failed build is still not a publishable image."""
    code, data = _gate(tmp_path, monkeypatch, _five(**{
        "cap-sandbox-http": lambda record: _mutate(
            record, "observed.build_invocation.build_exit", 1)}))
    text = _failure_text(data, code)
    assert "ERROR: cap-sandbox-http: the recorded build exited 1" in text, text


def test_a_record_that_cannot_place_itself_in_a_run_blocks_as_error(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No run id is not "unknown run, presumably fine"."""
    def break_it(record: dict) -> None:
        identity = record["producer"]["identity"]
        identity["run_id"] = None
        identity["verdict"] = "UNKNOWN"
        identity["checks"]["run_id_recorded"] = {"declared": "the record names the run",
                                                 "read_back": None, "relation": None,
                                                 "status": "ERROR"}
    code, data = _gate(tmp_path, monkeypatch, _five(**{"cap-egress-proxy": break_it}))
    text = _failure_text(data, code)
    assert data["producer_verdicts"]["cap-egress-proxy"]["state"] == "ERROR"
    assert "nothing ties this record to a run" in text, text


# -- MISMATCH: the machine disagreed with the repository -----------------------------------

def test_a_disagreeing_comparison_blocks_as_mismatch(tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """The F-44 case, now with teeth: the pinned producer was not the one that built it."""
    code, data = _gate(tmp_path, monkeypatch, _five(**{
        "cap-backend": lambda record: _statuses(record, "workflow_vs_observed", "MISMATCH")}))
    text = _failure_text(data, code)
    assert data["producer_verdicts"]["cap-backend"]["state"] == "MISMATCH"
    assert "workflow_vs_observed disagrees" in text, text
    assert data["verdict"] == "FAIL"


def test_a_disagreeing_digest_layer_blocks_as_mismatch(tmp_path: Path,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """One layer out of five is enough: the builder is not running the pinned BuildKit."""
    code, data = _gate(tmp_path, monkeypatch, _five(**{
        "cap-sandbox-browser": lambda record: _statuses(record, "lock_vs_observed",
                                                        "MISMATCH")}))
    text = _failure_text(data, code)
    assert "lock_vs_observed disagrees" in text, text
    assert data["producer_verdicts"]["cap-sandbox-browser"]["state"] == "MISMATCH"


def test_an_unanswered_comparison_blocks_as_unknown(tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """UNKNOWN is its own word, and it blocks: "could not read" is not "agrees"."""
    code, data = _gate(tmp_path, monkeypatch, _five(**{
        "cap-frontend": lambda record: _statuses(record, "lock_vs_workflow", "UNKNOWN")}))
    text = _failure_text(data, code)
    assert data["producer_verdicts"]["cap-frontend"]["state"] == "UNKNOWN"
    assert "UNKNOWN: cap-frontend: lock_vs_workflow was not answered" in text, text
    assert "MISMATCH" not in text


def test_a_record_of_another_commit_blocks_as_mismatch(tmp_path: Path,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """The record has to belong to the tag being cut, not merely exist."""
    def break_it(record: dict) -> None:
        identity = record["producer"]["identity"]
        identity["source_revision"] = "b" * 40
        identity["verdict"] = "MISMATCH"
        identity["checks"]["source_revision"]["relation"] = "different"
        record["source_revision"] = "b" * 40
    code, data = _gate(tmp_path, monkeypatch, _five(**{"cap-backend": break_it}))
    text = _failure_text(data, code)
    assert "which is not the tagged commit" in text, text
    assert data["producer_verdicts"]["cap-backend"]["state"] == "MISMATCH"


def test_a_record_from_another_run_blocks_as_mismatch(tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    """The cherry-pick refusal: a conforming record from a different run is not this release's.

    Without it, "all five conforming" could be assembled from whichever past runs happened to
    agree -- the same habit A2.1's one-round rule refuses for the observation set, applied here
    where it would decide what gets published.
    """
    def break_it(record: dict) -> None:
        record["producer"]["identity"]["run_id"] = "99999999999"
    code, data = _gate(tmp_path, monkeypatch, _five(**{"cap-egress-proxy": break_it}))
    text = _failure_text(data, code)
    assert "is not this run" in text, text
    assert data["producer_verdicts"]["cap-egress-proxy"]["state"] == "MISMATCH"


def test_a_rehearsal_record_cannot_stand_in_for_a_release_build(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the two release image jobs may produce release evidence.

    The CI rehearsal and the observation job build with the same producer and can both read
    CONFORMING -- but neither pushes, and one of them layers the browser image on a local
    archive. Accepting their records would publish a claim about bytes no one shipped.
    """
    def break_it(record: dict) -> None:
        record["producer"]["identity"]["job"] = "release-image-builds"
    code, data = _gate(tmp_path, monkeypatch, _five(**{"cap-sandbox-http": break_it}))
    text = _failure_text(data, code)
    assert "a rehearsal record is not release evidence" in text, text
    assert data["producer_verdicts"]["cap-sandbox-http"]["state"] == "MISMATCH"


def test_the_oci_layout_hand_off_is_refused_for_a_published_image(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stage 3, with a reader: the release binds its base to a digest it pushed.

    A2.1's `oci-layout://` named context is how a non-publishing build reaches a same-round base;
    if a published image were built on one, the base would be bytes no registry serves. The gate
    reads the binding out of the build's own record and refuses.
    """
    def break_it(record: dict) -> None:
        record["producer"]["build_path"]["base_handoff"] = "same_round_oci_layout"
    code, data = _gate(tmp_path, monkeypatch, _five(**{"cap-sandbox-browser": break_it}))
    text = _failure_text(data, code)
    assert "same-round local layout" in text, text


def test_a_build_that_pushed_nothing_cannot_be_published(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`pushed: true` in the evidence against a recorded argv that never said --push."""
    def break_it(record: dict) -> None:
        record["producer"]["build_path"]["publishes"] = False
    code, data = _gate(tmp_path, monkeypatch, _five(**{"cap-frontend": break_it}))
    text = _failure_text(data, code)
    assert "pushed nothing" in text, text


def test_an_unbound_base_image_blocks_as_mismatch(tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """A pinned producer on a base nobody can name has produced something unreproducible."""
    def break_it(record: dict) -> None:
        bases = record["producer"]["base_images"]["bases"]
        bases[0]["status"] = "MISMATCH"
        bases[0]["binding"] = "registry_tag"
    code, data = _gate(tmp_path, monkeypatch, _five(**{"cap-backend": break_it}))
    text = _failure_text(data, code)
    assert "not bound by digest" in text, text


# -- AMBIGUOUS: two records, or one in the wrong place -------------------------------------

def test_two_disagreeing_records_for_one_image_block_as_ambiguous(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One image cannot be two builds, and the gate does not pick a winner by filename order."""
    records = _five()
    other = json.loads(json.dumps(records[0]))
    other["index_digest"] = "sha256:" + "9" * 64
    _write_extra(tmp_path, "cap-backend-second.json", other)
    code, data = _gate(tmp_path, monkeypatch, records)
    text = _failure_text(data, code)
    assert "AMBIGUOUS: cap-backend has 2 records that do not agree" in text, text


def test_a_producer_sidecar_in_the_image_directory_is_refused(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """F-48's regression: the sidecar that reads as an image of its own.

    Batch 2 wrote `${OUT}.producer.json` beside the evidence and the gate globs that directory, so
    a release cut from that commit would have failed on "evidence for images the release does not
    declare" for a reason that had nothing to do with the images. The arrangement is refused by
    name now, so it cannot come back as a silence.
    """
    records = _five()
    sidecar = json.loads(json.dumps(records[0]["producer"]))
    _write_extra(tmp_path, "cap-backend.json.producer.json", sidecar)
    code, data = _gate(tmp_path, monkeypatch, records)
    text = _failure_text(data, code)
    assert "cap-backend.json.producer.json is a producer record sitting in the image-record " \
        "directory" in text, text


def _write_extra(tmp_path: Path, name: str, payload: dict) -> None:
    (tmp_path / "release-image-evidence").mkdir(exist_ok=True)
    (tmp_path / "release-image-evidence" / name).write_text(
        json.dumps(payload), encoding="utf-8")


# -- the set: four of five ---------------------------------------------------------------

def test_four_conforming_records_are_not_a_release(tmp_path: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """4/5 is the failure this gate exists to make impossible, producer half included."""
    records = [record for record in _five() if record["image"] != "cap-sandbox-browser"]
    for path in (tmp_path / "release-image-evidence").glob("cap-sandbox-browser.json"):
        path.unlink()
    code, data = _gate(tmp_path, monkeypatch, records)
    text = _failure_text(data, code)
    assert "MISSING: cap-sandbox-browser: no build evidence uploaded at all" in text, text
    assert data["verdict"] == "FAIL"


def test_a_producer_mismatch_in_one_image_blocks_the_whole_set(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Four conforming images do not average out a fifth that disagrees."""
    code, data = _gate(tmp_path, monkeypatch, _five(**{
        "cap-sandbox-http": lambda record: _statuses(record, "controlled_pin_vs_lock",
                                                     "MISMATCH")}))
    text = _failure_text(data, code)
    assert "controlled_pin_vs_lock disagrees" in text, text
    assert data["producer_verdicts"]["cap-sandbox-http"]["state"] == "MISMATCH"
    assert data["producer_summary"]["MISMATCH"] == ["cap-sandbox-http"]


# -- the words stay separate -------------------------------------------------------------

def test_the_four_blocking_words_are_reported_separately(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One release, four different failures, four different lines.

    Stage 6's claim in one test: the gate blocks all of them, and none of them is reported as
    another. An operator has to be able to tell "go look at that runner's builder" from "that
    job never uploaded its record".
    """
    def mismatch(record: dict) -> None:
        _statuses(record, "workflow_vs_observed", "MISMATCH")

    def error(record: dict) -> None:
        _mutate(record, "contract_gaps", ["observed.builder.nodes"])

    def missing(record: dict) -> None:
        record["producer"] = {"recorded": False}

    def unknown(record: dict) -> None:
        _statuses(record, "lock_vs_observed", "UNKNOWN")

    records = _five(**{"cap-backend": mismatch, "cap-frontend": error,
                       "cap-sandbox-http": missing, "cap-sandbox-browser": unknown})
    code, data = _gate(tmp_path, monkeypatch, records)
    assert code == 1
    assert data["producer_summary"] == {
        "MISMATCH": ["cap-backend"], "ERROR": ["cap-frontend"], "AMBIGUOUS": [],
        "MISSING": ["cap-sandbox-http"], "UNKNOWN": ["cap-sandbox-browser"]}
    states = {name: verdict["state"] for name, verdict in data["producer_verdicts"].items()}
    assert states["cap-backend"] == "MISMATCH" and states["cap-sandbox-browser"] == "UNKNOWN"
    assert states["cap-frontend"] == "ERROR" and states["cap-sandbox-http"] == "MISSING"


def test_the_gate_does_not_rely_on_a_record_it_cannot_parse(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A producer field of the wrong shape is a refusal, never a crash and never a pass."""
    code, data = _gate(tmp_path, monkeypatch, _five(**{
        "cap-backend": lambda record: record.__setitem__("producer", "not a record")}))
    text = _failure_text(data, code)
    assert "MISSING" in text, text


# -- the fixtures themselves -------------------------------------------------------------

def test_the_conforming_fixture_is_the_recorder_output_the_gate_claims_to_read(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The positive control earns its keep here too.

    If a field the contract lists were absent from the fixture the gate would be reading, the
    "all five pass" case would be fiction; this names the fields it checks rather than trusting
    the shape.
    """
    contract = json.loads(CONTRACT_PATH.read_text("utf-8"))
    record = conforming_producer("cap-sandbox-browser")
    for dotted in contract["a2_2_obligations"]["evidence_fields"]:
        node = record
        for part in dotted.split("."):
            assert isinstance(node, dict) and part in node, dotted
            node = node[part]
        assert node, dotted
    assert record["base_images"]["bases"], "the browser record has no base binding to check"
    assert any("registry_reference" == base["binding"]
               for base in record["base_images"]["bases"]), record["base_images"]["bases"]
    assert record["build_path"]["publishes"] is True
    assert record["identity"]["job"] == "release-images"
    assert record["identity"]["source_revision"] == GATE_SHA
    assert Path(RELEASE_BUILDS["cap-sandbox-browser"][0]).exists()
