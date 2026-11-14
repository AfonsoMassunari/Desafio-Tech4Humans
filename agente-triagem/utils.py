"""Utilitários compartilhados de classificação."""
from __future__ import annotations
import re
import unicodedata
def event_text(event: dict[str, Any]) -> str:
    return f"{event.get('assunto', '')} {event.get('corpo', '')}"


def sem_acento(texto: str) -> str:
    import unicodedata

    nfkd = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalize_key(texto: str) -> str:
    return re.sub(r"\s+", " ", sem_acento(texto)).strip()


# --------------------------------------------------------------------------
# Extração de datas/valores (apoio tanto ao fallback de regras quanto ao LLM).
# --------------------------------------------------------------------------


def extract_dates(text: str) -> list[str]:
    return re.findall(r"\b(\d{2}/\d{2}/\d{4})\b", text)


def extract_money(text: str) -> str | None:
    match = re.search(r"R\$\s*[\d.,]+", text, re.IGNORECASE)
    return match.group(0) if match else None


def extract_patrimony(text: str) -> str | None:
    match = re.search(r"patrim[oô]nio\s*(?:n[ºo]?\s*)?[:=]?\s*(\d+)", text, re.IGNORECASE)
    return match.group(1) if match else None


PRODUTOS_CONHECIDOS = ("Portal do Cliente", "App Agenda", "Painel de Vendas")


def extract_product(text: str) -> str | None:
    lower = text.lower()
    for produto in PRODUTOS_CONHECIDOS:
        if produto.lower() in lower:
            return produto
    return None