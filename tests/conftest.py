"""Fixtures comuns. Todos os testes rodam offline: qualquer conexão de rede é recusada.

Os dados são fictícios: o CNPJ 11.222.333/0001-81 é um número de exemplo com dígitos verificadores
válidos, o município é São Paulo (IBGE 3550308) e o certificado é autoassinado, gerado na hora no
lugar de um A1 ICP-Brasil.
"""

from __future__ import annotations

import datetime as dt
import socket
from decimal import Decimal
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from nfse_exportacao.config import Settings
from nfse_exportacao.xml_builder import DadosDPS

CNPJ = "11222333000181"
MUNICIPIO = "3550308"
SENHA_PFX = "senha-de-teste"
SENHA_ERRADA = "nao-e-esta"


@pytest.fixture(autouse=True)
def _sem_rede(monkeypatch: pytest.MonkeyPatch) -> None:
    def recusar(*args: object, **kwargs: object) -> None:
        raise OSError("acesso à rede bloqueado nos testes")

    monkeypatch.setattr(socket.socket, "connect", recusar)
    monkeypatch.setattr(socket, "create_connection", recusar)


@pytest.fixture(scope="session")
def certificado(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, x509.Certificate]:
    """Um .pfx com chave RSA e certificado autoassinado, no papel do A1."""
    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "EMPRESA FICTICIA LTDA")])
    agora = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(nome)
        .issuer_name(nome)
        .public_key(chave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(agora - dt.timedelta(minutes=1))
        .not_valid_after(agora + dt.timedelta(days=1))
        .sign(chave, hashes.SHA256())
    )
    pfx = tmp_path_factory.mktemp("cert") / "teste.pfx"
    pfx.write_bytes(
        pkcs12.serialize_key_and_certificates(
            b"teste", chave, cert, None, serialization.BestAvailableEncryption(SENHA_PFX.encode())
        )
    )
    return pfx, cert


@pytest.fixture
def dados() -> DadosDPS:
    return DadosDPS(
        numero_dps=7,
        serie_dps="1",
        data_emissao=dt.date(2026, 3, 3),
        competencia=dt.date(2026, 3, 3),
        tp_ambiente="2",
        valor_servico_brl=Decimal("5123.45"),
        valor_servico_usd=Decimal("1000.00"),
        descricao="Servicos de desenvolvimento de software",
        codigo_tributacao_nacional="010401",
        prestador_cnpj=CNPJ,
        prestador_municipio_ibge=MUNICIPIO,
        prestador_cep="01001000",
        prestador_logradouro="R EXEMPLO",
        prestador_numero="1",
        prestador_bairro="CENTRO",
        tomador_nome="EXAMPLE CLIENT INC",
        tomador_pais="US",
        tomador_cep_ext="00000",
        tomador_cidade="CITY",
        tomador_estado="ST",
        tomador_logradouro="STREET",
        tomador_bairro="NA",
        pais_resultado="US",
        aliq_simples_aprox=Decimal("6"),
    )


@pytest.fixture
def settings(tmp_path: Path, certificado: tuple[Path, x509.Certificate]) -> Settings:
    """Configuração completa, sem ler nenhum .env."""
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        cert_path=certificado[0],
        cert_password=SENHA_PFX,
        nfse_ambiente="producaorestrita",
        prestador_cnpj=CNPJ,
        prestador_municipio_ibge=MUNICIPIO,
        prestador_cep="01001000",
        prestador_logradouro="R EXEMPLO",
        prestador_numero="1",
        prestador_bairro="CENTRO",
        servico_descricao="Servicos de desenvolvimento de software",
        tomador_nome="EXAMPLE CLIENT INC",
        tomador_pais="US",
        tomador_cep_ext="00000",
        tomador_cidade="CITY",
        tomador_estado="ST",
        tomador_logradouro="STREET",
        tomador_bairro="NA",
        aliq_simples_aprox=6.0,
        output_dir=tmp_path / "out",
    )


def texto_wise(
    numero: str = "1234567890",
    usd: str = "1,000.00",
    brl: str = "5,123.45",
    *,
    sem: str | None = None,
) -> str:
    """Texto no formato que o pdfplumber extrai de um comprovante Wise (duas colunas mescladas).

    Montado à mão a partir das expressões de `parser.py`, não copiado de um comprovante real.
    `sem` retira uma das linhas, para testar um campo faltando.
    """
    linhas = {
        "cabecalho": "Wise Brasil Corretora de Cambio",
        "id": f"Transfer #{numero}",
        "rotulo_datas": "Transfer created Funded",
        "datas": "March 2, 2026 10:00 AM March 3, 2026",
        "rotulo_usd": "Amount paid by EXAMPLE CLIENT INC",
        "usd": f"{usd} USD",
        "rotulo_brl": "Total to EMPRESA FICTICIA LTDA",
        "brl": f"{brl} BRL",
    }
    if sem is not None:
        del linhas[sem]
    return "\n".join(linhas.values()) + "\n"


@pytest.fixture
def recibo(tmp_path: Path) -> Path:
    caminho = tmp_path / "wise_teste.txt"
    caminho.write_text(texto_wise(), encoding="utf-8")
    return caminho


def pdf_de_texto(linhas: list[str]) -> bytes:
    """Um PDF de uma página, uma linha de texto em Helvetica embaixo da outra.

    Escrito à mão (sem biblioteca de PDF) para que os testes passem pelo `pdfplumber` de verdade.
    """
    comandos = ["BT", "/F1 11 Tf", "14 TL", "50 780 Td"]
    for linha in linhas:
        texto = linha.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        comandos.append(f"({texto}) Tj T*")
    comandos.append("ET")
    conteudo = "\n".join(comandos).encode("latin-1")
    objetos = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(conteudo) + conteudo + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    posicoes = []
    for i, objeto in enumerate(objetos, start=1):
        posicoes.append(len(pdf))
        pdf += b"%d 0 obj\n" % i + objeto + b"\nendobj\n"
    xref = len(pdf)
    pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objetos) + 1)
    for posicao in posicoes:
        pdf += b"%010d 00000 n \n" % posicao
    pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objetos) + 1, xref)
    return bytes(pdf)


def escrever_recibo_pdf(caminho: Path, texto: str) -> Path:
    caminho.write_bytes(pdf_de_texto(texto.splitlines()))
    return caminho


@pytest.fixture
def pasta(tmp_path: Path) -> Path:
    """Quatro comprovantes em PDF: três bons e um sem o valor em reais (wise_02), mais um arquivo que não é PDF."""
    p = tmp_path / "recibos"
    p.mkdir()
    escrever_recibo_pdf(p / "wise_01.pdf", texto_wise(numero="1000000001", usd="1,000.00", brl="5,123.45"))
    escrever_recibo_pdf(p / "wise_02.pdf", texto_wise(numero="1000000002", sem="brl"))
    escrever_recibo_pdf(p / "wise_03.pdf", texto_wise(numero="1000000003", usd="2,000.00", brl="10,500.00"))
    escrever_recibo_pdf(p / "wise_04.pdf", texto_wise(numero="1000000004", usd="500.00", brl="2,600.50"))
    (p / "anotacoes.txt").write_text("não é um recibo", encoding="utf-8")
    return p
