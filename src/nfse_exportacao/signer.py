from __future__ import annotations

import base64
from copy import deepcopy
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import pkcs12
from lxml import etree

NS_NFSE = "http://www.sped.fazenda.gov.br/nfse"
NS_DS = "http://www.w3.org/2000/09/xmldsig#"
ALG_C14N = "http://www.w3.org/2001/10/xml-exc-c14n#"
ALG_SHA1 = "http://www.w3.org/2000/09/xmldsig#sha1"
ALG_RSA_SHA1 = "http://www.w3.org/2000/09/xmldsig#rsa-sha1"
ALG_ENVELOPED = "http://www.w3.org/2000/09/xmldsig#enveloped-signature"


def _ds(tag: str) -> str:
    return f"{{{NS_DS}}}{tag}"


def _construir_signed_info(ref_id: str, digest_value: str) -> etree._Element:
    """Constrói SignedInfo como elemento raiz isolado — evita o bug de c14n do lxml
    quando há redefinição encadeada de default namespace (DPS/nfse → Signature/ds#)."""
    si = etree.Element(_ds("SignedInfo"), nsmap={None: NS_DS})
    etree.SubElement(si, _ds("CanonicalizationMethod"), Algorithm=ALG_C14N)
    etree.SubElement(si, _ds("SignatureMethod"), Algorithm=ALG_RSA_SHA1)
    ref = etree.SubElement(si, _ds("Reference"), URI=f"#{ref_id}")
    transforms = etree.SubElement(ref, _ds("Transforms"))
    etree.SubElement(transforms, _ds("Transform"), Algorithm=ALG_ENVELOPED)
    etree.SubElement(transforms, _ds("Transform"), Algorithm=ALG_C14N)
    etree.SubElement(ref, _ds("DigestMethod"), Algorithm=ALG_SHA1)
    etree.SubElement(ref, _ds("DigestValue")).text = digest_value
    return si


def assinar_xml(xml_bytes: bytes, pfx_path: Path, password: str) -> bytes:
    """Assina a DPS no padrão XMLDSig enveloped, sem prefixo no namespace da Signature."""
    with pfx_path.open("rb") as f:
        private_key, cert, _ = pkcs12.load_key_and_certificates(f.read(), password.encode())
    if private_key is None or cert is None:
        raise RuntimeError("PFX sem chave privada ou certificado")

    root = etree.fromstring(xml_bytes)
    inf = root.find(f"{{{NS_NFSE}}}infDPS")
    if inf is None or "Id" not in inf.attrib:
        raise RuntimeError("XML não contém <infDPS Id='...'>")
    ref_id = inf.attrib["Id"]

    inf_c14n = etree.tostring(inf, method="c14n", exclusive=True, with_comments=False)
    digest_value = base64.b64encode(_sha1(inf_c14n)).decode("ascii")

    sig = etree.SubElement(root, _ds("Signature"), nsmap={None: NS_DS})
    signed_info_in_tree = _construir_signed_info(ref_id, digest_value)
    # Move children to in-tree SignedInfo (mantém referência ao mesmo elemento)
    sig.append(signed_info_in_tree)
    si_c14n = etree.tostring(signed_info_in_tree, method="c14n", exclusive=True, with_comments=False)
    sig_value = private_key.sign(si_c14n, padding.PKCS1v15(), hashes.SHA1())

    etree.SubElement(sig, _ds("SignatureValue")).text = base64.b64encode(sig_value).decode("ascii")
    key_info = etree.SubElement(sig, _ds("KeyInfo"))
    x509_data = etree.SubElement(key_info, _ds("X509Data"))
    cert_b64 = base64.b64encode(cert.public_bytes(serialization.Encoding.DER)).decode("ascii")
    etree.SubElement(x509_data, _ds("X509Certificate")).text = cert_b64

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


def _sha1(data: bytes) -> bytes:
    h = hashes.Hash(hashes.SHA1())
    h.update(data)
    return h.finalize()
