"""Leitura do comprovante Wise."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from conftest import texto_wise
from nfse_exportacao.parser import extrair


def test_le_valores_e_datas_do_comprovante(recibo: Path) -> None:
    r = extrair(recibo)
    assert r.valor_usd == Decimal("1000.00")
    assert r.valor_brl == Decimal("5123.45")
    assert r.data_pagamento == dt.date(2026, 3, 3)  # a data "Funded", não a de criação
    assert r.referencia == "Wise #1234567890"
    assert r.descricao_detalhe == "Transferência via Wise #1234567890 em 03/03/2026"


def test_separador_de_milhar_e_removido(tmp_path: Path) -> None:
    caminho = tmp_path / "wise_grande.txt"
    caminho.write_text(texto_wise(usd="12,345.67", brl="65,432.10"), encoding="utf-8")
    r = extrair(caminho)
    assert (r.valor_usd, r.valor_brl) == (Decimal("12345.67"), Decimal("65432.10"))


def test_reconhece_pelo_nome_do_arquivo(tmp_path: Path) -> None:
    caminho = tmp_path / "wise_sem_cabecalho.txt"
    caminho.write_text(texto_wise(sem="cabecalho"), encoding="utf-8")
    assert extrair(caminho).valor_brl == Decimal("5123.45")


@pytest.mark.parametrize(("linha", "campo"), [("id", "id"), ("datas", "funded"), ("usd", "usd"), ("brl", "brl")])
def test_campo_faltando_e_nomeado(tmp_path: Path, linha: str, campo: str) -> None:
    caminho = tmp_path / "wise_incompleto.txt"
    caminho.write_text(texto_wise(sem=linha), encoding="utf-8")
    with pytest.raises(ValueError, match=campo):
        extrair(caminho)


def test_formato_desconhecido_e_recusado(tmp_path: Path) -> None:
    caminho = tmp_path / "outro.txt"
    caminho.write_text("Invoice 42\nTotal 100.00 USD\n", encoding="utf-8")
    with pytest.raises(ValueError, match="não reconhecido"):
        extrair(caminho)
