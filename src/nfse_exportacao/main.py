from __future__ import annotations

import argparse
import json
import shutil
import sys
from decimal import Decimal
from pathlib import Path

from .config import Settings, get_settings
from .nfse_client import (
    consultar_chave_por_id,
    consultar_nfse,
    descompactar_nfse,
    dps_existe,
    enviar_dps,
)
from .parser import extrair
from .ptax import cotacao_usd_brl
from .signer import assinar_xml
from .state import DpsCounter
from .xml_builder import DadosDPS, id_dps, montar_dps_xml, validar_dps

SERIE_DPS = "1"


def _salvar_saida(recibo_path: Path, resposta: dict, output_dir: Path) -> Path:
    base = output_dir / f"nfse-{recibo_path.stem}"
    saida_json = base.with_suffix(".json")
    saida_json.write_text(json.dumps(resposta, indent=2, ensure_ascii=False))
    if "nfseXmlGZipB64" in resposta:
        base.with_suffix(".xml").write_bytes(descompactar_nfse(resposta["nfseXmlGZipB64"]))
    return saida_json


def processar_recibo(recibo_path: Path, numero_dps: int) -> tuple[Path, dict]:
    settings = get_settings()
    settings.output_dir.mkdir(parents=True, exist_ok=True)

    recibo = extrair(recibo_path)
    if recibo.valor_brl is not None:
        valor_brl = recibo.valor_brl
    else:
        ptax = cotacao_usd_brl(recibo.data_pagamento)
        valor_brl = (recibo.valor_usd * ptax).quantize(Decimal("0.01"))

    codigo_trib_nac = "010401"  # LC116 1.04 → 6 dígitos

    dados = DadosDPS(
        numero_dps=numero_dps,
        serie_dps=SERIE_DPS,
        data_emissao=recibo.data_pagamento,
        competencia=recibo.data_pagamento,
        tp_ambiente="1" if settings.nfse_ambiente == "producao" else "2",
        valor_servico_brl=valor_brl,
        valor_servico_usd=recibo.valor_usd,
        descricao=f"{settings.servico_descricao} — {recibo.descricao_detalhe}",
        codigo_tributacao_nacional=codigo_trib_nac,
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

    xml_dps = montar_dps_xml(dados)
    validar_dps(xml_dps)
    xml_assinado = assinar_xml(xml_dps, settings.cert_path, settings.cert_password)
    resposta = enviar_dps(xml_assinado, settings.nfse_base_url, settings.cert_path, settings.cert_password)
    saida_json = _salvar_saida(recibo_path, resposta, settings.output_dir)
    return saida_json, resposta


def emitir_um(
    recibo_path: Path,
    settings: Settings,
    counter: DpsCounter,
    numero_override: int | None = None,
) -> tuple[bool, str]:
    """Emite (ou recupera) uma NFS-e. Retorna (sucesso, mensagem)."""
    numero = numero_override if numero_override is not None else counter.peek()
    identificador = id_dps(settings.prestador_cnpj, settings.prestador_municipio_ibge, SERIE_DPS, numero)

    if dps_existe(identificador, settings.nfse_base_url, settings.cert_path, settings.cert_password):
        try:
            chave = consultar_chave_por_id(
                identificador, settings.nfse_base_url, settings.cert_path, settings.cert_password
            )
            nfse = consultar_nfse(chave, settings.nfse_base_url, settings.cert_path, settings.cert_password)
            _salvar_saida(recibo_path, nfse, settings.output_dir)
            counter.consume(numero)
            return True, f"recuperada nDPS={numero} chave={chave}"
        except Exception as e:  # noqa: BLE001
            return False, f"falha ao recuperar DPS existente: {e}"

    _, resposta = processar_recibo(recibo_path, numero)
    if resposta.get("_http_status") == 201:
        counter.consume(numero)
        return True, f"emitida nDPS={numero} chave={resposta.get('chaveAcesso')}"

    erros = resposta.get("erros") or resposta.get("Erros") or []
    detalhe = "; ".join(f"{e.get('Codigo')}: {e.get('Descricao')}" for e in erros)
    return False, f"rejeitada HTTP {resposta.get('_http_status')} — {detalhe} (nDPS {numero} mantido)"


def main() -> int:
    p = argparse.ArgumentParser(description="Gera NFS-e Nacional (exportação de serviços) a partir de comprovantes Wise")
    p.add_argument("recibo", type=Path, nargs="?", help="Caminho para um recibo (PDF/TXT/HTML)")
    p.add_argument("--batch", type=Path, metavar="DIR", help="Processa todos os PDFs do diretório")
    p.add_argument("--mover-para", type=Path, metavar="DIR", help="Após sucesso, move o PDF para este diretório")
    p.add_argument("--numero", type=int, help="Número da DPS (override do contador; só com arquivo único)")
    p.add_argument("--peek", action="store_true", help="Mostra o próximo número do contador")
    p.add_argument("--consultar", metavar="CHAVE", help="Consulta NFS-e por chave de acesso")
    args = p.parse_args()

    settings = get_settings()
    counter = DpsCounter(settings.prestador_cnpj, SERIE_DPS, settings.nfse_ambiente, settings.output_dir)

    if args.peek:
        print(f"Próximo nDPS [{settings.nfse_ambiente}] CNPJ {settings.prestador_cnpj} série {SERIE_DPS}: {counter.peek()}")
        return 0

    if args.consultar:
        nfse = consultar_nfse(args.consultar, settings.nfse_base_url, settings.cert_path, settings.cert_password)
        base = settings.output_dir / f"consulta-{args.consultar}"
        base.with_suffix(".json").write_text(json.dumps(nfse, indent=2, ensure_ascii=False))
        if "nfseXmlGZipB64" in nfse:
            base.with_suffix(".xml").write_bytes(descompactar_nfse(nfse["nfseXmlGZipB64"]))
        print(f"✓ NFS-e {args.consultar} salva em {base}.xml/.json")
        return 0

    if args.batch:
        return _rodar_batch(args.batch, args.mover_para, settings, counter)

    if not args.recibo or not args.recibo.exists():
        print(f"Arquivo não encontrado: {args.recibo}", file=sys.stderr)
        return 1

    print(f"Ambiente: {settings.nfse_ambiente}")
    ok, msg = emitir_um(args.recibo, settings, counter, args.numero)
    if ok:
        print(f"✓ {msg}")
        if args.mover_para:
            args.mover_para.mkdir(parents=True, exist_ok=True)
            _mover(args.recibo, args.mover_para)
        return 0
    print(f"✗ {msg}", file=sys.stderr)
    return 2


def _rodar_batch(batch_dir: Path, mover_para: Path | None, settings: Settings, counter: DpsCounter) -> int:
    if not batch_dir.is_dir():
        print(f"Diretório não existe: {batch_dir}", file=sys.stderr)
        return 1
    pdfs = sorted(batch_dir.glob("*.pdf"))
    if not pdfs:
        print(f"Nenhum PDF em {batch_dir}", file=sys.stderr)
        return 1

    print(f"Ambiente: {settings.nfse_ambiente}")
    print(f"Lote: {len(pdfs)} PDF(s) em {batch_dir}")
    if mover_para:
        mover_para.mkdir(parents=True, exist_ok=True)
        print(f"Após sucesso, move para: {mover_para}")
    print()

    sucessos = 0
    falhas = 0
    for pdf in pdfs:
        print(f"=== {pdf.name} ===")
        try:
            ok, msg = emitir_um(pdf, settings, counter)
        except Exception as e:  # noqa: BLE001
            ok, msg = False, f"erro inesperado: {e}"
        if ok:
            sucessos += 1
            print(f"  ✓ {msg}")
            if mover_para:
                _mover(pdf, mover_para)
        else:
            falhas += 1
            print(f"  ✗ {msg}", file=sys.stderr)
        print()

    print(f"Resumo: {sucessos} sucesso(s), {falhas} falha(s)")
    return 0 if falhas == 0 else 2


def _mover(pdf: Path, destino_dir: Path) -> None:
    destino = destino_dir / pdf.name
    shutil.move(str(pdf), str(destino))
    print(f"  → movido para {destino}")


if __name__ == "__main__":
    raise SystemExit(main())
