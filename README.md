# z4j-brain

[![PyPI version](https://img.shields.io/pypi/v/z4j-brain.svg)](https://pypi.org/project/z4j-brain/)
[![License](https://img.shields.io/pypi/l/z4j-brain.svg)](https://github.com/z4jdev/z4j/blob/main/LICENSE)

This package is a compatibility shim. **Install [`z4j`](https://pypi.org/project/z4j/) instead.**

```bash
pip install z4j
```

z4j is the open-source control plane for Python task infrastructure.
See [z4j.com](https://z4j.com) for details and [z4j.dev](https://z4j.dev)
for documentation.

## Why this package exists

Pre-1.4.0, the central z4j process was distributed under the
`z4j-brain` PyPI name. The 1.4.0 consolidation cut moved that
content into the `z4j` distribution; `z4j-brain` continues to exist
as a metadata-only redirect so that legacy install commands
(`pip install z4j-brain`, existing Dockerfiles, `requirements.txt`
files in the wild) keep working.

`pip install z4j-brain` resolves to `z4j-brain==1.4.0`, which
declares a single dependency on `z4j>=1.4.0`. The brain code
ships in the `z4j` wheel and is imported as `z4j_brain` (the
internal Python module name is unchanged, so any code doing
`from z4j_brain import ...` continues to work).

## Frozen at 1.4.0

This shim is intentionally not maintained beyond 1.4.0. New
features land in `z4j`. The shim's only job is to keep the
legacy install path resolving cleanly.

## License

AGPL-3.0-or-later (matches the `z4j` distribution it depends on).
