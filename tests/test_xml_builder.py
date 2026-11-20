"""Montagem da DPS e validação contra o XSD oficial (docs/xsd/Schemas/1.01).

Vários testes fixam uma regra que o XSD não mostra e que o servidor cobra com um código de erro
(ver docs/gotchas.md).
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest
from lxml import etree

from conftest import CNPJ, MUNICIPIO
from nfse_exportacao.xml_builder import NS_NFSE, DadosDPS, _fmt_dec, id_dps, montar_dps_xml, validar_dps


def _q(caminho: str) -> str:
    return "/".join(f"{{{NS_NFSE}}}{parte}" for parte in caminho.split("/"))


def _raiz(dados: DadosDPS) -> etree._Element:
    return etree.fromstring(montar_dps_xml(dados))


def _filhos(elemento: etree._Element) -> list[str]:
    return [etree.QName(filho).localname for filho in elemento]


def test_identificador_tem_45_caracteres_com_tpInsc_2_para_cnpj() -> None:
    # E0004: tpInsc=2 é CNPJ (1 é CPF), regra L102 do Anexo I
    identificador = id_dps(CNPJ, MUNICIPIO, "1", 7)
    assert identificador == "DPS" + MUNICIPIO + "2" + CNPJ + "00001" + "000000000000007"
    assert len(identificador) == 45


def test_id_da_infDPS_bate_com_os_campos(dados: DadosDPS) -> None:
    inf = _raiz(dados).find(_q("infDPS"))
    assert inf is not None
    assert inf.get("Id") == id_dps(CNPJ, MUNICIPIO, "1", 7)
    assert inf.findtext(_q("nDPS")) == "7"
    assert inf.findtext(_q("serie")) == "1"


def test_dps_valida_contra_o_xsd_oficial(dados: DadosDPS) -> None:
    validar_dps(montar_dps_xml(dados))


def test_dps_invalida_e_recusada_antes_de_sair_da_maquina(dados: DadosDPS) -> None:
    with pytest.raises(ValueError, match="inválido contra XSD"):
        validar_dps(montar_dps_xml(replace(dados, prestador_cnpj="123")))


def test_prestador_sem_endereco(dados: DadosDPS) -> None:
    # E0128: o endereço do prestador não é informado quando ele mesmo emite (tpEmit=1)
    inf = _raiz(dados).find(_q("infDPS"))
    assert inf is not None
    assert inf.findtext(_q("tpEmit")) == "1"
    prest = inf.find(_q("prest"))
    assert prest is not None
    assert "end" not in _filhos(prest)


def test_simples_me_epp_traz_regApTribSN(dados: DadosDPS) -> None:
    # E0166: opSimpNac=3 exige regApTribSN, entre opSimpNac e regEspTrib
    reg = _raiz(dados).find(_q("infDPS/prest/regTrib"))
    assert reg is not None
    assert _filhos(reg) == ["opSimpNac", "regApTribSN", "regEspTrib"]
    assert [filho.text for filho in reg] == ["3", "1", "0"]


def test_exportacao_sem_campos_proibidos_no_tributo_municipal(dados: DadosDPS) -> None:
    # pAliq, tpImunidade, exigSusp e BM são proibidos na exportação
    trib_mun = _raiz(dados).find(_q("infDPS/valores/trib/tribMun"))
    assert trib_mun is not None
    assert _filhos(trib_mun) == ["tribISSQN", "cPaisResult", "tpRetISSQN"]
    assert [filho.text for filho in trib_mun] == ["3", "US", "1"]


def test_comercio_exterior_transfronteirico_em_dolar(dados: DadosDPS) -> None:
    com_ext = _raiz(dados).find(_q("infDPS/serv/comExt"))
    assert com_ext is not None
    assert com_ext.findtext(_q("mdPrestacao")) == "1"
    assert com_ext.findtext(_q("tpMoeda")) == "840"
    assert com_ext.findtext(_q("vServMoeda")) == "1000.00"


def test_totais_aproximados_em_reais_todos_federais(dados: DadosDPS) -> None:
    # vTotTrib preenche a DANFSe; pTotTribSN seria aceito mas deixaria os campos em branco
    tot = _raiz(dados).find(_q("infDPS/valores/trib/totTrib"))
    assert tot is not None
    assert _filhos(tot) == ["vTotTrib"]
    assert tot.findtext(_q("vTotTrib/vTotTribFed")) == "307.41"  # 5123.45 × 6%
    assert tot.findtext(_q("vTotTrib/vTotTribEst")) == "0.00"
    assert tot.findtext(_q("vTotTrib/vTotTribMun")) == "0.00"


def test_tomador_sem_nif_usa_cNaoNIF(dados: DadosDPS) -> None:
    toma = _raiz(dados).find(_q("infDPS/toma"))
    assert toma is not None
    assert toma.findtext(_q("cNaoNIF")) == "2"
    assert toma.find(_q("NIF")) is None


def test_tomador_com_nif(dados: DadosDPS) -> None:
    xml = montar_dps_xml(replace(dados, tomador_nif="123456789"))
    validar_dps(xml)
    toma = etree.fromstring(xml).find(_q("infDPS/toma"))
    assert toma is not None
    assert toma.findtext(_q("NIF")) == "123456789"
    assert toma.find(_q("cNaoNIF")) is None


@pytest.mark.parametrize("ambiente", ["1", "2"])
def test_tpAmb_segue_o_ambiente(dados: DadosDPS, ambiente: str) -> None:
    # E0006: tpAmb diferente do ambiente do endpoint
    assert _raiz(replace(dados, tp_ambiente=ambiente)).findtext(_q("infDPS/tpAmb")) == ambiente


@pytest.mark.parametrize(
    ("valor", "texto"), [(Decimal("0"), "0"), (Decimal("10"), "10.00"), (Decimal("1234.5"), "1234.50")]
)
def test_formato_decimal_do_xsd(valor: Decimal, texto: str) -> None:
    assert _fmt_dec(valor) == texto
