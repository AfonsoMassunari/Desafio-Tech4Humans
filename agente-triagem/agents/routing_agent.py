"""Agente especializado em encaminhamentos para times fora do catálogo."""
from __future__ import annotations

from typing import Any

from agents.agent_context import AgentContext
from models import Decision
from utils import event_text


class RoutingAgent:
    def __init__(self, context: AgentContext) -> None:
        self.context = context

    async def handle(self, event: dict[str, Any], decision: Decision) -> None:
        team = decision.time_destino
        if team not in self.context.teams:
            if self.context.ticket_agent is None:
                raise RuntimeError("Agente de chamados não configurado para fallback de encaminhamento.")
            await self.context.ticket_agent.handle(
                event,
                Decision(
                    trilha="ticket",
                    categoria="Outros pedidos",
                    campos={"descricao": event_text(event)[:1000]},
                ),
            )
            return

        await self.context.notify_team(
            event,
            team,
            event.get("assunto", f"Pedido encaminhado para {team}"),
            f"Encaminhado automaticamente pela triagem N1.\n"
            f"Solicitante: {event.get('de')}\nMensagem: {event.get('corpo', '')}",
        )
        from mcp_client import call_tool

        await call_tool(
            self.context.session,
            "registrar",
            base="chamados",
            evento_id=event["id"],
            dados={
                "tipo": "Outros pedidos",
                "campos": {"descricao": f"[{team}] " + event_text(event)[:900]},
                "resumo": event.get("assunto", f"Encaminhado para {team}"),
                "observacao": (
                    f"Encaminhado para o time {team} ({self.context.team_email(team)}) "
                    "por não constar no catálogo."
                ),
            },
        )
        await self.context.respond(
            event,
            f"Encaminhei sua solicitação para o time de {team}, que vai dar continuidade.",
        )
