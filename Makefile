.PHONY: check test package-check sync-package

check:
	./scripts/check.sh

test:
	PYTHONPATH=src python -B -m unittest discover -s tests -v

package-check:
	./scripts/sync-packaging.sh --check

sync-package:
	./scripts/sync-packaging.sh
