"""Fixed, reproducible quality evaluation for the active MallMind V3 path.

The package intentionally lives outside ``tests/``: it is a release-evaluation
asset, not a small unit-test helper.  ``runner.py`` talks to a real SSE server;
the ``test_*.py`` files only verify fixture and metric correctness offline.
"""
