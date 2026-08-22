"""Application services.

Orchestration that sits between the HTTP layer and the domain: session
lifecycle, board assembly, and the mapping from domain objects to wire types.
No business rules live here - those belong in :mod:`fhe.core`.
"""
