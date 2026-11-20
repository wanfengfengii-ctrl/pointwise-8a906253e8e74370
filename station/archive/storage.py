"""Transactional SQLite storage for complete stopped-round publications."""

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from os import PathLike


class CommitError(RuntimeError):
    """The round could not be committed as one publication."""


@dataclass(frozen=True, slots=True)
class StoredResult:
    result_id: int
    round_id: str
    report_payload: str
    evidence_payload: str | None
    receipt_id: str | None
    receipt_round_id: str | None

    @property
    def complete(self) -> bool:
        return (
            self.evidence_payload is not None
            and self.receipt_id == _receipt_id(self.result_id)
            and self.receipt_round_id == self.round_id
        )


_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS reports (
        result_id INTEGER PRIMARY KEY,
        round_id TEXT UNIQUE NOT NULL,
        payload TEXT NOT NULL
    )''',
    '''CREATE TABLE IF NOT EXISTS evidence (
        result_id INTEGER PRIMARY KEY REFERENCES reports(result_id),
        payload TEXT NOT NULL
    )''',
    '''CREATE TABLE IF NOT EXISTS receipts (
        result_id INTEGER PRIMARY KEY REFERENCES reports(result_id),
        round_id TEXT NOT NULL,
        receipt_id TEXT UNIQUE NOT NULL
    )''',
)

_SELECT = '''SELECT r.result_id, r.round_id, r.payload AS report_payload,
                    e.payload AS evidence_payload, p.receipt_id, p.round_id AS receipt_round_id
             FROM reports AS r
             LEFT JOIN evidence AS e ON e.result_id = r.result_id
             LEFT JOIN receipts AS p ON p.result_id = r.result_id'''


def _receipt_id(result_id: int) -> str:
    return f'receipt-{result_id}'


class ArchiveStore:
    def __init__(self, database: str | PathLike[str]) -> None:
        self._db = sqlite3.connect(database, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        try:
            self._db.execute('PRAGMA foreign_keys = ON')
            self.initialize()
        except BaseException:
            self.close()
            raise

    @contextmanager
    def _transaction(self):
        self._db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self._db.execute('COMMIT')
        except BaseException:
            if self._db.in_transaction:
                self._db.execute('ROLLBACK')
            raise

    def initialize(self) -> None:
        with self._transaction():
            for statement in _SCHEMA:
                self._db.execute(statement)

    def commit_round(self, round_id: str, report_payload: str, evidence_payload: str) -> StoredResult:
        try:
            with self._transaction():
                existing = self.result_for_round(round_id)
                if existing is not None:
                    return existing
                cursor = self._db.execute(
                    'INSERT INTO reports (round_id, payload) VALUES (?, ?)', (round_id, report_payload),
                )
                result_id = cursor.lastrowid
                receipt = _receipt_id(result_id)
                self._db.execute(
                    'INSERT INTO evidence (result_id, payload) VALUES (?, ?)', (result_id, evidence_payload),
                )
                self._db.execute(
                    'INSERT INTO receipts (result_id, round_id, receipt_id) VALUES (?, ?, ?)',
                    (result_id, round_id, receipt),
                )
                stored = StoredResult(result_id, round_id, report_payload, evidence_payload, receipt, round_id)
        except sqlite3.Error as exc:
            raise CommitError(f'could not commit round {round_id}') from exc
        return stored

    def _load(self, query: str, value: str | int) -> StoredResult | None:
        row = self._db.execute(query, (value,)).fetchone()
        return StoredResult(**dict(row)) if row is not None else None

    def result_for_round(self, round_id: str) -> StoredResult | None:
        return self._load(_SELECT + ' WHERE r.round_id = ?', round_id)

    def load_result(self, result_id: int) -> StoredResult | None:
        if type(result_id) is not int or not 1 <= result_id <= (1 << 63) - 1:
            return None
        return self._load(_SELECT + ' WHERE r.result_id = ?', result_id)

    def close(self) -> None:
        self._db.close()
