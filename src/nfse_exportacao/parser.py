from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional

import pdfplumber


@dataclass
class Recibo:
    referencia: str               # ex: "Wise #1234567890"
    data_pagamento: date          # competência (data "Funded" do comprovante Wise)
    valor_usd: Decimal            # valor bruto pago pelo tomador, em USD
    valor_brl: Optional[Decimal] = None  # quando o recibo já traz BRL convertido (Wise); senão usa PTAX
    descricao_detalhe: str = ""   # texto curto p/ compor a descrição da nota


def _extrair_texto(arquivo: Path) -> str:
    if arquivo.suffix.lower() == ".pdf":
        with pdfplumber.open(arquivo) as pdf:
            return "\n".join(p.extract_text() or "" for p in pdf.pages)
    return arquivo.read_text(encoding="utf-8")


def _parse_wise(texto: str, arquivo: Path) -> Recibo:
    # PDF é em duas colunas; pdfplumber mescla lado-a-lado. Funded é a SEGUNDA
    # data após o header "Transfer created Funded".
    m_id = re.search(r"#(\d{6,})", texto)
    m_funded = re.search(
        r"Transfer created\s+Funded\s*\n\w+\s+\d+,\s+\d{4}\s+[\d:]+\s+\w+\s+(\w+\s+\d+,\s+\d{4})",
        texto,
    )
    m_usd = re.search(r"Amount paid by[^\n]+\n([\d.,]+)\s+USD", texto)
    m_brl = re.search(r"Total to[^\n]+\n([\d.,]+)\s+BRL", texto)
    if not (m_id and m_funded and m_usd and m_brl):
        falta = [n for n, v in [("id", m_id), ("funded", m_funded), ("usd", m_usd), ("brl", m_brl)] if not v]
        raise ValueError(f"Recibo Wise com campos faltando em {arquivo.name}: {falta}")

    funded = datetime.strptime(m_funded.group(1).strip(), "%B %d, %Y").date()
    valor_usd = Decimal(m_usd.group(1).replace(",", ""))
    valor_brl = Decimal(m_brl.group(1).replace(",", ""))
    return Recibo(
        referencia=f"Wise #{m_id.group(1)}",
        data_pagamento=funded,
        valor_usd=valor_usd,
        valor_brl=valor_brl,
        descricao_detalhe=f"Transferência via Wise #{m_id.group(1)} em {funded.strftime('%d/%m/%Y')}",
    )


def extrair(arquivo: Path) -> Recibo:
    texto = _extrair_texto(arquivo)
    if "Wise Brasil" in texto or arquivo.name.startswith("wise_"):
        return _parse_wise(texto, arquivo)
    raise ValueError(f"Formato de recibo não reconhecido: {arquivo.name}")
