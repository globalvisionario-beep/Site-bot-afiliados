#!/usr/bin/env python3
"""
Bot de ofertas (afiliados Shopee Brasil).

Uso:
  python3 bot/atualizar.py                      -> atualização diária (o que o GitHub Actions roda)
  python3 bot/atualizar.py diagnostico "texto"  -> testa uma busca na API (não altera o site)
  python3 bot/atualizar.py diagnostico 123456   -> testa a consulta de um itemId (não altera o site)

Variáveis de ambiente obrigatórias:
  SHOPEE_APP_ID, SHOPEE_APP_SECRET

Só usa a biblioteca padrão do Python (nada para instalar).
"""
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ENDPOINT = "https://open-api.affiliate.shopee.com.br/graphql"
RAIZ = Path(__file__).resolve().parent.parent
ARQ_PRODUTOS = RAIZ / "produtos.json"
ARQ_CONFIG = Path(__file__).resolve().parent / "config.json"
FUSO = timezone(timedelta(hours=-3))  # horário de Brasília

CAMPOS = (
    "itemId productName price priceMin priceMax priceDiscountRate "
    "sales ratingStar imageUrl productLink offerLink"
)
TEMPLATE_BUSCA = (
    "query { productOfferV2(keyword: %KW%, sortType: %ORD%, page: %PG%, limit: %LIM%) "
    "{ nodes { %CAMPOS% } pageInfo { hasNextPage } } }"
)
TEMPLATE_ID = "query { productOfferV2(itemId: %ID%, limit: 1) { nodes { %CAMPOS% } } }"
STOPWORDS = {"para", "com", "sem", "por", "uma", "uns", "das", "dos"}


class ErroAPI(Exception):
    pass


def log(*args):
    print(*args, flush=True)


# --------------------------------------------------------------------------
# Comunicação com a API
# --------------------------------------------------------------------------
def chamar_api(query, app_id, secret):
    """Envia uma consulta GraphQL assinada. Tenta 3 vezes em caso de falha de rede."""
    corpo = json.dumps({"query": query}, separators=(",", ":"), ensure_ascii=False)
    ultimo_erro = None
    for tentativa in range(3):
        ts = str(int(time.time()))
        assinatura = hashlib.sha256((app_id + ts + corpo + secret).encode("utf-8")).hexdigest()
        req = urllib.request.Request(
            ENDPOINT,
            data=corpo.encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"SHA256 Credential={app_id}, Timestamp={ts}, Signature={assinatura}",
            },
        )
        dados = None
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                dados = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            texto = e.read().decode("utf-8", "replace")
            try:
                dados = json.loads(texto)
            except ValueError:
                dados = None
            if not dados or "errors" not in dados:
                ultimo_erro = ErroAPI(f"HTTP {e.code}: {texto[:300]}")
                time.sleep(2 * (tentativa + 1))
                continue
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            ultimo_erro = ErroAPI(f"Falha de rede: {e}")
            time.sleep(2 * (tentativa + 1))
            continue

        if dados.get("errors"):
            raise ErroAPI(json.dumps(dados["errors"], ensure_ascii=False)[:600])
        time.sleep(0.4)  # respeita o limite de requisições
        return dados.get("data") or {}
    raise ultimo_erro or ErroAPI("Falha desconhecida")


# --------------------------------------------------------------------------
# Tratamento dos dados
# --------------------------------------------------------------------------
def para_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def normalizar(texto):
    texto = unicodedata.normalize("NFD", str(texto).lower())
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9 ]+", " ", texto)


def normalizar_no(n):
    preco = para_float(n.get("price")) or para_float(n.get("priceMin"))
    desconto = int(para_float(n.get("priceDiscountRate")))
    if preco and 0 < desconto < 95:
        original = round(preco / (1 - desconto / 100), 2)
    else:
        original = preco
    return {
        "itemId": int(para_float(n.get("itemId"))),
        "nome": (n.get("productName") or "").strip(),
        "preco": round(preco, 2),
        "preco_original": original,
        "desconto": desconto,
        "vendas": int(para_float(n.get("sales"))),
        "nota": round(para_float(n.get("ratingStar")), 2),
        "imagem": n.get("imageUrl") or "",
        "link": n.get("offerLink") or "",
        "bruto": n,
    }


def buscar(busca, cfg, app_id, secret):
    """Busca por palavra-chave (relevância e mais vendidos) e junta os resultados."""
    vistos, itens = set(), []
    for ordenacao in (1, 2):  # 1 = relevância, 2 = vendas
        for pagina in range(1, int(cfg["paginas_busca"]) + 1):
            q = (
                TEMPLATE_BUSCA.replace("%KW%", json.dumps(busca, ensure_ascii=False))
                .replace("%ORD%", str(ordenacao))
                .replace("%PG%", str(pagina))
                .replace("%LIM%", str(int(cfg["itens_por_pagina"])))
                .replace("%CAMPOS%", CAMPOS)
            )
            bloco = chamar_api(q, app_id, secret).get("productOfferV2") or {}
            for n in bloco.get("nodes") or []:
                item = normalizar_no(n)
                if item["itemId"] and item["itemId"] not in vistos:
                    vistos.add(item["itemId"])
                    itens.append(item)
            if not (bloco.get("pageInfo") or {}).get("hasNextPage"):
                break
    return itens


def buscar_por_id(item_id, app_id, secret):
    q = TEMPLATE_ID.replace("%ID%", str(int(item_id))).replace("%CAMPOS%", CAMPOS)
    nos = (chamar_api(q, app_id, secret).get("productOfferV2") or {}).get("nodes") or []
    return normalizar_no(nos[0]) if nos else None


def palavras_obrigatorias(produto):
    base = produto.get("palavras_obrigatorias") or produto.get("busca", "").split()
    saida = []
    for palavra in base:
        for w in normalizar(palavra).split():
            if len(w) >= 3 and w not in STOPWORDS:
                saida.append(w[:5] if len(w) > 5 else w)  # raiz da palavra (eletrico/eletrica)
    return saida


def escolher(itens, produto, cfg, excluir_ids):
    """Escolhe o produto MAIS BARATO que cumpre todos os critérios."""
    obrigatorias = palavras_obrigatorias(produto)
    referencia = produto.get("preco_original") or produto.get("preco") or 0
    faixa = cfg.get("faixa_preco") or [0, 0]
    piso = para_float(produto.get("preco_minimo"))
    teto = para_float(produto.get("preco_maximo"))

    def ok(i, vendas_min):
        if i["itemId"] in excluir_ids or not i["link"] or i["preco"] <= 0:
            return False
        if i["nota"] < cfg["nota_minima"] or i["vendas"] < vendas_min:
            return False
        if i["desconto"] < cfg["desconto_minimo_percentual"]:
            return False
        nome = normalizar(i["nome"])
        if not all(p in nome for p in obrigatorias):
            return False
        if referencia and faixa[1]:
            if not (referencia * faixa[0] <= i["preco"] <= referencia * faixa[1]):
                return False
        if piso and i["preco"] < piso:
            return False
        if teto and i["preco"] > teto:
            return False
        return True

    for vendas_min in (cfg["vendas_preferidas"], cfg["vendas_minimas"]):
        candidatos = [i for i in itens if ok(i, vendas_min)]
        if candidatos:
            return min(candidatos, key=lambda i: (i["preco"], -i["vendas"]))
    return None


def aplicar(produto, item, cfg, agora):
    produto.update(
        {
            "itemId": item["itemId"],
            "nome": item["nome"] or produto.get("nome", ""),
            "imagem": item["imagem"] or produto.get("imagem", ""),
            "preco": item["preco"],
            "preco_original": item["preco_original"],
            "desconto": item["desconto"],
            "nota": item["nota"],
            "vendas": item["vendas"],
            "em_promocao": item["desconto"] >= cfg["desconto_minimo_percentual"],
            "verificado_em": agora,
        }
    )
    if item["link"]:
        produto["link"] = item["link"]


# --------------------------------------------------------------------------
# Rotina principal
# --------------------------------------------------------------------------
def atualizar(cfg, app_id, secret):
    with open(ARQ_PRODUTOS, encoding="utf-8") as f:
        site = json.load(f)
    produtos = site.get("produtos", [])
    historico = site.setdefault("historico_trocas", [])
    agora = datetime.now(FUSO).isoformat(timespec="seconds")
    ids_no_site = {p.get("itemId") for p in produtos if p.get("itemId")}
    trocas, erros = 0, 0

    for p in produtos:
        codigo = p.get("codigo")
        try:
            # 1) Produto novo, com itemId informado por você: só preenche os dados.
            if p.get("itemId") and not p.get("link"):
                item = buscar_por_id(p["itemId"], app_id, secret)
                if item:
                    aplicar(p, item, cfg, agora)
                    log(f"[{codigo}] preenchido com o itemId {item['itemId']}: {item['nome'][:50]}")
                else:
                    log(f"[{codigo}] itemId {p['itemId']} não foi encontrado na API.")
                continue

            # 2) Produto novo, só com palavra-chave: escolhe o melhor da busca.
            if not p.get("itemId"):
                if not p.get("busca"):
                    log(f"[{codigo}] sem 'busca' nem 'itemId', ignorado.")
                    continue
                novo = escolher(buscar(p["busca"], cfg, app_id, secret), p, cfg, ids_no_site)
                if novo:
                    aplicar(p, novo, cfg, agora)
                    ids_no_site.add(novo["itemId"])
                    log(f"[{codigo}] primeiro produto escolhido: R$ {novo['preco']:.2f} | {novo['nome'][:50]}")
                else:
                    log(f"[{codigo}] nenhum produto com nota/vendas/desconto suficientes para '{p['busca']}'.")
                continue

            # 3) Produto que já está no site: a promoção ainda existe?
            atual = buscar_por_id(p["itemId"], app_id, secret)
            if atual and atual["preco"] > 0 and atual["desconto"] >= cfg["desconto_minimo_percentual"]:
                aplicar(p, atual, cfg, agora)
                log(f"[{codigo}] promoção continua ({atual['desconto']}%): R$ {atual['preco']:.2f}")
                continue

            # 4) Promoção acabou: procura o mesmo tipo de produto em promoção hoje.
            log(f"[{codigo}] promoção acabou. Procurando substituto para '{p.get('busca')}'...")
            if trocas >= int(cfg["max_trocas_por_execucao"]):
                log(f"[{codigo}] limite de trocas por execução atingido; fica para amanhã.")
                continue
            if atual:
                aplicar(p, atual, cfg, agora)  # atualiza o preço, marca como sem promoção
            else:
                p["em_promocao"] = False
            novo = escolher(buscar(p["busca"], cfg, app_id, secret), p, cfg, ids_no_site)
            if novo:
                historico.append(
                    {
                        "data": agora,
                        "codigo": codigo,
                        "de": p.get("nome", ""),
                        "para": novo["nome"],
                        "preco": novo["preco"],
                    }
                )
                ids_no_site.add(novo["itemId"])
                aplicar(p, novo, cfg, agora)
                p["trocado_em"] = agora
                trocas += 1
                log(f"[{codigo}] TROCADO por: R$ {novo['preco']:.2f} | {novo['nome'][:50]}")
            else:
                log(f"[{codigo}] nenhum substituto bom hoje; mantém o atual e tenta de novo amanhã.")
        except ErroAPI as e:
            erros += 1
            log(f"[{codigo}] ERRO da API: {e}")

    if produtos and erros == len(produtos):
        log("Todas as consultas falharam (confira App ID e Secret). Nada foi salvo.")
        sys.exit(1)

    site["atualizado_em"] = agora
    site["historico_trocas"] = historico[-100:]
    with open(ARQ_PRODUTOS, "w", encoding="utf-8") as f:
        json.dump(site, f, ensure_ascii=False, indent=2)
        f.write("\n")
    log(f"Pronto: {trocas} troca(s), {erros} erro(s).")


def linha(i):
    return (
        f"  {i['itemId']} | R$ {i['preco']:.2f} (-{i['desconto']}%) | nota {i['nota']} "
        f"| {i['vendas']} vendas | {i['nome'][:60]}"
    )


def diagnostico(alvo, cfg, app_id, secret):
    alvo = alvo.strip()
    if not alvo:
        log("Informe uma palavra-chave ou um itemId.")
        sys.exit(1)
    if alvo.isdigit():
        q = TEMPLATE_ID.replace("%ID%", alvo).replace("%CAMPOS%", CAMPOS)
        log("Resposta bruta da API para o itemId:")
        log(json.dumps(chamar_api(q, app_id, secret), indent=2, ensure_ascii=False))
        return
    itens = buscar(alvo, cfg, app_id, secret)
    log(f"{len(itens)} produtos encontrados para '{alvo}'. Primeiro resultado (bruto):")
    if itens:
        log(json.dumps(itens[0]["bruto"], indent=2, ensure_ascii=False))
    log("\nPrimeiros 15 resultados:")
    for i in itens[:15]:
        log(linha(i))
    escolhido = escolher(itens, {"busca": alvo}, cfg, set())
    log("\nO bot escolheria:")
    log(linha(escolhido) if escolhido else "  nenhum (nenhum passou nos filtros de nota, vendas e desconto)")


def main():
    modo = sys.argv[1] if len(sys.argv) > 1 else "atualizar"
    app_id = os.environ.get("SHOPEE_APP_ID", "").strip()
    secret = os.environ.get("SHOPEE_APP_SECRET", "").strip()
    if not app_id or not secret:
        log("ERRO: defina SHOPEE_APP_ID e SHOPEE_APP_SECRET (em Settings > Secrets do GitHub).")
        sys.exit(1)
    with open(ARQ_CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    try:
        if modo == "diagnostico":
            diagnostico(sys.argv[2] if len(sys.argv) > 2 else "", cfg, app_id, secret)
        else:
            atualizar(cfg, app_id, secret)
    except ErroAPI as e:
        log(f"ERRO da API: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
