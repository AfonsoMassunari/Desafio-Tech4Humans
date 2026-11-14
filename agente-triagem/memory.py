"""Memória persistente local do agente, sem substituir o MCP como fonte oficial."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Any

from config import AGENT_MEMORY_PATH


class AgentMemory:
    """Persiste histórico de decisões e ações para auditoria entre execuções."""

    def __init__(self, path: str = AGENT_MEMORY_PATH) -> None:
        self.path = path
        self._lock = threading.RLock()
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_memory (
                    run_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    decision_json TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, event_id)
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def save_event(self, run_id: str, event: dict[str, Any]) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_memory(run_id, event_id, event_json, updated_at)
                VALUES (?, ?, ?, datetime('now'))
                ON CONFLICT(run_id, event_id) DO UPDATE SET
                    event_json=excluded.event_json,
                    updated_at=excluded.updated_at
                """,
                (run_id, event["id"], json.dumps(event, ensure_ascii=False)),
            )

    def save_decision(self, run_id: str, event_id: str, decision: Any) -> None:
        value = {
            "trilha": decision.trilha,
            "categoria": decision.categoria,
            "campos": decision.campos,
            "resumo": decision.resumo,
            "time_destino": decision.time_destino,
            "motivo": decision.motivo,
            "faq_id": decision.faq_id,
        }
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE agent_memory
                SET decision_json=?, updated_at=datetime('now')
                WHERE run_id=? AND event_id=?
                """,
                (json.dumps(value, ensure_ascii=False), run_id, event_id),
            )

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM agent_memory ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "run_id": row["run_id"],
                "event": json.loads(row["event_json"]),
                "decision": json.loads(row["decision_json"]) if row["decision_json"] else None,
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]
