# z4j-brain

[![PyPI version](https://img.shields.io/pypi/v/z4j-brain.svg)](https://pypi.org/project/z4j-brain/)
[![Python](https://img.shields.io/pypi/pyversions/z4j-brain.svg)](https://pypi.org/project/z4j-brain/)
[![License](https://img.shields.io/pypi/l/z4j-brain.svg)](https://github.com/z4jdev/z4j-brain/blob/main/LICENSE)

The z4j brain — server, dashboard, and API.

Operators run one brain process per environment. Agents (one per
worker / app process) connect over WebSocket and stream task,
worker, queue, and schedule events. The dashboard surfaces every
event for inspection and exposes the operator action surface
(retry, cancel, bulk retry, purge, restart, schedule CRUD).

## Install

```bash
pip install z4j-brain
z4j-brain serve
```

## Documentation

Full docs at [z4j.dev](https://z4j.dev).

## License

AGPL-3.0-or-later — see [LICENSE](LICENSE). Your application code
imports only the Apache-2.0 agent packages and is never
AGPL-tainted.

## Links

- Homepage: https://z4j.com
- Documentation: https://z4j.dev
- PyPI: https://pypi.org/project/z4j-brain/
- Issues: https://github.com/z4jdev/z4j-brain/issues
- Changelog: [CHANGELOG.md](CHANGELOG.md)
- Security: security@z4j.com (see [SECURITY.md](SECURITY.md))
