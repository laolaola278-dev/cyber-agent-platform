"""F-7: the release pipeline must publish the whole image set the chart deploys.

CAP runs five images in production -- backend, frontend, the two sandbox images
and the egress proxy -- and the Helm chart references all five. `release.yml`
published two. The consequence is not cosmetic: a fresh `helm install` of the
released chart gets `ImagePullBackOff` on three deployments, and the chart's own
defaults said so by naming `cap-sandbox-http:latest`, `cap-sandbox-browser:latest`
and `cap-egress-proxy:latest` -- images no release artifact ever produced, so
they only exist on a machine that happened to build them locally.

The comparison is *derived*, not listed: the chart's image references are read
out of `values.yaml` and the templates (resolving the `.Values.*` interpolations
the templates actually use), and the release set is read out of `release.yml`'s
matrix. Adding a seventh image to the chart without teaching the release about
it must fail here, which a hand-written list of five names could not do.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHART = PROJECT_ROOT / "deployment" / "helm" / "cap"
VALUES = CHART / "values.yaml"
TEMPLATES = CHART / "templates"
RELEASE_YML = PROJECT_ROOT / ".github" / "workflows" / "release.yml"
CI_YML = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"

#: Image names this repository owns, i.e. the ones a release must publish.
CAP_OWNER_PREFIX = "cap-"


def _values(doc: dict | None = None) -> dict:
    raw = doc if doc is not None else yaml.safe_load(VALUES.read_text("utf-8"))
    return raw


def _release_doc() -> dict:
    return yaml.safe_load(RELEASE_YML.read_text("utf-8"))


def _lookup(values: dict, dotted: str):
    node: object = values
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


#: `image: {{ include "cap.imageRef" (dict "image" .Values.backend.image ...) }}`
_HELPER_IMAGE = re.compile(
    r"""image:\s*\{\{\s*include\s+"cap\.imageRef"\s+\(dict\s+"image"\s+\.Values\.([A-Za-z0-9_.]+)"""
)
_IMAGE_INTERP = re.compile(
    r"image:\s*[\"']?\{\{\s*\.Values\.([A-Za-z0-9_.]+)\s*\}\}"
    r"(?::\{\{\s*\.Values\.([A-Za-z0-9_.]+)\s*\}\})?"
    r"(?:@sha256:[0-9a-f]+)?"
)
_LITERAL_IMAGE = re.compile(r"""image:\s*["']([^"'{}]+)["']""")


def _chart_app_version() -> str:
    chart = (CHART / "Chart.yaml").read_text("utf-8")
    match = re.search(r'^appVersion:\s*["\']?([^"\'\s]+)', chart, re.MULTILINE)
    assert match, "Chart.yaml has no appVersion -- the chart cannot default a tag"
    return match.group(1)


def image_ref(block: dict, site: str) -> str:
    """Resolve an {repository, tag, digest} block the way cap.imageRef does."""
    repository = block.get("repository")
    assert repository, f"{site}: image block has no repository"
    digest = (block.get("digest") or "").strip()
    if digest:
        return f"{repository}@{digest}"
    tag = (block.get("tag") or "").strip() or _chart_app_version()
    return f"{repository}:{tag}"


_IMAGE_SHAPED = re.compile(r"^[A-Za-z0-9._\-/]+(:[A-Za-z0-9._\-]+)?(@sha256:[0-9a-f]{16,})?$")


def _values_image_refs(node: object, path: str = "") -> dict[str, str]:
    """Every image-shaped string in values.yaml, wherever it sits.

    The two sandbox images are not pod templates: the worker receives them as
    environment values and creates sandbox Pods with them at runtime, so a scan
    of `image:` lines alone under-counts what a deployment has to pull. Reading
    the shape (a registry/repository, optional :tag, optional @digest) rather
    than a key list keeps a newly added image reference visible.
    """
    found: dict[str, str] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            name = str(key).lower()
            if "image" not in name:
                found.update(_values_image_refs(value, child))
                continue
            # An image reference reaches this chart in two shapes: a full string
            # (an env value handed to the worker) or an {repository, tag, digest}
            # block that a template composes. Both are a deployment coordinate,
            # so both count -- missing one is how the sandbox images stayed
            # invisible to a scan of pod `image:` lines.
            if isinstance(value, str) and _IMAGE_SHAPED.match(value):
                found[child] = value
            elif isinstance(value, dict) and "repository" in value:
                found[child] = image_ref(value, child)
            else:
                found.update(_values_image_refs(value, child))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            found.update(_values_image_refs(item, f"{path}[{index}]"))
    return found


def chart_images(values: dict | None = None) -> dict[str, str]:
    """Every container image the production chart can render, source -> reference.

    Keys are the workload or value path that carries the image; values are the
    reference as the chart would emit it for the default values, so a `:latest`
    default is visible here rather than hidden behind an interpolation.
    """
    values = _values(values)
    found: dict[str, str] = {}
    for template in sorted(TEMPLATES.glob("*.yaml")):
        text = template.read_text("utf-8")
        for line in text.splitlines():
            helper = _HELPER_IMAGE.search(line)
            if helper:
                path = helper.group(1)
                block = _lookup(values, path)
                if not isinstance(block, dict):
                    raise AssertionError(
                        f"{template.name} passes .Values.{path} to cap.imageRef but "
                        "values.yaml has no image block there"
                    )
                workload = path.split(".")[0]
                found[f"{template.name}:{workload}"] = image_ref(block, path)
                continue
            interpolated = _IMAGE_INTERP.search(line)
            if interpolated:
                repo_path, tag_path = interpolated.group(1), interpolated.group(2)
                repo = _lookup(values, repo_path)
                tag = _lookup(values, tag_path) if tag_path else None
                if repo is None:
                    raise AssertionError(
                        f"{template.name} reads .Values.{repo_path} which values.yaml "
                        "does not define -- the chart cannot be rendered"
                    )
                ref = str(repo) + (f":{tag}" if tag is not None else "")
                workload = interpolated.group(1).split(".")[0]
                found[f"{template.name}:{workload}"] = ref
                continue
            literal = _LITERAL_IMAGE.search(line)
            if literal:
                # A hard-coded image in a template is a second defect class: the
                # operator cannot retarget it through values at all.
                found[f"{template.name}:literal"] = literal.group(1)
    for path, ref in _values_image_refs(values).items():
        found[f"values.yaml:{path}"] = ref
    return found


def cap_image_names(references: dict[str, str]) -> set[str]:
    """The image *names* this repository owns, ignoring which registry org hosts them."""
    names = set()
    for ref in references.values():
        last = ref.split("/")[-1]
        name = last.split(":")[0].split("@")[0]
        if name.startswith(CAP_OWNER_PREFIX):
            names.add(name)
    return names


RELEASE_BUILD_SCRIPT = PROJECT_ROOT / "scripts" / "release" / "build_release_image.sh"


def release_matrix(doc: dict | None = None) -> dict[str, dict]:
    """The images `release.yml` builds and publishes, keyed by image name."""
    doc = _release_doc() if doc is None else doc
    matrix = doc["jobs"]["release-images"]["strategy"]["matrix"]
    out: dict[str, dict] = {}
    for entry in matrix.get("include") or []:
        out[entry["image"]] = entry
    for component in matrix.get("component") or []:
        # The pre-F-7 shape: a bare component name, image derived from it.
        out[f"cap-{component}"] = {"component": component}
    return out


def release_build_steps(doc: dict | None = None) -> list[dict]:
    """The release images that build outside the matrix, keyed by image name.

    The browser image cannot be a matrix cell -- its base is an image this same
    release publishes -- so it is its own job, and the completeness check has to
    see it rather than discover it by absence.
    """
    doc = _release_doc() if doc is None else doc
    found: list[dict] = []
    for job_name, job in doc["jobs"].items():
        if job_name == "release-images":
            continue
        for step in job.get("steps", []) or []:
            run = step.get("run")
            if isinstance(run, str) and "build_release_image.sh" in run:
                match = re.search(r"--name\s+(\S+)", run)
                if match:
                    found.append({"image": match.group(1), "job": job_name})
    return found


def release_step_flags(doc: dict | None = None) -> dict:
    """The build-push-action inputs, when a release image still uses them."""
    doc = _release_doc() if doc is None else doc
    for job in doc["jobs"].values():
        for step in job.get("steps") or []:
            if str(step.get("uses", "")).startswith("docker/build-push-action"):
                return step.get("with") or {}
    return {}


def published_images(doc: dict | None = None) -> set[str]:
    """Everything the release graph builds and pushes, matrix cells and not."""
    names = set(release_matrix(doc))
    names |= {step["image"] for step in release_build_steps(doc)}
    return names


def test_chart_and_release_publish_the_same_image_set() -> None:
    """ARTIFACT-GATE 1: set equality between what deploys and what is published."""
    references = chart_images()
    deployed = cap_image_names(references)
    published = published_images()
    missing = deployed - published
    extra = published - deployed
    assert not missing, (
        "the production chart deploys CAP images that no release job publishes, so a "
        f"fresh install pulls refs that do not exist: {sorted(missing)}\n"
        "release publishes: " + ", ".join(sorted(published))
    )
    assert not extra, (
        f"release.yml publishes images no production workload uses: {sorted(extra)}"
    )
    assert deployed, "the parser found no CAP-owned image in the chart -- scanner stale"


def test_no_production_chart_reference_is_latest() -> None:
    """ARTIFACT-GATE 2: :latest is untraceable, and cannot be a release contract."""
    offenders = {key: ref for key, ref in chart_images().items() if ref.endswith(":latest")}
    assert not offenders, (
        "these chart references resolve to a tag that no release produces and no "
        f"digest can pin: {offenders}"
    )


def test_chart_image_references_are_all_templated() -> None:
    """Every CAP image the chart runs must be retargetable through values.

    A literal `image: cap-egress-proxy:latest` in a template cannot be pinned to
    a release tag or a digest by the operator, which is how F-7 stayed invisible:
    the default values were never what the template would have used.
    """
    literals = {k: v for k, v in chart_images().items() if k.endswith(":literal")}
    cap_literals = {
        k: v for k, v in literals.items() if v.split("/")[-1].startswith(CAP_OWNER_PREFIX)
    }
    assert not cap_literals, (
        f"hard-coded CAP image references in templates: {cap_literals}"
    )


def ci_job_steps(job_name: str) -> list[dict]:
    doc = yaml.safe_load(CI_YML.read_text("utf-8"))
    job = (doc.get("jobs") or {}).get(job_name)
    assert isinstance(job, dict), f"ci.yml has no {job_name} job"
    return job.get("steps") or []


def ci_job_scripts(job_name: str) -> list[str]:
    """Every shell a CI job runs, plus the lines that would push an image.

    Scoping the search to a job's own steps matters: ci.yml talks about `--push`
    in a comment explaining why it does *not* use the flag, and a whole-file
    substring check would fail on that comment while still passing if a step
    grew `push: true`.
    """
    scripts: list[str] = []
    for step in ci_job_steps(job_name):
        run = step.get("run")
        if isinstance(run, str):
            scripts.append(run)
        with_map = step.get("with") or {}
        if isinstance(with_map, dict) and with_map.get("push"):
            scripts.append(f"--push {with_map.get('push')}")
    return scripts


def ci_job_uses(job_name: str) -> list[str]:
    return [str(step.get("uses") or "") for step in ci_job_steps(job_name)]


def test_every_release_image_is_built_with_version_revision_and_attestations() -> None:
    """release.yml §12/§14/§15: one contract for every image, however it builds."""
    doc = _release_doc()
    matrix = release_matrix(doc)
    assert matrix, "release.yml builds no images"
    extra = {step["image"] for step in release_build_steps(doc)}
    assert extra == {"cap-sandbox-browser"}, (
        f"images built outside the matrix: {sorted(extra)} -- the browser is the one "
        "with a real dependency; anything else is duplicated build logic"
    )
    text = RELEASE_YML.read_text("utf-8")
    for name, entry in sorted(matrix.items()):
        assert name.startswith(CAP_OWNER_PREFIX), f"{name}: release images are CAP-owned"
        context = entry.get("context") or ""
        assert context or entry.get("role"), f"{name}: no build context and no role"
        assert entry["dockerfile"] in text, f"{name}: dockerfile never reaches the build"

    # The build contract lives in the shared script, so all five images get the
    # same args and one edit cannot fix two of them and miss the other three.
    script = RELEASE_BUILD_SCRIPT.read_text("utf-8")
    assert "--provenance=true" in script and "--sbom=true" in script, (
        "release builds must attach SBOM and provenance attestations"
    )
    assert "--push" in script and "--load" in script, (
        "the script must distinguish the publish build from the dry build"
    )
    # CI's dry build cannot use buildx for the browser image: a buildx container
    # builder does not see the host docker store, so its local base would have to
    # come from a registry that does not exist yet. That is what --local-docker is
    # for -- and the evidence must say which driver produced it, or a dry build
    # could be read as an attested release artifact.
    assert "--local-docker" in script, "the dry-build driver switch is gone"
    assert '"attestations": {' in script and "CAP_EVIDENCE_BUILD_ARGS" in script, (
        "attestations must be recorded from the flags the build actually passed"
    )
    ci_build_scripts = ci_job_scripts("release-image-builds")
    assert ci_build_scripts, "ci.yml defines no release-image-builds steps"
    assert any("--local-docker" in s for s in ci_build_scripts), (
        "CI's image builds no longer name their driver"
    )
    assert not [s for s in ci_build_scripts if "--push" in s], (
        "CI must not push release images -- --push belongs to release.yml"
    )
    assert any("trivy" in s.lower() for s in ci_job_uses("release-image-builds")), (
        "CI no longer scans the images it builds"
    )
    assert "--build-arg" in script and "VERSION" in script and "REVISION" in script
    assert text.count("--push") >= 2, "every release build must be a push build"
    step_flags = release_step_flags(doc)
    if step_flags:  # a matrix cell still using build-push-action, if any
        assert step_flags.get("push") is True
        assert step_flags.get("sbom") is True
        assert step_flags.get("provenance") is True
    assert "${{ needs.validate-tag.outputs.version }}" in text, (
        "release image tags must come from the validated VERSION, not a literal"
    )


#: Workflows that build CAP images and deploy them to a throwaway cluster, and
#: the test modules that read those images back. They have to agree on the tag:
#: `kind load` refuses an image it does not have, and a helm install that falls
#: back to the chart's release defaults deploys nothing the job ever built. Both
#: happened in one round, and both surfaced as a wall of errors behind a single
#: `:latest` that had no business being in a certification file.
CERT_WORKFLOWS = (
    "cap-k8s-certification.yml",
    "cap-ga-certification.yml",
    "cap-ga-reliability.yml",
)
CERT_TEST_MODULES = (
    "test_phase_28_6_k8s_certification.py",
    "test_phase_28_7_ga_certification.py",
    "test_phase_28_7_ga_tier2_supply_chain.py",
    "test_phase_28_7_ga_tier2_resilience.py",
)

_QUOTED_CAP_IMAGE = re.compile(r'["\'](cap-[a-z0-9-]+):([A-Za-z0-9._-]+)["\']')
_HELM_IMAGE_TAG = re.compile(r"--set\s+[A-Za-z0-9_.]*image\.tag=([A-Za-z0-9._-]+)")
_KIND_IMAGE = re.compile(r"(?<![\w{-])(cap-[a-z0-9-]+):([A-Za-z0-9._-]+)")


def literal_cap_images(text: str) -> set[tuple[str, str]]:
    """`(name, tag)` for every CAP image this text names with a literal tag."""
    return {m for m in _QUOTED_CAP_IMAGE.findall(text)}


def workflow_image_tags(text: str) -> set[str]:
    """Every CAP image tag the workflow states literally."""
    return {tag for _, tag in _KIND_IMAGE.findall(text)} | set(
        _HELM_IMAGE_TAG.findall(text)
    )


def test_certification_rounds_name_their_images_with_one_tag() -> None:
    """ARTIFACT-GATE 10's precondition, checked before a cluster is involved."""
    tags: set[str] = set()
    for name in CERT_WORKFLOWS:
        text = (PROJECT_ROOT / ".github" / "workflows" / name).read_text("utf-8")
        found = workflow_image_tags(text)
        assert found, f"{name} builds or deploys no CAP image by name"
        assert len(found) == 1, f"{name} uses more than one image tag: {sorted(found)}"
        tags |= found

    for module_name in CERT_TEST_MODULES:
        text = (PROJECT_ROOT / "backend" / "tests" / module_name).read_text("utf-8")
        literals = {
            (image, tag)
            for image, tag in literal_cap_images(text)
            # f-strings interpolate the tag; a bare quoted ref is the drift.
            if tag not in {"{IMAGE_TAG}", "{SANDBOX_IMAGE_TAG}"}
        }
        assert not literals, (
            f"{module_name} names CAP images literally: {sorted(literals)} -- read "
            "the tag from CAP_CERT_IMAGE_TAG, the variable the workflow exports"
        )
        obtained = "CAP_CERT_IMAGE_TAG" in text or "IMAGE_TAG" in text
        assert obtained, (
            f"{module_name} has no CAP_CERT_IMAGE_TAG (directly or imported) to "
            "disagree with, so anything it names about images is a literal"
        )

    defaults = set()
    for module_name in CERT_TEST_MODULES:
        text = (PROJECT_ROOT / "backend" / "tests" / module_name).read_text("utf-8")
        match = re.search(r'CAP_CERT_IMAGE_TAG",\s*"([^"]+)"', text)
        if match:
            defaults.add(match.group(1))
    assert defaults, "no certification module declares the tag it expects any more"
    assert defaults == tags, (
        f"the certification jobs build {sorted(tags)} but the gates expect "
        f"{sorted(defaults)}"
    )

    #: The Trivy policy names images, never coordinates: GA-GATE 22 iterates it,
    #: and three `:latest` entries in this one file failed a strict GA round that
    #: had nothing wrong with the images themselves.
    policy = json.loads(
        (PROJECT_ROOT / "scripts" / "certification" / "security_policy.json").read_text("utf-8")
    )
    targets = policy["scan_targets"]
    assert all(":" not in target for target in targets), (
        f"security policy scan targets carry tags: {targets}"
    )
    assert set(targets) == cap_image_names(chart_images()), (
        f"the Trivy policy scans {sorted(targets)} but the chart deploys "
        f"{sorted(cap_image_names(chart_images()))}"
    )


def test_the_policy_scan_target_check_is_sensitive() -> None:
    """Dropping a target has to break the equality with the chart's set."""
    targets = json.loads(
        (PROJECT_ROOT / "scripts" / "certification" / "security_policy.json").read_text("utf-8")
    )["scan_targets"]
    deployed = cap_image_names(chart_images())
    assert set(targets) == deployed
    assert len(targets) == len(deployed), "the policy lists a name twice"
    for index in range(len(targets)):
        without = [target for position, target in enumerate(targets) if position != index]
        assert set(without) != deployed, f"removing {targets[index]!r} changed nothing"


def test_the_drift_guard_notices_a_stale_tag() -> None:
    """The control: this is the exact text that broke the Kubernetes round."""
    stale = 'images = ["cap-backend:ci", "cap-sandbox-http:latest"]'
    assert ("cap-sandbox-http", "latest") in literal_cap_images(stale)
    assert workflow_image_tags('kind load cap-sandbox-http:latest --name c') == {"latest"}
    mixed = (
        'kind load docker-image cap-backend:ci cap-x:latest --name c\n'
        'helm install --set worker.sandbox.image.tag=ci\n'
    )
    assert workflow_image_tags(mixed) == {"ci", "latest"}
    assert _QUOTED_CAP_IMAGE.findall('f"cap-backend:{IMAGE_TAG}"') == []


def security_matrix(doc: dict | None = None) -> set[str]:
    """The images `release.yml` scans after publishing them."""
    doc = _release_doc() if doc is None else doc
    job = doc["jobs"].get("release-image-security") or {}
    return set(((job.get("strategy") or {}).get("matrix") or {}).get("image") or [])


def test_every_published_image_is_scanned_in_the_release() -> None:
    """ARTIFACT-GATE 6, on the release side rather than CI's.

    `release-image-builds` scans a matrix derived from the same cells that build,
    so it cannot drift. `release-image-security` lists its five names by hand,
    which means the one place that scans *what an operator will actually pull*
    is the place a new image can be forgotten: build it, publish it, certify it,
    and never Trivy the artifact. Set equality with the chart's own image set is
    what makes that impossible.
    """
    published = published_images()
    scanned = security_matrix()
    deployed = cap_image_names(chart_images())
    assert scanned, "release.yml scans no published image"
    assert scanned == published, (
        f"the release publishes {sorted(published)} but scans {sorted(scanned)}"
    )
    assert scanned == deployed, (
        f"the chart deploys {sorted(deployed)} but the release scans {sorted(scanned)}"
    )
    for name in sorted(scanned):
        assert set(scanned) - {name} != deployed, (
            f"removing {name} from the scan matrix changed nothing"
        )
    step_text = " ".join(
        [str(step.get("run") or "") for step in doc_steps("release-image-security")]
        + [json.dumps(step.get("with") or {}) for step in doc_steps("release-image-security")]
    )
    publish_run = re.sub(r"\s+", " ", step_text)
    assert "HIGH,CRITICAL" in publish_run, (
        "the release scan no longer blocks on the project's severity policy"
    )
    assert '"exit-code": "1"' in publish_run, "the release scan cannot fail the release"
    assert '"ignore-unfixed": true' in publish_run, (
        "the release scan no longer ignores unfixable noise"
    )


def doc_steps(job_name: str, doc: dict | None = None) -> list[dict]:
    doc = _release_doc() if doc is None else doc
    return (doc["jobs"].get(job_name) or {}).get("steps") or []


def test_every_uploaded_release_asset_is_attached_to_the_release() -> None:
    """Nothing is "shipped" that the publish step does not actually attach.

    `values-release-<version>.yaml` exists to make `helm upgrade -f` install the
    digests this release recorded, and §6 of the closure report says it ships
    beside the chart. It was uploaded into the release-assets artifact and then
    left out of `gh release create`'s file list -- so the only copy an operator
    could reach was a workflow artifact that expires. Uploading is not publishing.
    """
    doc = _release_doc()
    upload = next(
        step for step in doc["jobs"]["release-chart"]["steps"]
        if str(step.get("uses", "")).startswith("actions/upload-artifact")
    )
    shipped = [
        re.sub(r"\s+", " ", line.strip())
        for line in str((upload.get("with") or {}).get("path", "")).splitlines()
        if line.strip()
    ]
    assert shipped, "release-chart uploads no assets"
    publish_run = " ".join(
        re.sub(r"\s+", " ", str(step.get("run", "")))
        for step in doc["jobs"]["publish-release"]["steps"]
    )
    assert "gh release create" in publish_run, "publish-release no longer creates a release"
    missing = [path for path in shipped if path not in publish_run]
    assert not missing, (
        f"release assets uploaded but never attached to the GitHub Release: {missing}"
    )


def test_sandbox_browser_does_not_depend_on_a_mutable_local_tag() -> None:
    """ARTIFACT-GATE 9: the browser base is stated per build, never inherited.

    `FROM cap-sandbox-http:latest` built fine on a machine that happened to hold
    that image and could not say which bytes it used. On a clean runner it fails,
    which is the good outcome; on a developer box it silently succeeds against
    whatever was built last. The base is therefore an ARG with no default, and
    every caller has to pass it.
    """
    dockerfile = (
        PROJECT_ROOT / "backend" / "docker" / "sandbox-browser" / "Dockerfile"
    ).read_text("utf-8")
    froms = re.findall(r"^FROM\s+(\S+)", dockerfile, re.MULTILINE)
    assert froms == ["${SANDBOX_HTTP_BASE}"], (
        f"sandbox-browser must build FROM the declared base ARG, found {froms}"
    )
    arg = re.search(
        r"^ARG\s+SANDBOX_HTTP_BASE(\s*=\S+)?",
        dockerfile,
        re.MULTILINE,
    )
    assert arg, "no ARG SANDBOX_HTTP_BASE -- the base has to be an explicit input"
    assert not arg.group(1), (
        "the base ARG must have no default, or it is a :latest by another name: "
        + repr(arg.group(0))
    )
    callers = {
        "backend/docker/build_sandbox_images.sh": (
            PROJECT_ROOT / "backend" / "docker" / "build_sandbox_images.sh"
        ).read_text("utf-8"),
        "release.yml": RELEASE_YML.read_text("utf-8"),
    }
    for site, text in callers.items():
        assert "SANDBOX_HTTP_BASE" in text, (
            f"{site} builds the browser image without naming its base"
        )


# -- the scanner can fail ----------------------------------------------------

def test_scanner_reports_both_directions_of_the_set_difference() -> None:
    """The comparison has to notice a change on either side, not just neither."""
    deployed = cap_image_names(chart_images())
    published = published_images()
    assert deployed and published, "one side parsed empty -- the guard would be vacuous"

    # Dropping any image release publishes must surface as exactly that image
    # missing from the release set -- F-7 is this assertion, seen from the other
    # end, for the three the release never had.
    for name in sorted(deployed & published):
        shrunk = {k for k in published if k != name}
        assert deployed - shrunk == {name}, (
            f"dropping {name} from the release matrix was not reported as a gap"
        )

    # An image release publishes that nothing deploys is the other direction.
    assert (published | {"cap-not-a-workload"}) - deployed == {"cap-not-a-workload"}


def test_scanner_fails_if_a_chart_reference_disappears(tmp_path: Path) -> None:
    """A chart with no images at all must not read as 'already consistent'."""
    written = cap_image_names(chart_images())
    assert written, "values.yaml renders no CAP image -- the guard would be vacuous"
    mutated = _values()
    mutated["worker"]["sandbox"]["image"] = "example.invalid/cap-sandbox-http:9.9.9"
    after = cap_image_names(chart_images(mutated))
    assert "cap-sandbox-http" in after, "renaming the repository must not drop the image"
    assert after == written, "the mutation changed the derived set in an unexpected way"


# ---------------------------------------------------------------------------
# The two inline release gates, executed -- the same method F-21 established for
# the certification gate. These decide whether a partial image set can become a
# GitHub Release, so reading them is not enough.
# ---------------------------------------------------------------------------

COMPLETENESS_STEP = "Require a complete, attested, digest-recorded image set"
RENDER_STEP = "Render the digest-pinned values file"
COMPLETENESS_HEREDOC = ("ARTIFACT_GATE_PY", )
RENDER_HEREDOC = ("VALUES_PY", )


def _release_gate_body(step_name: str, heredoc: str) -> str:
    steps = _release_doc()["jobs"]["release-image-completeness"]["steps"]
    run = next(step["run"] for step in steps if step.get("name") == step_name)
    opener = f"python3 - <<'{heredoc}'\n"
    return run[run.index(opener) + len(opener) : run.rindex(f"\n{heredoc}")]


def _record(name: str, version: str = "9.9.9-rc1", **overrides: object) -> dict:
    record = {
        "image": name,
        "ref": f"ghcr.io/o/{name}:{version}",
        "tag": version,
        "pushed": True,
        "index_digest": "sha256:" + "a" * 64,
        "platform_digest_linux_amd64": "sha256:" + "b" * 64,
        "dockerfile_sha256": "c" * 64,
        "context_sha256": "d" * 64,
        "base_refs": ["python:3.13.12-slim-bookworm@sha256:" + "f" * 64],
        "source_revision": "e" * 40,
        "attestations": {"sbom": True, "provenance": True},
    }
    record.update(overrides)
    return record


def _write_records(tmp_path: Path, records: list[dict], version: str = "9.9.9-rc1") -> Path:
    evidence = tmp_path / "release-image-evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    for record in records:
        (evidence / f"{record['image']}.json").write_text(
            json.dumps(record, indent=2), encoding="utf-8"
        )
    (tmp_path / "outputs").mkdir(exist_ok=True)
    return evidence


def _exec_release_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, records: list[dict],
                       version: str = "9.9.9-rc1") -> tuple[int, dict | None]:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VERSION", version)
    _write_records(tmp_path, records, version)
    code = 0
    try:
        exec(  # noqa: S102 -- executing the product's own publication gate
            compile(_release_gate_body(COMPLETENESS_STEP, "ARTIFACT_GATE_PY"),
                    "<release image completeness gate>", "exec"),
            {"__name__": "cap_release_image_gate"},
        )
    except SystemExit as exit_info:
        code = exit_info.code if isinstance(exit_info.code, int) else 1
    merged = list((tmp_path / "outputs" / "release-images").glob("release-images-*.json"))
    data = json.loads(merged[0].read_text("utf-8")) if merged else None
    return code, data


def _all_five() -> list[dict]:
    return [_record(name) for name in (
        "cap-backend", "cap-frontend", "cap-sandbox-http", "cap-sandbox-browser",
        "cap-egress-proxy",
    )]


def test_completeness_gate_accepts_five_complete_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, merged = _exec_release_gate(tmp_path, monkeypatch, _all_five())
    assert code == 0, merged
    assert merged["verdict"] == "PASS"
    assert set(merged["images"]) == {
        "backend", "frontend", "sandbox_http", "sandbox_browser", "egress_proxy"
    }


def test_completeness_gate_refuses_a_missing_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Four of five is the F-7 shape, and it must not reach publish-release."""
    records = [r for r in _all_five() if r["image"] != "cap-sandbox-browser"]
    code, merged = _exec_release_gate(tmp_path, monkeypatch, records)
    assert code == 1
    assert any("cap-sandbox-browser" in reason for reason in merged["failures"])


def test_completeness_gate_refuses_a_record_without_a_digest_or_attestation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = _all_five()
    records[0] = {**records[0], "index_digest": None}
    records[1] = {**records[1], "attestations": {"sbom": False, "provenance": True}}
    records[2] = {**records[2], "pushed": False}
    records[3] = {**records[3], "tag": "1.0.0-rc0"}
    records[4] = {**records[4], "base_refs": []}
    code, merged = _exec_release_gate(tmp_path, monkeypatch, records)
    assert code == 1
    reasons = " ".join(merged["failures"])
    assert "index_digest" in reasons and "sbom" in reasons and "never published" in reasons
    assert "tagged '1.0.0-rc0'" in reasons
    assert "base_refs" in reasons, (
        "an image that cannot say what it was built on is unauditable: "
        f"{reasons}"
    )


def test_completeness_gate_refuses_evidence_for_an_undeclared_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = _all_five() + [_record("cap-something-else")]
    code, merged = _exec_release_gate(tmp_path, monkeypatch, records)
    assert code == 1
    assert any("does not declare" in reason for reason in merged["failures"])


def test_values_renderer_pins_every_image_the_chart_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, merged = _exec_release_gate(tmp_path, monkeypatch, _all_five())
    assert code == 0
    monkeypatch.setenv("VERSION", "9.9.9-rc1")
    exec(  # noqa: S102 -- executing the renderer the release ships
        compile(_release_gate_body(RENDER_STEP, "VALUES_PY"), "<values renderer>", "exec"),
        {"__name__": "cap_values_renderer"},
    )
    rendered = yaml.safe_load(
        (tmp_path / "values-release-9.9.9-rc1.yaml").read_text("utf-8")
    )
    values = _values()
    coordinates = {
        "backend.image": rendered["backend"]["image"],
        "frontend.image": rendered["frontend"]["image"],
        "worker.sandbox.image": rendered["worker"]["sandbox"]["image"],
        "worker.sandbox.browserImage": rendered["worker"]["sandbox"]["browserImage"],
        "egressProxy.image": rendered["egressProxy"]["image"],
    }
    assert len(coordinates) == len(cap_image_names(chart_images()))
    for dotted, block in coordinates.items():
        assert block["digest"].startswith("sha256:"), f"{dotted}: no digest in the release values"
        assert block["tag"] == "9.9.9-rc1"
        assert _lookup(values, dotted + ".repository"), f"{dotted} is not a chart value path"
