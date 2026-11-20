"""O fluxo de emissão: nunca reenviar uma DPS que já existe, e o contador só avança com a nota emitida.

As chamadas ao servidor são substituídas; a leitura, a montagem, a validação e a assinatura são as
reais.
"""

from __future__ import annotations

import base64
import gzip
from pathlib import Path
from typing import Any

import pytest
import requests
from lxml import etree

from conftest import CNPJ, MUNICIPIO
from nfse_exportacao import main as cli
from nfse_exportacao.config import Settings
from nfse_exportacao.signer import NS_DS, NS_NFSE
from nfse_exportacao.state import DpsCounter
from nfse_exportacao.xml_builder import id_dps, validar_dps


@pytest.fixture
def contador(settings: Settings) -> DpsCounter:
    return DpsCounter(settings.prestador_cnpj, cli.SERIE_DPS, settings.nfse_ambiente, settings.output_dir)


def _nao_pode(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("esta chamada não deveria acontecer")


def test_dps_existente_e_recuperada_sem_reenvio(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, contador: DpsCounter, recibo: Path
) -> None:
    # reenviar uma DPS cuja nota já existe dispararia o cancelamento por substituição
    monkeypatch.setattr(cli, "dps_existe", lambda *a: True)
    monkeypatch.setattr(cli, "consultar_chave_por_id", lambda *a: "CHAVE")
    monkeypatch.setattr(cli, "consultar_nfse", lambda *a: {"chaveAcesso": "CHAVE", "_http_status": 200})
    monkeypatch.setattr(cli, "processar_recibo", _nao_pode)
    monkeypatch.setattr(cli, "enviar_dps", _nao_pode)

    ok, mensagem = cli.emitir_um(recibo, settings, contador)

    assert ok and mensagem == "recuperada nDPS=1 chave=CHAVE"
    assert contador.peek() == 2
    assert (settings.output_dir / "nfse-wise_teste.json").exists()


def test_falha_ao_recuperar_nao_reenvia_nem_avanca(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, contador: DpsCounter, recibo: Path
) -> None:
    monkeypatch.setattr(cli, "dps_existe", lambda *a: True)
    monkeypatch.setattr(cli, "consultar_chave_por_id", lambda *a: (_ for _ in ()).throw(requests.HTTPError("403")))
    monkeypatch.setattr(cli, "processar_recibo", _nao_pode)

    ok, mensagem = cli.emitir_um(recibo, settings, contador)

    assert not ok and "falha ao recuperar" in mensagem
    assert contador.peek() == 1


def test_emissao_aceita_avanca_o_contador(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, contador: DpsCounter, recibo: Path
) -> None:
    monkeypatch.setattr(cli, "dps_existe", lambda *a: False)
    monkeypatch.setattr(cli, "processar_recibo", lambda caminho, n: (caminho, {"_http_status": 201, "chaveAcesso": "K"}))

    assert cli.emitir_um(recibo, settings, contador) == (True, "emitida nDPS=1 chave=K")
    assert contador.peek() == 2


def test_rejeicao_mantem_o_numero_para_a_correcao(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, contador: DpsCounter, recibo: Path
) -> None:
    rejeicao = {"_http_status": 400, "erros": [{"Codigo": "E0166", "Descricao": "regApTribSN obrigatório"}]}
    monkeypatch.setattr(cli, "dps_existe", lambda *a: False)
    monkeypatch.setattr(cli, "processar_recibo", lambda caminho, n: (caminho, rejeicao))

    ok, mensagem = cli.emitir_um(recibo, settings, contador)

    assert not ok
    assert "E0166: regApTribSN obrigatório" in mensagem and "nDPS 1 mantido" in mensagem
    assert contador.peek() == 1


def test_timeout_nao_avanca_o_contador(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, contador: DpsCounter, recibo: Path
) -> None:
    # depois de um timeout, rodar de novo é seguro: o contador não andou e o HEAD descobre o que houve
    def timeout(*args: Any) -> Any:
        raise requests.Timeout("sem resposta")

    monkeypatch.setattr(cli, "dps_existe", lambda *a: False)
    monkeypatch.setattr(cli, "processar_recibo", timeout)

    with pytest.raises(requests.Timeout):
        cli.emitir_um(recibo, settings, contador)
    assert contador.peek() == 1


def test_numero_forcado(monkeypatch: pytest.MonkeyPatch, settings: Settings, contador: DpsCounter, recibo: Path) -> None:
    consultados: list[str] = []

    def existe(identificador: str, *a: Any) -> bool:
        consultados.append(identificador)
        return False

    monkeypatch.setattr(cli, "dps_existe", existe)
    monkeypatch.setattr(cli, "processar_recibo", lambda caminho, n: (caminho, {"_http_status": 201, "chaveAcesso": "K"}))

    assert cli.emitir_um(recibo, settings, contador, numero_override=7)[0]
    assert consultados == [id_dps(CNPJ, MUNICIPIO, "1", 7)]
    assert contador.peek() == 8


def test_ponta_a_ponta_do_recibo_a_nota_salva(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, contador: DpsCounter, recibo: Path
) -> None:
    enviado: dict[str, bytes] = {}

    def enviar(xml_assinado: bytes, base_url: str, cert_path: Path, cert_password: str) -> dict[str, Any]:
        enviado["xml"] = xml_assinado
        enviado["url"] = base_url.encode()
        nfse = base64.b64encode(gzip.compress(b"<NFSe>autorizada</NFSe>")).decode()
        return {"_http_status": 201, "chaveAcesso": "K" * 50, "nfseXmlGZipB64": nfse}

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "dps_existe", lambda *a: False)
    monkeypatch.setattr(cli, "enviar_dps", enviar)

    ok, _ = cli.emitir_um(recibo, settings, contador)

    assert ok
    assert enviado["url"] == settings.nfse_base_url_homolog.encode()
    raiz = etree.fromstring(enviado["xml"])
    validar_dps(enviado["xml"])
    assert raiz.find(f"{{{NS_DS}}}Signature") is not None

    def campo(nome: str) -> str | None:
        return raiz.findtext(f".//{{{NS_NFSE}}}{nome}")

    assert campo("tpAmb") == "2"  # producaorestrita
    assert campo("vServ") == "5123.45"  # "Total to", em reais
    assert campo("vServMoeda") == "1000.00"  # "Amount paid by", em dólares
    assert campo("dCompet") == "2026-03-03"  # data "Funded"
    assert campo("xDescServ") == (
        "Servicos de desenvolvimento de software — Transferência via Wise #1234567890 em 03/03/2026"
    )
    saida = settings.output_dir / "nfse-wise_teste"
    assert saida.with_suffix(".xml").read_bytes() == b"<NFSe>autorizada</NFSe>"
    assert saida.with_suffix(".json").exists()
    assert contador.peek() == 2


def test_lote_segue_apos_uma_falha_e_move_so_os_aceitos(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, contador: DpsCounter, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pasta = tmp_path / "recibos"
    emitidas = tmp_path / "emitidas"
    pasta.mkdir()
    for nome in ("a_rejeitado.pdf", "b_aceito.pdf", "c_erro.pdf"):
        (pasta / nome).write_bytes(b"%PDF ficticio")

    def emitir(caminho: Path, *a: Any) -> tuple[bool, str]:
        if caminho.name == "c_erro.pdf":
            raise RuntimeError("erro inesperado de teste")
        return caminho.name == "b_aceito.pdf", "mensagem"

    monkeypatch.setattr(cli, "emitir_um", emitir)

    assert cli._rodar_batch(pasta, emitidas, settings, contador) == 2
    assert sorted(p.name for p in pasta.iterdir()) == ["a_rejeitado.pdf", "c_erro.pdf"]
    assert [p.name for p in emitidas.iterdir()] == ["b_aceito.pdf"]
    assert "Resumo: 1 sucesso(s), 2 falha(s)" in capsys.readouterr().out


def test_lote_sem_pdfs(settings: Settings, contador: DpsCounter, tmp_path: Path) -> None:
    assert cli._rodar_batch(tmp_path, None, settings, contador) == 1
    assert cli._rodar_batch(tmp_path / "nao-existe", None, settings, contador) == 1


def test_peek_na_linha_de_comando(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr("sys.argv", ["main", "--peek"])
    assert cli.main() == 0
    assert capsys.readouterr().out.strip() == f"Próximo nDPS [producaorestrita] CNPJ {CNPJ} série 1: 1"


def test_recibo_inexistente_na_linha_de_comando(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr("sys.argv", ["main", str(tmp_path / "nao-existe.pdf")])
    assert cli.main() == 1
