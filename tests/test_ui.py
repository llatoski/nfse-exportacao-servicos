"""A interface, rodada pelo AppTest do Streamlit (sem navegador): os três passos, a autorização e as travas."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from conftest import SENHA_ERRADA
from nfse_exportacao import config
from nfse_exportacao import lote as L
from nfse_exportacao.config import Settings

UI = Path(__file__).resolve().parents[1] / "src" / "nfse_exportacao" / "ui.py"


@pytest.fixture
def enviados(monkeypatch: pytest.MonkeyPatch) -> list[bytes]:
    lista: list[bytes] = []

    def enviar(xml: bytes, *a: Any) -> dict[str, Any]:
        lista.append(xml)
        return {"_http_status": 201, "chaveAcesso": f"K{len(lista)}"}

    monkeypatch.setattr(L, "dps_existe", lambda *a: False)
    monkeypatch.setattr(L, "enviar_dps", enviar)
    return lista


def _abrir(monkeypatch: pytest.MonkeyPatch, settings: Settings, pasta: Path) -> AppTest:
    settings.recibos_dir = pasta
    settings.emitidas_dir = pasta / "emitidas"
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    app = AppTest.from_file(str(UI), default_timeout=60)
    app.run()
    assert not app.exception, app.exception
    return app


def _metrica(app: AppTest, rotulo: str) -> str:
    return next(m.value for m in app.metric if m.label == rotulo)


def _texto(app: AppTest) -> str:
    return "\n".join(m.value for m in app.markdown)


def test_abre_no_passo_1_mostrando_ambiente_cnpj_e_contador(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, pasta: Path
) -> None:
    app = _abrir(monkeypatch, settings, pasta)
    assert "AMBIENTE DE TESTES" in _texto(app)
    assert '<div class="passo atual"><b>1</b>Procurar os recibos</div>' in _texto(app)
    assert _metrica(app, "Empresa (CNPJ)") == "11.222.333/0001-81"
    assert _metrica(app, "Próximo número da DPS") == "1"
    assert _metrica(app, "Certificado A1") == "✅ ok"
    assert app.text_input(key="pasta").value == str(pasta)
    assert not app.button(key="procurar").disabled


def test_fluxo_completo_procurar_autorizar_emitir(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, pasta: Path, enviados: list[bytes]
) -> None:
    app = _abrir(monkeypatch, settings, pasta)

    app.button(key="procurar").click().run()
    assert app.session_state.etapa == 2
    assert enviados == []  # preparar não envia nada
    assert '<div class="passo feito"><b>✓</b>Procurar os recibos</div>' in _texto(app)
    tabela = app.dataframe[0].value
    assert list(tabela["Recibo"]) == ["wise_01.pdf", "wise_02.pdf", "wise_03.pdf", "wise_04.pdf"]
    assert list(tabela["Nº DPS"]) == ["1", "—", "2", "3"]
    assert list(tabela["Recebido (BRL)"])[0] == "R$ 5.123,45"
    assert _metrica(app, "Notas no pacote") == "3"
    assert _metrica(app, "Total recebido (BRL)") == "R$ 18.223,95"
    assert _metrica(app, "Números da DPS") == "1 a 3"
    assert any("wise_02.pdf ficou fora do pacote" in e.value for e in app.error)

    assert app.button(key="emitir").disabled  # sem conferir, não emite
    app.checkbox(key="conferi").check().run()
    assert not app.button(key="emitir").disabled
    app.button(key="emitir").click().run()

    assert not app.exception, app.exception
    assert app.session_state.etapa == 3
    assert len(enviados) == 3
    assert _metrica(app, "Emitidas") == "3"
    assert _metrica(app, "Próximo número da DPS") == "4"
    assert any("3 nota(s) salva(s)" in s.value for s in app.success)
    assert sorted(p.name for p in (pasta / "emitidas").iterdir()) == ["wise_01.pdf", "wise_03.pdf", "wise_04.pdf"]

    app.button(key="recomecar").click().run()
    assert app.session_state.etapa == 1


def test_producao_exige_digitar_a_palavra(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, pasta: Path, enviados: list[bytes]
) -> None:
    settings.nfse_ambiente = "producao"
    app = _abrir(monkeypatch, settings, pasta)
    assert "PRODUÇÃO" in _texto(app)

    app.button(key="procurar").click().run()
    app.checkbox(key="conferi").check().run()
    assert app.button(key="emitir").disabled
    app.text_input(key="palavra").input("emitir").run()
    assert app.button(key="emitir").disabled  # maiúsculas
    app.text_input(key="palavra").input("EMITIR").run()
    assert not app.button(key="emitir").disabled
    assert enviados == []


def test_certificado_com_problema_bloqueia_o_inicio(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, pasta: Path
) -> None:
    settings.cert_password = SENHA_ERRADA
    app = _abrir(monkeypatch, settings, pasta)
    assert _metrica(app, "Certificado A1") == "❌ problema"
    assert any("senha errada" in e.value for e in app.error)
    assert app.button(key="procurar").disabled


def test_pasta_inexistente(monkeypatch: pytest.MonkeyPatch, settings: Settings, pasta: Path) -> None:
    app = _abrir(monkeypatch, settings, pasta)
    app.text_input(key="pasta").input(str(pasta / "nao-existe")).run()
    app.button(key="procurar").click().run()
    assert app.session_state.etapa == 1
    assert any("não existe" in e.value for e in app.error)


def test_rejeicao_aparece_e_diz_onde_parou(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, pasta: Path
) -> None:
    respostas = iter(
        [{"_http_status": 201, "chaveAcesso": "K1"}, {"_http_status": 400, "erros": [{"Codigo": "E0166", "Descricao": "x"}]}]
    )
    monkeypatch.setattr(L, "dps_existe", lambda *a: False)
    monkeypatch.setattr(L, "enviar_dps", lambda *a: next(respostas))
    app = _abrir(monkeypatch, settings, pasta)
    app.button(key="procurar").click().run()
    app.checkbox(key="conferi").check().run()
    app.button(key="emitir").click().run()

    assert _metrica(app, "Emitidas") == "1"
    assert _metrica(app, "Com problema") == "1"
    assert _metrica(app, "Não enviadas") == "1"
    assert any("parou no Nº 2" in e.value and "próximo número da DPS é 2" in e.value for e in app.error)


def test_contador_mudou_antes_de_autorizar(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, pasta: Path, enviados: list[bytes]
) -> None:
    from nfse_exportacao.state import DpsCounter

    app = _abrir(monkeypatch, settings, pasta)
    app.button(key="procurar").click().run()
    DpsCounter(settings.prestador_cnpj, "1", settings.nfse_ambiente, settings.output_dir).consume(1)
    app.checkbox(key="conferi").check().run()
    app.button(key="emitir").click().run()

    assert enviados == []
    assert app.session_state.etapa == 1
    assert any("Nada foi enviado" in w.value for w in app.warning)


def test_descartar_volta_ao_passo_1(monkeypatch: pytest.MonkeyPatch, settings: Settings, pasta: Path) -> None:
    app = _abrir(monkeypatch, settings, pasta)
    app.button(key="procurar").click().run()
    app.button(key="descartar").click().run()
    assert app.session_state.etapa == 1
    assert "lote" not in app.session_state
