"""Cotação PTAX do Banco Central, com a API substituída por respostas simuladas."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from nfse_exportacao import ptax


class RespostaFalsa:
    def __init__(self, valores: list[dict[str, Any]]) -> None:
        self._valores = valores

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, Any]:
        return {"value": self._valores}


def test_volta_ao_ultimo_dia_util(monkeypatch: pytest.MonkeyPatch) -> None:
    pedidas: list[str] = []

    def get(url: str, params: dict[str, str], timeout: int) -> RespostaFalsa:
        pedidas.append(params["@dataCotacao"])
        if params["@dataCotacao"] == "'03-06-2026'":  # sexta-feira
            return RespostaFalsa([{"cotacaoVenda": 5.4321, "dataHoraCotacao": "2026-03-06 13:00:00"}])
        return RespostaFalsa([])

    monkeypatch.setattr(ptax.requests, "get", get)
    assert ptax.cotacao_usd_brl(dt.date(2026, 3, 8)) == Decimal("5.4321")  # domingo
    assert pedidas == ["'03-08-2026'", "'03-07-2026'", "'03-06-2026'"]


def test_sem_cotacao_em_sete_dias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ptax.requests, "get", lambda url, params, timeout: RespostaFalsa([]))
    with pytest.raises(RuntimeError, match="7 dias"):
        ptax.cotacao_usd_brl(dt.date(2026, 3, 8))
