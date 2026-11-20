"""Cliente mTLS da Sefin Nacional, com o `requests` substituído por respostas simuladas."""

from __future__ import annotations

import base64
import gzip
import os
import stat
from pathlib import Path
from typing import Any

import pytest
import requests
from cryptography import x509

from conftest import SENHA_PFX
from nfse_exportacao import nfse_client
from nfse_exportacao.nfse_client import consultar_nfse, descompactar_nfse, dps_existe, enviar_dps

BASE = "https://sefin.producaorestrita.nfse.gov.br"
Certificado = tuple[Path, x509.Certificate]


class RespostaFalsa:
    def __init__(self, status: int, corpo: Any = None, texto: str = "") -> None:
        self.status_code = status
        self._corpo = corpo
        self.text = texto

    def json(self) -> Any:
        if self._corpo is None:
            raise ValueError("sem JSON")
        return self._corpo

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def _gzip_b64(dados: bytes) -> str:
    return base64.b64encode(gzip.compress(dados)).decode("ascii")


def test_envio_em_json_com_gzip_e_base64(monkeypatch: pytest.MonkeyPatch, certificado: Certificado) -> None:
    # 415: XML cru no corpo é recusado; o servidor espera JSON com o XML em gzip + base64
    chamada: dict[str, Any] = {}

    def post(url: str, **kwargs: Any) -> RespostaFalsa:
        chamada["url"] = url
        chamada.update(kwargs)
        cert_pem, key_pem = kwargs["cert"]
        chamada["modo_da_chave"] = stat.S_IMODE(os.stat(key_pem).st_mode)
        chamada["chave_existe"] = Path(key_pem).exists() and Path(cert_pem).exists()
        return RespostaFalsa(201, {"chaveAcesso": "K" * 50, "nfseXmlGZipB64": _gzip_b64(b"<NFSe/>")})

    monkeypatch.setattr(nfse_client.requests, "post", post)
    resposta = enviar_dps(b"<DPS>assinada</DPS>", BASE + "/", certificado[0], SENHA_PFX)

    assert chamada["url"] == BASE + "/SefinNacional/nfse"
    assert set(chamada["json"]) == {"dpsXmlGZipB64"}
    assert gzip.decompress(base64.b64decode(chamada["json"]["dpsXmlGZipB64"])) == b"<DPS>assinada</DPS>"
    assert chamada["chave_existe"]
    assert chamada["modo_da_chave"] == 0o600
    assert resposta["_http_status"] == 201
    assert descompactar_nfse(resposta["nfseXmlGZipB64"]) == b"<NFSe/>"


def test_rejeicao_devolve_os_erros_sem_excecao(monkeypatch: pytest.MonkeyPatch, certificado: Certificado) -> None:
    erros = {"erros": [{"Codigo": "E0166", "Descricao": "regApTribSN obrigatório"}]}
    monkeypatch.setattr(nfse_client.requests, "post", lambda url, **kw: RespostaFalsa(400, erros))
    resposta = enviar_dps(b"<DPS/>", BASE, certificado[0], SENHA_PFX)
    assert resposta["_http_status"] == 400
    assert resposta["erros"][0]["Codigo"] == "E0166"


def test_resposta_que_nao_e_json(monkeypatch: pytest.MonkeyPatch, certificado: Certificado) -> None:
    monkeypatch.setattr(nfse_client.requests, "post", lambda url, **kw: RespostaFalsa(502, None, "Bad Gateway"))
    with pytest.raises(requests.HTTPError):
        enviar_dps(b"<DPS/>", BASE, certificado[0], SENHA_PFX)


@pytest.mark.parametrize(("status", "existe"), [(200, True), (404, False)])
def test_dps_existe(monkeypatch: pytest.MonkeyPatch, certificado: Certificado, status: int, existe: bool) -> None:
    urls: list[str] = []

    def head(url: str, **kwargs: Any) -> RespostaFalsa:
        urls.append(url)
        return RespostaFalsa(status)

    monkeypatch.setattr(nfse_client.requests, "head", head)
    assert dps_existe("DPS123", BASE, certificado[0], SENHA_PFX) is existe
    assert urls == [BASE + "/SefinNacional/dps/DPS123"]


def test_dps_existe_com_erro_do_servidor_nao_responde_que_nao(
    monkeypatch: pytest.MonkeyPatch, certificado: Certificado
) -> None:
    # um erro do servidor não pode ser lido como "não existe", ou a DPS seria reenviada
    monkeypatch.setattr(nfse_client.requests, "head", lambda url, **kw: RespostaFalsa(500))
    with pytest.raises(requests.HTTPError):
        dps_existe("DPS123", BASE, certificado[0], SENHA_PFX)


def test_consulta_por_chave(monkeypatch: pytest.MonkeyPatch, certificado: Certificado) -> None:
    urls: list[str] = []

    def get(url: str, **kwargs: Any) -> RespostaFalsa:
        urls.append(url)
        return RespostaFalsa(200, {"chaveAcesso": "K", "nfseXmlGZipB64": _gzip_b64(b"<NFSe/>")})

    monkeypatch.setattr(nfse_client.requests, "get", get)
    nfse = consultar_nfse("K", BASE, certificado[0], SENHA_PFX)
    assert urls == [BASE + "/SefinNacional/nfse/K"]
    assert nfse["_http_status"] == 200


def test_pfx_com_senha_errada_e_recusado(certificado: Certificado) -> None:
    with pytest.raises(ValueError):
        enviar_dps(b"<DPS/>", BASE, certificado[0], "senha-errada")
