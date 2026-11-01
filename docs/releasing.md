# GitHub distribution

The distribution is `fabric-semantic-merge`, the Python import is `fabric_merge`, and the command is `fabric-merge`. Users install from `Samnor/fabric-semantic-merge` with pip. The public repository is the distribution source; no package-index upload is needed.

## Install

Python 3.11+ and Git must be installed:

```sh
python -m pip install 'git+https://github.com/Samnor/fabric-semantic-merge.git'
fabric-merge --version
```

This installs the current default branch. Pin a reviewed full commit SHA for repeatability:

```sh
python -m pip install 'git+https://github.com/Samnor/fabric-semantic-merge.git@COMMIT_SHA'
```

A version tag can also be used after that tag is published, for example `@v0.1.0`. From a local checkout, `python -m pip install .` works. Azure CLI is a separate prerequisite only for live Fabric commands. The [pip VCS documentation](https://pip.pypa.io/en/stable/topics/vcs-support/) describes supported Git URL forms.

## Verify the public tree

```sh
python -m pip install -e '.[dev]'
python -B -m unittest discover -s tests -t . -v
ruff check src tests scripts formal/check.py
ruff format --check src tests scripts formal/check.py
python -m build --outdir runs/release-NEW/dist
python -m twine check --strict runs/release-NEW/dist/*
python scripts/check_release.py runs/release-NEW/dist
```

The archives provide an additional packaging check; users can install directly from Git without downloading a release asset. The guard rejects private run paths, unexpected archive files, stale runtime bytes and inconsistent metadata. It detects common secret/local-path patterns, not every possible secret. Review the exact candidate Git file set before committing. Keep private run folders, tenant receipts, credentials and the lab-specific runner outside the public repository.

CI runs offline tests and packaging checks, including a fresh pip installation from the exact checked-out Git commit and an offline merge. It obtains no cloud/publishing credentials and changes no Fabric resources. Configured remote checks are not reported as passed until they actually run.

## First publication

After reviewing the public file set and MIT license, create the first commit and publish an empty public repository using GitHub CLI:

```sh
git add .gitignore AGENTS.md CHANGELOG.md CONTRIBUTING.md LICENSE MANIFEST.in README.md SECURITY.md pyproject.toml .github docs examples formal scripts src tests
git diff --cached --stat
git diff --cached --check
git commit -m 'Prepare initial open source release'
gh repo create Samnor/fabric-semantic-merge --public --source=. --remote=origin --push
```

If the repository already exists, inspect it and add its remote instead of creating another. Enable private vulnerability reporting and protect main with required CI. Commits, pushes, repository creation and settings changes are explicit maintainer actions; local preparation does not perform them.

After publication, verify the HTTPS URL in a fresh environment outside the checkout, run `fabric-merge --version` and an offline merge, then record the installed commit SHA. To mark a reviewed version, tag its commit `v0.1.0`; never move or reuse a published version tag. Keep the package version and changelog current.
