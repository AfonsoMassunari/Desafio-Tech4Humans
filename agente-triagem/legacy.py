"""Integração com o sistema legado de férias."""
from __future__ import annotations
import re
from typing import Any

from config import AGENT_NAME, LEGACY_URL
from dashboard import STORE


def _read_page_message(page: Any, selector: str) -> str:
    locator = page.locator(selector)
    if locator.count() == 0:
        return ""
    return locator.first.inner_text().strip()


def post_vacation(event: dict[str, Any], start: str, end: str) -> str:
    """Preenche e envia o formulário legado usando Playwright.

    O sistema de férias não possui API: a confirmação válida precisa vir da
    própria tela, incluindo o protocolo gerado pelo sistema.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise RuntimeError(
            "Playwright não está instalado. Instale as dependências do agente "
            "e execute 'playwright install chromium'."
        ) from error

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(f"{LEGACY_URL}/legado/ferias", wait_until="domcontentloaded", timeout=20_000)
            page.locator("input[name='agente']").fill(AGENT_NAME)
            page.locator("input[name='email']").fill(str(event.get("de", "")))
            page.locator("input[name='inicio']").fill(start)
            page.locator("input[name='fim']").fill(end)
            page.locator("input[name='evento']").fill(str(event["id"]))
            page.get_by_role("button", name="Lançar férias").click()
            page.wait_for_load_state("domcontentloaded")

            error_message = _read_page_message(page, ".erro")
            if error_message:
                STORE.record_action(
                    event["id"],
                    "legacy_form",
                    {
                        "url": f"{LEGACY_URL}/legado/ferias",
                        "inicio": start,
                        "fim": end,
                        "evento": event["id"],
                    },
                    error=error_message,
                )
                raise ValueError(error_message)

            body = page.locator("body").inner_text()
            if "Férias lançadas" not in body:
                STORE.record_action(
                    event["id"],
                    "legacy_form",
                    {"url": f"{LEGACY_URL}/legado/ferias", "evento": event["id"]},
                    error="A tela de férias não confirmou o lançamento.",
                )
                raise ValueError("A tela de férias não confirmou o lançamento.")
            protocol = re.search(r"FER-[A-Z0-9-]+", body)
            protocol_value = protocol.group(0) if protocol else "protocolo não identificado"
            STORE.record_action(
                event["id"],
                "legacy_form",
                {
                    "url": f"{LEGACY_URL}/legado/ferias",
                    "inicio": start,
                    "fim": end,
                    "evento": event["id"],
                },
                {"protocolo": protocol_value},
            )
            return protocol_value
        finally:
            browser.close()