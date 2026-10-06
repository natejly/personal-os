"""Memory knobs that are not user settings. Stdlib only, so migrations can import it without a cycle."""
# Settings-table row holding the epoch seconds of the last consolidation proposal run: the tidy-up
# counter is derived from rows newer than this, so it survives a relaunch.
TIDY_AT_KEY = "memoryTidyAt"
# How long after an assistant reply finished an auto-learn write can land (extraction is one LLM call
# queued behind the reply); a wider window would start guessing which reply a memory came from.
BACKFILL_WINDOW_S = 180
# A reply's row is created when it starts, so a long reply began well before it finished; this bounds
# the SQL pre-filter, and the exact finish time is checked in Python.
BACKFILL_LOOKBACK_S = 86400
