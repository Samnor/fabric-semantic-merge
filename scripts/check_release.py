"""Validate release archives offline; never upload or obtain cloud credentials."""

import argparse
import ast
from email.parser import BytesParser
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
import tomllib
import zipfile


ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {
    ".gitignore",
    "AGENTS.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "MANIFEST.in",
    "README.md",
    "SECURITY.md",
    "pyproject.toml",
}
TREES = {"docs", "examples", "formal", "scripts", "src", "tests", ".github"}
PRIVATE_PARTS = {
    "runs",
    ".git",
    ".venv",
    "__pycache__",
    ".tools",
    "states",
    ".azure",
    "build",
    "dist",
    "release-local",
}
PRIVATE_CONTENT = (
    re.compile(rb"/" + rb"(?:Volumes|Users)/"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})"),
    re.compile(rb"eyJ[A-Za-z0-9_-]{15,}\.eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}"),
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def version(root):
    tree = ast.parse((root / "src/fabric_merge/__init__.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets
        ):
            value = ast.literal_eval(node.value)
            require(isinstance(value, str), "Version must be a literal string")
            return value
    raise ValueError("Missing package version")


def check_content(name, data):
    require(
        not any(p.search(data) for p in PRIVATE_CONTENT), f"Private content: {name}"
    )


def check_path(name):
    p = PurePosixPath(name)
    require(not p.is_absolute() and ".." not in p.parts, f"Unsafe path: {name}")
    require(not (set(p.parts) & PRIVATE_PARTS), f"Private path: {name}")
    require(
        not any(x.startswith(".env") for x in p.parts)
        and p.suffix not in {".pem", ".key", ".pyc", ".jar"}
        and p.name not in {".pypirc", "pilot.py", "test_pilot.py"},
        f"Excluded file: {name}",
    )


def public_files(root):
    for name in sorted(ROOT_FILES):
        p = root / name
        require(p.is_file() and not p.is_symlink(), f"Missing public file: {name}")
        yield p
    for name in sorted(TREES):
        for p in sorted((root / name).rglob("*")):
            rel = p.relative_to(root)
            if (
                set(rel.parts) & PRIVATE_PARTS
                or any(x.endswith(".egg-info") for x in rel.parts)
                or p.suffix == ".pyc"
            ):
                continue
            require(not p.is_symlink(), f"Public symlink: {rel}")
            if p.is_file():
                yield p


def metadata(data, expected):
    msg = BytesParser().parsebytes(data)
    require(msg["Name"] == "fabric-semantic-merge", "Distribution name mismatch")
    require(msg["Version"] == expected, "Distribution version mismatch")
    require(msg["Requires-Python"] == ">=3.11", "Python requirement mismatch")
    require(msg["License-Expression"] == "MIT", "Missing MIT metadata")
    require("LICENSE" in msg.get_all("License-File", []), "Missing license metadata")


def check(directory, root=ROOT, tag=None):
    expected = version(root)
    if tag is not None:
        require(tag == f"v{expected}", "Tag does not match package version")
    config = tomllib.loads((root / "pyproject.toml").read_text())
    require(config["project"]["license"] == "MIT", "License mismatch")
    for p in public_files(root):
        check_path(str(p.relative_to(root)))
        check_content(str(p.relative_to(root)), p.read_bytes())
    runtime = {
        str(p.relative_to(root / "src")): p.read_bytes()
        for p in sorted((root / "src/fabric_merge").glob("*.py"))
    }
    wheels = list(directory.glob("*.whl"))
    sdists = list(directory.glob("*.tar.gz"))
    require(len(wheels) == len(sdists) == 1, "Expected exactly one wheel and one sdist")
    require(
        wheels[0].name == f"fabric_semantic_merge-{expected}-py3-none-any.whl",
        "Unexpected wheel",
    )
    require(
        sdists[0].name == f"fabric_semantic_merge-{expected}.tar.gz", "Unexpected sdist"
    )
    dist_info = f"fabric_semantic_merge-{expected}.dist-info/"
    with zipfile.ZipFile(wheels[0]) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)), "Duplicate wheel members")
        require(
            set(names)
            == set(runtime)
            | {
                dist_info + n
                for n in [
                    "METADATA",
                    "WHEEL",
                    "entry_points.txt",
                    "top_level.txt",
                    "RECORD",
                    "licenses/LICENSE",
                ]
            },
            "Unexpected wheel contents",
        )
        for name in names:
            check_path(name)
            data = archive.read(name)
            check_content(name, data)
            if name in runtime:
                require(data == runtime[name], f"Wheel runtime differs: {name}")
        metadata(archive.read(dist_info + "METADATA"), expected)
        require(
            archive.read(dist_info + "licenses/LICENSE")
            == (root / "LICENSE").read_bytes(),
            "Wheel license differs",
        )
        require(
            b"fabric-merge = fabric_merge.cli:main"
            in archive.read(dist_info + "entry_points.txt"),
            "Missing CLI entry point",
        )
    with tarfile.open(sdists[0], "r:gz") as archive:
        entries = [m for m in archive.getmembers() if not m.isdir()]
        require(
            len({m.name for m in entries}) == len(entries), "Duplicate sdist members"
        )
        prefix = f"fabric_semantic_merge-{expected}/"
        members = {}
        for m in entries:
            require(m.isfile() and m.name.startswith(prefix), "Unexpected sdist member")
            name = m.name[len(prefix) :]
            check_path(name)
            p = PurePosixPath(name)
            require(
                name in {"PKG-INFO", "setup.cfg"}
                or name in ROOT_FILES
                or p.parts[0] in TREES,
                f"Unexpected sdist file: {name}",
            )
            data = archive.extractfile(m).read()
            check_content(name, data)
            members[name] = data
        for name, data in runtime.items():
            require(
                members.get("src/" + name) == data, f"Sdist runtime differs: {name}"
            )
        for name in [
            "LICENSE",
            "README.md",
            "pyproject.toml",
            "formal/Migration.tla",
            "scripts/check_release.py",
            "tests/test_distribution.py",
            "examples/left-definition-v2.json",
        ]:
            require(
                members.get(name) == (root / name).read_bytes(),
                f"Sdist missing/different: {name}",
            )
        metadata(members.get("PKG-INFO", b""), expected)
        require(
            members.get("setup.cfg") == b"[egg_info]\ntag_build = \ntag_date = 0\n\n",
            "Unexpected generated setup.cfg",
        )
    return {
        "status": "passed",
        "version": expected,
        "runtime_modules": len(runtime),
        "wheel": wheels[0].name,
        "sdist": sdists[0].name,
        "cloud_requests": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--tag")
    args = parser.parse_args()
    try:
        print(json.dumps(check(args.directory, tag=args.tag), indent=2))
    except (ValueError, OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        parser.exit(1, f"Release check failed: {exc}\n")


if __name__ == "__main__":
    main()
