"""Assinatura XMLDSig, conferida de forma independente do código que a produz."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from lxml import etree

from conftest import SENHA_PFX
from nfse_exportacao.signer import ALG_C14N, ALG_ENVELOPED, NS_DS, NS_NFSE, assinar_xml
from nfse_exportacao.xml_builder import DadosDPS, montar_dps_xml, validar_dps

Certificado = tuple[Path, x509.Certificate]


def _ds(nome: str) -> str:
    return f"{{{NS_DS}}}{nome}"


@pytest.fixture
def assinado(dados: DadosDPS, certificado: Certificado) -> bytes:
    return assinar_xml(montar_dps_xml(dados), certificado[0], SENHA_PFX)


def _sha1_b64(dados: bytes) -> str:
    h = hashes.Hash(hashes.SHA1())
    h.update(dados)
    return base64.b64encode(h.finalize()).decode("ascii")


def test_signature_sem_prefixo_de_namespace(assinado: bytes) -> None:
    # E1228: <ds:Signature> é recusado; o namespace tem de ser o default
    assert b'<Signature xmlns="http://www.w3.org/2000/09/xmldsig#">' in assinado
    assert b"ds:" not in assinado
    sig = etree.fromstring(assinado).find(_ds("Signature"))
    assert sig is not None and sig.prefix is None


def test_canonicalizacao_exclusiva(assinado: bytes) -> None:
    # E0714: a canonicalização inclusiva é recusada
    raiz = etree.fromstring(assinado)
    metodo = raiz.find(f".//{_ds('CanonicalizationMethod')}")
    assert metodo is not None and metodo.get("Algorithm") == ALG_C14N
    transforms = [t.get("Algorithm") for t in raiz.iter(_ds("Transform"))]
    assert transforms == [ALG_ENVELOPED, ALG_C14N]


def test_reference_aponta_para_infDPS(assinado: bytes) -> None:
    raiz = etree.fromstring(assinado)
    inf = raiz.find(f"{{{NS_NFSE}}}infDPS")
    assert inf is not None
    referencia = raiz.find(f".//{_ds('Reference')}")
    assert referencia is not None and referencia.get("URI") == f"#{inf.get('Id')}"


def test_assinatura_confere_com_a_chave_publica(assinado: bytes, certificado: Certificado) -> None:
    raiz = etree.fromstring(assinado)
    inf = raiz.find(f"{{{NS_NFSE}}}infDPS")
    sig = raiz.find(_ds("Signature"))
    assert inf is not None and sig is not None
    assert sig.findtext(f".//{_ds('DigestValue')}") == _sha1_b64(etree.tostring(inf, method="c14n", exclusive=True))
    signed_info = sig.find(_ds("SignedInfo"))
    assert signed_info is not None
    certificado[1].public_key().verify(
        base64.b64decode(sig.findtext(_ds("SignatureValue")) or ""),
        etree.tostring(signed_info, method="c14n", exclusive=True),
        padding.PKCS1v15(),
        hashes.SHA1(),
    )


def test_alterar_um_valor_depois_de_assinar_quebra_o_digest(assinado: bytes) -> None:
    raiz = etree.fromstring(assinado)
    v_serv = raiz.find(f".//{{{NS_NFSE}}}vServ")
    assert v_serv is not None
    v_serv.text = "9999.99"
    inf = raiz.find(f"{{{NS_NFSE}}}infDPS")
    assert inf is not None
    digest = raiz.findtext(f".//{_ds('DigestValue')}")
    assert digest != _sha1_b64(etree.tostring(inf, method="c14n", exclusive=True))


def test_assinatura_de_outra_chave_nao_confere(assinado: bytes) -> None:
    outra = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key()
    sig = etree.fromstring(assinado).find(_ds("Signature"))
    assert sig is not None
    signed_info = sig.find(_ds("SignedInfo"))
    assert signed_info is not None
    with pytest.raises(InvalidSignature):
        outra.verify(
            base64.b64decode(sig.findtext(_ds("SignatureValue")) or ""),
            etree.tostring(signed_info, method="c14n", exclusive=True),
            padding.PKCS1v15(),
            hashes.SHA1(),
        )


def test_certificado_vai_em_KeyInfo(assinado: bytes, certificado: Certificado) -> None:
    der = base64.b64decode(etree.fromstring(assinado).findtext(f".//{_ds('X509Certificate')}") or "")
    assert der == certificado[1].public_bytes(serialization.Encoding.DER)


def test_dps_assinada_continua_valida_contra_o_xsd(assinado: bytes) -> None:
    validar_dps(assinado)


def test_senha_errada_do_pfx_e_recusada(dados: DadosDPS, certificado: Certificado) -> None:
    with pytest.raises(ValueError):
        assinar_xml(montar_dps_xml(dados), certificado[0], "senha-errada")
