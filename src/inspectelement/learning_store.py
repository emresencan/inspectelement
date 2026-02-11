from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .models import ElementSummary, LocatorCandidate, PageContext


class LearningStore:
    def __init__(self, base_dir: Path | None = None) -> None:
        root = base_dir or (Path.home() / ".inspectelement")
        root.mkdir(parents=True, exist_ok=True)
        self.db_path = root / "learning.db"
        self.json_path = root / "learning.json"
        self._lock = threading.Lock()
        self._use_sqlite = self._initialize_sqlite()
        if not self._use_sqlite:
            self._initialize_json()

    def _initialize_sqlite(self) -> bool:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS feedback (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        hostname TEXT NOT NULL,
                        page_title TEXT,
                        element_signature TEXT NOT NULL,
                        locator_type TEXT NOT NULL,
                        locator TEXT NOT NULL,
                        rule TEXT NOT NULL,
                        was_good INTEGER NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS weights (
                        rule TEXT PRIMARY KEY,
                        weight REAL NOT NULL DEFAULT 0
                    )
                    """
                )
                conn.commit()
            return True
        except sqlite3.Error:
            return False

    def _initialize_json(self) -> None:
        if self.json_path.exists():
            return
        payload = {"feedback": [], "weights": {}}
        self.json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def record_feedback(
        self,
        page_context: PageContext,
        element_summary: ElementSummary,
        candidate: LocatorCandidate,
        was_good: bool,
    ) -> None:
        with self._lock:
            if self._use_sqlite:
                self._record_feedback_sqlite(page_context, element_summary, candidate, was_good)
            else:
                self._record_feedback_json(page_context, element_summary, candidate, was_good)

    def _record_feedback_sqlite(
        self,
        page_context: PageContext,
        element_summary: ElementSummary,
        candidate: LocatorCandidate,
        was_good: bool,
    ) -> None:
        delta = 0.2 if was_good else -0.2
        timestamp = datetime.utcnow().isoformat()
        prefix = candidate.rule.split(":", 1)[0]
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO feedback (
                    hostname,
                    page_title,
                    element_signature,
                    locator_type,
                    locator,
                    rule,
                    was_good,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    page_context.hostname,
                    page_context.page_title,
                    element_summary.signature(),
                    candidate.locator_type,
                    candidate.locator,
                    candidate.rule,
                    int(was_good),
                    timestamp,
                ),
            )
            cur.execute(
                """
                INSERT INTO weights (rule, weight)
                VALUES (?, ?)
                ON CONFLICT(rule) DO UPDATE SET
                    weight = MIN(2.0, MAX(-2.0, weights.weight + excluded.weight))
                """,
                (candidate.rule, delta),
            )
            cur.execute(
                """
                INSERT INTO weights (rule, weight)
                VALUES (?, ?)
                ON CONFLICT(rule) DO UPDATE SET
                    weight = MIN(2.0, MAX(-2.0, weights.weight + excluded.weight))
                """,
                (prefix, delta / 2.0),
            )
            conn.commit()

    def _record_feedback_json(
        self,
        page_context: PageContext,
        element_summary: ElementSummary,
        candidate: LocatorCandidate,
        was_good: bool,
    ) -> None:
        payload = self._read_json()
        delta = 0.2 if was_good else -0.2
        weights: dict[str, float] = payload.setdefault("weights", {})
        prefix = candidate.rule.split(":", 1)[0]
        weights[candidate.rule] = max(-2.0, min(2.0, float(weights.get(candidate.rule, 0.0)) + delta))
        weights[prefix] = max(-2.0, min(2.0, float(weights.get(prefix, 0.0)) + (delta / 2.0)))

        payload.setdefault("feedback", []).append(
            {
                "context": asdict(page_context),
                "element_signature": element_summary.signature(),
                "locator_type": candidate.locator_type,
                "locator": candidate.locator,
                "rule": candidate.rule,
                "was_good": bool(was_good),
                "created_at": datetime.utcnow().isoformat(),
            }
        )
        self.json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _read_json(self) -> dict:
        if not self.json_path.exists():
            return {"feedback": [], "weights": {}}
        return json.loads(self.json_path.read_text(encoding="utf-8"))

    def get_rule_weights(self) -> dict[str, float]:
        with self._lock:
            if self._use_sqlite:
                try:
                    with sqlite3.connect(self.db_path) as conn:
                        cur = conn.cursor()
                        cur.execute("SELECT rule, weight FROM weights")
                        return {rule: float(weight) for rule, weight in cur.fetchall()}
                except sqlite3.Error:
                    self._use_sqlite = False
                    self._initialize_json()
            payload = self._read_json()
            return {key: float(value) for key, value in payload.get("weights", {}).items()}

    def reset(self) -> None:
        with self._lock:
            if self._use_sqlite:
                try:
                    with sqlite3.connect(self.db_path) as conn:
                        cur = conn.cursor()
                        cur.execute("DELETE FROM feedback")
                        cur.execute("DELETE FROM weights")
                        conn.commit()
                    return
                except sqlite3.Error:
                    self._use_sqlite = False
                    self._initialize_json()
            payload = {"feedback": [], "weights": {}}
            self.json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
