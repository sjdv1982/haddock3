"""Shared library for the CNS caching contract-compliance suite.

The suite observes ``haddock3`` only through ordinary CLI invocations and the
run directories they produce.  It never imports, monkeypatches or introspects
the caching implementation.  It does read HADDOCK3's *public* per-step
``io.json``, which is the same data ``haddock3-traceback`` consumes; it never
reads the cache's own bookkeeping (``CACHE``, ``CNS_DEPENDENCIES``), because
those are the artifacts under test.
"""
