"""Modelos de dados usados pelos agentes."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
@dataclass
class Decision:
    """Resultado da classificação de um evento."""

    trilha: str  # faq | ticket | incident | vacation | security | team_route | escalate
    categoria: str = ""  # tipo do catálogo (trilha=ticket) ou criticidade (trilha=incident)
    campos: dict[str, Any] = field(default_factory=dict)
    resumo: str = ""
    time_destino: str = ""
    motivo: str = ""
    faq_id: str = ""
    completo: bool = True  # False => faltam campos obrigatórios, precisa coletar.