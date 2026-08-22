"""HTTP delivery layer.

Owns transport concerns only: request validation, serialisation, error mapping,
and streaming. Every decision that matters lives in :mod:`fhe.core`, so the same
answers are produced whether they are reached through this API, a test, or the
simulator.
"""
