# Comandos do projeto. `make help` lista todos.
PY := .venv/bin/python

.PHONY: help install test ui peek

help:
	@echo "make install  cria .venv e instala as dependências (inclui pytest)"
	@echo "make test     roda os testes, offline"
	@echo "make ui       abre a interface em http://127.0.0.1:8501"
	@echo "make peek     mostra o próximo número da DPS"

install:
	python3 -m venv .venv
	$(PY) -m pip install -r requirements-dev.txt

test:
	$(PY) -m pytest

ui:
	PYTHONPATH=src .venv/bin/streamlit run src/nfse_exportacao/ui.py

peek:
	PYTHONPATH=src $(PY) -m nfse_exportacao.main --peek
