# Fix Log

> Rotated daily into `docs/archive/fix-log/` — see [INDEX.md](docs/archive/fix-log/INDEX.md) for older entries.

**Big files, whole folders and bad downloads are now tested end to end — 2026-11-21**
We re-ran the file tests on a practice site: folders of 3 and 20 files, empty files, names with accents, and files up to 200 MB. Every file arrived once, in order and unchanged, and a folder handed to a single-file box is refused in plain words. An empty or never-ending download now stops with a clear reason instead of "HTTP 500". One gap remains: a very slow site can make a big upload report a failure even though the file arrived.
— 2026-11-21
