from __future__ import annotations

import atexit
import base64
import gzip
import os
import tempfile
from pathlib import Path

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12


def _pfx_para_pem(pfx_path: Path, password: str) -> tuple[Path, Path]:
    """Converte .pfx em arquivos PEM temporários — limpos no fim do processo."""
    with pfx_path.open("rb") as f:
        key, cert, _ = pkcs12.load_key_and_certificates(f.read(), password.encode())
    if key is None or cert is None:
        raise RuntimeError("PFX sem chave ou certificado")

    cert_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pem")
    key_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pem")
    cert_tmp.write(cert.public_bytes(serialization.Encoding.PEM))
    key_tmp.write(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_tmp.close()
    key_tmp.close()
    os.chmod(key_tmp.name, 0o600)
    atexit.register(lambda: (os.unlink(cert_tmp.name), os.unlink(key_tmp.name)))
    return Path(cert_tmp.name), Path(key_tmp.name)


def descompactar_nfse(b64_gzip: str) -> bytes:
    """Reverte o encoding (gzip + base64) usado pela API para devolver XMLs."""
    return gzip.decompress(base64.b64decode(b64_gzip))


def _mtls_kwargs(cert_path: Path, cert_password: str) -> dict:
    cert_pem, key_pem = _pfx_para_pem(cert_path, cert_password)
    return {"cert": (str(cert_pem), str(key_pem))}


def dps_existe(id_dps: str, base_url: str, cert_path: Path, cert_password: str) -> bool:
    """HEAD /SefinNacional/dps/{id} — retorna True se já há NFS-e gerada para esse DPS."""
    url = f"{base_url.rstrip('/')}/SefinNacional/dps/{id_dps}"
    resp = requests.head(url, timeout=30, **_mtls_kwargs(cert_path, cert_password))
    if resp.status_code == 200:
        return True
    if resp.status_code == 404:
        return False
    resp.raise_for_status()
    return False


def consultar_chave_por_id(id_dps: str, base_url: str, cert_path: Path, cert_password: str) -> str:
    """GET /SefinNacional/dps/{id} — retorna chaveAcesso da NFS-e gerada (só se o cert for ator)."""
    url = f"{base_url.rstrip('/')}/SefinNacional/dps/{id_dps}"
    resp = requests.get(
        url,
        headers={"Accept": "application/json"},
        timeout=30,
        **_mtls_kwargs(cert_path, cert_password),
    )
    resp.raise_for_status()
    return resp.json()["chaveAcesso"]


def consultar_nfse(chave: str, base_url: str, cert_path: Path, cert_password: str) -> dict:
    """GET /SefinNacional/nfse/{chaveAcesso} — retorna a NFS-e em JSON (com nfseXmlGZipB64)."""
    url = f"{base_url.rstrip('/')}/SefinNacional/nfse/{chave}"
    resp = requests.get(
        url,
        headers={"Accept": "application/json"},
        timeout=30,
        **_mtls_kwargs(cert_path, cert_password),
    )
    data = resp.json()
    data["_http_status"] = resp.status_code
    resp.raise_for_status()
    return data


def enviar_dps(xml_assinado: bytes, base_url: str, cert_path: Path, cert_password: str) -> dict:
    """Envia DPS assinada para POST /SefinNacional/nfse via mTLS.

    Encoding obrigatório: XML → gzip → base64 → campo `dpsXmlGZipB64` em JSON.
    ATENÇÃO: reenviar DPS com chave de NFS-e já existente dispara cancelamento por
    substituição automático — NÃO fazer retry cego em caso de timeout.
    """
    cert_pem, key_pem = _pfx_para_pem(cert_path, cert_password)
    url = f"{base_url.rstrip('/')}/SefinNacional/nfse"

    payload = {"dpsXmlGZipB64": base64.b64encode(gzip.compress(xml_assinado)).decode("ascii")}
    resp = requests.post(
        url,
        json=payload,
        headers={"Accept": "application/json"},
        cert=(str(cert_pem), str(key_pem)),
        timeout=60,
    )

    try:
        data = resp.json()
    except ValueError:
        resp.raise_for_status()
        raise RuntimeError(f"Resposta inesperada (não-JSON): {resp.text[:500]}")
    data["_http_status"] = resp.status_code
    return data
