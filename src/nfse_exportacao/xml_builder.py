from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

from lxml import etree


NS_NFSE = "http://www.sped.fazenda.gov.br/nfse"
NS_DS = "http://www.w3.org/2000/09/xmldsig#"
VERSAO = "1.01"
VER_APLIC = "nfse-exportacao-0.1"

_XSD_PATH = Path(__file__).resolve().parents[2] / "docs" / "xsd" / "Schemas" / "1.01" / "DPS_v1.01.xsd"


@dataclass
class DadosDPS:
    numero_dps: int
    serie_dps: str
    data_emissao: date
    competencia: date
    valor_servico_brl: Decimal
    valor_servico_usd: Decimal
    descricao: str
    codigo_tributacao_nacional: str  # 6 dígitos, ex "010401"

    prestador_cnpj: str
    prestador_municipio_ibge: str
    prestador_cep: str
    prestador_logradouro: str
    prestador_numero: str
    prestador_bairro: str
    prestador_complemento: Optional[str] = None
    prestador_im: Optional[str] = None  # opcional; obrigatório só se houver registro CNC

    tomador_nome: str = ""
    tomador_pais: str = "US"
    tomador_nif: Optional[str] = None  # se None usa cNaoNIF
    tomador_cod_nao_nif: str = "2"  # 2 = Não exigência do NIF
    tomador_cep_ext: str = "00000"  # placeholder
    tomador_cidade: str = ""
    tomador_estado: str = ""
    tomador_logradouro: str = ""
    tomador_numero: str = "S/N"
    tomador_bairro: str = ""

    tp_ambiente: str = "2"  # 1=Produção 2=Homologação
    moeda_iso_num: str = "840"  # USD
    pais_resultado: str = "US"  # cPaisResult — onde se verifica o resultado da exportação
    op_simp_nac: str = "3"  # 3 = ME/EPP
    reg_ap_trib_sn: str = "1"  # 1 = tributos federais e municipal pelo SN (obrigatório quando opSimpNac=3)
    reg_esp_trib: str = "0"  # 0 = Nenhum
    aliq_simples_aprox: Decimal = field(default_factory=lambda: Decimal("0"))  # pTotTribSN

    # TODO RTC IBS/CBS: a partir de 01/01/2026 é permitido declarar IBS/CBS;
    # quando o município de incidência for parametrizado e/ou houver migração para a
    # nova tributação (2027+), preencher o grupo IBSCBS aqui. Por ora omitido (minOccurs=0).


def _q(tag: str) -> str:
    return f"{{{NS_NFSE}}}{tag}"


def _fmt_dec(value: Decimal) -> str:
    v = Decimal(value).quantize(Decimal("0.01"))
    s = f"{v:.2f}"
    # XSD aceita "0" ou "N.NN"; manter ".00" funciona pelo padrão `[1-9]...(\.[0-9]{2})?` para valores > 0
    if v == 0:
        return "0"
    return s


def id_dps(cnpj: str, municipio: str, serie: str, numero: int) -> str:
    """Compõe o identificador da DPS (45 chars: DPS + 42 numéricos). Regra L102 do Anexo I."""
    tp_insc = "2"  # 1=CPF, 2=CNPJ
    insc = cnpj.zfill(14)
    serie_p = serie.zfill(5)
    num_p = str(numero).zfill(15)
    return f"DPS{municipio}{tp_insc}{insc}{serie_p}{num_p}"


def montar_dps_xml(dados: DadosDPS) -> bytes:
    dps = etree.Element(_q("DPS"), nsmap={None: NS_NFSE}, attrib={"versao": VERSAO})

    inf = etree.SubElement(dps, _q("infDPS"))
    inf.set("Id", id_dps(dados.prestador_cnpj, dados.prestador_municipio_ibge, dados.serie_dps, dados.numero_dps))

    etree.SubElement(inf, _q("tpAmb")).text = dados.tp_ambiente
    dt = datetime.combine(dados.data_emissao, datetime.min.time(), tzinfo=timezone.utc)
    etree.SubElement(inf, _q("dhEmi")).text = dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    etree.SubElement(inf, _q("verAplic")).text = VER_APLIC
    etree.SubElement(inf, _q("serie")).text = dados.serie_dps
    etree.SubElement(inf, _q("nDPS")).text = str(dados.numero_dps)
    etree.SubElement(inf, _q("dCompet")).text = dados.competencia.strftime("%Y-%m-%d")
    etree.SubElement(inf, _q("tpEmit")).text = "1"  # Prestador
    etree.SubElement(inf, _q("cLocEmi")).text = dados.prestador_municipio_ibge

    # prest — endereço NÃO deve ser informado quando o próprio prestador é o emitente (E0128)
    prest = etree.SubElement(inf, _q("prest"))
    etree.SubElement(prest, _q("CNPJ")).text = dados.prestador_cnpj
    if dados.prestador_im:
        etree.SubElement(prest, _q("IM")).text = dados.prestador_im
    reg_trib = etree.SubElement(prest, _q("regTrib"))
    etree.SubElement(reg_trib, _q("opSimpNac")).text = dados.op_simp_nac
    if dados.op_simp_nac == "3":
        etree.SubElement(reg_trib, _q("regApTribSN")).text = dados.reg_ap_trib_sn
    etree.SubElement(reg_trib, _q("regEspTrib")).text = dados.reg_esp_trib

    # toma (exterior)
    toma = etree.SubElement(inf, _q("toma"))
    if dados.tomador_nif:
        etree.SubElement(toma, _q("NIF")).text = dados.tomador_nif
    else:
        etree.SubElement(toma, _q("cNaoNIF")).text = dados.tomador_cod_nao_nif
    etree.SubElement(toma, _q("xNome")).text = dados.tomador_nome
    end_t = etree.SubElement(toma, _q("end"))
    end_ext = etree.SubElement(end_t, _q("endExt"))
    etree.SubElement(end_ext, _q("cPais")).text = dados.tomador_pais
    etree.SubElement(end_ext, _q("cEndPost")).text = dados.tomador_cep_ext
    etree.SubElement(end_ext, _q("xCidade")).text = dados.tomador_cidade
    etree.SubElement(end_ext, _q("xEstProvReg")).text = dados.tomador_estado
    etree.SubElement(end_t, _q("xLgr")).text = dados.tomador_logradouro
    etree.SubElement(end_t, _q("nro")).text = dados.tomador_numero
    etree.SubElement(end_t, _q("xBairro")).text = dados.tomador_bairro

    # serv
    serv = etree.SubElement(inf, _q("serv"))
    loc = etree.SubElement(serv, _q("locPrest"))
    # Local da prestação no Brasil (estabelecimento do prestador) para item 1.04 (incidência EP)
    etree.SubElement(loc, _q("cLocPrestacao")).text = dados.prestador_municipio_ibge
    cserv = etree.SubElement(serv, _q("cServ"))
    etree.SubElement(cserv, _q("cTribNac")).text = dados.codigo_tributacao_nacional
    etree.SubElement(cserv, _q("xDescServ")).text = dados.descricao
    com_ext = etree.SubElement(serv, _q("comExt"))
    etree.SubElement(com_ext, _q("mdPrestacao")).text = "1"  # Transfronteiriço
    etree.SubElement(com_ext, _q("vincPrest")).text = "0"  # Sem vínculo
    etree.SubElement(com_ext, _q("tpMoeda")).text = dados.moeda_iso_num
    etree.SubElement(com_ext, _q("vServMoeda")).text = _fmt_dec(dados.valor_servico_usd)
    etree.SubElement(com_ext, _q("mecAFComexP")).text = "01"  # Nenhum
    etree.SubElement(com_ext, _q("mecAFComexT")).text = "01"  # Nenhum
    etree.SubElement(com_ext, _q("movTempBens")).text = "1"  # Não
    etree.SubElement(com_ext, _q("mdic")).text = "0"  # Não enviar para o MDIC

    # valores
    val = etree.SubElement(inf, _q("valores"))
    vserv_p = etree.SubElement(val, _q("vServPrest"))
    etree.SubElement(vserv_p, _q("vServ")).text = _fmt_dec(dados.valor_servico_brl)
    trib = etree.SubElement(val, _q("trib"))
    trib_mun = etree.SubElement(trib, _q("tribMun"))
    etree.SubElement(trib_mun, _q("tribISSQN")).text = "3"  # Exportação de serviço
    etree.SubElement(trib_mun, _q("cPaisResult")).text = dados.pais_resultado
    etree.SubElement(trib_mun, _q("tpRetISSQN")).text = "1"  # Não Retido
    # pAliq, tpImunidade, exigSusp, BM omitidos (proibidos para exportação)
    # totTrib é um choice. pTotTribSN dá só o número e não popula a DANFSe; usamos
    # vTotTrib (Fed/Est/Mun em R$) p/ a DANFSe renderizar os "Totais Aproximados".
    # Para SN ME/EPP exportação: DAS é federal único → toda alíquota vai em Federais,
    # ISS = 0 (exportação) → Municipais = 0, Estaduais = 0 (serviço não tem ICMS).
    tot_trib = etree.SubElement(trib, _q("totTrib"))
    v_tot = etree.SubElement(tot_trib, _q("vTotTrib"))
    v_fed = (dados.valor_servico_brl * dados.aliq_simples_aprox / Decimal("100")).quantize(Decimal("0.01"))
    etree.SubElement(v_tot, _q("vTotTribFed")).text = _fmt_dec(v_fed)
    etree.SubElement(v_tot, _q("vTotTribEst")).text = "0.00"
    etree.SubElement(v_tot, _q("vTotTribMun")).text = "0.00"

    # IBSCBS omitido (minOccurs=0). TODO: a partir de 2027, com migração para RTC,
    # adicionar o grupo IBSCBS conforme NT 004/RTC.

    return etree.tostring(dps, xml_declaration=True, encoding="UTF-8", standalone=True)


def _carregar_schema(xsd_path: Path) -> etree.XMLSchema:
    # tiposSimples_v1.01.xsd traz patterns inválidos com âncoras literais `^...$` em alguns
    # tipos (ex: TSSerieDPS). Removemos essas âncoras em memória para que a validação local
    # reflita o que o servidor realmente espera (libxml2 não trata ^/$ como meta-caracteres em XSD).
    simples = xsd_path.parent / "tiposSimples_v1.01.xsd"
    parser = etree.XMLParser(resolve_entities=False)
    root_tree = etree.parse(str(xsd_path), parser)

    base = xsd_path.parent
    fixed: dict[str, bytes] = {}
    for f in base.iterdir():
        if f.suffix == ".xsd":
            txt = f.read_text(encoding="utf-8")
            if f.name == simples.name:
                txt = txt.replace('pattern value="^', 'pattern value="').replace('$"/>', '"/>')
            fixed[f.name] = txt.encode("utf-8")

    class _Resolver(etree.Resolver):
        def resolve(self, system_url, public_id, context):  # type: ignore[override]
            name = Path(system_url).name
            if name in fixed:
                return self.resolve_string(fixed[name].decode("utf-8"), context)
            return None

    parser2 = etree.XMLParser()
    parser2.resolvers.add(_Resolver())
    main_bytes = fixed[xsd_path.name]
    schema_tree = etree.fromstring(main_bytes, parser2, base_url=str(xsd_path))
    return etree.XMLSchema(schema_tree)


def validar_dps(xml: bytes, xsd_path: Path = _XSD_PATH) -> None:
    schema = _carregar_schema(xsd_path)
    doc = etree.fromstring(xml)
    if not schema.validate(doc):
        erros = "; ".join(f"L{e.line}: {e.message}" for e in schema.error_log)
        raise ValueError(f"DPS XML inválido contra XSD: {erros}")
