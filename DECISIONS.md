# Decision Log

## 2026-04-02 PS

After the second session with Claude Code, I decided to write the code to
provide conditional updating of the database. Claude added a constraint to
the database and a new field with the date. This redundant data is not needed.
Instead, I checked the date to ensure that only one snapshots row per day is
created. If an existing snapshots entry exists, it will not add another set of
records for that day to the installs table in the database.
