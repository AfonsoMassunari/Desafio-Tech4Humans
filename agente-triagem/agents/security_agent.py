"""Agente especializado em phishing e comprometimento de contas."""
from __future__ import annotations

from typing import Any

from agents.agent_context import AgentContext
from config import DOMINIO_EMPRESA
from mcp_client import call_tool
from models import Decision


class SecurityAgent:
    def __init__(self, context: AgentContext) -> None:
        self.context = context

    async def handle(self, event: dict[str, Any], decision: Decision) -> None:
        sender = (event.get("de") or "").lower()
        externo = bool(sender and not sender.endswith(DOMINIO_EMPRESA))

        await self.context.notify_team(
            event,
            "Segurança",
            f"Alerta de segurança - evento {event['id']}",
            (
                f"Evento {event['id']} de {event.get('de')} ({'externo' if externo else 'interno'}). "
                f"Motivo: {decision.motivo or 'possível tentativa de phishing/comprometimento.'}\n"
                f"Assunto original: {event.get('assunto', '')}"
            ),
        )

        if externo:
            await call_tool(
                self.context.session,
                "atualizar",
                base="eventos",
                id=event["id"],
                evento_id=event["id"],
                dados={
                    "status": "respondido",
                    "observacao": "Phishing externo encaminhado à Segurança; remetente não respondido.",
                },
            )
            return

        result = await call_tool(
            self.context.session,
            "registrar",
            base="incidentes",
            evento_id=event["id"],
            dados={
                "criticidade": "P0",
                "produto": "Segurança da conta",
                "resumo": decision.motivo
                or event.get("assunto")
                or "Possível comprometimento de conta reportado.",
            },
        )
        incident_id = result["registro"]["id"]
        await self.context.respond(
            event,
            f"Recebemos seu relato e abrimos o incidente {incident_id} com prioridade máxima. "
            "O time de Segurança foi acionado e vai entrar em contato.",
        )
