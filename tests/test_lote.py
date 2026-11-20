"""O lote da interface: preparar sem enviar nada, emitir exatamente o que foi conferido, parar no primeiro
problema sem pular números. Os recibos são PDFs de verdade, lidos pelo pdfplumber; o servidor é simulado."""

from __future__ import annotations

import base64
import datetime as dt
import gzip
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import requests
from cryptography import x509

from conftest import CNPJ, MUNICIPIO, SENHA_ERRADA, SENHA_PFX
from nfse_exportacao import lote as L
from nfse_exportacao.config import Settings
from nfse_exportacao.state import DpsCounter
from nfse_exportacao.xml_builder import id_dps, validar_dps


@pytest.fixture
def contador(settings: Settings) -> DpsCounter:
    return DpsCounter(settings.prestador_cnpj, "1", settings.nfse_ambiente, settings.output_dir)


def _nao_pode(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("esta chamada ao servidor não deveria acontecer")


def _nfse(vserv: str) -> str:
    xml = (
        '<NFSe xmlns="http://www.sped.fazenda.gov.br/nfse"><infNFSe><DPS><infDPS><valores><vServPrest>'
        f"<vServ>{vserv}</vServ></vServPrest></valores></infDPS></DPS></infNFSe></NFSe>"
    ).encode()
    return base64.b64encode(gzip.compress(xml)).decode()


class ServidorFalso:
    """Responde ao envio; `respostas` por número da DPS (o padrão é aceitar)."""

    def __init__(self, respostas: dict[int, Any] | None = None, existentes: dict[int, str] | None = None) -> None:
        self.respostas = respostas or {}
        self.existentes = existentes or {}  # número da DPS → vServ da nota que já está no servidor
        self.enviados: list[bytes] = []

    @staticmethod
    def _numero(identificador_ou_xml: str | bytes) -> int:
        texto = identificador_ou_xml.decode() if isinstance(identificador_ou_xml, bytes) else identificador_ou_xml
        inicio = texto.index("DPS" + MUNICIPIO)
        return int(texto[inicio + 30 : inicio + 45])

    def instalar(self, monkeypatch: pytest.MonkeyPatch) -> ServidorFalso:
        monkeypatch.setattr(L, "dps_existe", lambda ident, *a: self._numero(ident) in self.existentes)
        monkeypatch.setattr(L, "consultar_chave_por_id", lambda ident, *a: f"CHAVE{self._numero(ident)}")
        monkeypatch.setattr(
            L,
            "consultar_nfse",
            lambda chave, *a: {"chaveAcesso": chave, "nfseXmlGZipB64": _nfse(self.existentes[int(chave[5:])])},
        )
        monkeypatch.setattr(L, "enviar_dps", self.enviar)
        return self

    def enviar(self, xml: bytes, *a: Any) -> dict[str, Any]:
        self.enviados.append(xml)
        n = self._numero(xml)
        resposta = self.respostas.get(n, {"_http_status": 201, "chaveAcesso": f"K{n}", "nfseXmlGZipB64": _nfse("1")})
        if isinstance(resposta, Exception):
            raise resposta
        return dict(resposta)


# --- preparar ---------------------------------------------------------------------------------------


def test_preparar_numera_em_sequencia_e_deixa_fora_o_recibo_com_erro(
    monkeypatch: pytest.MonkeyPatch, pasta: Path, settings: Settings, contador: DpsCounter
) -> None:
    monkeypatch.setattr(L, "dps_existe", _nao_pode)
    monkeypatch.setattr(L, "enviar_dps", _nao_pode)

    lote = L.preparar(pasta, settings, contador)

    assert [n.recibo.name for n in lote.notas] == ["wise_01.pdf", "wise_02.pdf", "wise_03.pdf", "wise_04.pdf"]
    assert [(n.recibo.name, n.numero_dps) for n in lote.prontas] == [
        ("wise_01.pdf", 1),
        ("wise_03.pdf", 2),
        ("wise_04.pdf", 3),
    ]
    assert [n.recibo.name for n in lote.fora] == ["wise_02.pdf"]
    assert lote.fora[0].erro == 'comprovante Wise sem valor recebido em BRL ("Total to")'
    assert lote.fora[0].numero_dps is None
    assert lote.total_brl == Decimal("18223.95")
    assert lote.total_usd == Decimal("3500.00")
    assert contador.peek() == 1  # preparar não consome número


def test_cada_dps_preparada_e_valida_assinada_e_identificada(
    pasta: Path, settings: Settings, contador: DpsCounter
) -> None:
    lote = L.preparar(pasta, settings, contador)
    primeira = lote.prontas[0]
    for nota in lote.prontas:
        validar_dps(nota.xml_assinado)
        assert b'<Signature xmlns="http://www.w3.org/2000/09/xmldsig#">' in nota.xml_assinado
        assert nota.id_dps == id_dps(CNPJ, MUNICIPIO, "1", nota.numero_dps or 0)
        assert f'Id="{nota.id_dps}"'.encode() in nota.xml_assinado
    assert primeira.referencia == "Wise #1000000001"
    assert primeira.competencia == dt.date(2026, 3, 3)
    assert primeira.tributos_aprox == Decimal("307.41")
    assert primeira.cambio == Decimal("5.1235")
    assert primeira.descricao.endswith("Transferência via Wise #1000000001 em 03/03/2026")


def test_preparar_comeca_onde_o_contador_esta(pasta: Path, settings: Settings, contador: DpsCounter) -> None:
    contador.consume(11)
    assert [n.numero_dps for n in L.preparar(pasta, settings, contador).prontas] == [12, 13, 14]


def test_impressao_do_pacote_muda_com_qualquer_byte(pasta: Path, settings: Settings, contador: DpsCounter) -> None:
    lote = L.preparar(pasta, settings, contador)
    antes = lote.impressao
    nota = lote.prontas[1]
    nota.xml_assinado = nota.xml_assinado.replace(b"10500.00", b"10500.01")
    assert lote.impressao != antes and len(antes) == 16


# --- emitir -----------------------------------------------------------------------------------------


def test_emite_exatamente_o_que_foi_conferido_e_move_os_recibos(
    monkeypatch: pytest.MonkeyPatch, pasta: Path, settings: Settings, contador: DpsCounter
) -> None:
    servidor = ServidorFalso().instalar(monkeypatch)
    lote = L.preparar(pasta, settings, contador)
    vistos: list[str] = []

    resultados = L.emitir(lote, settings, contador, pasta / "emitidas", lambda r: vistos.append(r.estado))

    assert servidor.enviados == [n.xml_assinado for n in lote.prontas]
    assert [r.estado for r in resultados] == vistos == ["emitida"] * 3
    assert [r.chave for r in resultados] == ["K1", "K2", "K3"]
    assert contador.peek() == 4
    assert sorted(p.name for p in (pasta / "emitidas").iterdir()) == ["wise_01.pdf", "wise_03.pdf", "wise_04.pdf"]
    assert (pasta / "wise_02.pdf").exists()  # o recibo fora do pacote fica onde estava
    assert (settings.output_dir / "nfse-wise_03.json").exists()
    assert (settings.output_dir / "nfse-wise_03.xml").exists()


def test_rejeicao_para_o_lote_sem_pular_numero(
    monkeypatch: pytest.MonkeyPatch, pasta: Path, settings: Settings, contador: DpsCounter
) -> None:
    rejeicao = {"_http_status": 400, "erros": [{"Codigo": "E0166", "Descricao": "regApTribSN obrigatório"}]}
    servidor = ServidorFalso(respostas={2: rejeicao}).instalar(monkeypatch)
    lote = L.preparar(pasta, settings, contador)

    resultados = L.emitir(lote, settings, contador, pasta / "emitidas")

    assert [r.estado for r in resultados] == ["emitida", "rejeitada", "não enviada"]
    assert "E0166: regApTribSN obrigatório" in resultados[1].mensagem
    assert len(servidor.enviados) == 2  # a terceira nunca saiu
    assert contador.peek() == 2  # o número recusado fica livre para a correção
    assert [p.name for p in (pasta / "emitidas").iterdir()] == ["wise_01.pdf"]
    assert (pasta / "wise_03.pdf").exists() and (pasta / "wise_04.pdf").exists()


def test_erro_de_rede_para_sem_avancar(
    monkeypatch: pytest.MonkeyPatch, pasta: Path, settings: Settings, contador: DpsCounter
) -> None:
    ServidorFalso(respostas={1: requests.ConnectionError("sem rota")}).instalar(monkeypatch)
    lote = L.preparar(pasta, settings, contador)

    resultados = L.emitir(lote, settings, contador, pasta / "emitidas")

    assert [r.estado for r in resultados] == ["erro", "não enviada", "não enviada"]
    assert "Rodar de novo é seguro" in resultados[0].mensagem
    assert contador.peek() == 1
    assert not (pasta / "emitidas").exists()


def test_dps_que_ja_existe_e_recuperada_e_nao_reenviada(
    monkeypatch: pytest.MonkeyPatch, pasta: Path, settings: Settings, contador: DpsCounter
) -> None:
    servidor = ServidorFalso(existentes={1: "5123.45"}).instalar(monkeypatch)
    lote = L.preparar(pasta, settings, contador)

    resultados = L.emitir(lote, settings, contador, pasta / "emitidas")

    assert [r.estado for r in resultados] == ["recuperada", "emitida", "emitida"]
    assert resultados[0].chave == "CHAVE1" and not resultados[0].aviso
    assert servidor.enviados == [n.xml_assinado for n in lote.prontas[1:]]
    assert (pasta / "emitidas" / "wise_01.pdf").exists()
    assert contador.peek() == 4


def test_nota_recuperada_com_outro_valor_para_e_o_recibo_fica(
    monkeypatch: pytest.MonkeyPatch, pasta: Path, settings: Settings, contador: DpsCounter
) -> None:
    # por exemplo: o arquivo do contador se perdeu e o número 1 já foi usado por outro recibo
    servidor = ServidorFalso(existentes={1: "999.00"}).instalar(monkeypatch)
    lote = L.preparar(pasta, settings, contador)

    resultados = L.emitir(lote, settings, contador, pasta / "emitidas")

    assert [r.estado for r in resultados] == ["recuperada", "não enviada", "não enviada"]
    assert resultados[0].divergente and "999.00" in resultados[0].aviso
    assert servidor.enviados == []
    assert (pasta / "wise_01.pdf").exists() and not (pasta / "emitidas").exists()
    assert (settings.output_dir / "nfse-nDPS-1-recuperada.json").exists()  # não leva o nome do recibo
    assert not (settings.output_dir / "nfse-wise_01.json").exists()
    assert contador.peek() == 2  # o número 1 está usado no servidor


def test_contador_que_mudou_depois_de_preparar_impede_o_envio(
    monkeypatch: pytest.MonkeyPatch, pasta: Path, settings: Settings, contador: DpsCounter
) -> None:
    servidor = ServidorFalso().instalar(monkeypatch)
    lote = L.preparar(pasta, settings, contador)
    contador.consume(1)  # outra emissão aconteceu no meio tempo

    with pytest.raises(L.LoteDesatualizado, match="era 1, agora 2"):
        L.emitir(lote, settings, contador)
    assert servidor.enviados == []


def test_ambiente_que_mudou_depois_de_preparar_impede_o_envio(
    monkeypatch: pytest.MonkeyPatch, pasta: Path, settings: Settings, contador: DpsCounter
) -> None:
    servidor = ServidorFalso().instalar(monkeypatch)
    lote = L.preparar(pasta, settings, contador)
    settings.nfse_ambiente = "producao"

    with pytest.raises(L.LoteDesatualizado, match="ambiente"):
        L.emitir(lote, settings, contador)
    assert servidor.enviados == []


def test_certificado_que_nao_abre_deixa_todos_fora_do_pacote(
    pasta: Path, settings: Settings, contador: DpsCounter
) -> None:
    settings.cert_password = SENHA_ERRADA
    lote = L.preparar(pasta, settings, contador)
    assert lote.prontas == [] and len(lote.fora) == 4


# --- certificado ------------------------------------------------------------------------------------


def test_certificado_valido(certificado: tuple[Path, x509.Certificate]) -> None:
    c = L.verificar_certificado(certificado[0], SENHA_PFX)
    assert c.ok and c.titular == "EMPRESA FICTICIA LTDA"
    assert c.valido_ate == certificado[1].not_valid_after_utc.date()
    assert c.mensagem.startswith("válido até")


def test_certificado_vencido(certificado: tuple[Path, x509.Certificate]) -> None:
    c = L.verificar_certificado(certificado[0], SENHA_PFX, hoje=dt.date(2099, 1, 1))
    assert not c.ok and "vencido" in c.mensagem


def test_certificado_com_senha_errada_ou_ausente(certificado: tuple[Path, x509.Certificate], tmp_path: Path) -> None:
    assert "senha errada" in L.verificar_certificado(certificado[0], "x").mensagem
    assert "não encontrado" in L.verificar_certificado(tmp_path / "nao.pfx", "x").mensagem
    assert not L.verificar_certificado(tmp_path, "x").ok
