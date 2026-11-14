"""Cliente MCP resiliente às falhas simuladas do mundo."""
from __future__ import annotations
import asyncio
import json
from typing import Any
from mcp import ClientSession
from config import AGENT_NAME, MAX_RETRIES, READ_TIMEOUT_SECONDS, log
from dashboard import STORE


async def _reconcile_write(
    session: ClientSession,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any] | None:
    """Verifica se uma escrita já foi aplicada antes de permitir novo retry."""
    event_id = str(arguments.get("evento_id") or "")
    base = str(arguments.get("base") or "")
    if not event_id and name not in ("enviar_mensagem",):
        return None

    if name == "enviar_mensagem":
        filtro = {"evento_id": event_id} if event_id else {}
        if not filtro:
            return None
        lookup = await call_tool(session, "consultar", read_only=True, base="mensagens", filtro=filtro, limite=20)
        records = lookup.get("registros", [])
        return {"status": "ok", "mensagem": records[0], "reconciliado": True} if records else None

    if name == "responder_evento":
        lookup = await call_tool(
            session, "consultar", read_only=True, base="eventos", filtro={"id": event_id}, limite=1
        )
        record = next(iter(lookup.get("registros", [])), None)
        if record and any(action.get("ferramenta") == "responder_evento" for action in record.get("acoes", [])):
            return {"status": "ok", "registro": record, "reconciliado": True}
        return None

    if name == "atualizar":
        record_id = str(arguments.get("id") or "")
        if not record_id or not base:
            return None
        lookup = await call_tool(
            session, "consultar", read_only=True, base=base, filtro={"id": record_id}, limite=1
        )
        record = next(iter(lookup.get("registros", [])), None)
        if record and (event_id in record.get("pedidos", []) or base == "eventos" and record.get("id") == event_id):
            return {"status": "ok", "registro": record, "reconciliado": True}
        return None

    if name == "registrar" and base in ("chamados", "incidentes"):
        lookup = await call_tool(
            session, "consultar", read_only=True, base=base, busca=event_id, limite=20
        )
        for record in lookup.get("registros", []):
            if event_id in record.get("pedidos", []):
                return {"status": "ok", "registro": record, "reconciliado": True}
    return None
class MCPToolError(Exception):
    def __init__(self, codigo: int, mensagem: str, extras: dict[str, Any] | None = None) -> None:
        super().__init__(mensagem)
        self.codigo = codigo
        self.mensagem = mensagem
        self.extras = extras or {}


async def call_tool(
    session: ClientSession,
    name: str,
    *,
    read_only: bool = False,
    timeout: float = READ_TIMEOUT_SECONDS,
    **arguments: Any,
) -> dict[str, Any]:
    """Chama uma ferramenta MCP com retry para as falhas transitórias do mundo.

    - 503 (indisponível): sempre pode repetir.
    - 409 (conflito): em escrita, reconsulta o estado antes de permitir novo
      retry; só repete quando a ação ainda não aparece aplicada.
    - 504 (timeout): para leitura, sempre pode repetir. Para escrita, a ação
      pode ou não ter sido executada (50% de chance) — por isso não repetimos
      automaticamente aqui; quem chama deve reconsultar o estado antes de
      tentar de novo (ver `registrar_com_reconciliacao`/`atualizar_seguro`).
    - `timeout`: limite do CLIENTE para a chamada. Precisa ser maior do que
      qualquer espera legítima do servidor (ex.: `esperar_segundos` de
      `proximo_evento`) somada à folga das falhas simuladas, senão o cliente
      aborta chamadas que o servidor ainda responderia a tempo.
    """
    arguments = {**arguments, "agente": AGENT_NAME}

    last_error: MCPToolError | None = None
    event_id = str(arguments.get("evento_id") or "")
    for attempt in range(MAX_RETRIES):
        try:
            result = await asyncio.wait_for(
                session.call_tool(name, arguments), timeout=timeout
            )
            if not result.content:
                raise MCPToolError(500, f"A ferramenta '{name}' não retornou conteúdo.")
            payload = json.loads(result.content[0].text)
            if payload.get("status") == "erro":
                raise MCPToolError(
                    payload.get("codigo", 500),
                    payload.get("mensagem", "Erro desconhecido do MCP."),
                    {k: v for k, v in payload.items() if k not in ("status", "codigo", "mensagem")},
                )
            if name == "proximo_evento" and payload.get("evento"):
                STORE.record_event(payload["evento"])
            if event_id:
                STORE.record_action(event_id, name, arguments, payload)
            return payload
        except MCPToolError as error:
            last_error = error
            if event_id and (error.codigo >= 500 or error.codigo == 409):
                STORE.record_action(event_id, name, arguments, error=f"{error.codigo}: {error.mensagem}")
            if not read_only and error.codigo in (409, 504):
                reconciled = await _reconcile_write(session, name, arguments)
                if reconciled is not None:
                    log.warning("%s: ação já aplicada; não repetirei a escrita.", name)
                    STORE.record_action(
                        event_id,
                        "reconcile_write",
                        arguments,
                        reconciled,
                    )
                    return reconciled
            retryable = error.codigo == 503 or error.codigo == 409 or (read_only and error.codigo == 504)
            if not retryable or attempt == MAX_RETRIES - 1:
                raise
            espera = min(2**attempt, 8)
            log.warning("%s: erro %s (%s); tentando novamente em %ss...", name, error.codigo, error.mensagem, espera)
            await asyncio.sleep(espera)
        except asyncio.TimeoutError:
            last_error = MCPToolError(504, f"Timeout do cliente chamando '{name}'.")
            if not read_only:
                reconciled = await _reconcile_write(session, name, arguments)
                if reconciled is not None:
                    log.warning("%s: ação já aplicada após timeout; não repetirei a escrita.", name)
                    STORE.record_action(event_id, "reconcile_write", arguments, reconciled)
                    return reconciled
            if attempt == MAX_RETRIES - 1 or not read_only:
                raise last_error
            log.warning("%s: timeout do cliente; tentando novamente...", name)

    assert last_error is not None
    raise last_error