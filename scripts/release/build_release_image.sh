#!/usr/bin/env bash
# Build (and optionally publish) one CAP release image, and record what was built.
#
# Why this exists next to docker/build-push-action: three of the five release
# images (the two sandboxes and the egress proxy) build from a *staged* context
# that has to be assembled first, and the browser image layers on the HTTP
# sandbox image the same run produced. A matrix cell cannot express either of
# those, and the release evidence has to be the same shape for all five images
# or the completeness gate ends up checking two different formats.
#
# The evidence file is the point: a release image without a recorded index
# digest, platform digest, Dockerfile SHA, base digests and context hash cannot
# be re-built or audited, which is exactly what F-7 left unanswerable for the
# images the release never published.
#
# BATCH 3 A2.2 changed *what runs the build*. Until now this script called
# `docker buildx build`, which dispatches to whichever buildx plugin the runner
# image happens to carry -- F-44, measured at five heads and non-deterministic
# at three of them. The build command is now
#
#     <absolute path the job installed> build --builder <the name this file declares>
#
# installed by scripts/release/install_controlled_buildx.py, and the argv is
# written down before it runs so the producer record can be contradicted by it.
# `--local-docker` remains for a developer who wants a docker-driver build on a
# machine of their own; no job that builds a release image uses it, and its
# record says the daemon's BuildKit built those bytes rather than pretending
# otherwise.
#
# Usage:
#   build_release_image.sh --name cap-sandbox-http --role http \
#       --dockerfile backend/docker/sandbox-http/Dockerfile \
#       --registry ghcr.io/owner --version 1.0.6-rc1 --revision <sha> \
#       --buildx /tmp/cap-controlled-buildx/buildx --builder cap-release-producer \
#       --workflow .github/workflows/release.yml --job release-images \
#       --out outputs/release-images/cap-sandbox-http.json [--push] \
#       [--build-arg KEY=VALUE ...] [--named-context NAME=REF] [--oci-out FILE]
#
#   --role        sandbox role staged by prepare_sandbox_context.sh
#                 (http | browser | egress-proxy); omit for a plain directory
#   --context     build context directory, required when --role is absent
#   --buildx      the absolute path of the controlled buildx to run (required
#                 unless --local-docker)
#   --builder     the builder name to build with (required unless --local-docker)
#   --workflow    the workflow file whose env: block declares that builder, so
#                 `configured.workflow` stays a claim about the repository
#   --job         which job of that file this build belongs to
#   --producer-pin the executable pin (defaults to scripts/release/controlled_buildx.json)
#   --named-context NAME=REF  passed to build as --build-context
#   --oci-out FILE  export `type=oci` to FILE instead of --load, for a dry build
#                 whose bytes have to hand a base to another build with no
#                 registry and no host store in the path
#   --push        push the image and attach SBOM + provenance attestations
#   --local-docker  build with the docker driver instead of buildx, for a dry
#                 build that may layer on a locally-built CAP base (a buildx
#                 container builder cannot see the host docker store). No CI
#                 job uses this any more: the pinned producer reaches the same
#                 bytes through a digest-bound named context.
#
# The producer record, the argv the build ran with, and the recorder's own log
# go beside `--out` under a `producer/` subdirectory, and the record is embedded
# in the evidence file the gate reads. They are kept out of the directory the
# gate globs on purpose: a second `*.json` in there reads as a second image.

set -euo pipefail

NAME="" ROLE="" DOCKERFILE="" CONTEXT="" REGISTRY="" VERSION="" REVISION="" OUT=""
BUILDX="" BUILDER="" WORKFLOW="" JOB="" OCI_OUT=""
PRODUCER_PIN="scripts/release/controlled_buildx.json"
PUSH=0
LOCAL_DOCKER=0
INDEX_DIGEST="" CONFIG_DIGEST="" CONTEXT_SHA="" DOCKERFILE_SHA="" BASES=""
# Both build drivers leave this empty unless they actually resolved a child
# manifest, and `set -u` makes "unless" load-bearing: the buildx path has no
# other assignment, so omitting the default crashed every non-local build
# (CI saw it as `PLATFORM_DIGEST: unbound variable` on the http base build).
PLATFORM_DIGEST=""
PLATFORM_NAME=""
EXTRA_ARGS=()
NAMED_CONTEXTS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name) NAME="$2"; shift 2 ;;
    --role) ROLE="$2"; shift 2 ;;
    --dockerfile) DOCKERFILE="$2"; shift 2 ;;
    --context) CONTEXT="$2"; shift 2 ;;
    --registry) REGISTRY="$2"; shift 2 ;;
    --version) VERSION="$2"; shift 2 ;;
    --revision) REVISION="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    --buildx) BUILDX="$2"; shift 2 ;;
    --builder) BUILDER="$2"; shift 2 ;;
    --workflow) WORKFLOW="$2"; shift 2 ;;
    --job) JOB="$2"; shift 2 ;;
    --producer-pin) PRODUCER_PIN="$2"; shift 2 ;;
    --oci-out) OCI_OUT="$2"; shift 2 ;;
    --named-context) NAMED_CONTEXTS+=("--build-context" "$2"); shift 2 ;;
    --build-arg) EXTRA_ARGS+=("--build-arg" "$2"); shift 2 ;;
    --push) PUSH=1; shift ;;
    --local-docker) LOCAL_DOCKER=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

for required in NAME DOCKERFILE VERSION REVISION OUT; do
  [[ -n "${!required}" ]] || { echo "--${required,,} is required" >&2; exit 2; }
done

if [[ "$LOCAL_DOCKER" != "1" ]]; then
  # Fail closed on the producer rather than falling back to the runner. A build that quietly
  # went back to `docker buildx` when its caller forgot a flag would produce the record F-44 was
  # filed over -- a producer that looks pinned because the YAML says so.
  for required in BUILDX BUILDER WORKFLOW JOB; do
    [[ -n "${!required}" ]] \
      || { echo "--${required,,} is required unless --local-docker" >&2; exit 2; }
  done
  # The rule is "name the file, do not let PATH decide", so a POSIX absolute path and a
  # drive-absolute one are both accepted: CI runs the first, a developer's Git Bash the second,
  # and a bare `buildx` is neither.
  if [[ ! "$BUILDX" =~ ^(/|[A-Za-z]:[/\\]) ]]; then
    echo "--buildx must be an absolute executable path, not a name resolved through PATH" >&2
    exit 2
  fi
  # The same refusal the installer makes, restated at the point of use: this script is the thing
  # that would run the build, so it is the last place a plugin dispatch can be turned away.
  case "$BUILDX" in
    */cli-plugins/*|*/docker-buildx)
      echo "--buildx points at a docker CLI plugin path: the release build runs the installed " \
           "executable, not whatever the runner's plugin dispatch would resolve to" >&2
      exit 2
      ;;
  esac
  [[ -x "$BUILDX" ]] \
    || { echo "no executable at $BUILDX -- install it with scripts/release/install_controlled_buildx.py" \
           >&2; exit 1; }
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"
[[ -f "$DOCKERFILE" ]] || { echo "no such Dockerfile: $DOCKERFILE" >&2; exit 1; }
if [[ "$LOCAL_DOCKER" != "1" ]]; then
  # Checked from here rather than where the arguments were parsed: both paths are relative to the
  # repository this script lives in, and a caller running it from another directory would
  # otherwise be told its pin is missing when only its cwd was.
  [[ -f "$PRODUCER_PIN" ]] \
    || { echo "no producer pin at $PRODUCER_PIN, so nothing declares what the executable is" \
           >&2; exit 1; }
  [[ -f "$WORKFLOW" ]] \
    || { echo "no workflow file at $WORKFLOW: configured.workflow has to be read from the file" \
           >&2; exit 1; }
fi

CLEANUP=""
if [[ -n "$ROLE" ]]; then
  CONTEXT="$(mktemp -d)"
  CLEANUP="$CONTEXT"
  bash backend/docker/prepare_sandbox_context.sh "$CONTEXT" "$ROLE"
else
  [[ -n "$CONTEXT" ]] || { echo "--context is required without --role" >&2; exit 2; }
fi
trap '[[ -n "$CLEANUP" ]] && rm -rf "$CLEANUP"' EXIT

REF="${REGISTRY:+${REGISTRY}/}${NAME}:${VERSION}"

# What went in, before anything can be argued to have changed it.
DOCKERFILE_SHA="$(sha256sum "$DOCKERFILE" | cut -d' ' -f1)"
# From *inside* the context: `sha256sum` prints the path it was given, and the
# staging directory is a fresh `mktemp -d` every run, so hashing with absolute
# paths made context_sha256 different for two builds of the same commit -- the
# field would then record which temporary directory was used, not which files
# went in, and could not show a difference anyone cared about.
CONTEXT_SHA="$(cd "$CONTEXT" && find . -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | cut -d' ' -f1)"
# A `FROM ${VAR}` base resolves from the Dockerfile's ARG default, and a caller's
# --build-arg wins over that default: the release names the browser image's base
# as the digest it just published, and the evidence has to record *that*.
BASES="$(python3 - "$DOCKERFILE" ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} <<'PY'
import re
import sys

dockerfile, *builder_args = sys.argv[1:]
text = open(dockerfile, encoding="utf-8").read()
args = dict(re.findall(r"^ARG\s+(\w+)=(\S+)", text, re.MULTILINE))
for token in builder_args:
    if token == "--build-arg" or "=" not in token:
        continue
    name, value = token.split("=", 1)
    args[name] = value
bases = []
for ref in re.findall(r"^FROM\s+(\S+)", text, re.MULTILINE):
    if ref.startswith("${"):
        name = ref[2:-1]
        ref = args.get(name, "")
        if not ref:
            sys.exit(f"{name} resolves nothing: this image's base has to be named")
    bases.append(ref)
print("\n".join(bases))
PY
)"

BUILD_ARGS=(--build-arg "VERSION=${VERSION}" --build-arg "REVISION=${REVISION}")
if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
  BUILD_ARGS+=("${EXTRA_ARGS[@]}")
fi
if [[ ${#NAMED_CONTEXTS[@]} -gt 0 ]]; then
  BUILD_ARGS+=("${NAMED_CONTEXTS[@]}")
fi

OUT_PARENT="$(dirname "$OUT")"
mkdir -p "$OUT_PARENT"
# Beside the evidence, not in it: the completeness gate reads `*.json` in the evidence directory,
# and a second file in there with an `image` field reads as a second image of the same name.
PRODUCER_DIR="$OUT_PARENT/producer"
mkdir -p "$PRODUCER_DIR"
BASENAME="$(basename "$OUT")"
BUILD_COMMAND_FILE="$PRODUCER_DIR/$BASENAME.build-command.json"

# The same instrument the observation job uses: write the argv down, run it, then put the exit
# status into the same file. This is what turns "the controlled executable built these bytes"
# from a sentence this script asserts about itself into a claim the recorder can contradict --
# an argv headed `docker` is a plugin dispatch and reads as a disagreement even when every
# declared value agrees with the pins.
record_and_run() {
  python3 - "$BUILD_COMMAND_FILE" "$NAME" "$@" <<'PY'
import json
import sys
out_path, image, *argv = sys.argv[1:]
with open(out_path, "w", encoding="utf-8") as handle:
    json.dump({"image": image, "argv": argv, "exit": None}, handle)
PY
  local status=0
  "$@" || status=$?
  python3 - "$BUILD_COMMAND_FILE" "$status" <<'PY'
import json
import sys
path, status = sys.argv[1], int(sys.argv[2])
with open(path, encoding="utf-8") as handle:
    doc = json.load(handle)
doc["exit"] = status
with open(path, "w", encoding="utf-8") as handle:
    json.dump(doc, handle)
PY
  return $status
}

if [[ "$LOCAL_DOCKER" == "1" ]]; then
  # The docker driver resolves `FROM <local-image>` from the host store, which is the only way to
  # build a browser image on top of an HTTP sandbox image that exists nowhere but this machine.
  # No CI job uses this any more -- a buildx container builder reaches the same bytes through a
  # digest-bound named context -- so what is left here is a developer path, and its evidence says
  # the daemon's BuildKit built it rather than naming a producer that never ran.
  record_and_run docker build "${BUILD_ARGS[@]}" --tag "$REF" --file "$DOCKERFILE" "$CONTEXT" \
    || { echo "docker build failed for $NAME" >&2; exit 1; }
  INDEX_DIGEST=""
  CONFIG_DIGEST="$(docker image inspect "$REF" --format '{{.Id}}')"
  PLATFORM_NAME="$(docker image inspect "$REF" --format '{{.Os}}/{{.Architecture}}')"
  echo "built $REF locally with the docker driver ($CONFIG_DIGEST)"
else
  if [[ "$PUSH" == "1" ]]; then
    # Attestations belong to what the registry serves; a dry build has no
    # attestation subject, and buildx refuses to load one into the docker store.
    BUILD_ARGS+=(--provenance=true --sbom=true --push)
  elif [[ -n "$OCI_OUT" ]]; then
    BUILD_ARGS+=(--provenance=false --sbom=false --output "type=oci,dest=$OCI_OUT")
  else
    BUILD_ARGS+=(--provenance=false --sbom=false --load)
  fi
  META="$(mktemp -d)/metadata.json"
  record_and_run "$BUILDX" build --builder "$BUILDER" "${BUILD_ARGS[@]}" \
    --tag "$REF" --file "$DOCKERFILE" --metadata-file "$META" "$CONTEXT" \
    || { echo "$BUILDX build failed for $NAME" >&2; exit 1; }
  INDEX_DIGEST="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['containerimage.digest'])" "$META")"
  CONFIG_DIGEST="$(python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print(d.get('containerimage.config.digest',''))" "$META")"
  if [[ "$PUSH" != "1" && -z "$OCI_OUT" ]]; then
    # `--load` put these bytes in the local store, so the platform is knowable here
    # exactly as it is for the docker driver. Leaving it blank made two records of
    # the same commit disagree about a field a reader compares side by side. An
    # `--output type=oci` build loads nothing, so there is no store entry to ask; that
    # record says so by naming the archive it did produce.
    PLATFORM_NAME="$(docker image inspect "$REF" --format '{{.Os}}/{{.Architecture}}')"
  fi
fi

if [[ "$PUSH" == "1" ]]; then
  # The child manifest for the platform this project supports. The index digest
  # pins the image; the platform digest is what a node actually pulls, and a
  # single-arch child must be recorded as such rather than guessed later. The
  # same read names the platform it picked: a released record that states a
  # digest but leaves `platform` blank while a dry record fills it is a field
  # whose meaning depends on which path built it.
  #
  # Read with the executable that built it, not through `docker buildx`: an
  # registry read done by a different CLI than the one that produced the bytes
  # is a second producer in one evidence file, which is what A2.2 removes.
  MANIFEST_JSON="$("$BUILDX" imagetools inspect "$REF" --format '{{json .Manifest}}')"
  PARSED_PLATFORM="$(printf '%s' "$MANIFEST_JSON" | python3 -c '
import json, sys
manifest = json.loads(sys.stdin.read())
entries = manifest.get("manifests") or []
if not entries:
    print("SINGLE_MANIFEST\t")
else:
    wanted = [e for e in entries if (e.get("platform") or {}).get("os") == "linux"
              and (e.get("platform") or {}).get("architecture") == "amd64"]
    if wanted:
        child = wanted[0]
        platform = child.get("platform") or {}
        print("%s\t%s/%s" % (child["digest"], platform.get("os"), platform.get("architecture")))
    else:
        print("NO_AMD64_CHILD\t")
')"
  PLATFORM_DIGEST="${PARSED_PLATFORM%%$'\t'*}"
  PLATFORM_NAME="${PARSED_PLATFORM#*$'\t'}"
fi

# B2 pinned the builder in YAML and this says what ran, with the two kept in separate objects --
# an "actual" value the script wrote itself would be a restatement, not evidence. A2.2 added the
# half B2 could not reach: the release build now runs under a named builder and an executable this
# repository pins, so the recorder is *given* that builder name, that pin, that workflow file and
# that job, and reads the builder, the container, the registry and the CLI back for itself.
# `--local-docker` is the one path with no BuildKit container to read, and the recorder says so
# rather than inventing one: `docker build` uses the daemon's embedded BuildKit, so the builder
# installed in the job is not the producer and asking its container for a digest would report a
# true answer to a wrong question.
PRODUCER_FILE="$PRODUCER_DIR/$BASENAME.producer.json"
PRODUCER_LOG="$PRODUCER_DIR/$BASENAME.producer.log"
PRODUCER_ARGS=(--out "$PRODUCER_FILE" --lock deployment/third-party-images.json --self-check
               --image "$NAME" --revision "$REVISION" --dockerfile "$DOCKERFILE"
               --build-command "$BUILD_COMMAND_FILE")
if [[ "$LOCAL_DOCKER" == "1" ]]; then
  PRODUCER_ARGS+=(--docker-cli-build)
else
  PRODUCER_ARGS+=(--builder "$BUILDER" --controlled-pin "$PRODUCER_PIN"
                  --workflow "$WORKFLOW" --job "$JOB")
fi
if [[ -n "$OCI_OUT" ]]; then
  PRODUCER_ARGS+=(--oci-tar "$OCI_OUT")
fi
PRODUCER_CODE=0
python3 scripts/release/record_build_producer.py "${PRODUCER_ARGS[@]}" \
  > "$PRODUCER_LOG" 2>&1 || PRODUCER_CODE=$?
if [[ "$PRODUCER_CODE" == "0" ]]; then
  CAP_EVIDENCE_PRODUCER_FILE="$PRODUCER_FILE"
else
  # `--self-check` is non-zero for a read the instrument promised and could not make -- a broken
  # observation, never for a measurement that came back unwelcome. On the release path that is a
  # failed job: an image whose producer cannot be named must not reach the completeness gate at
  # all. `--local-docker` is a developer's own build, claims no pinned producer, and stays a
  # warning that its evidence repeats.
  echo "the producer record is incomplete (recorder exit $PRODUCER_CODE); see $PRODUCER_LOG" \
    >&2
  tail -n 20 "$PRODUCER_LOG" >&2 || true
  CAP_EVIDENCE_PRODUCER_FILE=""
fi
CAP_EVIDENCE_OUT="$OUT" CAP_EVIDENCE_NAME="$NAME" CAP_EVIDENCE_REF="$REF" \
CAP_EVIDENCE_REGISTRY="$REGISTRY" CAP_EVIDENCE_TAG="$VERSION" CAP_EVIDENCE_PUSHED="$PUSH" \
CAP_EVIDENCE_INDEX="$INDEX_DIGEST" CAP_EVIDENCE_CONFIG="$CONFIG_DIGEST" \
CAP_EVIDENCE_PLATFORM="$PLATFORM_DIGEST" CAP_EVIDENCE_DOCKERFILE="$DOCKERFILE" \
CAP_EVIDENCE_DRIVER="$([[ "$LOCAL_DOCKER" == "1" ]] && echo docker || echo buildx)" \
CAP_EVIDENCE_PLATFORM_NAME="$PLATFORM_NAME" \
CAP_EVIDENCE_DOCKERFILE_SHA="$DOCKERFILE_SHA" \
CAP_EVIDENCE_CONTEXT_SHA="$CONTEXT_SHA" CAP_EVIDENCE_BASES="$(printf '%s\n' "$BASES")" \
CAP_EVIDENCE_REVISION="$REVISION" \
CAP_EVIDENCE_PRODUCER_FILE="$CAP_EVIDENCE_PRODUCER_FILE" \
CAP_EVIDENCE_PRODUCER_RECORD="$PRODUCER_FILE" \
CAP_EVIDENCE_PRODUCER_CODE="$PRODUCER_CODE" \
CAP_EVIDENCE_BUILDER="$BUILDER" CAP_EVIDENCE_BUILDX="$BUILDX" \
CAP_EVIDENCE_WORKFLOW="$WORKFLOW" CAP_EVIDENCE_JOB="$JOB" \
CAP_EVIDENCE_OCI_ARCHIVE="$OCI_OUT" \
CAP_EVIDENCE_BUILD_ARGS="${BUILD_ARGS[*]}" \
python3 - <<'PY'
import json
import os

env = os.environ
pushed = env["CAP_EVIDENCE_PUSHED"] == "1"
build_args = env.get("CAP_EVIDENCE_BUILD_ARGS", "").split()


def or_none(name: str) -> str | None:
    return env.get(name) or None


evidence = {
    "image": env["CAP_EVIDENCE_NAME"],
    "ref": env["CAP_EVIDENCE_REF"] if pushed else f"{env['CAP_EVIDENCE_NAME']}:dry-build",
    "registry": or_none("CAP_EVIDENCE_REGISTRY"),
    "repository": env["CAP_EVIDENCE_NAME"],
    "tag": env["CAP_EVIDENCE_TAG"],
    "pushed": pushed,
    "index_digest": or_none("CAP_EVIDENCE_INDEX"),
    "config_digest": or_none("CAP_EVIDENCE_CONFIG"),
    "platform_digest_linux_amd64": or_none("CAP_EVIDENCE_PLATFORM"),
    "build_driver": env["CAP_EVIDENCE_DRIVER"],
    "platform": or_none("CAP_EVIDENCE_PLATFORM_NAME"),
    "dockerfile": env["CAP_EVIDENCE_DOCKERFILE"],
    "dockerfile_sha256": env["CAP_EVIDENCE_DOCKERFILE_SHA"],
    "context_sha256": env["CAP_EVIDENCE_CONTEXT_SHA"],
    "base_refs": [line for line in env["CAP_EVIDENCE_BASES"].splitlines() if line],
    "source_revision": env["CAP_EVIDENCE_REVISION"],
    # A2.2: which producer this record says ran, in the image's own words. `producer` below is
    # the read-back; these name the claim it is a read-back *of*, so a reader can tell a build
    # that named a builder from one that fell through to the runner's plugin, and can find the
    # standalone record and the recorder's own complaint when the read did not complete.
    "builder_named": or_none("CAP_EVIDENCE_BUILDER"),
    "buildx_executable": or_none("CAP_EVIDENCE_BUILDX"),
    "workflow": or_none("CAP_EVIDENCE_WORKFLOW"),
    "workflow_job": or_none("CAP_EVIDENCE_JOB"),
    "oci_archive": or_none("CAP_EVIDENCE_OCI_ARCHIVE"),
    "producer_record": or_none("CAP_EVIDENCE_PRODUCER_RECORD"),
    "producer_record_exit": int(env.get("CAP_EVIDENCE_PRODUCER_CODE") or 0),
    # Absent producer is a finding, not a blank: the release gate refuses a pushed image whose
    # producer was never read, and says so as MISSING rather than as a field it cannot parse.
    "producer": (json.load(open(env["CAP_EVIDENCE_PRODUCER_FILE"], encoding="utf-8"))
                 if env.get("CAP_EVIDENCE_PRODUCER_FILE") else {"recorded": False}),
    "attestations": {
        "sbom": "--sbom=true" in build_args,
        "provenance": "--provenance=true" in build_args,
    },
}
path = env["CAP_EVIDENCE_OUT"]
with open(path, "w", encoding="utf-8") as handle:
    json.dump(evidence, handle, indent=2)
    handle.write("\n")
print(json.dumps(evidence, indent=2))
PY

if [[ "$LOCAL_DOCKER" != "1" && "$PRODUCER_CODE" != "0" ]]; then
  # Written first, then failed: the evidence and the recorder's log are what an operator reads to
  # find out which read was lost, and a job that exits before writing them leaves only a red tick.
  echo "FAIL: $NAME was built but its producer could not be fully recorded (recorder exit " \
"$PRODUCER_CODE)" >&2
  exit 1
fi
