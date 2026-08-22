"""Pure domain layer.

Everything in :mod:`fhe.core` is deterministic and free of I/O: no database,
no HTTP, no filesystem, no clock reads that are not injected.  This is what
makes the draft engine testable from plain unit tests and reusable identically
by the live draft, the mock simulator, and the backtests.

The boundary is enforced by ``tests/architecture/test_core_purity.py``.
"""
