"""ScopedDb (design doc 3.4): the database as an extension with `db:own` sees it, limited to the tables its manifest lists
under data.tables. SQLite checks every statement as it is prepared, so a query that reads, writes or joins another table,
changes the schema, attaches a file or runs a pragma fails with an authorization error, however it is spelled.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from ..db import Database

_TABLE_ACTIONS = {sqlite3.SQLITE_READ, sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}  # arg1 is the table
_PLAIN_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT,
                  sqlite3.SQLITE_RECURSIVE}


class ScopedDb:
    def __init__(self, db: Database, tables: tuple[str, ...]) -> None:
        self._db, self.tables = db, frozenset(t.lower() for t in tables)

    def _authorize(self, action: int, arg1: str | None, *_: object) -> int:
        if action in _PLAIN_ACTIONS or action in _TABLE_ACTIONS and (arg1 or "").lower() in self.tables:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """A connection that commits when the block ends and rolls back when it raises, like Database.tx."""
        with self._db.tx() as c:
            c.set_authorizer(self._authorize)
            yield c
