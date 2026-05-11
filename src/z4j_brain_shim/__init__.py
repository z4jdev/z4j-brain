"""z4j-brain compatibility shim.

This package is a metadata-only redirect. The brain server source
lives in the ``z4j`` distribution. ``pip install z4j-brain`` pulls
in ``z4j`` transitively so existing install paths keep working.

Nothing imports this module. The brain code stays at the
``z4j_brain`` import path (shipped by the ``z4j`` distribution).
"""

__doc__: str  # silence linters that demand a __doc__ attribute
