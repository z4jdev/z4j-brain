# Changelog

All notable changes to this package are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.4] - 2026-04-30

- Agents page now shows each agent's z4j-core version with an *update available* badge when an upgrade is recommended.
- Settings → System: operator-initiated *Check for updates* card. No automatic polling.

## [1.3.3] - 2026-04-30

- Existing schedules from connected agents (celery-beat, rq-scheduler, apscheduler, arqcron, hueyperiodic, taskiqscheduler) now appear automatically in the dashboard.
- New *Sync now* button on the Schedules page.

## [1.3.2] - 2026-04-30

- Bug fix: global admins can manage subscriptions on any project.

## [1.3.1] - 2026-04-30

- Bug fix: Workers tab populates correctly on agent connect.

## [1.3.0] - 2026-05-15

Initial release of the 1.3.x line. Earlier 1.x versions are yanked from PyPI.
