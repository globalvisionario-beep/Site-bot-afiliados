# Site de ofertas com bot (afiliados Shopee)

Site estático com busca por número, mais um bot que roda sozinho todo dia às 6h no GitHub Actions. Não precisa de servidor nem de celular ligado.

## O que o bot faz

1. Para cada produto do `produtos.json`, consulta a API de afiliados da Shopee.
2. Se a promoção continua (desconto de pelo menos 10%), só atualiza preço, nota e vendas.
3. Se a promoção acabou, busca o mesmo tipo de produto (pela palavra-chave `busca`) e escolhe o **mais barato** que cumpra:
   - nota de 4,6 ou mais;
   - 3000 vendas ou mais (se não houver nenhum, aceita 1000 ou mais, nunca menos);
   - em promoção hoje;
   - nome com as palavras da busca;
   - preço entre 40% e 160% do preço original do produto antigo (evita trocar um triturador por uma peça de reposição).
4. Troca o produto, mantendo o **mesmo número**, com o seu link de afiliado (`offerLink`).

Todos os limites ficam em `bot/config.json`.

## Instalação (uma vez só)

**1. Criar o repositório.** No GitHub, crie um repositório **público** (o GitHub Pages grátis exige isso). Dê o nome que quiser, por exemplo `ofertas`.

**2. Enviar os arquivos.** Envie tudo desta pasta. Atenção: a pasta `.github` é oculta e costuma ficar de fora ao arrastar arquivos. Para esse arquivo, use *Add file > Create new file*, digite `.github/workflows/ofertas.yml` no nome e cole o conteúdo.

**3. Guardar as chaves.** Em *Settings > Secrets and variables > Actions > New repository secret*, crie:

- `SHOPEE_APP_ID`: seu App ID
- `SHOPEE_APP_SECRET`: seu Secret

Nunca coloque essas chaves em arquivos do repositório.

**4. Ativar o Pages.** Em *Settings > Pages*, em *Source* escolha **GitHub Actions**.

**5. Testar a API.** Vá em *Actions > Atualizar ofertas e publicar > Run workflow*. No campo "diagnostico" digite `mini triturador eletrico` e rode. Abra a execução e leia o log: ele mostra os produtos encontrados e qual o bot escolheria. Também dá para digitar um itemId para ver a resposta bruta da API.

**6. Rodar de verdade.** Rode o workflow de novo com o campo vazio. Ele preenche os produtos, salva e publica. O endereço do site aparece no fim da execução (algo como `https://seu-usuario.github.io/ofertas/`).

## Adicionar produtos

Edite o `produtos.json` no próprio GitHub (ícone do lápis) e adicione uma linha. Ao salvar, o workflow roda sozinho e preenche o produto.

```json
{ "codigo": 3, "busca": "mini ventilador portatil" }
```

Campos opcionais por produto:

- `"itemId": 123456789`: você escolhe o produto exato. O bot só preenche os dados. O itemId é o último número do link da Shopee (`...-i.SHOPID.ITEMID`).
- `"palavras_obrigatorias": ["triturador", "eletrico"]`: palavras que o nome do substituto precisa ter.
- `"preco_minimo"` e `"preco_maximo"`: limites de preço para o substituto.

Não reaproveite números de código. O número identifica o produto no vídeo.

## Usar nos vídeos

Mostre na tela "Produto 12" e deixe o site na bio. Para ir direto ao produto, use `seu-site/#12`.

## Domínio próprio (opcional)

Compre um `.com.br` (cerca de R$ 40 por ano), aponte para o GitHub Pages seguindo *Settings > Pages > Custom domain* e crie um arquivo `CNAME` com o domínio na raiz do repositório.

## Se algo der errado

- **Execução vermelha com "ERRO da API":** confira App ID e Secret. O texto do erro aparece no log.
- **Nenhum produto escolhido:** veja o diagnóstico. Talvez a busca seja específica demais ou os filtros (nota, vendas, desconto) estejam rígidos.
- **Produto trocado errado:** use `palavras_obrigatorias` e `preco_minimo` naquele item.
- Os nomes dos campos da API foram conferidos na documentação pública, mas o bot não foi testado com a sua conta. O diagnóstico existe para isso.
