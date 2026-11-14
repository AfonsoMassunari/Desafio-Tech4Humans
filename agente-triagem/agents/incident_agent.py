"""Agente especializado em criação e correlação de incidentes."""
from __future__ import annotations

from typing import Any

from agents.agent_context import AgentContext
from mcp_client import call_tool
from models import Decision
from utils import event_text, extract_product, normalize_key, PRODUTOS_CONHECIDOS


class IncidentAgent:
    def __init__(self, context: AgentContext) -> None:
        self.context = context

    async def handle(self, event: dict[str, Any], decision: Decision) -> None:
        criticidade = (decision.categoria or "P2").upper()
        if criticidade not in ("P0", "P1", "P2"):
            criticidade = "P2"
        produto = str(decision.campos.get("produto") or "") or extract_product(event_text(event)) or ""
        resumo = decision.resumo or event.get("assunto") or "Falha coletiva identificada."
        key = normalize_key(produto) if produto else ""

        existing_id = self.context.open_incidents.get(key) if key else None
        if existing_id:
            check = await call_tool(
                self.context.session,
                "consultar",
                read_only=True,
                base="incidentes",
                filtro={"id": existing_id},
            )
            record = next(
                (item for item in check.get("registros", []) if item["id"] == existing_id),
                None,
            )
            if record and record.get("status") == "aberto":
                await call_tool(
                    self.context.session,
                    "atualizar",
                    base="incidentes",
                    id=existing_id,
                    evento_id=event["id"],
                    dados={"pedidos": event["id"]},
                )
                await self.context.respond(
                    event,
                    f"Identificamos que isso já está relacionado ao incidente {existing_id}, "
                    "em andamento. Vou vincular seu relato a ele.",
                )
                return
            if key:
                self.context.open_incidents.pop(key, None)

        result = await call_tool(
            self.context.session,
            "registrar",
            base="incidentes",
            evento_id=event["id"],
            dados={"criticidade": criticidade, "produto": produto, "resumo": resumo},
        )
        incident_id = result["registro"]["id"]
        if key:
            self.context.open_incidents[key] = incident_id

        team = "Produto" if produto in PRODUTOS_CONHECIDOS else "Operações"
        await self.context.notify_team(
            event,
            team,
            f"Incidente {incident_id} ({criticidade}) - {produto or resumo}",
            f"Incidente {incident_id} aberto com criticidade {criticidade}. "
            f"Evento inicial: {event['id']}.\nResumo: {resumo}",
        )
        await self.context.respond(
            event,
            f"Identificamos um incidente relacionado{f' a {produto}' if produto else ''}. "
            f"Registramos como {incident_id} e acionamos o time responsável.",
        )
