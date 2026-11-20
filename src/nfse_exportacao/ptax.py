from datetime import date, timedelta
from decimal import Decimal

import requests

_PTAX_URL = (
    "https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/"
    "CotacaoDolarDia(dataCotacao=@dataCotacao)"
)


def cotacao_usd_brl(dia: date) -> Decimal:
    """Retorna a cotação PTAX de venda do USD em BRL para o dia útil informado.

    Se o dia não tiver cotação (fim de semana/feriado), busca o último dia útil anterior.
    """
    for delta in range(0, 7):
        d = dia - timedelta(days=delta)
        params = {
            "@dataCotacao": f"'{d.strftime('%m-%d-%Y')}'",
            "$format": "json",
            "$select": "cotacaoVenda,dataHoraCotacao",
        }
        resp = requests.get(_PTAX_URL, params=params, timeout=15)
        resp.raise_for_status()
        valores = resp.json().get("value", [])
        if valores:
            return Decimal(str(valores[0]["cotacaoVenda"]))
    raise RuntimeError(f"Sem cotação PTAX nos 7 dias anteriores a {dia.isoformat()}")
