#!/usr/bin/env python3
"""Name the image inside an OCI archive or layout directory, so a base can be bound without a registry.

Two CAP images are built on top of another CAP image: `cap-sandbox-browser` on
`cap-sandbox-http`. The release path binds that base to the digest it just pushed, which is the
real dependency and stays that way. The non-publishing paths (the observation job, and the CI
dry-run of the release images) push nothing and cannot see the host docker store from a
`docker-container` builder, so the base has to reach the builder another way: as a named context
addressed by the digest of the archive this same round produced.

Either way the digest has to be *read*, and this is the one place that reads it:

* an attestation descriptor is not the image, so it is excluded the way BuildKit's own
  `vnd.docker.reference.type` annotation says;
* more than one remaining candidate is AMBIGUOUS and refused, not picked -- choosing "the first
  manifest" from a multi-platform archive would bind a base to whichever architecture happened to
  sort first, which is the class of mistake F-34 was filed over;
* nothing remaining is a stated failure with what the index did carry, because an empty answer
  printed as an empty string would become a build argument with no value.

Exit 0 and one digest on stdout, or exit 1 with the reason on stderr. Nothing else is printed, so
the caller can use it in a command substitution and still see the complaint.
"""

from __future__ import annotations

import argparse
import json
import sys
import tarfile
from pathlib import Path

INDEX_NAME = "index.json"
ATTESTATION_ANNOTATION = "vnd.docker.reference.type"
ATTESTATION_VALUE = "attestation-manifest"


def read_index(path: Path) -> dict:
    """The `index.json` of an OCI layout directory or of an archive, without unpacking either."""
    if path.is_dir():
        candidate = path / INDEX_NAME
        if not candidate.is_file():
            raise SystemExit(f"{path} is a directory with no {INDEX_NAME} in it")
        return json.loads(candidate.read_text(encoding="utf-8"))
    if path.is_file():
        with tarfile.open(path) as archive:
            for member in archive.getmembers():
                if member.name.removeprefix("./") == INDEX_NAME and member.isfile():
                    handle = archive.extractfile(member)
                    if handle is not None:
                        return json.loads(handle.read())
        raise SystemExit(f"{path} carried no {INDEX_NAME}")
    raise SystemExit(f"no OCI layout directory or archive at {str(path)!r}")


def image_manifests(index: dict) -> tuple[list[dict], list[dict]]:
    """Split what an index names into the images and the attestations about them."""
    images: list[dict] = []
    attestations: list[dict] = []
    for entry in index.get("manifests") or []:
        annotations = entry.get("annotations") or {}
        if annotations.get(ATTESTATION_ANNOTATION) == ATTESTATION_VALUE:
            attestations.append(entry)
        else:
            images.append(entry)
    return images, attestations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", help="an OCI layout directory, or an oci/ docker-archive tarball")
    args = parser.parse_args(argv)

    index = read_index(Path(args.path))
    images, attestations = image_manifests(index)
    seen = [entry.get("digest") for entry in images]
    if not images:
        print(f"{args.path}: the index names no image manifest "
              f"(attestations present: {len(attestations)})", file=sys.stderr)
        return 1
    if len(images) > 1:
        # AMBIGUOUS, stated as such: an archive with more than one image manifest needs the
        # caller to say which platform it means, and silently picking one would bind a base to
        # bytes its reader cannot name.
        print(f"AMBIGUOUS: {args.path} names {len(images)} image manifests, not one: "
              f"{seen}", file=sys.stderr)
        return 1
    digest = images[0].get("digest")
    if not digest:
        print(f"{args.path}: its one image manifest carries no digest", file=sys.stderr)
        return 1
    print(digest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
