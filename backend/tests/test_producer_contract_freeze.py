"""BATCH 3 A2.2 Stage 0: the A2.1 producer contract, frozen, and the checks that keep it frozen.

A2.2 may change one thing about A2.1's comparisons: it may make them *stricter*. It may not
weaken them, and "do not weaken" written in a plan document is the kind of claim this project has
already had to audit once -- the F-33 series found prose asserting gates that no reader executed.
So the freeze is a file (`scripts/release/producer_contract.json`) and this module executes it
against the recorder's own output: the checks fail if a later commit drops a required field,
removes a comparison, merges two of them, quietly re-scores the runner's CLI plugin, widens the
release path's exemptions, or deletes one of the tests the freeze itself cites as its enforcement.

Three things these tests deliberately do not do:

* They do not assert that the pins agree with each other -- that is the recorder's job, and
  repeating it here would only create a second place to be wrong.
* They do not rebuild the A2.1 fixture. They import it, so the freeze is checked against the same
  stubbed machine that earned the verdict it freezes.
* They do not let the freeze grow lazily. Every comparison below is one-directional: the live list
  must *contain* the frozen one. Adding an entry to the JSON to match a code change is a weakening
  dressed as a fix, and the diff of that file is where it shows up.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "release"))

import record_build_producer as recorder  # noqa: E402

from tests.test_producer_observation_contract import (  # noqa: E402
    BUILDKIT,
    BUILDX,
    CONTROLLED,
    FIVE,
    PIN,
    five_records,
    observe_run,
    recorded,
    scored,
)

CONTRACT_PATH = PROJECT_ROOT / "scripts/release/producer_contract.json"
CONTRACT = json.loads(CONTRACT_PATH.read_text("utf-8"))
INHERITED = CONTRACT["inherited_from_a2_1"]
PRODUCER = INHERITED["producer"]
FROZEN_BUILDX = PRODUCER["buildx"]
FROZEN_BUILDER = PRODUCER["builder"]
FROZEN_COMPARISONS = PRODUCER["comparisons"]
FROZEN_INSTRUMENT = PRODUCER["instrument_contract"]
FROZEN_LAYERS = PRODUCER["digest_layers"]
A2_2 = CONTRACT["a2_2_obligations"]
A2_1_DOC = (PROJECT_ROOT / "docs/quality"
            / "cap-post-rc-batch-3-producer-authority-2026-09-23.md")
#: The comparisons A2.1 answers with, and no fewer. A merge would show up here as a missing key,
#: which is the only shape "two comparisons became one" can take in a finished record.
COMPARISON_NAMES = ("lock_vs_workflow", "controlled_pin_vs_lock", "workflow_vs_observed",
                    "lock_vs_observed")


@pytest.fixture(scope="module")
def conforming(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """One A2.1-shaped record, produced by the code being held to the freeze."""
    where = tmp_path_factory.mktemp("a2-1-conforming")
    code, payload = recorded(where, observe_run())
    assert code == 0, payload.get("contract_gaps")
    return payload


@pytest.fixture(scope="module")
def scored_five(tmp_path_factory: pytest.TempPathFactory) -> dict:
    where = tmp_path_factory.mktemp("a2-1-set")
    return scored(where, five_records(where), FIVE)


# -- the frozen identity is the identity the repository actually ships ------------------

def test_the_frozen_identity_is_the_identity_the_repository_ships() -> None:
    """The contract quotes a buildx and a BuildKit; the files that ship must say the same.

    A freeze that disagrees with `controlled_buildx.json` or the lock is worse than no freeze,
    because it would license A2.2 to install one binary and certify another.
    """
    assert FROZEN_BUILDX["version"] == PIN["version"] == BUILDX["version"]
    assert FROZEN_BUILDX["git_commit"] == PIN["expected_git_commit"]
    assert FROZEN_BUILDX["sha256"] == PIN["integrity"]["expected"]
    assert FROZEN_BUILDX["download_url"] == PIN["source"]["download_url"]
    assert FROZEN_BUILDX["download_url"] == BUILDX["source"]
    assert FROZEN_BUILDX["asset_id"] == PIN["source"]["asset_id"]
    assert FROZEN_BUILDX["asset_size_bytes"] == PIN["source"]["asset_size_bytes"]
    assert FROZEN_BUILDX["pin_file"] == "scripts/release/controlled_buildx.json"
    assert FROZEN_BUILDX["never_invoked_as"] == ["docker buildx",
                                                 "docker-buildx through the CLI plugin dispatch"]
    assert FROZEN_BUILDER["buildkit_image"] == f"moby/buildkit:{BUILDKIT['tag']}"
    assert FROZEN_BUILDER["buildkit_index_digest"] == BUILDKIT["digest"]
    assert FROZEN_BUILDER["buildkit_index_digest"] in BUILDKIT["image_ref"]
    assert FROZEN_BUILDER["driver"] == "docker-container"
    assert FROZEN_BUILDER["default_builder_accepted"] is False
    assert FROZEN_BUILDER["name_must_be_explicit"] is True
    assert PRODUCER["set_acceptance"]["expected_images"] == FIVE


def test_the_freeze_cites_evidence_that_exists_and_says_the_same_thing() -> None:
    """The verdict this file inherits has to trace back to the report that earned it."""
    assert INHERITED["verdict"] == "BATCH 3 A2.1 PRODUCER CONFORMING"
    evidence = INHERITED["evidence"]
    for key in ("acceptance_head", "acceptance_run", "full_ci_pass_head", "full_ci_pass_run",
                "acceptance_artifact", "set_file"):
        assert evidence.get(key), f"the freeze cites no {key}"
    assert len(evidence["acceptance_head"]) in (7, 40)
    assert A2_1_DOC.exists(), "the freeze cites a report that is not in the repository"
    text = A2_1_DOC.read_text("utf-8")
    assert evidence["acceptance_head"] in text, (
        "the acceptance head the freeze names does not appear in the report it cites")
    assert INHERITED["verdict"] in text
    assert PRODUCER["builder"]["a2_1_builder_name"] in text


# -- the comparisons stay four, separate, and unshrunk ---------------------------------

def test_every_frozen_comparison_is_still_answered_separately(conforming: dict) -> None:
    """Four named answers, each with its own fields -- and an alignment built from them."""
    comparison = conforming["comparison"]
    for name in COMPARISON_NAMES:
        block = comparison.get(name)
        assert isinstance(block, dict), f"{name} is no longer answered as its own comparison"
        assert block["status"] == "CONFORMING", (
            f"{name} reads {block['status']} on the A2.1 fixture. The freeze was earned from a "
            "conforming record, and a contract nobody can satisfy is not a contract.")
        assert isinstance(block["fields"], dict) and block["fields"], name
    alignment = conforming["producer_alignment"]
    assert not isinstance(alignment, bool), "the alignment became one boolean"
    # Superset, not equality: the frozen claim is that none of A2.1's four answers may be dropped
    # or merged, which equality would overstate by also forbidding a fifth named comparison.
    assert set(COMPARISON_NAMES) <= set(alignment["components"]), alignment["components"]
    for name in COMPARISON_NAMES:
        assert alignment["components"][name] == "CONFORMING", name
    assert alignment["verdict"] == "CONFORMING"


def test_a2_2_may_not_shrink_any_required_field_list(conforming: dict) -> None:
    """A required field may be added; none of A2.1's may be dropped or demoted to informative."""
    for name in COMPARISON_NAMES:
        frozen = set(FROZEN_COMPARISONS[name]["required"])
        block = conforming["comparison"][name]
        missing = frozen - set(block["required"])
        assert not missing, f"{name}: A2.2 stopped requiring {sorted(missing)}"
        absent = {field for field in frozen if field not in block["fields"]}
        assert not absent, f"{name}: {sorted(absent)} is required but no longer compared"


def test_a2_2_may_not_shrink_any_blocking_field_list(conforming: dict) -> None:
    """`blocking` is the stricter of the two lists, so it gets the same one-way rule."""
    for name in COMPARISON_NAMES:
        frozen = set(FROZEN_COMPARISONS[name].get("blocking", []))
        live = set(conforming["comparison"][name].get("blocking", []))
        assert frozen <= live, f"{name}: {sorted(frozen - live)} stopped blocking on its own"


def test_the_frozen_field_names_are_the_ones_the_record_carries(conforming: dict) -> None:
    """A rename that keeps semantics still has to be named, because renaming is how a check dies.

    A required-field list can keep its length while every entry in it quietly changes address.
    The freeze therefore records the field names each comparison is built from, scored or not.
    """
    for name in COMPARISON_NAMES:
        fields = conforming["comparison"][name]["fields"]
        for field in FROZEN_COMPARISONS[name]["fields"]:
            assert field in fields, f"{name}.{field} vanished from the record"


def test_the_instrument_contract_may_grow_but_not_shrink() -> None:
    """A field the observation promised cannot stop being promised."""
    frozen = set(FROZEN_INSTRUMENT["required_paths"])
    missing = frozen - set(recorder.CONTRACT_REQUIRED)
    assert not missing, f"the recorder no longer promises {sorted(missing)}"


def test_a2_2_narrows_the_build_mode_exemptions_rather_than_widening_them() -> None:
    """A2.1 exempted the release build path from the observation-only fields because that path
    declared none of them. A2.2 gives it a workflow file, a controlled executable, a named builder
    and a recorded invocation -- so the exemption list can only get shorter. A new entry would be
    a promise withdrawn, and that is what this check catches.
    """
    frozen = set(FROZEN_INSTRUMENT["build_mode_exemptions"])
    widened = set(recorder.OBSERVE_ONLY_PREFIXES) - frozen
    assert not widened, f"the release path was newly exempted from {sorted(widened)}"


# -- the facts A2.1 kept on the page stay on the page ----------------------------------

def test_the_runner_plugin_answer_stays_recorded_and_unscored(tmp_path: Path) -> None:
    """A2.1's claim is only meaningful while the runner's own answer is still visible.

    The buildx a hosted runner dispatches to is not deterministic (v0.37.0 at two A2.1 heads,
    v0.37.1 at others), so deleting `cli_plugin_buildx_version` would make every future record
    look clean for the cheapest possible reason. It has to stay -- and it has to stay *unscored*,
    because scoring it would re-open F-44 as a failure of a job that no longer depends on it.
    """
    _, payload = recorded(tmp_path, observe_run(version="v0.37.0"))
    fields = payload["comparison"]["workflow_vs_observed"]["fields"]
    kept = fields["cli_plugin_buildx_version"]
    assert kept["read_back"] == "v0.37.0", kept
    assert kept["scored"] is False, kept
    assert payload["comparison"]["workflow_vs_observed"]["status"] == "CONFORMING"
    assert payload["comparison"]["buildx_binaries"]["built_with"] == CONTROLLED
    assert payload["comparison"]["buildx_binaries"]["cli_plugin_version"] == "v0.37.0"


def test_the_digest_layers_stay_separate_in_the_record(conforming: dict) -> None:
    """Each layer is scored against its own pinned-side value, and both are required."""
    block = conforming["comparison"]["lock_vs_observed"]
    assert "buildkit_digest" in block["required"]
    assert "buildkit_child_config" in block["required"]
    assert block["digest_relation"]["relation"] == "running_is_a_child_of_the_pinned_index"
    assert block["config_digest_relation"]["relation"] == (
        "running_config_is_the_pinned_child_config")
    assert set(FROZEN_LAYERS["layers"]) == {"manifest", "config"}
    for name, layer in FROZEN_LAYERS["layers"].items():
        field = layer["field"]
        assert field in block["fields"], f"{name}: {field} is no longer compared"
        assert field in block["required"], f"{name}: the frozen layer is no longer required"


# -- the refusal, frozen ---------------------------------------------------------------

@pytest.mark.parametrize("spelling, digest, expected_relation", [
    ("child", None, "running_is_a_child_of_the_pinned_index"),
    ("index", BUILDKIT["digest"], "same_digest"),
])
def test_the_conforming_case_does_not_depend_on_child_equality(
        tmp_path: Path, spelling: str, digest: str | None,
        expected_relation: str) -> None:
    """What CI measured, frozen as a rule: the running image may name the index *or* its child.

    A daemon that pulled `name:tag@<index-digest>` records the index digest in `RepoDigests`. A
    required field demanding equality with the platform child would call that conforming build a
    mismatch, so A2.1 refused to add one -- and A2.2 must not, which is testable as "both
    spellings still come out CONFORMING, each on its own layer".
    """
    run = observe_run() if digest is None else observe_run(
        repo_digests=[f"moby/buildkit@{digest}"])
    _, payload = recorded(tmp_path, run)
    block = payload["comparison"]["lock_vs_observed"]
    assert block["digest_relation"]["relation"] == expected_relation, spelling
    assert block["status"] == "CONFORMING", block["digest_relation"]
    assert block["config_digest_relation"]["status"] == "CONFORMING", (
        "the config layer is a separate answer and has to be readable in both spellings")
    assert not payload.get("contract_gaps"), payload["contract_gaps"]
    assert FROZEN_LAYERS["not_required_on_purpose"]["claim"].startswith("running image")


def test_a_required_field_that_is_blank_is_never_a_match(conforming: dict) -> None:
    """The direction of every A2.1 rule, restated as a freeze: blanks are not agreement.

    In the conforming record that means every required field has an *answer*, not an absent one:
    a field that reads `None` and still contributes to CONFORMING would be invisible here and
    fatal in a release.
    """
    assert conforming.get("contract_gaps", []) == []
    for name in COMPARISON_NAMES:
        block = conforming["comparison"][name]
        for field in block["required"]:
            assert block["fields"][field]["relation"] == "equal", f"{name}.{field}"
        for field in block.get("blocking", []):
            assert block["fields"][field]["relation"] != "different", f"{name}.{field}"


def test_the_frozen_set_acceptance_is_what_the_scorer_applies(scored_five: dict) -> None:
    """A2.2 inherits an acceptance rule, not a verdict string: the rows must still show it."""
    rules = PRODUCER["set_acceptance"]
    assert scored_five["verdict"] == "CONFORMING", scored_five["problems"]
    assert scored_five["expected_images"] == rules["expected_images"]
    assert len(scored_five["rounds"]) == 1, "the frozen 'one round' rule did not survive"
    for image in rules["expected_images"]:
        row = scored_five["images"][image]
        assert set(COMPARISON_NAMES) <= set(row["comparisons"]), row["comparisons"]
        assert row["status"] == "CONFORMING"
        assert row["build_exit"] == 0
        assert row["contract_gaps"] == []
        assert row["producer_alignment"] == "CONFORMING"
        assert row["bases"], "a row with no base bindings is not a build that can be re-derived"
        assert all(base["status"] == "READ" for base in row["bases"]), row["bases"]
    assert scored_five["authorizes_a2_2"] is True


# -- the freeze's own bookkeeping ------------------------------------------------------

def test_every_enforcement_check_the_freeze_cites_still_exists() -> None:
    """A rule pointing at a deleted test is the prose-gate failure again, one layer up.

    `non_weakening.rules` is the contract's list of what enforces it, so this module's own
    namespace is the thing to check it against.
    """
    here = globals()
    for rule in INHERITED["non_weakening"]["rules"]:
        assert rule["check"] in here, f"the freeze cites {rule['check']}, which is gone"
        assert callable(here[rule["check"]]), rule["check"]
        assert rule.get("claim"), rule


def test_the_freeze_states_what_a2_2_owes_and_where_each_obligation_was_evidenced() -> None:
    """The two blocks of the contract mean different things, and a reader has to be able to tell.

    Until F-50 this test asserted the opposite -- `OPEN` / `NOT YET EVIDENCED` -- because that was
    true when it was written. What has to survive the change is the part that makes the status a
    claim rather than prose: it names a commit, both strings name the *same* commit, each one
    says which artifact enforces the obligation, and the detailed one names the observation still
    owed. A status that could be edited to any sentence would test nothing, so the shape is
    pinned here.
    """
    sha_pattern = re.compile(r"^[0-9a-f]{40}$")
    named: list[str] = []
    for label, text in (("status.a2_2_obligations", CONTRACT["status"]["a2_2_obligations"]),
                        ("a2_2_obligations.status", A2_2["status"])):
        assert text.upper().startswith("EVIDENCED AT "), f"{label} does not state where it was met"
        candidate = text.split()[2]
        assert sha_pattern.match(candidate), f"{label} names {candidate!r}, not a full commit sha"
        assert "release-image-completeness" in text, (
            f"{label} does not say which artifact enforces the obligation")
        named.append(candidate)
    assert named[0] == named[1], "the two status strings claim different candidates"
    assert "F-51" in A2_2["status"], (
        "the detailed status hides the live-publication item still owed")
    obligations = A2_2["obligations"]
    for key in ("one_install_mechanism", "build_command", "base_handoff", "evidence", "authority",
                "reader", "diagnostics"):
        assert obligations.get(key), f"A2.2 owes an {key} and the contract does not name it"
    assert A2_2["close_only_after_recertification"] == ["F-44", "F-47"]
    assert "builds no release image" in obligations["build_command"]


def test_the_untouched_seal_is_stated_in_the_freeze() -> None:
    """A2.2 changes how a *future* release is built; the sealed one is not its material.

    The contract carries that boundary so a later reader cannot infer from a switched release
    path that `v1.0.6-rc1` was rebuilt by it.
    """
    sealed = CONTRACT["sealed_release"]
    assert sealed["tag"] == "v1.0.6-rc1"
    assert sealed["lifecycle"] == "CLOSED"
    assert sealed["a2_2_rebuilds_it"] is False
    assert sealed["a2_2_promotes_or_reruns_it"] is False
    assert sealed["stable_release"] == "v1.0.5"
