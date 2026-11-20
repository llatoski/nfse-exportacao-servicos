"""Contador persistente do número da DPS."""

from __future__ import annotations

from pathlib import Path

from conftest import CNPJ
from nfse_exportacao.state import DpsCounter


def test_comeca_em_1_e_avanca_a_partir_do_numero_usado(tmp_path: Path) -> None:
    contador = DpsCounter(CNPJ, "1", "producao", tmp_path)
    assert contador.peek() == 1
    contador.consume(1)
    assert contador.peek() == 2
    contador.consume(7)  # recuperação manual com --numero 7
    assert contador.peek() == 8


def test_persiste_entre_execucoes(tmp_path: Path) -> None:
    DpsCounter(CNPJ, "1", "producao", tmp_path).consume(4)
    assert DpsCounter(CNPJ, "1", "producao", tmp_path).peek() == 5


def test_ambientes_e_series_tem_contadores_separados(tmp_path: Path) -> None:
    DpsCounter(CNPJ, "1", "producao", tmp_path).consume(10)
    assert DpsCounter(CNPJ, "1", "producaorestrita", tmp_path).peek() == 1
    assert DpsCounter(CNPJ, "2", "producao", tmp_path).peek() == 1
    assert (tmp_path / f".dps-counter-{CNPJ}-1-producao.json").exists()


def test_cria_a_pasta_de_saida(tmp_path: Path) -> None:
    pasta = tmp_path / "out" / "aninhada"
    DpsCounter(CNPJ, "1", "producao", pasta)
    assert pasta.is_dir()
