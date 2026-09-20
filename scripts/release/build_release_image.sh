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
# Usage:
#   build_release_image.sh --name cap-sandbox-http --role http \
#       --dockerfile backend/docker/sandbox-http/Dockerfile \
#       --registry ghcr.io/owner --version 1.0.6-rc1 --revision <sha> \
#       --out outputs/release-images/cap-sandbox-http.json [--push] \
#       [--build-arg KEY=VALUE ...]
#
#   --role        sandbox role staged by prepare_sandbox_context.sh
#                 (http | browser | egress-proxy); omit for a plain directory
#   --context     build context directory, required when --role is absent
#   --push        push the image and attach SBOM + provenance attestations
#   --local-docker  build with the docker driver instead of buildx, for a dry
#                 build that may layer on a locally-built CAP base (a buildx
#                 container builder cannot see the host docker store, so CI uses
#                 this mode and the release uses --push with a registry digest)

set -euo pipefail

NAME="" ROLE="" DOCKERFILE="" CONTEXT="" REGISTRY="" VERSION="" REVISION="" OUT=""
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
    --build-arg) EXTRA_ARGS+=("--build-arg" "$2"); shift 2 ;;
    --push) PUSH=1; shift ;;
    --local-docker) LOCAL_DOCKER=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

for required in NAME DOCKERFILE VERSION REVISION OUT; do
  [[ -n "${!required}" ]] || { echo "--${required,,} is required" >&2; exit 2; }
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"
[[ -f "$DOCKERFILE" ]] || { echo "no such Dockerfile: $DOCKERFILE" >&2; exit 1; }

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

if [[ "$LOCAL_DOCKER" == "1" ]]; then
  # The docker driver resolves `FROM <local-image>` from the host store, which is
  # the only way a browser image can be built here on top of an HTTP sandbox image
  # that exists nowhere but this runner. No attestation, no push, no index digest:
  # what this mode proves is buildability, and it says so in its own evidence.
  docker build "${BUILD_ARGS[@]}" --tag "$REF" --file "$DOCKERFILE" "$CONTEXT"
  INDEX_DIGEST=""
  CONFIG_DIGEST="$(docker image inspect "$REF" --format '{{.Id}}')"
  PLATFORM_NAME="$(docker image inspect "$REF" --format '{{.Os}}/{{.Architecture}}')"
  echo "built $REF locally with the docker driver ($CONFIG_DIGEST)"
else
  if [[ "$PUSH" == "1" ]]; then
    # Attestations belong to what the registry serves; a dry build has no
    # attestation subject, and buildx refuses to load one into the docker store.
    BUILD_ARGS+=(--provenance=true --sbom=true --push)
  else
    BUILD_ARGS+=(--provenance=false --sbom=false --load)
  fi
  META="$(mktemp -d)/metadata.json"
  docker buildx build "${BUILD_ARGS[@]}" --tag "$REF" --file "$DOCKERFILE" \
    --metadata-file "$META" "$CONTEXT"
  INDEX_DIGEST="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['containerimage.digest'])" "$META")"
  CONFIG_DIGEST="$(python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print(d.get('containerimage.config.digest',''))" "$META")"
  if [[ "$PUSH" != "1" ]]; then
    # `--load` put these bytes in the local store, so the platform is knowable here
    # exactly as it is for the docker driver. Leaving it blank made two records of
    # the same commit disagree about a field a reader compares side by side.
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
  MANIFEST_JSON="$(docker buildx imagetools inspect "$REF" --format '{{json .Manifest}}')"
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

mkdir -p "$(dirname "$OUT")"
CAP_EVIDENCE_OUT="$OUT" CAP_EVIDENCE_NAME="$NAME" CAP_EVIDENCE_REF="$REF" \
CAP_EVIDENCE_REGISTRY="$REGISTRY" CAP_EVIDENCE_TAG="$VERSION" CAP_EVIDENCE_PUSHED="$PUSH" \
CAP_EVIDENCE_INDEX="$INDEX_DIGEST" CAP_EVIDENCE_CONFIG="$CONFIG_DIGEST" \
CAP_EVIDENCE_PLATFORM="$PLATFORM_DIGEST" CAP_EVIDENCE_DOCKERFILE="$DOCKERFILE" \
CAP_EVIDENCE_DRIVER="$([[ "$LOCAL_DOCKER" == "1" ]] && echo docker || echo buildx)" \
CAP_EVIDENCE_PLATFORM_NAME="$PLATFORM_NAME" \
CAP_EVIDENCE_DOCKERFILE_SHA="$DOCKERFILE_SHA" \
CAP_EVIDENCE_CONTEXT_SHA="$CONTEXT_SHA" CAP_EVIDENCE_BASES="$(printf '%s\n' "$BASES")" \
CAP_EVIDENCE_REVISION="$REVISION" \
CAP_EVIDENCE_BUILD_ARGS="${BUILD_ARGS[*]}" \
python3 - <<'PY'
import json
import os

env = os.environ
pushed = env["CAP_EVIDENCE_PUSHED"] == "1"
build_args = env.get("CAP_EVIDENCE_BUILD_ARGS", "").split()
evidence = {
    "image": env["CAP_EVIDENCE_NAME"],
    "ref": env["CAP_EVIDENCE_REF"] if pushed else f"{env['CAP_EVIDENCE_NAME']}:dry-build",
    "registry": env.get("CAP_EVIDENCE_REGISTRY") or None,
    "repository": env["CAP_EVIDENCE_NAME"],
    "tag": env["CAP_EVIDENCE_TAG"],
    "pushed": pushed,
    "index_digest": env["CAP_EVIDENCE_INDEX"] or None,
    "config_digest": env["CAP_EVIDENCE_CONFIG"] or None,
    "platform_digest_linux_amd64": env["CAP_EVIDENCE_PLATFORM"] or None,
    "build_driver": env["CAP_EVIDENCE_DRIVER"],
    "platform": env["CAP_EVIDENCE_PLATFORM_NAME"] or None,
    "dockerfile": env["CAP_EVIDENCE_DOCKERFILE"],
    "dockerfile_sha256": env["CAP_EVIDENCE_DOCKERFILE_SHA"],
    "context_sha256": env["CAP_EVIDENCE_CONTEXT_SHA"],
    "base_refs": [line for line in env["CAP_EVIDENCE_BASES"].splitlines() if line],
    "source_revision": env["CAP_EVIDENCE_REVISION"],
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
