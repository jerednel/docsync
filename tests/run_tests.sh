#!/usr/bin/env bash
# Run the docsync unit + end-to-end tests (no network, uses an in-memory fake Confluence).
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m pip install -q -r scripts/requirements.txt
python3 -m unittest discover -s tests -v
