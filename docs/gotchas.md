# NFS-e Nacional: armadilhas técnicas

Descobertas iteração por iteração durante a implementação deste projeto. Cada item resolve um código de erro específico do servidor. **Crítico**: o XSD por si só NÃO basta para gerar uma DPS aceita — várias regras estão no Anexo I (`docs/anexo_i-sefin_adn-dps_nfse-v1-01.xlsx`) ou só aparecem como erro no servidor.

## DANFSe — Totais Aproximados de Tributos (Lei 12.741)

A choice `<totTrib>` tem 4 opções (mutuamente exclusivas):
- `vTotTrib` com Fed/Est/Mun em R$
- `pTotTrib` com Fed/Est/Mun em %
- `pTotTribSN` (alíquota única do SN)
- `indTotTrib` (não informar nada)

`pTotTribSN` é aceito pelo servidor mas **NÃO popula a seção "Totais Aproximados" da DANFSe** (campos Federais/Estaduais/Municipais ficam em branco). Para a DANFSe ficar completa: usar `vTotTrib` com breakdown monetário.

Para Simples Nacional ME/EPP exportação (Anexo III, Fator R 6%): toda a alíquota vai em `vTotTribFed` (DAS é federal único), `vTotTribEst=0.00`, `vTotTribMun=0.00` (ISS exonerado pela exportação). Cálculo: `vTotTribFed = vServ * aliq_simples / 100`.

Regras (Anexo I): `indTotTrib` proibido para ME/EPP (E0712); `pTotTribSN` proibido para MEI (E0710); `vTotTrib`/`pTotTrib` aceitos para ME/EPP sem restrição.

## Wrapping da requisição

- POST `/SefinNacional/nfse` espera **JSON** com `{"dpsXmlGZipB64": "<base64 do gzip do XML assinado>"}`, Content-Type `application/json`. Mandar XML cru retorna **415**.
- Resposta de sucesso é HTTP 201 com `nfseXmlGZipB64` (mesmo encoding) contendo a NFS-e gerada.

## Assinatura XMLDSig (foi o ponto mais trabalhoso)

- **Sem prefixo de namespace na Signature.** `<Signature xmlns:ds="...">` triggers **E1228**. Deve ser `<Signature xmlns="http://www.w3.org/2000/09/xmldsig#">` (default xmlns, sem `ds:`). Signxml 4.x não suporta isso nativamente → escrever signer manual com `cryptography` + `lxml`.
- **Canonicalização: usar EXCLUSIVE c14n** (`http://www.w3.org/2001/10/xml-exc-c14n#`), NÃO inclusive 1.0. O NFe tradicional usa inclusive, mas a NFS-e Nacional rejeita com **E0714** se for inclusive.
- **Algoritmo de digest/sig: SHA-1** funcionou (`rsa-sha1` + `sha1`). SHA-256 também é aceito pela spec; ambos foram testados, mas SHA-1 está em produção no projeto pelo alinhamento com NFe tradicional.
- **Bug do lxml com c14n inclusive + redefinição de default namespace encadeada** (`<DPS xmlns="nfse"><Signature xmlns="ds#"><SignedInfo>...`): lxml gera `<Transforms xmlns="">` espúrio nos descendentes de Reference. Não afeta exclusive c14n. Se algum dia voltar a inclusive, contornar canonicalizando SignedInfo num documento isolado.

## Identificador da DPS (Id no `<infDPS>`)

Formato fixo de 45 chars: `DPS` + município(7) + tpInsc(1) + inscFederal(14) + serie(5) + nDPS(15). **tpInsc=1 é CPF, tpInsc=2 é CNPJ** (regra L102 do Anexo I — contraintuitivo). Erro de descasamento entre Id e campos: **E0004**.

## Construção da DPS

- **Endereço do prestador NÃO é informado** quando o próprio prestador é o emitente (`tpEmit=1`) — o servidor busca do cadastro CNPJ. Informar dispara **E0128**. Remover o bloco `<end>` inteiro de `<prest>`.
- **Simples Nacional ME/EPP (`opSimpNac=3`) exige `<regApTribSN>`** dentro de `<regTrib>`, entre `opSimpNac` e `regEspTrib`. Sem ele → **E0166**. Valor 1 = tributos federais + ISSQN apurados pelo SN.
- **Exportação de serviços** (`tribISSQN=3`, `mdPrestacao=1`, tomador via `<endExt>`): NÃO informar `pAliq`, `tpImunidade`, `exigSusp`, `BM` em `<tribMun>` — são proibidos para exportação. Apenas `tribISSQN=3`, `cPaisResult=<ISO>`, `tpRetISSQN=1`.
- **Tomador sem NIF/EIN**: usar `<cNaoNIF>2</cNaoNIF>` ("Não exigência do NIF"). Quando houver, usar `<NIF>` direto.

## Endpoints (mTLS-only, cert ICP-Brasil)

- Produção: `https://sefin.nfse.gov.br/SefinNacional/nfse`
- Produção restrita (sandbox): `https://sefin.producaorestrita.nfse.gov.br/SefinNacional/nfse`
- Swagger só é acessível via mTLS (use `--cert/--key` no curl com cert credenciado): `/SefinNacional/swagger/docs/v1`
- **Processamento síncrono** + **idempotência traiçoeira**: reenviar DPS com mesma chave de NFS-e existente dispara cancelamento por substituição automático. NÃO retry cego em timeout — usar `GET /dps/{id}` para verificar antes.

## OpenSSL legacy

PFX gerados pela Soluti / SERPRO usam encriptação legada (RC2-40-CBC). OpenSSL 3.x precisa de `-legacy` ao extrair PEM:
```
openssl pkcs12 -legacy -in cert.pfx -clcerts -nokeys -out cert.pem -password pass:...
```

## Fonte oficial

`docs/anexo_i-sefin_adn-dps_nfse-v1-01.xlsx` é a fonte da verdade para regras de negócio — vale mais que o manual PDF (6 páginas, raso). Abas relevantes: `LEIAUTE DPS_NFS-e ` (campo a campo) e `RN DPS_NFS-e` (regras de negócio com códigos de erro).

## Download de DANFSe (PDF) é gateado por hCaptcha

Endpoint do portal Emissor: `GET https://www.nfse.gov.br/EmissorNacional/Notas/Download/DANFSe/{chave}` (e equivalente `/Download/NFSe/{chave}` para XML autorizado). Acesso requer:
1. Login via cert client em `GET /EmissorNacional/Certificado` → cookies `ASP.NET_SessionId`, `Emissor`, `ARRAffinity`.
2. **hCaptcha resolvido por requisição** (sitekey `e02c27a0-0542-4c9a-88da-e48697acd87c`). O JS `/EmissorNacional/Areas/Notas/Scripts/Emitidas.js` intercepta clicks e abre modal: POST `/EmissorNacional/DPS/ModalCaptcha/SolicitarCaptcha` com `h-captcha-response` → resposta `{Sucesso: true, RedirectUrl}` → browser segue redirect para baixar.

GET direto com sessão autenticada mas sem fluxo de captcha retorna **403 IIS genérico** (não 401/302). Curl/requests/Playwright headless não vão funcionar sem resolver o captcha. Opções viáveis: Playwright headed com humano-no-loop, serviço pago tipo 2captcha/AntiCaptcha (sitekey + URL → token), ou download manual. **Não é possível automatizar 100% sem custo ou input humano.**

API mTLS (`sefin.nfse.gov.br/SefinNacional/...`) **não expõe endpoint para o PDF DANFSe** — só retorna o XML autorizado via `GET /nfse/{chave}`. PDF é só pelo portal Emissor.
