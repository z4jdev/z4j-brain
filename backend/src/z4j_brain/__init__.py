"""z4j-brain - the z4j server, API, and dashboard host.

Part of the z4j monorepo. Licensed under **AGPL v3**.

If you are an organization whose policy forbids AGPL-licensed code, a
commercial license is available - contact licensing@z4j.com.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _pkg_version

# Read THIS package's wheel version from installed metadata. Previously
# this re-exported ``z4j_core.__version__`` (the wire-protocol version),
# which made the brain's startup banner report e.g. "1.0.1" even after
# we'd shipped z4j-brain 1.0.4 to PyPI - confusing for operators.
#
# The wire-protocol version is still exposed (for compat checks against
# agents) as ``protocol_version`` below, just under a different name so
# operator-facing surfaces (logs, banners, /api/v1/health) report the
# brain wheel version.
try:
    __version__ = _pkg_version("z4j-brain")
except PackageNotFoundError:
    # Editable installs and source checkouts that haven't been
    # ``pip install -e .``'d won't have package metadata. Fall back to
    # the protocol version so dev environments aren't broken.
    from z4j_core.version import __version__  # type: ignore[no-redef]

from z4j_core.version import __version__ as protocol_version

__all__ = ["__version__", "protocol_version"]
