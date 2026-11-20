"""O lote da interface, em três passos: procurar e preparar, autorizar, emitir e salvar.

Diferença para a linha de comando: aqui cada DPS é montada, validada e assinada *antes* da
autorização, e o que se envia são exatamente os bytes que foram conferidos. Os números das DPS são
reservados em sequência a partir do contador; por isso a emissão para no primeiro problema, para não
pular números. Antes de cada envio o servidor é consultado: uma DPS que já existe é recuperada, nunca
reenviada (o reenvio cancelaria a nota existente por substituição).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from lxml import etree

from .config import Settings
from .emissao import SERIE_DPS, dados_dps, listar_recibos, mover, salvar_saida
from .nfse_client import consultar_chave_por_id, consultar_nfse, descompactar_nfse, dps_existe, enviar_dps
from .parser import extrair
from .ptax import cotacao_usd_brl
from .signer import assinar_xml
from .state import DpsCounter
from .xml_builder import id_dps, montar_dps_xml, validar_dps

Estado = Literal["emitida", "recuperada", "rejeitada", "erro", "não enviada"]


class LoteDesatualizado(RuntimeError):
    """O pacote não corresponde mais ao estado atual (contador, ambiente ou CNPJ): prepare de novo."""


@dataclass
class Certificado:
    ok: bool
    mensagem: str
    titular: str = ""
    valido_ate: date | None = None


def verificar_certificado(caminho: Path, senha: str, hoje: date | None = None) -> Certificado:
    """Abre o .pfx e diz de quem é e até quando vale, sem contatar ninguém."""
    hoje = hoje or date.today()
    try:
        chave, cert, _ = pkcs12.load_key_and_certificates(caminho.read_bytes(), senha.encode())
    except FileNotFoundError:
        return Certificado(False, f"arquivo do certificado não encontrado: {caminho}")
    except IsADirectoryError:
        return Certificado(False, "CERT_PATH não aponta para um arquivo .pfx")
    except ValueError:
        return Certificado(False, "não foi possível abrir o certificado: senha errada ou arquivo inválido")
    if chave is None or cert is None:
        return Certificado(False, "o arquivo não traz chave privada e certificado")
    nomes = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    titular = str(nomes[0].value) if nomes else cert.subject.rfc4514_string()
    valido_ate = cert.not_valid_after_utc.date()
    if valido_ate < hoje:
        return Certificado(False, f"certificado vencido em {valido_ate:%d/%m/%Y}", titular, valido_ate)
    return Certificado(True, f"válido até {valido_ate:%d/%m/%Y}", titular, valido_ate)


@dataclass
class Nota:
    """Um recibo e a DPS preparada para ele. `erro` preenchido = fora do pacote."""

    recibo: Path
    referencia: str = ""
    competencia: date | None = None
    valor_usd: Decimal | None = None
    valor_brl: Decimal | None = None
    ptax: Decimal | None = None  # só quando o recibo não traz o valor em reais
    numero_dps: int | None = None
    id_dps: str = ""
    descricao: str = ""
    tributos_aprox: Decimal | None = None
    xml_assinado: bytes = b""
    erro: str = ""

    @property
    def pronta(self) -> bool:
        return not self.erro and bool(self.xml_assinado)

    @property
    def cambio(self) -> Decimal | None:
        """Reais recebidos por dólar pago, como referência para conferir o recibo."""
        if self.valor_usd and self.valor_brl:
            return (self.valor_brl / self.valor_usd).quantize(Decimal("0.0001"), ROUND_HALF_UP)
        return None


@dataclass
class Lote:
    pasta: Path
    ambiente: str
    cnpj: str
    primeiro_numero: int
    notas: list[Nota] = field(default_factory=list)

    @property
    def prontas(self) -> list[Nota]:
        return [n for n in self.notas if n.pronta]

    @property
    def fora(self) -> list[Nota]:
        return [n for n in self.notas if not n.pronta]

    @property
    def total_usd(self) -> Decimal:
        return sum((n.valor_usd or Decimal(0) for n in self.prontas), Decimal(0))

    @property
    def total_brl(self) -> Decimal:
        return sum((n.valor_brl or Decimal(0) for n in self.prontas), Decimal(0))

    @property
    def impressao(self) -> str:
        """Impressão digital do pacote: SHA-256 das DPS assinadas, na ordem. Muda se qualquer byte mudar."""
        h = hashlib.sha256()
        for nota in self.prontas:
            h.update(nota.xml_assinado)
        return h.hexdigest()[:16]


def _campo(xml: bytes, nome: str) -> str | None:
    achados = etree.fromstring(xml).xpath(f"//*[local-name()='{nome}']")
    return achados[0].text if achados else None


def preparar(pasta: Path, settings: Settings, counter: DpsCounter) -> Lote:
    """Lê cada recibo da pasta e monta, valida e assina a DPS dele. Nada é enviado ao servidor.

    Um recibo que não pode ser lido fica fora do pacote, com o motivo, e não consome número.
    """
    lote = Lote(pasta, settings.nfse_ambiente, settings.prestador_cnpj, counter.peek())
    numero = lote.primeiro_numero
    for caminho in listar_recibos(pasta):
        nota = Nota(recibo=caminho)
        try:
            recibo = extrair(caminho)
            nota.referencia = recibo.referencia
            nota.competencia = recibo.data_pagamento
            nota.valor_usd = recibo.valor_usd
            if recibo.valor_brl is not None:
                nota.valor_brl = recibo.valor_brl
            else:
                nota.ptax = cotacao_usd_brl(recibo.data_pagamento)
                nota.valor_brl = (recibo.valor_usd * nota.ptax).quantize(Decimal("0.01"))
            dados = dados_dps(recibo, nota.valor_brl, numero, settings)
            xml = montar_dps_xml(dados)
            validar_dps(xml)
            nota.xml_assinado = assinar_xml(xml, settings.cert_path, settings.cert_password)
        except Exception as e:  # noqa: BLE001 - mostrado ao usuário; o recibo fica fora do pacote
            nota.erro = str(e) or e.__class__.__name__
            nota.xml_assinado = b""
        else:
            nota.numero_dps = numero
            nota.id_dps = id_dps(settings.prestador_cnpj, settings.prestador_municipio_ibge, SERIE_DPS, numero)
            nota.descricao = dados.descricao
            tributos = _campo(nota.xml_assinado, "vTotTribFed")
            nota.tributos_aprox = Decimal(tributos) if tributos else None
            numero += 1
        lote.notas.append(nota)
    return lote


@dataclass
class Resultado:
    nota: Nota
    estado: Estado
    mensagem: str = ""
    chave: str = ""
    saida: Path | None = None
    movido_para: Path | None = None
    aviso: str = ""
    divergente: bool = False  # a nota recuperada não confere com o recibo: o lote para e o recibo fica onde está


def _conferir_valor(resposta: dict, nota: Nota) -> str:
    """Compara o valor da nota recuperada do servidor com o do recibo."""
    if "nfseXmlGZipB64" not in resposta:
        return "a nota recuperada veio sem XML: confira o valor no portal"
    texto = _campo(descompactar_nfse(resposta["nfseXmlGZipB64"]), "vServ")
    try:
        valor = Decimal(texto) if texto else None
    except InvalidOperation:
        valor = None
    if valor != nota.valor_brl:
        return (
            f"a nota que já existia no servidor tem valor {texto or 'desconhecido'}, "
            f"e este recibo tem {nota.valor_brl}: confira antes de seguir"
        )
    return ""


def _emitir_uma(nota: Nota, settings: Settings, counter: DpsCounter, mover_para: Path | None) -> Resultado:
    assert nota.numero_dps is not None
    acesso = (settings.nfse_base_url, settings.cert_path, settings.cert_password)
    if counter.peek() != nota.numero_dps:
        return Resultado(
            nota, "erro", f"o contador está em {counter.peek()} e esta nota esperava {nota.numero_dps}: prepare de novo"
        )
    try:
        if dps_existe(nota.id_dps, *acesso):
            chave = consultar_chave_por_id(nota.id_dps, *acesso)
            resposta = consultar_nfse(chave, *acesso)
            divergencia = _conferir_valor(resposta, nota)
            resultado = Resultado(
                nota,
                "recuperada",
                "já existia no servidor: recuperada, não reenviada",
                chave=chave,
                aviso=divergencia,
                divergente=bool(divergencia),
            )
            # uma nota que não confere com o recibo não leva o nome dele
            nome = Path(f"nDPS-{nota.numero_dps}-recuperada") if divergencia else nota.recibo
            resultado.saida = salvar_saida(nome, resposta, settings.output_dir)
        else:
            resposta = enviar_dps(nota.xml_assinado, *acesso)
            status = resposta.get("_http_status")
            if status != 201:
                erros = resposta.get("erros") or resposta.get("Erros") or []
                detalhe = "; ".join(f"{e.get('Codigo')}: {e.get('Descricao')}" for e in erros) or "sem detalhe"
                return Resultado(nota, "rejeitada", f"HTTP {status}: {detalhe}")
            resultado = Resultado(nota, "emitida", "emitida", chave=str(resposta.get("chaveAcesso", "")))
            resultado.saida = salvar_saida(nota.recibo, resposta, settings.output_dir)
        counter.consume(nota.numero_dps)
    except Exception as e:  # noqa: BLE001 - qualquer falha de rede ou do servidor para o lote
        return Resultado(
            nota,
            "erro",
            f"{e.__class__.__name__}: {e}. Rodar de novo é seguro: antes de enviar, o programa pergunta ao "
            "servidor se a DPS já existe.",
        )
    if mover_para is not None and not resultado.divergente:
        try:
            resultado.movido_para = mover(nota.recibo, mover_para)
        except OSError as e:
            resultado.aviso = (resultado.aviso + "; " if resultado.aviso else "") + f"o recibo não foi movido: {e}"
    return resultado


def emitir(
    lote: Lote,
    settings: Settings,
    counter: DpsCounter,
    mover_para: Path | None = None,
    ao_avancar: Callable[[Resultado], None] | None = None,
) -> list[Resultado]:
    """Emite as notas prontas, na ordem.

    Para no primeiro problema (rejeição, erro, ou nota recuperada que não confere com o recibo); as
    seguintes ficam "não enviada" e o contador fica no número da primeira delas.
    """
    if (settings.nfse_ambiente, settings.prestador_cnpj) != (lote.ambiente, lote.cnpj):
        raise LoteDesatualizado("o ambiente ou o CNPJ mudou depois que o pacote foi preparado: prepare de novo")
    if counter.peek() != lote.primeiro_numero:
        raise LoteDesatualizado(
            f"o contador mudou depois que o pacote foi preparado (era {lote.primeiro_numero}, agora "
            f"{counter.peek()}): prepare de novo"
        )
    resultados: list[Resultado] = []
    parar = False
    for nota in lote.prontas:
        if parar:
            resultado = Resultado(nota, "não enviada", "não enviada: a emissão parou na nota anterior")
        else:
            resultado = _emitir_uma(nota, settings, counter, mover_para)
            parar = resultado.estado in ("rejeitada", "erro") or resultado.divergente
        resultados.append(resultado)
        if ao_avancar is not None:
            ao_avancar(resultado)
    return resultados
