# Contributing

Use Python 3.11+ and a virtual environment. No Azure account is needed for development.

```sh
git clone https://github.com/Samnor/fabric-semantic-merge.git
cd fabric-semantic-merge
python3 -m venv .venv
# macOS/Linux; Windows PowerShell: .venv\Scripts\Activate.ps1
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -B -m unittest discover -s tests -t . -v
ruff check src tests scripts formal/check.py
ruff format --check src tests scripts formal/check.py
python -m build
python -m twine check --strict dist/*
python scripts/check_release.py dist
```

The tests use synthetic fixtures and loopback HTTP. Never add live credentials, model exports, tenant identities, business data or cloud workload execution to CI. Keep diagnostic plans and journals under ignored `runs/`. Sanitize examples and error messages before posting an issue.

Use four-space indentation, snake_case and Ruff formatting. Add meaningful regression coverage when changing migration, security, recovery or evidence behavior. A change to a request body, operation identity, journal transition or verification contract needs an explanation of uncertain-outcome behavior and backwards compatibility. Do not retry a mutation automatically.

Submit a focused pull request describing the problem, resulting behavior, validation and remaining limits. See SECURITY.md for sensitive reports. Finite formal checks require Java 17+; instructions and assumptions are in formal/README.md. Passing the unchanged formal model does not prove a changed implementation corresponds to it.

Release preparation and maintainer setup are documented in docs/releasing.md.
