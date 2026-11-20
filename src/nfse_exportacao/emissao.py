"""Peças comuns à linha de comando e à interface: os recibos de uma pasta, os dados da DPS de um recibo,
onde a nota emitida é salva e para onde o recibo vai depois."""

from __future__ import annotations

import json
import shutil
from decimal import Decimal
from pathlib import Path

from .config import Settings
from .nfse_client import descompactar_nfse
from .parser import Recibo
from .xml_builder import DadosDPS

SERIE_DPS = "1"
CODIGO_TRIB_NAC = "010401"  # LC 116 item 1.04 → 6 dígitos


def listar_recibos(pasta: Path) -> list[Path]:
    """Os PDFs da pasta, sem entrar em subpastas, em ordem de nome."""
    return sorted(p for p in pasta.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")


def dados_dps(recibo: Recibo, valor_brl: Decimal, numero: int, settings: Settings) -> DadosDPS:
    """Os campos da DPS de um recibo, com os dados fixos do prestador e do tomador vindos do .env."""
    return DadosDPS(
        numero_dps=numero,
        serie_dps=SERIE_DPS,
        data_emissao=recibo.data_pagamento,
        competencia=recibo.data_pagamento,
        tp_ambiente="1" if settings.nfse_ambiente == "producao" else "2",
        valor_servico_brl=valor_brl,
        valor_servico_usd=recibo.valor_usd,
        descricao=f"{settings.servico_descricao} — {recibo.descricao_detalhe}",
        codigo_tributacao_nacional=CODIGO_TRIB_NAC,
        prestador_cnpj=settings.prestador_cnpj,
        prestador_municipio_ibge=settings.prestador_municipio_ibge,
        prestador_cep=settings.prestador_cep,
        prestador_logradouro=settings.prestador_logradouro,
        prestador_numero=settings.prestador_numero,
        prestador_complemento=settings.prestador_complemento or None,
        prestador_bairro=settings.prestador_bairro,
        prestador_im=settings.prestador_inscricao_municipal or None,
        tomador_nome=settings.tomador_nome,
        tomador_pais=settings.tomador_pais,
        tomador_nif=settings.tomador_documento_exterior or None,
        tomador_cep_ext=settings.tomador_cep_ext,
        tomador_cidade=settings.tomador_cidade,
        tomador_estado=settings.tomador_estado,
        tomador_logradouro=settings.tomador_logradouro,
        tomador_numero=settings.tomador_numero,
        tomador_bairro=settings.tomador_bairro,
        pais_resultado=settings.tomador_pais,
        aliq_simples_aprox=Decimal(str(settings.aliq_simples_aprox)),
    )


def salvar_saida(recibo_path: Path, resposta: dict, output_dir: Path) -> Path:
    """Grava a resposta do servidor (JSON) e a NFS-e (XML) em `output_dir/nfse-<recibo>.*`."""
    output_dir.mkdir(parents=True, exist_ok=True)
    base = output_dir / f"nfse-{recibo_path.stem}"
    saida_json = base.with_suffix(".json")
    saida_json.write_text(json.dumps(resposta, indent=2, ensure_ascii=False))
    if "nfseXmlGZipB64" in resposta:
        base.with_suffix(".xml").write_bytes(descompactar_nfse(resposta["nfseXmlGZipB64"]))
    return saida_json


def mover(arquivo: Path, destino_dir: Path) -> Path:
    """Move o recibo para a pasta dos emitidos e devolve o novo caminho."""
    destino_dir.mkdir(parents=True, exist_ok=True)
    destino = destino_dir / arquivo.name
    shutil.move(str(arquivo), str(destino))
    return destino
