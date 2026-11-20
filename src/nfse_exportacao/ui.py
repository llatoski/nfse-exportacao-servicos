"""Interface local, em três passos: procurar os recibos, conferir e autorizar o pacote, emitir e salvar.

Abrir com `make ui`, ou:
    PYTHONPATH=src .venv/bin/streamlit run src/nfse_exportacao/ui.py
O arquivo .streamlit/config.toml prende o servidor em 127.0.0.1 (só este computador o acessa).
"""

from __future__ import annotations

from decimal import Decimal
from html import escape
from pathlib import Path

import pandas as pd
import streamlit as st

from nfse_exportacao import lote as L
from nfse_exportacao.config import get_settings
from nfse_exportacao.emissao import SERIE_DPS, listar_recibos
from nfse_exportacao.state import DpsCounter

PALAVRA_PRODUCAO = "EMITIR"
PORTAL_EMITIDAS = "https://www.nfse.gov.br/EmissorNacional/Notas/Emitidas"
PASSOS = ("Procurar os recibos", "Conferir e autorizar", "Emitir e salvar")
ICONE = {"emitida": "✅", "recuperada": "♻️", "rejeitada": "⛔", "erro": "⚠️", "não enviada": "⏸️"}

CSS = """
<style>
.faixa {border-radius: .5rem; padding: .7rem 1rem; font-weight: 600; margin-bottom: .6rem}
.faixa.producao {background: #fde8e8; color: #9b1c1c; border: 2px solid #e02424}
.faixa.teste {background: #e1effe; color: #1e429f; border: 2px solid #3f83f8}
.passos {display: flex; gap: .5rem; margin: .4rem 0 1.2rem 0; flex-wrap: wrap}
.passo {flex: 1 1 12rem; border-radius: .5rem; padding: .55rem .8rem; border: 2px solid #d1d5db;
        color: #6b7280; background: #f9fafb}
.passo b {display: inline-block; width: 1.6rem; height: 1.6rem; line-height: 1.6rem; text-align: center;
          border-radius: 50%; background: #d1d5db; color: #fff; margin-right: .45rem}
.passo.atual {border-color: #1c64f2; color: #1e429f; background: #ebf5ff}
.passo.atual b {background: #1c64f2}
.passo.feito {border-color: #0e9f6e; color: #03543f; background: #f3faf7}
.passo.feito b {background: #0e9f6e}
</style>
"""


def reais(valor: Decimal | None) -> str:
    if valor is None:
        return "—"
    return "R$ " + f"{valor:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def dolares(valor: Decimal | None) -> str:
    if valor is None:
        return "—"
    return "US$ " + f"{valor:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def cnpj_formatado(cnpj: str) -> str:
    c = cnpj.zfill(14)
    return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}" if c.isdigit() else cnpj


def guia(etapa: int) -> None:
    """Os três passos no topo: os já feitos em verde, o atual em azul, os seguintes em cinza."""
    partes = []
    for i, nome in enumerate(PASSOS, start=1):
        classe = "feito" if i < etapa or etapa > len(PASSOS) else "atual" if i == etapa else ""
        marca = "✓" if classe == "feito" else str(i)
        partes.append(f'<div class="passo {classe}"><b>{marca}</b>{escape(nome)}</div>')
    st.markdown(f'<div class="passos">{"".join(partes)}</div>', unsafe_allow_html=True)


def recomecar() -> None:
    for chave in ("lote", "resultados", "mover_para"):
        st.session_state.pop(chave, None)
    st.session_state.etapa = 1


def tabela_pacote(lote: L.Lote) -> pd.DataFrame:
    linhas = []
    for n in lote.notas:
        linhas.append(
            {
                "Nº DPS": str(n.numero_dps) if n.pronta else "—",
                "Recibo": n.recibo.name,
                "Referência": n.referencia or "—",
                "Competência": f"{n.competencia:%d/%m/%Y}" if n.competencia else "—",
                "Pago (USD)": dolares(n.valor_usd),
                "Recebido (BRL)": reais(n.valor_brl),
                "Câmbio R$/US$": f"{n.cambio}".replace(".", ",") if n.cambio else "—",
                "Tributos aprox.": reais(n.tributos_aprox),
                "Situação": "✅ pronta" if n.pronta else "⛔ fora do pacote",
            }
        )
    return pd.DataFrame(linhas)


def tabela_resultados(resultados: list[L.Resultado]) -> pd.DataFrame:
    linhas = []
    for r in resultados:
        linhas.append(
            {
                "Nº DPS": str(r.nota.numero_dps),
                "Recibo": r.nota.recibo.name,
                "Recebido (BRL)": reais(r.nota.valor_brl),
                "Situação": f"{ICONE[r.estado]} {r.estado}",
                "Chave de acesso": r.chave or "—",
                "Salva em": str(r.saida) if r.saida else "—",
                "Recibo movido para": str(r.movido_para.parent) if r.movido_para else "—",
                "Observação": r.mensagem if r.estado not in ("emitida",) else "",
            }
        )
    return pd.DataFrame(linhas)


# --- página -----------------------------------------------------------------------------------------

st.set_page_config(page_title="NFS-e de exportação", page_icon="🧾", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)

settings = get_settings()
contador = DpsCounter(settings.prestador_cnpj, SERIE_DPS, settings.nfse_ambiente, settings.output_dir)
certificado = L.verificar_certificado(settings.cert_path, settings.cert_password)
producao = settings.nfse_ambiente == "producao"
st.session_state.setdefault("etapa", 1)
etapa: int = st.session_state.etapa

st.title("🧾 NFS-e de exportação de serviços")
if producao:
    st.markdown(
        '<div class="faixa producao">🔴 PRODUÇÃO · as notas emitidas aqui são reais e têm valor fiscal</div>',
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        '<div class="faixa teste">🔵 AMBIENTE DE TESTES (produção restrita) · as notas não têm valor fiscal</div>',
        unsafe_allow_html=True,
    )

c1, c2, c3, c4 = st.columns(4)
c1.metric("Empresa (CNPJ)", cnpj_formatado(settings.prestador_cnpj), help="PRESTADOR_CNPJ no .env")
c2.metric(
    "Próximo número da DPS",
    contador.peek(),
    help=f"Série {SERIE_DPS}. Cada ambiente tem o seu contador. Ele só avança quando uma nota é emitida.",
)
c3.metric(
    "Certificado A1",
    "✅ ok" if certificado.ok else "❌ problema",
    help=f"{certificado.titular} · {certificado.mensagem}" if certificado.titular else certificado.mensagem,
)
c4.metric("Notas salvas em", str(settings.output_dir), help="OUTPUT_DIR no .env: o XML e a resposta de cada nota")
if certificado.ok:
    st.caption(f"Certificado de {certificado.titular}, {certificado.mensagem}.")
else:
    st.error(f"Certificado: {certificado.mensagem}. Ajuste CERT_PATH e CERT_PASSWORD no .env e reabra a página.")

guia(etapa)

if aviso := st.session_state.pop("aviso_topo", None):
    st.warning(aviso)

# --- passo 1 ----------------------------------------------------------------------------------------
if etapa == 1:
    st.subheader("① Procurar os recibos")
    st.caption("Os comprovantes da Wise (PDF) que ainda não viraram nota. Neste passo nada é enviado.")
    pasta = st.text_input("Pasta dos recibos", value=str(settings.recibos_dir), key="pasta")
    emitidas = st.text_input(
        "Depois de emitidos, mover os recibos para", value=str(settings.emitidas_dir), key="emitidas"
    )
    if st.button(
        "Procurar recibos e preparar o pacote", type="primary", key="procurar", disabled=not certificado.ok
    ):
        caminho = Path(pasta).expanduser()
        if not caminho.is_dir():
            st.error(f"A pasta {caminho} não existe.")
        elif not listar_recibos(caminho):
            st.warning(f"Nenhum PDF em {caminho}.")
        else:
            with st.spinner("Lendo cada recibo e montando, validando e assinando a DPS dele…"):
                st.session_state.lote = L.preparar(caminho, settings, contador)
            st.session_state.mover_para = Path(emitidas).expanduser()
            st.session_state.etapa = 2
            st.rerun()

# --- passo 2 ----------------------------------------------------------------------------------------
elif etapa == 2:
    lote: L.Lote = st.session_state.lote
    prontas = lote.prontas
    st.subheader("② Conferir e autorizar")
    st.caption(
        f"Pasta {lote.pasta} · pacote {lote.impressao} · nada foi enviado ainda. Cada DPS abaixo já está "
        "montada, validada contra o XSD oficial e assinada: é exatamente isso que será enviado."
    )
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Notas no pacote", len(prontas), help=f"{len(lote.fora)} recibo(s) ficaram fora")
    m2.metric("Total recebido (BRL)", reais(lote.total_brl), help="O valor do serviço (vServ) de cada nota")
    m3.metric("Total pago (USD)", dolares(lote.total_usd), help="O valor em moeda estrangeira (vServMoeda)")
    m4.metric(
        "Números da DPS",
        f"{prontas[0].numero_dps} a {prontas[-1].numero_dps}" if prontas else "—",
        help="Reservados em sequência a partir do contador",
    )
    st.dataframe(tabela_pacote(lote), hide_index=True, width="stretch")
    for nota in lote.fora:
        st.error(f"⛔ {nota.recibo.name} ficou fora do pacote: {nota.erro}")
    if prontas:
        with st.expander("Ver a descrição e o XML assinado de cada DPS"):
            for nota in prontas:
                st.markdown(f"**Nº {nota.numero_dps}** · {nota.recibo.name} · identificador `{nota.id_dps}`")
                st.text(nota.descricao)
                st.code(nota.xml_assinado.decode("utf-8"), language="xml")

    with st.container(border=True):
        st.markdown("#### Autorização")
        if prontas:
            st.markdown(
                f"Serão enviadas **{len(prontas)}** nota(s), na ordem da tabela, ao ambiente "
                f"**{'de produção' if producao else 'de testes'}**. Os recibos emitidos vão para "
                f"`{st.session_state.mover_para}`. Se uma nota for recusada, a emissão para nela e as "
                "seguintes não são enviadas."
            )
        conferi = st.checkbox("Conferi os recibos, os valores e os números acima", key="conferi")
        palavra_ok = True
        if producao:
            palavra = st.text_input(
                f"Produção: digite {PALAVRA_PRODUCAO} para autorizar notas reais", key="palavra"
            )
            palavra_ok = palavra.strip() == PALAVRA_PRODUCAO
        pode = certificado.ok and bool(prontas) and conferi and palavra_ok
        b1, b2 = st.columns([3, 1])
        emitir = b1.button(
            f"Autorizar e emitir {len(prontas)} nota(s)", type="primary", disabled=not pode, key="emitir"
        )
        if b2.button("Descartar e voltar", key="descartar"):
            recomecar()
            st.rerun()

    if emitir:
        with st.status("Emitindo…", expanded=True) as status:

            def avancou(r: L.Resultado) -> None:
                status.write(f"{ICONE[r.estado]} Nº {r.nota.numero_dps} · {r.nota.recibo.name}: {r.mensagem}")

            try:
                resultados = L.emitir(lote, settings, contador, st.session_state.mover_para, avancou)
            except L.LoteDesatualizado as e:
                recomecar()
                st.session_state.aviso_topo = f"Nada foi enviado: {e}."
                st.rerun()
        st.session_state.resultados = resultados
        st.session_state.etapa = 3
        st.rerun()

# --- passo 3 ----------------------------------------------------------------------------------------
else:
    lote = st.session_state.lote
    resultados: list[L.Resultado] = st.session_state.resultados
    contagem = {estado: sum(r.estado == estado for r in resultados) for estado in ICONE}
    st.subheader("③ Emitidas e salvas")
    st.caption(f"Pacote {lote.impressao} · pasta {lote.pasta}")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Emitidas", contagem["emitida"])
    m2.metric("Recuperadas", contagem["recuperada"], help="Já existiam no servidor: baixadas, não reenviadas")
    m3.metric("Com problema", contagem["rejeitada"] + contagem["erro"])
    m4.metric("Não enviadas", contagem["não enviada"])
    st.dataframe(tabela_resultados(resultados), hide_index=True, width="stretch")

    parada = next((r for r in resultados if r.estado in ("rejeitada", "erro") or r.divergente), None)
    for r in resultados:
        if r.aviso:
            st.warning(f"Nº {r.nota.numero_dps} · {r.nota.recibo.name}: {r.aviso}")
    if parada is None:
        st.success(
            f"{contagem['emitida'] + contagem['recuperada']} nota(s) salva(s) em {settings.output_dir}. "
            f"O próximo número da DPS é {contador.peek()}."
        )
    else:
        st.error(
            f"A emissão parou no Nº {parada.nota.numero_dps} ({parada.nota.recibo.name}): {parada.mensagem} "
            f"As notas seguintes não foram enviadas, e o próximo número da DPS é {contador.peek()}. Os recibos "
            "não emitidos continuam na pasta: corrija e procure de novo."
        )
    st.markdown(f"Os PDFs das notas (DANFSe) se baixam no portal: [Emissor Nacional · Notas emitidas]({PORTAL_EMITIDAS}).")
    if st.button("Começar outro lote", type="primary", key="recomecar"):
        recomecar()
        st.rerun()
