---
name: nfse-exportacao
description: Use quando o usuário quiser emitir NFS-e (Nota Fiscal de Serviços Eletrônica) de exportação de serviços a partir de comprovantes de pagamento recebidos via Wise, ou operações relacionadas — consultar nota por chave, verificar próximo número da DPS, recuperar nota emitida, ou troubleshooting de erros do Sistema Nacional NFS-e (Sefin Nacional gov.br). Trigger em pedidos como "emitir nota fiscal", "rodar batch das notas", "ver próximo nDPS", "consultar NFS-e", "qual o estado do counter".
---

# Emitir NFS-e de exportação de serviços

Skill opcional para um agente de código (Claude Code) operar este projeto pela linha de comando. Uma pessoa não precisa dela: a interface (`make ui`) faz o mesmo fluxo, com a conferência e a autorização na tela. Os caminhos abaixo são relativos à raiz do projeto; `<recibos>` e `<emitidas>` são as pastas que o usuário escolher.

## Estrutura

- Projeto: raiz deste repositório (venv em `.venv/`)
- Recibos a processar: `<recibos>/*.pdf` (formato Wise "transfer confirmation")
- Recibos já emitidos: `<emitidas>/`
- Saídas (XML/JSON da NFS-e): `out/nfse-*.{xml,json}`
- Counter de DPS: `out/.dps-counter-<CNPJ>-<serie>-<ambiente>.json`
- Certificado A1: caminho em `CERT_PATH` no `.env` (senha em `CERT_PASSWORD`)
- Config: `.env` (verificar `NFSE_AMBIENTE`)

## Fluxo padrão (emissão em lote)

1. **Pré-flight** — confirmar:
   - `grep NFSE_AMBIENTE .env` → `producao` para notas reais, `producaorestrita` para sandbox
   - `PYTHONPATH=src .venv/bin/python -m nfse_exportacao.main --peek` para ver o próximo nDPS
   - Os recibos a processar estão em `<recibos>/` (fora da subpasta de emitidas)

2. **Mostrar plano** para o usuário antes de executar (lista de PDFs + valores em USD/BRL + total). Pedir confirmação antes de emitir em produção.

3. **Executar batch**:
   ```bash
   PYTHONPATH=src .venv/bin/python -m nfse_exportacao.main --batch <recibos> --mover-para <emitidas>
   ```

4. **Após sucesso** — mostrar resumo:
   - Quantas notas emitidas + chaves de acesso
   - Counter atualizado (próximo nDPS)
   - Lembrar o usuário de baixar os PDFs (DANFSe) em https://www.nfse.gov.br/EmissorNacional/Notas/Emitidas se precisar

## Operações auxiliares

| Operação | Comando |
|---|---|
| Próximo nDPS | `PYTHONPATH=src .venv/bin/python -m nfse_exportacao.main --peek` |
| Emitir uma só | `PYTHONPATH=src .venv/bin/python -m nfse_exportacao.main caminho/recibo.pdf --mover-para <emitidas>` |
| Consultar nota por chave | `PYTHONPATH=src .venv/bin/python -m nfse_exportacao.main --consultar <chave50d>` |
| Forçar nDPS específico | `--numero N` (geralmente não fazer; só em recovery manual) |

## Premissas fiscais do código (NÃO mudar sem alinhar com o usuário e o contador)

- Emissor: pessoa jurídica no Simples Nacional, ME/EPP (`opSimpNac=3`, `regApTribSN=1`)
- Município do prestador conveniado ao Padrão Nacional; Inscrição Municipal opcional
- Código LC 116: 1.04 (`cTribNac=010401`) — elaboração de programas de computador
- Tomador no exterior, endereço em `<endExt>`, sem NIF por padrão (`cNaoNIF=2`)
- Tratamento: exportação de serviço (`tribISSQN=3`, `mdPrestacao=1` transfronteiriço, ISS = 0)
- Valores do comprovante Wise: `vServ` = "Total to …" (BRL líquido); `vServMoeda` = "Amount paid by …" (USD bruto); `dCompet` = data "Funded"
- `vTotTrib` (informativo, só para a DANFSe) usa `ALIQ_SIMPLES_APROX` do `.env`: confirmar a alíquota com o contador

## Mudança importante de campo: vTotTrib, não pTotTribSN

A DANFSe (PDF visual) só preenche "Totais Aproximados dos Tributos" se a DPS usar `vTotTrib` (R$). Se aparecer DANFSe com esses campos vazios, conferir que `xml_builder.py` usa `vTotTrib` (Fed/Est/Mun) e NÃO `pTotTribSN`.

## Troubleshooting (erros comuns do servidor)

| Código | Causa típica | Fix |
|---|---|---|
| **E0004** | Id da DPS ≠ campos | tpInsc deve ser 2 (CNPJ), não 1 |
| **E0006** | tpAmb ≠ ambiente do endpoint | sincronizar `<tpAmb>` com `NFSE_AMBIENTE` |
| **E0128** | endereço do prestador informado | omitir `<end>` em `<prest>` quando tpEmit=1 |
| **E0166** | regApTribSN faltando | obrigatório quando opSimpNac=3 |
| **E0714** | erro na assinatura | usar EXCLUSIVE c14n (`xml-exc-c14n#`), nunca inclusive |
| **E1228** | namespace com prefixo | `<Signature xmlns="...xmldsig#">` sem `ds:` |
| **415** | content-type errado | enviar JSON `{"dpsXmlGZipB64": "<gzip+b64>"}` |
| **403** | cert sem credenciamento | verificar com o contador se o cert é e-CNPJ válido para o ambiente |

Mais detalhes em `docs/gotchas.md`.

## Idempotência e retry

- O cliente faz `HEAD /dps/{id}` antes de cada emissão; se a DPS já existe, baixa a NFS-e existente e avança o counter automaticamente. **Não fazer retry cego** se o POST der timeout — só re-rodar o comando, que o HEAD vai detectar.
- Reenviar com a mesma chave dispara cancelamento por substituição (armadilha grave). O HEAD evita isso.

## Para cancelamento

Cancelamento ainda **não implementado em código**. Fazer manualmente no portal: https://www.nfse.gov.br/EmissorNacional/Notas/Emitidas (precisa do cert importado no navegador). Após cancelar, mover o PDF correspondente de `<emitidas>/` de volta para `<recibos>/` se quiser re-emitir.

## Quando NÃO usar esta skill

- Para tomadores brasileiros (toda a lógica é exportação para o exterior)
- Para prestadores fora do Simples Nacional ME/EPP, ou MEI (regras de `regTrib` e `totTrib` diferentes)
- Para municípios fora do Padrão Nacional (podem usar sistemas próprios)
- Para emitir em sandbox/produção restrita: trocar `NFSE_AMBIENTE=producaorestrita` no `.env` antes de qualquer coisa, e lembrar que o counter é separado por ambiente.
