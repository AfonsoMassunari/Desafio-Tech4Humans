"""Agente classificador: regras determinísticas, LLM e fallback."""
from __future__ import annotations
import json
import logging
import re
from typing import Any
from config import DOMINIO_EMPRESA, LLM_API_KEY, LLM_BASE_URL, LLM_HABILITADO, LLM_MODEL, log
from models import Decision
from utils import event_text, extract_dates, extract_money, extract_patrimony, extract_product
try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None  # type: ignore[assignment, misc]
PROMPT_SISTEMA = """\
Você é o classificador de um sistema de Triagem N1 de uma empresa chamada \
t4h (domínio de e-mail @t4h.exemplo). Sua única tarefa é ler um evento \
recebido (e-mail/mensagem) e devolver um JSON com a trilha de atendimento \
correta e os campos extraídos, sem inventar informação que não está no texto.

Trilhas possíveis (campo "trilha"):
- "faq": dúvida de procedimento coberta por uma FAQ (ex.: VPN, trocar senha, \
impressora, wifi visitante, sala de reunião, contracheque, 2FA, crachá, \
benefícios, prazo de reembolso, e-mail no celular, levar equipamento pra \
casa, compartilhar arquivo grande). Preencha "faq_id" com o id da FAQ mais \
próxima dentre as fornecidas, ou deixe vazio se nenhuma bater bem.
- "ticket": pedido individual que se encaixa em um tipo do catálogo \
(Acesso a sistema, Retirada de acessos, Novo equipamento, Troca de \
equipamento, Reembolso, Suporte a produto, Outros pedidos — NUNCA "Férias", \
que tem trilha própria). Preencha "categoria" com o tipo exato do catálogo e \
"campos" com os campos obrigatórios desse tipo que conseguir extrair do \
texto (deixe como string vazia "" o que não conseguir extrair, não invente).
- "incident": falha que afeta MÚLTIPLOS clientes/usuários ou risco grave e \
coletivo (ex.: "fora do ar", "erro 503", "vários clientes", "não conseguem \
X", instabilidade geral) — não um problema isolado de uma única conta. \
Preencha "categoria" com a criticidade estimada (P0 catastrófico/segurança \
grave, P1 indisponibilidade ampla, P2 degradação/atraso) e "resumo" com uma \
frase curta do problema, e tente reconhecer o produto afetado em "produto".
- "vacation": pedido REAL de férias com datas de início e fim. Preencha \
"campos" com "data_de_inicio" e "data_de_fim" no formato DD/MM/AAAA. Se for \
só uma dúvida sobre como pedir férias (sem datas), classifique como "faq".
- "security": sinais de phishing externo (remetente de domínio parecido mas \
diferente de @t4h.exemplo pedindo senha/validação de credencial/link \
suspeito) OU um relato interno grave de possível invasão/comprometimento de \
conta (login de local estranho, pedido estranho de troca de dados \
bancários, emails enviados sem o conhecimento do dono). Explique em "motivo".
- "team_route": pedido legítimo que não se encaixa em nenhum tipo do \
catálogo nem é férias, mas é claramente destinado a um time existente fora \
do catálogo de chamados (ex.: Marketing: patrocínio/evento/redes sociais; \
Jurídico: contrato/termos). Preencha "time_destino" com o nome exato do time.
- "ticket" com categoria "Outros pedidos" é o fallback para pedidos internos \
genuínos que não se encaixam em nada acima e não têm time claro.

Campos obrigatórios por tipo de catálogo (preencha em "campos"):
{catalogo_campos}

Times disponíveis (para "team_route" e para saber quem atende o quê):
{times_descricao}

FAQs disponíveis (para a trilha "faq"):
{faq_lista}

Responda SOMENTE um JSON válido com este formato exato:
{{
  "trilha": "...",
  "categoria": "...",
  "campos": {{}},
  "resumo": "...",
  "time_destino": "...",
  "motivo": "...",
  "faq_id": "..."
}}
"""


class Classifier:
    """Classificador híbrido: LLM com fallback determinístico por regras."""

    def __init__(self, catalog: dict[str, dict[str, Any]], teams: dict[str, dict[str, Any]], faq: list[dict[str, Any]]) -> None:
        self.catalog = catalog
        self.teams = teams
        self.faq = faq
        self.client = None
        if LLM_HABILITADO and AsyncOpenAI is not None:
            self.client = AsyncOpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)

    def _system_prompt(self) -> str:
        catalogo_campos = "\n".join(
            f"- {tipo}: {', '.join(info.get('campos_obrigatorios', []))}"
            for tipo, info in self.catalog.items()
            if tipo != "Férias"
        )
        times_descricao = "\n".join(
            f"- {nome}: {info.get('atende', '')}" for nome, info in self.teams.items()
        )
        faq_lista = "\n".join(
            f"- {item['id']}: {item['pergunta']}" for item in self.faq
        )
        return PROMPT_SISTEMA.format(
            catalogo_campos=catalogo_campos, times_descricao=times_descricao, faq_lista=faq_lista
        )

    async def classify(self, event: dict[str, Any]) -> Decision:
        rule_based = self._classify_by_rules(event)
        if rule_based is not None:
            return rule_based  # casos inequívocos não passam pelo LLM.

        if self.client is None:
            return self._enforce_criticality(event, self._fallback_rules(event))

        try:
            decision = await self._classify_by_llm(event)
            if decision is not None:
                return self._enforce_criticality(event, decision)
        except Exception as error:  # qualquer falha do LLM cai no fallback.
            log.warning("Classificação via LLM falhou (%s); usando fallback por regras.", error)

        return self._fallback_rules(event)

    def _enforce_criticality(self, event: dict[str, Any], decision: Decision) -> Decision:
        if decision.trilha != "incident":
            return decision
        lower = event_text(event).lower()
        if any(marker in lower for marker in ("todos os clientes", "para todos", "produto inteiro", "indisponível para todos")):
            decision.categoria = "P0"
        elif any(marker in lower for marker in ("muitos clientes", "vários clientes", "mais de 40", "mais de 100", "fora do ar", "erro 503", "503")):
            decision.categoria = "P1"
        elif any(marker in lower for marker in ("parcial", "intermit", "lentidão", "atraso", "contorno")):
            decision.categoria = "P2"
        return decision

    def _classify_by_rules(self, event: dict[str, Any]) -> Decision | None:
        """Regras determinísticas para os casos que NÃO devem depender do LLM."""
        text = event_text(event)
        lower = text.lower()
        sender = (event.get("de") or "").lower()
        externo = sender and not sender.endswith(DOMINIO_EMPRESA)

        # Phishing externo: domínio parecido mas diferente, pedindo senha/validação.
        if externo and any(
            marker in lower
            for marker in ("senha", "valide", "validar", "confirme sua conta", "clique no link", "credencia")
        ):
            return Decision(trilha="security", motivo=f"Remetente externo ({event.get('de')}) solicitando credenciais/validação.")

        return None

    def _fallback_rules(self, event: dict[str, Any]) -> Decision:
        """Classificador 100% por regex, usado se o LLM estiver indisponível."""
        text = event_text(event)
        lower = text.lower()

        dates = extract_dates(text)
        if ("férias" in lower or "ferias" in lower) and len(dates) >= 2:
            return Decision(trilha="vacation", campos={"data_de_inicio": dates[0], "data_de_fim": dates[1]})

        collective_markers = (
            "fora do ar", "não conseguem", "nao conseguem", "vários clientes", "varios clientes",
            "mais de 40", "mais de 100", "reclamações", "reclamacoes", "503", "erro em massa",
        )
        if any(marker in lower for marker in collective_markers):
            produto = extract_product(text) or ""
            criticidade = "P1" if any(m in lower for m in ("fora do ar", "indispon", "503")) else "P2"
            return Decision(
                trilha="incident", categoria=criticidade, resumo=event.get("assunto", "Falha coletiva"), campos={"produto": produto},
            )

        faq_scores: list[tuple[int, dict[str, Any]]] = []
        for item in self.faq:
            score = sum(1 for kw in item.get("palavras_chave", []) if kw.lower() in lower)
            if score:
                faq_scores.append((score, item))
        if faq_scores:
            faq_scores.sort(key=lambda pair: pair[0], reverse=True)
            return Decision(trilha="faq", faq_id=faq_scores[0][1]["id"])

        if "reembolso" in lower or "ressarcimento" in lower:
            return Decision(
                trilha="ticket",
                categoria="Reembolso",
                campos={
                    "valor": extract_money(text) or "",
                    "data": dates[0] if dates else "",
                    "comprovante": "Anexo mencionado no evento" if "anexo" in lower or "comprovante" in lower else "",
                },
            )

        if any(w in lower for w in ("saiu da empresa", "desligamento", "tirar os acessos", "retirar os acessos")):
            person = re.search(r"(?:de|da|do)\s+([A-ZÁÉÍÓÚÀ-Ü][\wÀ-ÿ]+(?:\s+[A-ZÁÉÍÓÚÀ-Ü][\wÀ-ÿ]+){1,4})", text)
            return Decision(
                trilha="ticket",
                categoria="Retirada de acessos",
                campos={"pessoa": person.group(1) if person else "", "data_de_saida": dates[0] if dates else ""},
            )

        if any(w in lower for w in ("acesso ao", "acesso a", "acesso de", "permissão", "permissao", "liberar acesso")):
            sistema_match = re.search(r"\b(crm|financeiro|reposit[oó]rio(?: de c[oó]digo)?|app agenda|portal do cliente)\b", lower)
            nivel = "escrita" if "escrita" in lower else "leitura"
            return Decision(
                trilha="ticket",
                categoria="Acesso a sistema",
                campos={"sistema": sistema_match.group(1) if sistema_match else "", "nivel_de_acesso": nivel, "justificativa": text[:500]},
            )

        equip_words = ("notebook", "mouse", "teclado", "headset", "monitor")
        if any(w in lower for w in equip_words):
            item = next((w for w in equip_words if w in lower), "")
            if any(w in lower for w in ("defeito", "quebrou", "parou", "desligando", "falhando", "estufada")):
                return Decision(
                    trilha="ticket",
                    categoria="Troca de equipamento",
                    campos={"item": item, "patrimonio": extract_patrimony(text) or "", "defeito": text[:500]},
                )
            return Decision(trilha="ticket", categoria="Novo equipamento", campos={"item": item, "motivo": text[:500]})

        produto = extract_product(text)
        if produto or "app " in lower or "portal" in lower or "painel de vendas" in lower:
            since_match = re.search(r"\bdesde\s+([^.,\n]+)", text, re.IGNORECASE)
            return Decision(
                trilha="ticket",
                categoria="Suporte a produto",
                campos={
                    "produto": produto or "",
                    "o_que_aconteceu": text[:500],
                    "desde_quando": since_match.group(1).strip() if since_match else "",
                },
            )

        if "patroc" in lower or "evento" in lower or "redes sociais" in lower or "marca da empresa" in lower:
            return Decision(trilha="team_route", time_destino="Marketing")
        if "contrato" in lower or "jur" in lower or "termo de uso" in lower:
            return Decision(trilha="team_route", time_destino="Jurídico")

        return Decision(trilha="ticket", categoria="Outros pedidos", campos={"descricao": text[:1000]})

    async def _classify_by_llm(self, event: dict[str, Any]) -> Decision | None:
        assert self.client is not None
        user_prompt = (
            f"Remetente: {event.get('de', '')}\n"
            f"Assunto: {event.get('assunto', '')}\n"
            f"Corpo: {event.get('corpo', '')}\n"
        )
        response = await self.client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=600,
            temperature=0,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content
        if not content:
            return None
        data = json.loads(content)
        trilha = str(data.get("trilha", "")).strip().lower()
        if trilha not in ("faq", "ticket", "incident", "vacation", "security", "team_route"):
            return None
        return Decision(
            trilha=trilha,
            categoria=str(data.get("categoria", "") or ""),
            campos={k: ("" if v is None else v) for k, v in (data.get("campos") or {}).items()},
            resumo=str(data.get("resumo", "") or ""),
            time_destino=str(data.get("time_destino", "") or ""),
            motivo=str(data.get("motivo", "") or ""),
            faq_id=str(data.get("faq_id", "") or ""),
        )