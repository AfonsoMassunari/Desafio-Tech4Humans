"""Agente especializado em chamados, dados incompletos e duplicidades."""
from __future__ import annotations

from typing import Any

from agents.agent_context import AgentContext
from mcp_client import call_tool
from models import Decision
from utils import event_text, normalize_key


class TicketAgent:
    def __init__(self, context: AgentContext) -> None:
        self.context = context

    def missing_fields(self, category: str, fields: dict[str, Any]) -> list[str]:
        required = self.context.catalog.get(category, {}).get("campos_obrigatorios", [])
        return [field for field in required if not str(fields.get(field, "")).strip()]

    async def find_existing(
        self,
        event: dict[str, Any],
        category: str,
    ) -> dict[str, Any] | None:
        result = await call_tool(
            self.context.session,
            "consultar",
            read_only=True,
            base="chamados",
            filtro={"tipo": category, "solicitante": event.get("de", "")},
            limite=20,
        )
        records = [
            item
            for item in result.get("registros", [])
            if item.get("status") not in ("resolvido", "cancelado")
        ]
        if not records:
            return None

        event_words = set(normalize_key(event.get("assunto", "")).split())
        for record in records:
            record_words = set(normalize_key(record.get("resumo", "")).split())
            if event_words and record_words and len(event_words & record_words) >= min(2, len(event_words)):
                return record
        return records[0]

    async def handle(self, event: dict[str, Any], decision: Decision) -> None:
        category = decision.categoria or "Outros pedidos"
        if category not in self.context.catalog:
            category = "Outros pedidos"
            decision.campos = {"descricao": event_text(event)[:1000]}

        fields = dict(decision.campos)
        if category == "Outros pedidos" and not fields.get("descricao"):
            fields["descricao"] = event_text(event)[:1000]

        existing = await self.find_existing(event, category)
        if existing:
            await call_tool(
                self.context.session,
                "atualizar",
                base="chamados",
                id=existing["id"],
                evento_id=event["id"],
                dados={"pedidos": event["id"]},
            )
            await self.context.respond(
                event,
                f"Já existe o chamado {existing['id']} ({existing.get('status', 'aberto')}) "
                "para isso. Vinculei sua mensagem a ele e o time está ciente.",
            )
            return

        missing = self.missing_fields(category, fields)
        if missing:
            await self.context.respond(
                event,
                f"Para abrir o chamado de '{category}', ainda preciso de: "
                f"{', '.join(missing)}. Pode me enviar esses dados?",
                wait=True,
            )
            return

        category_info = self.context.catalog.get(category, {})
        data: dict[str, Any] = {
            "tipo": category,
            "campos": fields,
            "resumo": event.get("assunto", category),
        }
        if category_info.get("aprovacao"):
            data["status"] = "aguardando aprovação"
            data["observacao"] = (
                f"Requer aprovação de: {category_info['aprovacao']}. "
                "Aguardando confirmação do gestor."
            )

        result = await call_tool(
            self.context.session,
            "registrar",
            base="chamados",
            evento_id=event["id"],
            dados=data,
        )
        ticket_id = result["registro"]["id"]
        team = category_info.get("time", "Operações")
        await self.context.notify_team(
            event,
            team,
            f"{ticket_id} - {event.get('assunto', category)}",
            f"Novo chamado {ticket_id} ({category}). Solicitante: {event.get('de')}.",
        )
        approval = (
            f" Como depende de aprovação ({category_info.get('aprovacao')}), "
            "o status inicial é 'aguardando aprovação'."
            if category_info.get("aprovacao")
            else ""
        )
        await self.context.respond(
            event,
            f"Seu pedido foi registrado como {ticket_id} e encaminhado para o time de {team}."
            f"{approval}",
        )
