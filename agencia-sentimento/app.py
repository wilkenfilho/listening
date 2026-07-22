"""
Painel de Análise de Sentimentos em Comentários (Apify: SIC + Campanhas BR | Pulsar: planilha)
Fluxo em uma tela só: escolhe a fonte, preenche o contexto, clica em um botão e
recebe coleta/leitura + sentimentalização + relatório aprofundado de uma vez.
Inclui: gastos por extração/usuário, login Google restrito ao domínio, menu de
Configurações restrito ao admin (chaves de API, prompts do Gemini e acesso de usuários).
"""

import io
import json
import math
import os
import random
import re
import time
from datetime import datetime

import pandas as pd
import plotly.express as px
import streamlit as st

# ----------------------------------------------------------------------
# CONFIGURAÇÃO DA PÁGINA E CONSTANTES DE NEGÓCIO
# ----------------------------------------------------------------------
st.set_page_config(
    page_title="Análise de Sentimentos - Comentários",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

ALLOWED_DOMAIN = "br-mediagroup.com"
ADMIN_EMAIL = "wilken.perez@br-mediagroup.com"

ACTOR_INSTAGRAM = "SbK00X0JYCPblD2wp"  # Instagram Comments Scraper
ACTOR_TIKTOK = "BDec00yAmCm1QbMEI"     # TikTok Comments Scraper
ACTOR_INSTAGRAM_POSTS = "shu8hvrXbJbY3Eb9W"  # Instagram Scraper (posts/reels/perfis)

RATE_PER_1000 = {"instagram": 1.90, "tiktok": 0.50, "instagram_posts": 1.50}
SENTIMENT_COLORS = {"Positivo": "#22C55E", "Neutro": "#94A3B8", "Negativo": "#EF4444"}
ORIGENS_APIFY = ["Campanha BR (Apify)", "SIC - Reels", "SIC - TikTok"]
FONTES = ["Apify (links de Instagram/TikTok)", "Pulsar (planilha exportada)"]

# Processamento em chunks (evita 1 chamada de Gemini por comentário e evita
# que o relatório aprofundado dependa de uma amostra pequena e não-fiel).
TAMANHO_LOTE_CLASSIFICACAO = 25   # comentários classificados por chamada de Gemini
TAMANHO_CHUNK_ANALISE = 150       # comentários lidos por chamada na análise aprofundada
TAMANHO_LOTE_SAUDABILIDADE = 20   # posts avaliados por chamada de Gemini (saudabilidade)

# Constantes (aproximadas) usadas só para estimar o ETA mostrado na tela.
SEG_POR_LOTE_CLASSIFICACAO = 6
SEG_POR_CHUNK_ANALISE = 10
SEG_SINTESE_FINAL = 12
SEG_POR_PLATAFORMA_APIFY = 25  # o tempo real do Actor varia bastante — é só uma referência
SEG_METRICAS_IG = 15
SEG_POR_LOTE_SAUDABILIDADE = 6

TIPOS_ESTUDO = [
    "Marca própria",
    "Estudo de concorrência",
    "Marca própria + concorrência",
    "Comportamental / cultural (sem marca específica)",
]
TIPOS_MARCA = ["Própria", "Concorrente"]


def nova_marca() -> dict:
    return {"nome": "", "tipo": "Própria", "o_que_faz": "", "produtos": [""]}


def novo_tema() -> dict:
    return {"nome": "", "descricao": ""}

PULSAR_SENTIMENT_MAP = {
    "positive": "Positivo",
    "negative": "Negativo",
    "neutral": "Neutro",
    "not_evaluable": "Neutro",
}

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# Amostras usadas somente em modo demonstração (Apify não configurado), para a
# aba de Posts/Reels/Perfis e o enriquecimento de métricas continuarem navegáveis offline.
DEMO_POSTS_SAMPLE = [
    {
        "inputUrl": "https://www.instagram.com/natgeo/",
        "id": "3923124318436838545",
        "type": "Image",
        "shortCode": "DZxvMgyH8yR",
        "caption": "[DEMO] Foto de exemplo de um post do Instagram, com legenda simulada.",
        "hashtags": [],
        "mentions": ["carstenpeter"],
        "url": "https://www.instagram.com/p/DZxvMgyH8yR/",
        "commentsCount": 110,
        "likesCount": 55952,
        "timestamp": "2026-06-20T10:00:04.000Z",
        "ownerFullName": "National Geographic",
        "ownerUsername": "natgeo",
        "ownerId": "787132",
        "isCommentsDisabled": False,
        "isPinned": False,
        "productType": "feed",
    },
    {
        "inputUrl": "https://www.instagram.com/nasawebb/reels/",
        "id": "3913028191006298064",
        "type": "Video",
        "shortCode": "DZN3mhZBQ_Q",
        "caption": "[DEMO] Legenda simulada de um reel sobre um tema qualquer.",
        "hashtags": [],
        "mentions": [],
        "url": "https://www.instagram.com/p/DZN3mhZBQ_Q/",
        "commentsCount": 92,
        "likesCount": 11481,
        "timestamp": "2026-06-05T19:58:30.000Z",
        "ownerFullName": "NASA Webb Telescope",
        "ownerUsername": "nasawebb",
        "ownerId": "549313808",
        "isPinned": True,
        "productType": "clips",
        "videoDuration": 228,
        "videoViewCount": 41365,
        "videoPlayCount": 241820,
        "isCommentsDisabled": False,
    },
]

DEMO_PERFIL_SAMPLE = [
    {
        "inputUrl": "https://www.instagram.com/humansofny/",
        "id": "242598499",
        "username": "humansofny",
        "url": "https://www.instagram.com/humansofny",
        "fullName": "Humans of New York",
        "biography": "[DEMO] Biografia simulada de um perfil qualquer.",
        "externalUrl": "https://bit.ly/exemplo",
        "followersCount": 12613771,
        "followsCount": 738,
        "isBusinessAccount": False,
        "businessCategoryName": None,
        "private": False,
        "verified": True,
        "postsCount": 5863,
    }
]
USERS_FILE = os.path.join(DATA_DIR, "usuarios_permitidos.json")
PROMPTS_FILE = os.path.join(DATA_DIR, "prompts.json")

DEFAULT_PROMPT_SENTIMENTO = """Você é um analista de social listening de uma agência de marketing.
Classifique o sentimento de CADA comentário da lista abaixo em Positivo, Negativo ou Neutro.
Seja fiel e literal ao que está escrito em cada comentário — não invente, não extrapole e não
generalize além do que o texto realmente diz.

Briefing do conteúdo: {{BRIEFING}}
Diretrizes da marca para esta análise: {{DIRETRIZES}}

Comentários (numerados):
{{COMENTARIOS_NUMERADOS}}

Responda SOMENTE em JSON, uma lista com um item por comentário, na mesma ordem numerada, neste
formato exato:
[{"indice": 1, "sentimento": "Positivo", "justificativa": "explicação curta em português"}, {"indice": 2, "sentimento": "Neutro", "justificativa": "..."}]"""

DEFAULT_PROMPT_SENTIMENTO_APIFY = """Você é um analista de sentimento especializado em comentários de redes sociais.

Contexto da campanha: {{CAMPANHA}}
Marca: {{MARCA}}
Briefing do conteúdo: {{BRIEFING}}
Diretrizes da marca: {{DIRETRIZES}}

Para CADA comentário da lista numerada abaixo, classifique com base estritamente no que está
escrito — não invente nem extrapole:
- sentimento: "Positivo", "Negativo" ou "Neutro"
- alvo: sobre o que o comentário fala — "conteúdo" (o vídeo/post/criador em si), "marca",
  "produto/serviço" ou "preço"
- emocao: a emoção predominante (ex: alegria, raiva, surpresa, desprezo, indiferença, admiração...)
- pertinencia: "pertinente" se o comentário tem relação com o post, a marca, o produto/serviço ou
  o preço; "não_pertinente" se for genérico, spam, ou sem relação nenhuma com a campanha
- justificativa: explicação curta em português

Comentários (numerados):
{{COMENTARIOS_NUMERADOS}}

Responda SOMENTE em JSON, uma lista com um item por comentário, na mesma ordem numerada, neste
formato exato:
[{"indice": 1, "sentimento": "Positivo", "alvo": "marca", "emocao": "alegria", "pertinencia": "pertinente", "justificativa": "..."}]

Regras importantes: a soma de itens classificados nunca deve ultrapassar o total de comentários
enviados, e cada comentário recebe exatamente uma classificação de cada campo."""

DEFAULT_PROMPT_ANALISE_BLOCO = """Você é um analista de social listening. Leia com atenção o bloco de
comentários abaixo e responda de forma estritamente fiel ao que está escrito — não invente
temas, exemplos ou conclusões que não estejam apoiados nos comentários deste bloco.

Contexto do estudo: {{CONTEXTO}}

Bloco de comentários ({{TOTAL_BLOCO}} comentários):
{{COMENTARIOS_BLOCO}}

Responda em português, em tópicos objetivos, cobrindo:
- Principais temas e assuntos recorrentes neste bloco.
- Padrões de sentimento observados (com 2-3 exemplos curtos e literais, sem inventar).
- Sinais de oportunidade, se houver.
- Sinais de risco ou crítica, se houver.
Seja direto e conciso — isto é um resumo intermediário, não o relatório final."""

DEFAULT_PROMPT_RELATORIO = """Você é um analista sênior de social listening e antropologia digital, \
escrevendo um relatório para apresentar a clientes de uma agência de marketing.

Campanha/conteúdo: {{CAMPANHA}}
Marca: {{MARCA}} | Mother brand: {{MOTHER_BRAND}} | Núcleo: {{NUCLEO}}
Briefing do conteúdo: {{BRIEFING}}
Diretrizes da marca: {{DIRETRIZES}}

Resumo quantitativo dos comentários analisados:
{{RESUMO_QUANTITATIVO}}

Resumos fiéis de cada bloco de comentários (esta é a base factual real da análise —
use isto como fonte principal, não invente nada além do que está aqui):
{{RESUMOS_DOS_BLOCOS}}

Amostra de comentários literais, para ilustrar com citações reais:
{{AMOSTRA_COMENTARIOS}}

Escreva uma análise aprofundada, técnica e dissertativa (não apenas bullets soltos) cobrindo,
nesta ordem:
1. Panorama geral do engajamento e do sentimento do público.
2. Insights sobre o comportamento e a percepção do público.
3. Oportunidades para a marca.
4. Riscos e pontos de atenção.
5. Uma leitura antropológica/cultural do que os comentários revelam sobre esse público.
6. Conclusão, amarrando os pontos acima em forma de storytelling.

Mantenha-se estritamente fiel aos resumos dos blocos e aos comentários — não fuja do assunto
nem introduza informação externa que não esteja apoiada no material fornecido. Escreva em
português, tom técnico mas acessível, como material de estudo entregável ao cliente."""

DEFAULT_PROMPT_SAUDABILIDADE = """Você é um analista de social listening avaliando a 'saudabilidade' \
(saúde do engajamento e da percepção do público) de cada post/conteúdo abaixo, com base na
distribuição de sentimento dos comentários já analisados e nas métricas do post.

Marca: {{MARCA}} | Campanha: {{CAMPANHA}}
Diretrizes da marca: {{DIRETRIZES}}

Posts (numerados):
{{POSTS_NUMERADOS}}

Para CADA post da lista numerada, avalie a saudabilidade dele para a marca — considere o
equilíbrio entre sentimento do público e as métricas de engajamento (quando disponíveis).
Responda SOMENTE em JSON, uma lista com um item por post, na mesma ordem numerada, neste
formato exato:
[{"indice": 1, "saudabilidade_score": 0, "classificacao": "Saudável", "resumo": "explicação curta em português, 1-2 frases"}]

Onde "saudabilidade_score" é um número de 0 a 100 (100 = ótima recepção do público, sem riscos;
0 = crise/rejeição forte do público) e "classificacao" é uma destas: "Saudável", "Atenção" ou
"Crítico". Nunca ultrapasse o total de posts enviados e mantenha a mesma ordem numerada."""

# ----------------------------------------------------------------------
# ESTADO DA SESSÃO
# ----------------------------------------------------------------------
DEFAULTS = {
    "fonte": FONTES[0],
    "links_input": "",
    "origem": ORIGENS_APIFY[0],
    "campanha": "",
    "marca": "",
    "mother_brand": "",
    "nucleo": "",
    "briefing": "",
    "diretrizes_marca": "",
    "estudo_nome": "",
    "estudo_objetivo": "",
    "tipo_estudo": TIPOS_ESTUDO[0],
    "marcas_estudo": [nova_marca()],
    "temas_estudo": [novo_tema()],
    "diretrizes_extra": "",
    "reclassificar_pulsar_gemini": False,
    "gerar_relatorio_auto": True,
    "buscar_metricas_posts": True,
    "resultados_df": None,
    "relatorio_texto": "",
    "apify_token": "",
    "gemini_key": "",
    "gemini_model": "gemini-flash-lite-latest",
    "bq_project": "",
    "bq_dataset": "",
    "bq_table_post_comments": "post_comments",
    "bq_table_gastos": "gastos",
    "bq_table_usuarios": "usuarios_permitidos",
    "bq_table_posts_ig": "posts_instagram",
    "bq_credentials_json": None,
    "log": [],
    "gastos": [],
    "usuario_atual": "convidado",
    "ig_limit": 50,
    "tk_limit": 50,
    "pode_configurar": False,
    "usuarios_permitidos": None,
    "prompt_sentimento": DEFAULT_PROMPT_SENTIMENTO,
    "prompt_sentimento_apify": DEFAULT_PROMPT_SENTIMENTO_APIFY,
    "prompt_analise_bloco": DEFAULT_PROMPT_ANALISE_BLOCO,
    "prompt_relatorio": DEFAULT_PROMPT_RELATORIO,
    "prompt_saudabilidade": DEFAULT_PROMPT_SAUDABILIDADE,
    "prompts_loaded": False,
    "job": None,
    "ig_posts_links_input": "",
    "ig_posts_results_type": "posts",
    "ig_posts_limit": 12,
    "ig_posts_df": None,
}
for key, value in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = value


def get_secret(key: str, default=None):
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default


if "secrets_loaded" not in st.session_state:
    st.session_state.apify_token = get_secret("APIFY_TOKEN", st.session_state.apify_token)
    st.session_state.gemini_key = get_secret("GEMINI_API_KEY", st.session_state.gemini_key)
    st.session_state.gemini_model = get_secret("GEMINI_MODEL", st.session_state.gemini_model)
    st.session_state.bq_project = get_secret("BQ_PROJECT", st.session_state.bq_project)
    st.session_state.bq_dataset = get_secret("BQ_DATASET", st.session_state.bq_dataset)
    st.session_state.bq_table_post_comments = get_secret(
        "BQ_TABLE_POST_COMMENTS", st.session_state.bq_table_post_comments
    )
    st.session_state.bq_table_gastos = get_secret("BQ_TABLE_GASTOS", st.session_state.bq_table_gastos)
    st.session_state.bq_table_usuarios = get_secret(
        "BQ_TABLE_USUARIOS", st.session_state.bq_table_usuarios
    )
    st.session_state.bq_table_posts_ig = get_secret(
        "BQ_TABLE_POSTS_IG", st.session_state.bq_table_posts_ig
    )
    bq_service_account = get_secret("BQ_SERVICE_ACCOUNT", None)
    if bq_service_account:
        st.session_state.bq_credentials_json = dict(bq_service_account)
    st.session_state.gemini_from_secrets = bool(st.session_state.gemini_key)
    st.session_state.apify_from_secrets = bool(st.session_state.apify_token)
    st.session_state.bq_from_secrets = bool(st.session_state.bq_credentials_json)
    st.session_state.secrets_loaded = True


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    st.session_state.log.insert(0, f"[{ts}] {msg}")


def is_apify_configured() -> bool:
    return bool(st.session_state.apify_token)


def is_gemini_configured() -> bool:
    return bool(st.session_state.gemini_key)


def is_bq_configured() -> bool:
    return bool(
        st.session_state.bq_project
        and st.session_state.bq_dataset
        and st.session_state.bq_credentials_json
    )


def detectar_plataforma(link: str) -> str:
    l = link.lower()
    if "instagram.com" in l:
        return "instagram"
    if "tiktok.com" in l:
        return "tiktok"
    return "desconhecido"


_REGEX_URL_REDES = re.compile(
    r"https?://(?:www\.)?(?:instagram\.com|tiktok\.com)/[^\s\)\]\"'<>]+", re.IGNORECASE
)


def extrair_urls_redes(texto: str) -> list:
    """Extrai links do Instagram/TikTok de um texto colado — funciona mesmo se vier
    com título junto (ex.: '[Nome (@user) • Reel do Instagram](https://instagram.com/p/ABC/)')
    ou com várias linhas, cada uma com ruído ao redor do link."""
    if not texto:
        return []
    encontrados = []
    for linha in texto.splitlines():
        linha = linha.strip()
        if not linha:
            continue
        matches = _REGEX_URL_REDES.findall(linha)
        if matches:
            for m in matches:
                url = m.rstrip(").,;:!?")
                if url not in encontrados:
                    encontrados.append(url)
        elif "instagram.com" in linha.lower() or "tiktok.com" in linha.lower():
            # linha com o domínio mas sem "http" reconhecido pelo regex — mantém
            # como veio, é melhor tentar do que descartar silenciosamente
            if linha not in encontrados:
                encontrados.append(linha)
    return encontrados


def extrair_urls_instagram(texto: str) -> list:
    """Igual a extrair_urls_redes, mas filtrando só links do Instagram (usado na
    aba de Posts & Perfis, que só trabalha com o Actor do Instagram)."""
    return [u for u in extrair_urls_redes(texto) if "instagram.com" in u.lower()]


_REGEX_POST_ESPECIFICO = re.compile(r"instagram\.com/(?:p|reel|tv)/[A-Za-z0-9_-]+", re.IGNORECASE)


def eh_link_post_especifico(url: str) -> bool:
    """True se o link já aponta pra um post/reel específico (tem shortcode na URL,
    ex.: /p/ABC123/ ou /reel/ABC123/). False se for link de perfil (ex.: /usuario/
    ou /usuario/reels/), onde faz sentido perguntar quantos posts recentes trazer."""
    return bool(_REGEX_POST_ESPECIFICO.search(url or ""))


def calcular_custo(plataforma: str, qtd_comentarios: int) -> float:
    return (qtd_comentarios / 1000) * RATE_PER_1000.get(plataforma, 0)


def get_bq_client():
    from google.cloud import bigquery
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_info(
        st.session_state.bq_credentials_json
    )
    return bigquery.Client(project=st.session_state.bq_project, credentials=creds)


def salvar_df_bigquery(df: pd.DataFrame, tabela: str, modo: str = "APPEND"):
    from google.cloud import bigquery

    client = get_bq_client()
    table_id = f"{st.session_state.bq_project}.{st.session_state.bq_dataset}.{tabela}"
    disposicao = (
        bigquery.WriteDisposition.WRITE_TRUNCATE
        if modo == "TRUNCATE"
        else bigquery.WriteDisposition.WRITE_APPEND
    )
    job_config = bigquery.LoadJobConfig(write_disposition=disposicao)
    job = client.load_table_from_dataframe(df, table_id, job_config=job_config)
    job.result()
    return table_id


def registrar_gasto(plataforma: str, qtd_comentarios: int) -> float:
    custo = calcular_custo(plataforma, qtd_comentarios)
    registro = {
        "usuario": st.session_state.get("usuario_atual", "desconhecido"),
        "data_hora": datetime.now().isoformat(timespec="seconds"),
        "plataforma": plataforma,
        "comentarios_coletados": qtd_comentarios,
        "custo_usd": round(custo, 4),
    }
    st.session_state.gastos.append(registro)
    if is_bq_configured():
        try:
            salvar_df_bigquery(pd.DataFrame([registro]), st.session_state.bq_table_gastos)
        except Exception as e:
            log(f"Aviso: não foi possível salvar o gasto no BigQuery ({e}).")
    return custo


# ----------------------------------------------------------------------
# POSTS / REELS / PERFIS DO INSTAGRAM (métricas: curtidas, comentários, views...)
# ----------------------------------------------------------------------
def coletar_posts_ig(links: list, results_type: str = "posts", limit: int = 12, only_newer_than=None):
    """Chama o Actor de Posts/Reels/Perfis do Instagram no Apify (1 chamada cobre
    vários links de uma vez — o próprio Actor aceita uma lista em 'directUrls').

    results_type:
      - "posts"   -> retorna os posts/reels de um perfil, ou o post/reel específico
                     se o link já for de um post/reel.
      - "details" -> retorna os dados do perfil (seguidores, bio, contagens, etc.).
    """
    from apify_client import ApifyClient

    client = ApifyClient(st.session_state.apify_token)
    run_input = {
        "resultsType": results_type,
        "directUrls": links,
        "resultsLimit": limit,
        "searchType": "hashtag",
        "searchLimit": 10,
        "addParentData": False,
    }
    # Campos opcionais: a Apify valida o schema e rejeita `None` em campos do tipo
    # string — só mandamos essas chaves quando têm valor de verdade.
    if only_newer_than:
        run_input["onlyPostsNewerThan"] = only_newer_than
    run = client.actor(ACTOR_INSTAGRAM_POSTS).call(run_input=run_input)
    if isinstance(run, dict):
        dataset_id = run.get("defaultDatasetId")
    else:
        dataset_id = getattr(run, "default_dataset_id", None) or getattr(run, "defaultDatasetId", None)
    return list(client.dataset(dataset_id).iterate_items())


def normalizar_item_post(item: dict) -> dict:
    """Achata um post/reel do Instagram em uma linha de métricas."""
    return {
        "link": item.get("url") or item.get("inputUrl", ""),
        "input_url": item.get("inputUrl", ""),
        "shortcode": item.get("shortCode", ""),
        "tipo_midia": item.get("type", ""),
        "is_reel": item.get("productType") == "clips",
        "owner_username": item.get("ownerUsername", ""),
        "owner_full_name": item.get("ownerFullName", ""),
        "caption": (item.get("caption") or "")[:300],
        "hashtags": ", ".join(item.get("hashtags") or []),
        "mentions": ", ".join(item.get("mentions") or []),
        "likes_post": item.get("likesCount"),
        "comentarios_post": item.get("commentsCount"),
        "views_post": item.get("videoViewCount"),
        "plays_post": item.get("videoPlayCount"),
        # O Instagram não expõe compartilhamentos via scraping — o Actor atual não
        # retorna esse número. Deixamos o campo pronto (fica vazio) para o dia em
        # que um Actor passar a trazer esse dado.
        "compartilhamentos_post": item.get("shareCount") or item.get("reshareCount"),
        "duracao_video_seg": item.get("videoDuration"),
        "data_publicacao_post": item.get("timestamp"),
        "pinado": item.get("isPinned"),
        "comentarios_desabilitados": item.get("isCommentsDisabled"),
        "data_extracao_metricas": datetime.now().isoformat(),
    }


def normalizar_item_perfil(item: dict) -> dict:
    """Achata um perfil do Instagram (resultsType='details') em uma linha."""
    return {
        "link": item.get("url") or item.get("inputUrl", ""),
        "username": item.get("username", ""),
        "nome_completo": item.get("fullName", ""),
        "biografia": item.get("biography", ""),
        "seguidores": item.get("followersCount"),
        "seguindo": item.get("followsCount"),
        "qtd_posts": item.get("postsCount"),
        "verificado": item.get("verified"),
        "privado": item.get("private"),
        "conta_business": item.get("isBusinessAccount"),
        "categoria_negocio": item.get("businessCategoryName"),
        "url_externa": item.get("externalUrl"),
        "data_extracao": datetime.now().isoformat(),
    }


# ----------------------------------------------------------------------
# SAUDABILIDADE POR POST (agrega sentimento + métricas, em lote via Gemini)
# ----------------------------------------------------------------------
def montar_prompt_saudabilidade(posts_numerados: str) -> str:
    return (
        st.session_state.prompt_saudabilidade.replace(
            "{{MARCA}}", st.session_state.marca or "Não informado"
        )
        .replace("{{CAMPANHA}}", st.session_state.campanha or "Não informado")
        .replace("{{DIRETRIZES}}", st.session_state.diretrizes_marca or "Nenhuma diretriz específica")
        .replace("{{POSTS_NUMERADOS}}", posts_numerados)
    )


def classificar_saudabilidade_demo(pos_pct: float, neg_pct: float) -> dict:
    score = int(max(0, min(100, round(50 + (pos_pct - neg_pct) * 50))))
    if score >= 70:
        label = "Saudável"
    elif score >= 40:
        label = "Atenção"
    else:
        label = "Crítico"
    return {
        "saudabilidade_score": score,
        "classificacao": label,
        "resumo": f"[DEMO] Estimativa simples: {pos_pct:.0%} positivo vs {neg_pct:.0%} negativo nos comentários.",
    }


def classificar_saudabilidade_lote_gemini(model, lote_info: list) -> dict:
    """lote_info: lista de dicts {post_link, resumo_sentimento, metricas_texto}.
    Retorna {post_link: {saudabilidade_score, saudabilidade_classificacao, saudabilidade_resumo}}."""
    numerados = "\n".join(
        f"{i+1}. Post: {info['post_link']}\n   Sentimento: {info['resumo_sentimento']}\n   Métricas: {info['metricas_texto']}"
        for i, info in enumerate(lote_info)
    )
    prompt = montar_prompt_saudabilidade(numerados)
    resp = model.generate_content(prompt)
    mapa = {}
    try:
        parsed = json.loads(resp.text.strip().strip("```json").strip("```"))
        mapa = {int(item.get("indice", 0)): item for item in parsed if isinstance(item, dict)}
    except Exception:
        mapa = {}
    resultado = {}
    for i, info in enumerate(lote_info):
        item = mapa.get(i + 1, {})
        resultado[info["post_link"]] = {
            "saudabilidade_score": item.get("saudabilidade_score"),
            "saudabilidade_classificacao": item.get("classificacao", "Atenção"),
            "saudabilidade_resumo": item.get("resumo", "[sem retorno estruturado do Gemini para este item]"),
        }
    return resultado


def classificar_saudabilidade_lote_demo(lote_info: list) -> dict:
    resultado = {}
    for info in lote_info:
        demo = classificar_saudabilidade_demo(info.get("pos_pct", 0), info.get("neg_pct", 0))
        resultado[info["post_link"]] = {
            "saudabilidade_score": demo["saudabilidade_score"],
            "saudabilidade_classificacao": demo["classificacao"],
            "saudabilidade_resumo": demo["resumo"],
        }
    return resultado


def montar_resumo_por_post(resultados: pd.DataFrame) -> pd.DataFrame:
    """Agrega a tabela de comentários (post_comments) em uma linha por post, juntando
    contagem/percentual de sentimento com as métricas do post e a saudabilidade —
    usado na aba de resultados e na exportação Excel completa."""
    grupo = resultados.groupby("post_link", as_index=False).agg(
        comentarios_analisados=("comentario", "count"),
        positivos=("sentimento", lambda s: int((s == "Positivo").sum())),
        neutros=("sentimento", lambda s: int((s == "Neutro").sum())),
        negativos=("sentimento", lambda s: int((s == "Negativo").sum())),
    )

    campos_extra = [
        "plataforma", "origem", "campanha", "marca", "mother_brand", "nucleo",
        "likes_post", "comentarios_post", "views_post", "plays_post",
        "compartilhamentos_post", "tipo_midia", "is_reel", "duracao_video_seg",
        "data_publicacao_post", "saudabilidade_score", "saudabilidade_classificacao",
        "saudabilidade_resumo",
    ]
    extras_presentes = [c for c in campos_extra if c in resultados.columns]
    if extras_presentes:
        extras = resultados.groupby("post_link", as_index=False)[extras_presentes].first()
        grupo = grupo.merge(extras, on="post_link", how="left")

    total = grupo["comentarios_analisados"].replace(0, pd.NA)
    grupo["pct_positivo"] = (grupo["positivos"] / total).round(3)
    grupo["pct_negativo"] = (grupo["negativos"] / total).round(3)
    return grupo


def montar_contexto_marcas() -> str:
    partes = []
    for m in st.session_state.marcas_estudo:
        if not m["nome"].strip():
            continue
        produtos = [p.strip() for p in m["produtos"] if p.strip()]
        bloco = f"Marca: {m['nome']} ({m['tipo']})\nO que faz: {m['o_que_faz'].strip() or 'Não informado'}"
        if produtos:
            bloco += f"\nProdutos: {', '.join(produtos)}"
        partes.append(bloco)
    return "\n\n".join(partes) if partes else ""


def montar_contexto_temas() -> str:
    partes = []
    for t in st.session_state.temas_estudo:
        if not t["nome"].strip():
            continue
        bloco = f"Tema/comportamento: {t['nome']}"
        if t["descricao"].strip():
            bloco += f"\nDescrição: {t['descricao'].strip()}"
        partes.append(bloco)
    return "\n\n".join(partes) if partes else ""


def mapear_contexto_pulsar():
    """Traduz o formulário de estudo/marcas/produtos/temas do Pulsar para os campos
    (campanha, marca, mother_brand, briefing, diretrizes_marca) já usados
    pelos prompts do Gemini e pelas exportações."""
    proprias = [m["nome"] for m in st.session_state.marcas_estudo if m["tipo"] == "Própria" and m["nome"].strip()]
    concorrentes = [m["nome"] for m in st.session_state.marcas_estudo if m["tipo"] == "Concorrente" and m["nome"].strip()]

    st.session_state.campanha = st.session_state.estudo_nome
    st.session_state.marca = ", ".join(proprias) if proprias else "Não informado"
    st.session_state.mother_brand = ", ".join(concorrentes) if concorrentes else ""
    st.session_state.briefing = st.session_state.estudo_objetivo

    blocos = [f"Tipo de estudo: {st.session_state.tipo_estudo}"]
    contexto_marcas = montar_contexto_marcas()
    if contexto_marcas:
        blocos.append(contexto_marcas)
    contexto_temas = montar_contexto_temas()
    if contexto_temas:
        blocos.append("Temas/comportamentos/tendências em estudo:\n\n" + contexto_temas)
    if not contexto_marcas and not contexto_temas:
        blocos.append("Nenhuma marca ou tema específico detalhado — considere apenas o objetivo do estudo acima.")
    if st.session_state.diretrizes_extra.strip():
        blocos.append(f"Observações adicionais: {st.session_state.diretrizes_extra.strip()}")
    st.session_state.diretrizes_marca = "\n\n".join(blocos)


def classificar_demo(texto: str) -> str:
    negativos = ["não gostei", "péssima", "quebrado", "ruim"]
    positivos = ["adorei", "incrível", "excelente", "recomendo"]
    t = str(texto).lower()
    if any(p in t for p in negativos):
        return "Negativo"
    if any(p in t for p in positivos):
        return "Positivo"
    return "Neutro"


def formatar_duracao(segundos: float) -> str:
    segundos = max(0, int(round(segundos)))
    m, s = divmod(segundos, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m}min"
    if m:
        return f"{m}min {s}s"
    return f"{s}s"


def dividir_em_lotes(lista, tamanho):
    lista = list(lista)
    return [lista[i:i + tamanho] for i in range(0, len(lista), tamanho)]


def classificar_lote_gemini(model, lote_linhas: list) -> list:
    """Classifica um lote de comentários numa única chamada de Gemini."""
    numerados = "\n".join(f"{i+1}. {r.get('comentario','')}" for i, r in enumerate(lote_linhas))
    prompt = (
        st.session_state.prompt_sentimento
        .replace("{{BRIEFING}}", st.session_state.briefing or "Não informado.")
        .replace("{{DIRETRIZES}}", st.session_state.diretrizes_marca or "Nenhuma diretriz específica.")
        .replace("{{COMENTARIOS_NUMERADOS}}", numerados)
    )
    resp = model.generate_content(prompt)
    mapa = {}
    try:
        parsed = json.loads(resp.text.strip().strip("```json").strip("```"))
        mapa = {int(item.get("indice", 0)): item for item in parsed if isinstance(item, dict)}
    except Exception:
        mapa = {}
    resultado = []
    for i, r in enumerate(lote_linhas):
        item = mapa.get(i + 1, {})
        nova = dict(r)
        nova["sentimento"] = item.get("sentimento", "Neutro")
        nova["justificativa"] = item.get("justificativa", "[sem retorno estruturado do Gemini para este item]")
        resultado.append(nova)
    return resultado


def classificar_lote_gemini_apify(model, lote_linhas: list) -> list:
    """Classificação específica do Apify: sentimento + alvo + emoção + pertinência."""
    numerados = "\n".join(f"{i+1}. {r.get('comentario','')}" for i, r in enumerate(lote_linhas))
    prompt = (
        st.session_state.prompt_sentimento_apify
        .replace("{{CAMPANHA}}", st.session_state.campanha or "Não informado.")
        .replace("{{MARCA}}", st.session_state.marca or "Não informado.")
        .replace("{{BRIEFING}}", st.session_state.briefing or "Não informado.")
        .replace("{{DIRETRIZES}}", st.session_state.diretrizes_marca or "Nenhuma diretriz específica.")
        .replace("{{COMENTARIOS_NUMERADOS}}", numerados)
    )
    resp = model.generate_content(prompt)
    mapa = {}
    try:
        parsed = json.loads(resp.text.strip().strip("```json").strip("```"))
        mapa = {int(item.get("indice", 0)): item for item in parsed if isinstance(item, dict)}
    except Exception:
        mapa = {}
    resultado = []
    for i, r in enumerate(lote_linhas):
        item = mapa.get(i + 1, {})
        nova = dict(r)
        nova["sentimento"] = item.get("sentimento", "Neutro")
        nova["alvo"] = item.get("alvo", "conteúdo")
        nova["emocao"] = item.get("emocao", "indiferença")
        nova["pertinencia"] = item.get("pertinencia", "pertinente")
        nova["justificativa"] = item.get("justificativa", "[sem retorno estruturado do Gemini para este item]")
        resultado.append(nova)
    return resultado


ALVOS_DEMO = ["conteúdo", "marca", "produto/serviço", "preço"]


def classificar_lote_demo(lote_linhas: list, apify: bool = False) -> list:
    resultado = []
    for r in lote_linhas:
        nova = dict(r)
        sentimento = classificar_demo(nova.get("comentario", ""))
        nova["sentimento"] = sentimento
        nova["justificativa"] = "[DEMO] classificação simulada por palavras-chave"
        if apify:
            nova["alvo"] = random.choice(ALVOS_DEMO)
            nova["emocao"] = {"Positivo": "satisfação", "Negativo": "insatisfação", "Neutro": "indiferença"}[sentimento]
            nova["pertinencia"] = "pertinente"
        resultado.append(nova)
    return resultado


def analisar_bloco_gemini(model, bloco_comentarios: list, contexto_texto: str) -> str:
    texto = "\n".join(f'- "{c}"' for c in bloco_comentarios)
    prompt = (
        st.session_state.prompt_analise_bloco
        .replace("{{CONTEXTO}}", contexto_texto)
        .replace("{{TOTAL_BLOCO}}", str(len(bloco_comentarios)))
        .replace("{{COMENTARIOS_BLOCO}}", texto)
    )
    resp = model.generate_content(prompt)
    return resp.text


def montar_contexto_estudo_texto() -> str:
    return (
        f"Campanha/estudo: {st.session_state.campanha or 'Não informado'}\n"
        f"Marca: {st.session_state.marca or 'Não informado'} | "
        f"Mother brand: {st.session_state.mother_brand or 'Não informado'} | "
        f"Núcleo: {st.session_state.nucleo or 'Não informado'}\n"
        f"Objetivo/briefing: {st.session_state.briefing or 'Não informado'}\n"
        f"Diretrizes: {st.session_state.diretrizes_marca or 'Nenhuma diretriz específica'}"
    )


def gerar_sintese_final(model, resultados_df: pd.DataFrame, resumos_blocos: list) -> str:
    contagem = resultados_df["sentimento"].value_counts()
    total = len(resultados_df)
    resumo_quant = "\n".join(
        f"- {s}: {int(contagem.get(s,0))} comentários ({contagem.get(s,0)/total:.0%})"
        for s in ["Positivo", "Neutro", "Negativo"]
    )
    amostra_partes = []
    for s in ["Positivo", "Neutro", "Negativo"]:
        exemplos = resultados_df[resultados_df["sentimento"] == s]["comentario"].head(5).tolist()
        if exemplos:
            amostra_partes.append(f"{s}:\n" + "\n".join(f'- "{c}"' for c in exemplos))
    amostra = "\n\n".join(amostra_partes)
    resumos_texto = "\n\n---\n\n".join(f"[Bloco {i+1}]\n{r}" for i, r in enumerate(resumos_blocos)) or "Nenhum bloco processado."

    prompt = (
        st.session_state.prompt_relatorio
        .replace("{{CAMPANHA}}", st.session_state.campanha or "Não informado")
        .replace("{{MARCA}}", st.session_state.marca or "Não informado")
        .replace("{{MOTHER_BRAND}}", st.session_state.mother_brand or "Não informado")
        .replace("{{NUCLEO}}", st.session_state.nucleo or "Não informado")
        .replace("{{BRIEFING}}", st.session_state.briefing or "Não informado")
        .replace("{{DIRETRIZES}}", st.session_state.diretrizes_marca or "Nenhuma diretriz específica")
        .replace("{{RESUMO_QUANTITATIVO}}", resumo_quant)
        .replace("{{RESUMOS_DOS_BLOCOS}}", resumos_texto)
        .replace("{{AMOSTRA_COMENTARIOS}}", amostra)
    )
    resp = model.generate_content(prompt)
    return resp.text


def ler_planilha_pulsar(arquivo) -> pd.DataFrame:
    """Lê um export do Pulsar (aba 'Contents') e normaliza para o schema do app."""
    df = pd.read_excel(arquivo, sheet_name="Contents")
    df = df.dropna(subset=["content"]).copy()
    out = pd.DataFrame(
        {
            "post_link": df.get("url", ""),
            "plataforma": df.get("source", "desconhecido"),
            "autor": df.get("user screen name", df.get("user name", "")),
            "comentario": df["content"],
            "data_extracao": df.get("date (UTC)", "").astype(str),
            "sentimento": df.get("sentiment class", "").map(PULSAR_SENTIMENT_MAP).fillna("Neutro"),
            "justificativa": "Classificação original do Pulsar (sentiment class).",
        }
    )
    return out.reset_index(drop=True)


# ----------------------------------------------------------------------
# MOTOR DE PROCESSAMENTO EM FILA (ETA + barra de progresso + cancelar)
# ----------------------------------------------------------------------
def estimar_segundos(n_comentarios: int, n_plataformas_apify: int, precisa_classificar: bool, gerar_relatorio: bool, buscar_metricas: bool = False) -> int:
    seg = n_plataformas_apify * SEG_POR_PLATAFORMA_APIFY
    if precisa_classificar and n_comentarios:
        seg += math.ceil(n_comentarios / TAMANHO_LOTE_CLASSIFICACAO) * SEG_POR_LOTE_CLASSIFICACAO
    if buscar_metricas and n_comentarios:
        seg += SEG_METRICAS_IG + SEG_POR_LOTE_SAUDABILIDADE  # pelo menos 1 lote de saudabilidade
    if gerar_relatorio and n_comentarios:
        seg += math.ceil(n_comentarios / TAMANHO_CHUNK_ANALISE) * SEG_POR_CHUNK_ANALISE + SEG_SINTESE_FINAL
    return seg


def recomputar_total_passos(job: dict):
    n = len(job["linhas_coletadas"]) or len(job["linhas_prontas"])
    passos = job["passos_apify_fixos"]
    if job["precisa_classificar"] and n:
        passos += math.ceil(n / TAMANHO_LOTE_CLASSIFICACAO)
    if job.get("buscar_metricas_posts"):
        links_ig = job.get("links_instagram_unicos")
        if links_ig is None:
            # ainda não sabemos quantos links são do Instagram — soma 1 passo de reserva
            passos += 1
        elif links_ig:
            passos += 1
        n_posts_unicos = len(job.get("fila_saudabilidade_flat") or [])
        if n_posts_unicos:
            passos += math.ceil(n_posts_unicos / TAMANHO_LOTE_SAUDABILIDADE)
        elif n:
            passos += 1  # pelo menos 1 lote de saudabilidade estimado
    if job["gerar_relatorio"] and n:
        passos += math.ceil(n / TAMANHO_CHUNK_ANALISE) + 1
    job["total_passos"] = max(passos, job["passos_concluidos"] + 1)


def criar_job_apify(links_por_plataforma: dict, gerar_relatorio: bool) -> dict:
    plataformas_pendentes = [p for p in ("instagram", "tiktok") if links_por_plataforma.get(p)]
    n_estimado = (
        len(links_por_plataforma.get("instagram", [])) * st.session_state.ig_limit
        + len(links_por_plataforma.get("tiktok", [])) * st.session_state.tk_limit
    )
    job = {
        "fonte": "apify",
        "fase": "coleta_apify" if plataformas_pendentes else "preparar_classificacao",
        "inicio": time.time(),
        "links_por_plataforma": links_por_plataforma,
        "plataformas_pendentes": plataformas_pendentes,
        "passos_apify_fixos": len(plataformas_pendentes),
        "linhas_coletadas": [],
        "linhas_prontas": [],
        "custo_total": 0.0,
        "precisa_classificar": True,
        "gerar_relatorio": gerar_relatorio,
        "buscar_metricas_posts": st.session_state.buscar_metricas_posts,
        "links_instagram_unicos": None,
        "metricas_posts_ig": {},
        "fila_saudabilidade": [],
        "fila_saudabilidade_flat": None,
        "saudabilidade_por_post": {},
        "fila_lotes_classificacao": [],
        "fila_chunks_analise": [],
        "resumos_blocos": [],
        "relatorio_final": "",
        "contexto_estudo_texto": montar_contexto_estudo_texto(),
        "tempos_passo": [],
        "passos_concluidos": 0,
        "total_passos": max(1, len(plataformas_pendentes)),
        "erro_msg": None,
    }
    job["total_passos"] = max(
        job["total_passos"],
        len(plataformas_pendentes)
        + (math.ceil(n_estimado / TAMANHO_LOTE_CLASSIFICACAO) if n_estimado else 0)
        + (1 if st.session_state.buscar_metricas_posts and links_por_plataforma.get("instagram") else 0)
        + (1 if st.session_state.buscar_metricas_posts and n_estimado else 0)
        + (math.ceil(n_estimado / TAMANHO_CHUNK_ANALISE) + 1 if gerar_relatorio and n_estimado else 0),
    )
    return job


def criar_job_pulsar(arquivo_pulsar, reclassificar: bool, gerar_relatorio: bool) -> dict:
    job = {
        "fonte": "pulsar",
        "fase": "ler_pulsar",
        "inicio": time.time(),
        "pulsar_bytes": arquivo_pulsar.getvalue(),
        "linhas_coletadas": [],
        "linhas_prontas": [],
        "custo_total": 0.0,
        "precisa_classificar": reclassificar,
        "gerar_relatorio": gerar_relatorio,
        "buscar_metricas_posts": st.session_state.buscar_metricas_posts,
        "links_instagram_unicos": None,
        "metricas_posts_ig": {},
        "fila_saudabilidade": [],
        "fila_saudabilidade_flat": None,
        "saudabilidade_por_post": {},
        "fila_lotes_classificacao": [],
        "fila_chunks_analise": [],
        "resumos_blocos": [],
        "relatorio_final": "",
        "contexto_estudo_texto": montar_contexto_estudo_texto(),
        "tempos_passo": [],
        "passos_concluidos": 0,
        "total_passos": 1,
        "passos_apify_fixos": 0,
        "erro_msg": None,
    }
    return job


def processar_passo_job(job: dict):
    """Executa exatamente UM passo (uma chamada de API) e avança a fase do job."""
    fase = job["fase"]

    if fase == "coleta_apify":
        plataforma = job["plataformas_pendentes"].pop(0)
        plinks = job["links_por_plataforma"][plataforma]
        rows = []
        if is_apify_configured():
            try:
                from apify_client import ApifyClient

                client = ApifyClient(st.session_state.apify_token)
                if plataforma == "instagram":
                    run_input = {
                        "directUrls": plinks,
                        "resultsLimit": st.session_state.ig_limit,
                        "includeNestedComments": False,
                    }
                    actor_id = ACTOR_INSTAGRAM
                else:
                    run_input = {
                        "postURLs": plinks,
                        "commentsPerPost": st.session_state.tk_limit,
                        "topLevelCommentsPerPost": st.session_state.tk_limit,
                        "maxRepliesPerComment": 0,
                        "profiles": [],
                        "resultsPerPage": 100,
                        "profileScrapeSections": ["videos"],
                        "profileSorting": "latest",
                        "excludePinnedPosts": False,
                    }
                    actor_id = ACTOR_TIKTOK

                run = client.actor(actor_id).call(run_input=run_input)
                if isinstance(run, dict):
                    dataset_id = run.get("defaultDatasetId")
                else:
                    dataset_id = getattr(run, "default_dataset_id", None) or getattr(run, "defaultDatasetId", None)
                items = list(client.dataset(dataset_id).iterate_items())
                for item in items:
                    rows.append(
                        {
                            "post_link": item.get("postUrl") or item.get("videoUrl") or item.get("url", ""),
                            "plataforma": plataforma,
                            "autor": item.get("ownerUsername") or item.get("uniqueId") or item.get("author", ""),
                            "comentario": item.get("text") or item.get("comment", ""),
                            "data_extracao": datetime.now().isoformat(),
                        }
                    )
                log(f"Apify ({plataforma}) retornou {len(rows)} comentários reais.")
            except Exception as e:
                job["erro_msg"] = f"Erro ao chamar Apify ({plataforma}): {e}"
                log(job["erro_msg"])
        else:
            time.sleep(0.3)
            exemplos = [
                "Adorei esse conteúdo, muito útil!",
                "Não gostei do produto, veio quebrado.",
                "Achei ok, nada de mais.",
                "Excelente atendimento, super recomendo!",
                "Poderia ser melhor explicado.",
                "Péssima experiência, não volto mais.",
                "Simplesmente incrível, superou minhas expectativas!",
                "Comentário neutro sobre o assunto.",
            ]
            for link in plinks:
                for _ in range(random.randint(3, 6)):
                    rows.append(
                        {
                            "post_link": link,
                            "plataforma": plataforma,
                            "autor": f"usuario_{random.randint(100,999)}",
                            "comentario": random.choice(exemplos),
                            "data_extracao": datetime.now().isoformat(),
                        }
                    )
            log(f"[DEMO] {len(rows)} comentários fictícios gerados ({plataforma}).")

        if rows:
            job["linhas_coletadas"].extend(rows)
            job["custo_total"] += registrar_gasto(plataforma, len(rows))

        job["fase"] = "preparar_classificacao" if not job["plataformas_pendentes"] else "coleta_apify"
        return

    if fase == "ler_pulsar":
        try:
            df = ler_planilha_pulsar(io.BytesIO(job["pulsar_bytes"]))
            log(f"Planilha do Pulsar lida: {len(df)} comentários.")
            if job["precisa_classificar"]:
                job["linhas_coletadas"] = df.drop(columns=["sentimento", "justificativa"]).to_dict("records")
            else:
                job["linhas_prontas"] = df.to_dict("records")
                log("Usando a classificação de sentimento original do Pulsar.")
        except Exception as e:
            job["erro_msg"] = f"Erro ao ler a planilha do Pulsar: {e}"
            log(job["erro_msg"])
        job["fase"] = "preparar_classificacao"
        return

    if fase == "preparar_classificacao":
        recomputar_total_passos(job)
        if job["precisa_classificar"] and job["linhas_coletadas"]:
            job["fila_lotes_classificacao"] = dividir_em_lotes(job["linhas_coletadas"], TAMANHO_LOTE_CLASSIFICACAO)
            job["fase"] = "classificacao"
        else:
            job["fase"] = "preparar_metricas_ig"
        return

    if fase == "classificacao":
        lote = job["fila_lotes_classificacao"].pop(0)
        eh_apify = job["fonte"] == "apify"
        if is_gemini_configured():
            try:
                import google.generativeai as genai

                genai.configure(api_key=st.session_state.gemini_key)
                model = genai.GenerativeModel(st.session_state.gemini_model)
                if eh_apify:
                    classificados = classificar_lote_gemini_apify(model, lote)
                else:
                    classificados = classificar_lote_gemini(model, lote)
            except Exception as e:
                job["erro_msg"] = f"Erro no Gemini ao classificar — aplicando fallback simulado ({e})."
                log(job["erro_msg"])
                classificados = classificar_lote_demo(lote, apify=eh_apify)
        else:
            classificados = classificar_lote_demo(lote, apify=eh_apify)
        job["linhas_prontas"].extend(classificados)
        if not job["fila_lotes_classificacao"]:
            log(f"Classificação concluída: {len(job['linhas_prontas'])} comentários.")
            job["fase"] = "preparar_metricas_ig"
        return

    if fase == "preparar_metricas_ig":
        recomputar_total_passos(job)
        if not job.get("buscar_metricas_posts"):
            job["links_instagram_unicos"] = []
            job["fase"] = "preparar_analise"
            return
        links_ig = sorted({
            r.get("post_link", "") for r in job["linhas_prontas"]
            if r.get("post_link") and "instagram.com" in str(r.get("post_link", "")).lower()
        })
        job["links_instagram_unicos"] = links_ig
        job["fase"] = "metricas_ig" if links_ig else "preparar_saudabilidade"
        return

    if fase == "metricas_ig":
        links_ig = job.get("links_instagram_unicos") or []
        metricas = {}
        if is_apify_configured():
            try:
                items = coletar_posts_ig(links_ig, results_type="posts", limit=1)
                for item in items:
                    if "shortCode" not in item:
                        continue
                    linha = normalizar_item_post(item)
                    if linha["link"]:
                        metricas[linha["link"]] = linha
                if items:
                    custo = registrar_gasto("instagram_posts", len(items))
                    job["custo_total"] += custo
                    log(
                        f"Actor de posts/perfis IG retornou {len(items)} item(ns) para "
                        f"{len(links_ig)} link(s) — custo: ${custo:.4f}."
                    )
                else:
                    log("Actor de posts/perfis IG não retornou itens para esses links.")
            except Exception as e:
                log(f"Aviso: não foi possível buscar métricas dos posts do Instagram ({e}).")
        else:
            for item in DEMO_POSTS_SAMPLE:
                linha = normalizar_item_post(item)
                if linha["link"]:
                    metricas[linha["link"]] = linha
            log("[DEMO] Métricas fictícias de posts do Instagram usadas.")
        job["metricas_posts_ig"] = metricas
        job["fase"] = "preparar_saudabilidade"
        return

    if fase == "preparar_saudabilidade":
        recomputar_total_passos(job)
        info_por_post = []
        if not job.get("buscar_metricas_posts"):
            job["fila_saudabilidade"] = []
            job["fila_saudabilidade_flat"] = []
            job["fase"] = "preparar_analise"
            return
        df_tmp = pd.DataFrame(job["linhas_prontas"]) if job["linhas_prontas"] else pd.DataFrame()
        if not df_tmp.empty and "sentimento" in df_tmp.columns and "post_link" in df_tmp.columns:
            for post_link, grupo_post in df_tmp.groupby("post_link"):
                total_post = len(grupo_post)
                pos_pct = (grupo_post["sentimento"] == "Positivo").mean()
                neg_pct = (grupo_post["sentimento"] == "Negativo").mean()
                neu_pct = 1 - pos_pct - neg_pct
                m = job["metricas_posts_ig"].get(post_link, {})
                partes = []
                for campo, rotulo in [
                    ("likes_post", "curtidas"), ("comentarios_post", "comentários no post"),
                    ("views_post", "views"), ("plays_post", "plays"),
                ]:
                    valor = m.get(campo)
                    if valor is not None and not (isinstance(valor, float) and pd.isna(valor)):
                        try:
                            partes.append(f"{rotulo}: {int(valor)}")
                        except (TypeError, ValueError):
                            pass
                info_por_post.append({
                    "post_link": post_link,
                    "resumo_sentimento": (
                        f"Positivo {pos_pct:.0%}, Neutro {neu_pct:.0%}, Negativo {neg_pct:.0%} "
                        f"(de {total_post} comentários)"
                    ),
                    "metricas_texto": ", ".join(partes) if partes else "Não disponível",
                    "pos_pct": pos_pct,
                    "neg_pct": neg_pct,
                })
        job["fila_saudabilidade_flat"] = info_por_post
        job["fila_saudabilidade"] = dividir_em_lotes(info_por_post, TAMANHO_LOTE_SAUDABILIDADE)
        job["fase"] = "saudabilidade" if job["fila_saudabilidade"] else "preparar_analise"
        return

    if fase == "saudabilidade":
        lote_info = job["fila_saudabilidade"].pop(0)
        if is_gemini_configured():
            try:
                import google.generativeai as genai

                genai.configure(api_key=st.session_state.gemini_key)
                model = genai.GenerativeModel(st.session_state.gemini_model)
                resultado_lote = classificar_saudabilidade_lote_gemini(model, lote_info)
            except Exception as e:
                log(f"Erro no Gemini ao calcular saudabilidade — aplicando estimativa simples ({e}).")
                resultado_lote = classificar_saudabilidade_lote_demo(lote_info)
        else:
            resultado_lote = classificar_saudabilidade_lote_demo(lote_info)
        job["saudabilidade_por_post"].update(resultado_lote)
        if not job["fila_saudabilidade"]:
            log(f"Saudabilidade calculada para {len(job['saudabilidade_por_post'])} post(s).")
            job["fase"] = "preparar_analise"
        return

    if fase == "preparar_analise":
        recomputar_total_passos(job)
        if job["gerar_relatorio"] and job["linhas_prontas"]:
            comentarios = [r.get("comentario", "") for r in job["linhas_prontas"]]
            job["fila_chunks_analise"] = dividir_em_lotes(comentarios, TAMANHO_CHUNK_ANALISE)
            job["fase"] = "analise_blocos"
        else:
            job["fase"] = "concluido"
        return

    if fase == "analise_blocos":
        bloco = job["fila_chunks_analise"].pop(0)
        if is_gemini_configured():
            try:
                import google.generativeai as genai

                genai.configure(api_key=st.session_state.gemini_key)
                model = genai.GenerativeModel(st.session_state.gemini_model)
                resumo = analisar_bloco_gemini(model, bloco, job["contexto_estudo_texto"])
            except Exception as e:
                resumo = f"[Bloco não pôde ser analisado pelo Gemini: {e}]"
                log(f"Erro ao analisar bloco: {e}")
        else:
            contagem_local = pd.Series(bloco).apply(classificar_demo).value_counts()
            resumo = "[DEMO] " + ", ".join(f"{k}: {v}" for k, v in contagem_local.items())
        job["resumos_blocos"].append(resumo)
        if not job["fila_chunks_analise"]:
            job["fase"] = "sintese"
        return

    if fase == "sintese":
        df_prontas = pd.DataFrame(job["linhas_prontas"])
        if is_gemini_configured():
            try:
                import google.generativeai as genai

                genai.configure(api_key=st.session_state.gemini_key)
                model = genai.GenerativeModel(st.session_state.gemini_model)
                job["relatorio_final"] = gerar_sintese_final(model, df_prontas, job["resumos_blocos"])
                log("Relatório aprofundado gerado a partir dos blocos (Gemini).")
            except Exception as e:
                job["relatorio_final"] = f"Não foi possível gerar o relatório final: {e}"
                log(f"Erro na síntese final: {e}")
        else:
            contagem = df_prontas["sentimento"].value_counts()
            total = len(df_prontas)
            resumo = "\n".join(
                f"- {s}: {int(contagem.get(s,0))} ({contagem.get(s,0)/total:.0%})"
                for s in ["Positivo", "Neutro", "Negativo"]
            )
            job["relatorio_final"] = (
                f"# [DEMO] Análise aprofundada — {st.session_state.campanha or 'estudo sem nome'}\n\n"
                f"## Panorama geral\n\n{resumo}\n\n"
                "## Observação\n\nRelatório simulado — configure a Gemini API Key para gerar a "
                "análise dissertativa completa a partir dos blocos processados."
            )
            log("[DEMO] Relatório-modelo gerado (Gemini não configurado).")
        job["fase"] = "concluido"
        return


# ----------------------------------------------------------------------
# GESTÃO DE USUÁRIOS PERMITIDOS
# ----------------------------------------------------------------------
def carregar_usuarios() -> list:
    if is_bq_configured():
        try:
            client = get_bq_client()
            table_id = (
                f"{st.session_state.bq_project}."
                f"{st.session_state.bq_dataset}."
                f"{st.session_state.bq_table_usuarios}"
            )
            df = client.query(f"SELECT * FROM `{table_id}`").to_dataframe()
            return df.to_dict("records")
        except Exception as e:
            log(f"Aviso: não foi possível carregar usuários do BigQuery ({e}).")
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def salvar_usuarios(usuarios: list):
    st.session_state.usuarios_permitidos = usuarios
    if is_bq_configured():
        try:
            salvar_df_bigquery(pd.DataFrame(usuarios), st.session_state.bq_table_usuarios, modo="TRUNCATE")
        except Exception as e:
            log(f"Aviso: não foi possível salvar usuários no BigQuery ({e}).")
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(usuarios, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log(f"Aviso: não foi possível salvar usuários em disco ({e}).")


if st.session_state.usuarios_permitidos is None:
    st.session_state.usuarios_permitidos = carregar_usuarios()


def usuario_e_admin(email: str) -> bool:
    return (email or "").strip().lower() == ADMIN_EMAIL.lower()


def buscar_permissao(email: str):
    for u in st.session_state.usuarios_permitidos:
        if u.get("email", "").strip().lower() == (email or "").strip().lower():
            return u
    return None


# ----------------------------------------------------------------------
# GESTÃO DOS PROMPTS
# ----------------------------------------------------------------------
def carregar_prompts():
    if os.path.exists(PROMPTS_FILE):
        try:
            with open(PROMPTS_FILE, "r", encoding="utf-8") as f:
                dados = json.load(f)
            st.session_state.prompt_sentimento = dados.get("prompt_sentimento", DEFAULT_PROMPT_SENTIMENTO)
            st.session_state.prompt_sentimento_apify = dados.get(
                "prompt_sentimento_apify", DEFAULT_PROMPT_SENTIMENTO_APIFY
            )
            st.session_state.prompt_analise_bloco = dados.get("prompt_analise_bloco", DEFAULT_PROMPT_ANALISE_BLOCO)
            st.session_state.prompt_relatorio = dados.get("prompt_relatorio", DEFAULT_PROMPT_RELATORIO)
            st.session_state.prompt_saudabilidade = dados.get("prompt_saudabilidade", DEFAULT_PROMPT_SAUDABILIDADE)
        except Exception:
            pass


def salvar_prompts():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(PROMPTS_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "prompt_sentimento": st.session_state.prompt_sentimento,
                    "prompt_sentimento_apify": st.session_state.prompt_sentimento_apify,
                    "prompt_analise_bloco": st.session_state.prompt_analise_bloco,
                    "prompt_relatorio": st.session_state.prompt_relatorio,
                    "prompt_saudabilidade": st.session_state.prompt_saudabilidade,
                },
                f,
                indent=2,
                ensure_ascii=False,
            )
    except Exception as e:
        log(f"Aviso: não foi possível salvar os prompts em disco ({e}).")


if not st.session_state.prompts_loaded:
    carregar_prompts()
    st.session_state.prompts_loaded = True


# ----------------------------------------------------------------------
# LOGIN (Google, restrito ao domínio) + CONTROLE DE ACESSO
# ----------------------------------------------------------------------
def auth_configurada() -> bool:
    try:
        return "auth" in st.secrets
    except Exception:
        return False


with st.sidebar:
    st.markdown("## 👤 Usuário")
    if auth_configurada():
        logged_in = hasattr(st, "user") and getattr(st.user, "is_logged_in", False)
        if not logged_in:
            st.info("Faça login com sua conta Google da agência para continuar.")
            st.button("🔐 Entrar com Google", on_click=st.login, type="primary")
            st.stop()

        email = (st.user.email or "").strip()

        if not email.endswith("@" + ALLOWED_DOMAIN):
            st.error(f"Acesso restrito a e-mails @{ALLOWED_DOMAIN}.\nVocê entrou como: {email}")
            st.button("Sair", on_click=st.logout)
            st.stop()

        if usuario_e_admin(email):
            st.session_state.pode_configurar = True
        else:
            permissao = buscar_permissao(email)
            if permissao is None or not permissao.get("ativo", False):
                st.error(
                    "Seu acesso ainda não foi liberado.\n\n"
                    f"Peça para **{ADMIN_EMAIL}** liberar o seu e-mail em "
                    "Configurações → Acesso de usuários."
                )
                st.button("Sair", on_click=st.logout)
                st.stop()
            st.session_state.pode_configurar = bool(permissao.get("pode_configurar", False))

        st.session_state.usuario_atual = email
        st.success(f"Logado como\n**{email}**")
        if usuario_e_admin(email):
            st.caption("👑 Administrador")
        st.button("Sair", on_click=st.logout)
    else:
        st.warning(
            "Login com Google ainda não configurado (`[auth]` em secrets.toml). "
            "Usando modo demonstração — Configurações ficam liberadas."
        )
        st.session_state.usuario_atual = st.text_input(
            "Seu nome/usuário (demo)", value=st.session_state.usuario_atual
        )
        st.session_state.pode_configurar = True

with st.sidebar:
    st.divider()
    st.markdown("### Status das conexões")
    st.write("🕷️ Apify:", "🟢 configurado" if is_apify_configured() else "🟡 demonstração")
    st.write("🧠 Gemini:", "🟢 configurado" if is_gemini_configured() else "🟡 demonstração")
    st.write("🗄️ BigQuery:", "🟢 configurado" if is_bq_configured() else "🟡 exportação em Excel")
    st.caption(
        "Edite as chaves na aba ⚙️ Configurações."
        if st.session_state.pode_configurar
        else f"Só {ADMIN_EMAIL} pode editar as chaves de API."
    )

# ----------------------------------------------------------------------
# CABEÇALHO
# ----------------------------------------------------------------------
st.title("🤖 Painel de Análise de Sentimentos em Comentários")
st.caption("Escolha a fonte, preencha o contexto e rode tudo com um clique.")

tab_labels = ["🚀 Análise", "📸 Posts & Perfis (IG)", "💰 Gastos"]
if st.session_state.pode_configurar:
    tab_labels.append("⚙️ Configurações")
tab_labels.append("🪵 Log")

tab_objects = st.tabs(tab_labels)
tabs = dict(zip(tab_labels, tab_objects))

# ----------------------------------------------------------------------
# 🚀 ANÁLISE — tela única
# ----------------------------------------------------------------------
with tabs["🚀 Análise"]:
    _job_em_andamento = st.session_state.job is not None and st.session_state.job["fase"] not in ("concluido", "cancelado")

    if _job_em_andamento:
        st.info("⏳ Um processamento já está em andamento — os campos abaixo ficam bloqueados até terminar ou você cancelar.")

    st.session_state.fonte = st.radio("Fonte dos dados", FONTES, horizontal=True, disabled=_job_em_andamento)

    arquivo_pulsar = None
    if st.session_state.fonte == FONTES[0]:
        c1, c2 = st.columns([1, 2])
        with c1:
            st.session_state.origem = st.selectbox("Origem", ORIGENS_APIFY)
        with c2:
            st.session_state.links_input = st.text_area(
                "Links — um por linha (Instagram e/ou TikTok, detectado automaticamente)",
                value=st.session_state.links_input,
                height=120,
                placeholder=(
                    "https://www.instagram.com/reel/exemplo1/\n"
                    "https://www.tiktok.com/@usuario/video/1234567890"
                ),
            )

        st.markdown("##### Contexto (usado no prompt do Gemini e nas exportações)")
        c1, c2, c3, c4 = st.columns(4)
        st.session_state.campanha = c1.text_input("Campanha", value=st.session_state.campanha)
        st.session_state.marca = c2.text_input("Marca (brand)", value=st.session_state.marca)
        st.session_state.mother_brand = c3.text_input("Mother brand", value=st.session_state.mother_brand)
        st.session_state.nucleo = c4.text_input("Núcleo (ex: Beauty, Alimentos...)", value=st.session_state.nucleo)
        st.session_state.briefing = st.text_area(
            "Briefing do conteúdo (do que se trata — dá mais contexto pra IA)",
            value=st.session_state.briefing, height=80,
        )
        st.session_state.diretrizes_marca = st.text_area(
            "Diretrizes da marca para a sentimentalização (critérios específicos desse cliente)",
            value=st.session_state.diretrizes_marca, height=80,
        )

    else:
        st.session_state.origem = "Pulsar"
        arquivo_pulsar = st.file_uploader(
            "Planilha exportada do Pulsar (.xlsx, aba 'Contents')", type=["xlsx"]
        )
        st.session_state.reclassificar_pulsar_gemini = st.checkbox(
            "Reclassificar sentimento com Gemini em vez de usar o do Pulsar "
            "(mais lento e mais caro para planilhas grandes)",
            value=st.session_state.reclassificar_pulsar_gemini,
        )

        st.markdown("##### Contexto do estudo (usado no prompt do Gemini)")
        c1, c2 = st.columns([2, 1])
        st.session_state.estudo_nome = c1.text_input("Nome do estudo", value=st.session_state.estudo_nome)
        st.session_state.nucleo = c2.text_input("Núcleo (ex: Beauty, Alimentos...)", value=st.session_state.nucleo)
        st.session_state.estudo_objetivo = st.text_area(
            "Objetivo do estudo (o que vocês querem entender com essa análise)",
            value=st.session_state.estudo_objetivo, height=70,
        )
        st.session_state.tipo_estudo = st.radio(
            "Este estudo é sobre...", TIPOS_ESTUDO, horizontal=True,
            index=TIPOS_ESTUDO.index(st.session_state.tipo_estudo),
        )

        st.markdown("###### Marcas envolvidas (opcional)")
        st.caption(
            "Preencha se o estudo girar em torno de marca(s) — própria(s) e/ou concorrente(s), "
            "o que cada uma faz, e os produtos específicos (adicione quantos precisar). Deixe o "
            "nome em branco se o estudo não tiver marca no centro."
        )
        for i, m in enumerate(st.session_state.marcas_estudo):
            with st.container(border=True):
                c1, c2, c3 = st.columns([2, 1, 0.4])
                m["nome"] = c1.text_input("Nome da marca", value=m["nome"], key=f"marca_nome_{i}")
                m["tipo"] = c2.selectbox(
                    "Tipo", TIPOS_MARCA, index=TIPOS_MARCA.index(m["tipo"]), key=f"marca_tipo_{i}"
                )
                if c3.button("🗑️", key=f"marca_remover_{i}", help="Remover marca"):
                    if len(st.session_state.marcas_estudo) > 1:
                        st.session_state.marcas_estudo.pop(i)
                        st.rerun()
                    else:
                        st.warning("Deixe pelo menos uma marca.")
                m["o_que_faz"] = st.text_area(
                    "O que essa marca faz / segmento de atuação",
                    value=m["o_que_faz"], height=60, key=f"marca_faz_{i}",
                )

                st.caption("Produtos dessa marca")
                for pi, produto in enumerate(m["produtos"]):
                    pc1, pc2 = st.columns([5, 0.4])
                    m["produtos"][pi] = pc1.text_input(
                        f"Produto {pi+1}", value=produto, key=f"produto_{i}_{pi}", label_visibility="collapsed",
                        placeholder=f"Produto {pi+1}",
                    )
                    if pc2.button("🗑️", key=f"produto_remover_{i}_{pi}", help="Remover produto"):
                        if len(m["produtos"]) > 1:
                            m["produtos"].pop(pi)
                        else:
                            m["produtos"][0] = ""
                        st.rerun()
                if st.button("➕ Adicionar produto", key=f"add_produto_{i}"):
                    m["produtos"].append("")
                    st.rerun()

        if st.button("➕ Adicionar marca"):
            st.session_state.marcas_estudo.append(nova_marca())
            st.rerun()

        st.markdown("###### Temas, comportamentos ou tendências em estudo (opcional)")
        st.caption(
            "Pra estudos que não giram em torno de marca — um ato, ação, produto genérico, "
            "comportamento, verbo, tendência, alimento, costume, cultura etc. Adicione quantos "
            "temas precisar."
        )
        for i, t in enumerate(st.session_state.temas_estudo):
            with st.container(border=True):
                c1, c2 = st.columns([2, 0.4])
                t["nome"] = c1.text_input(
                    "Tema / comportamento / tendência", value=t["nome"], key=f"tema_nome_{i}",
                    placeholder="Ex: veganismo, gírias da geração Z, romantizar a rotina...",
                )
                if c2.button("🗑️", key=f"tema_remover_{i}", help="Remover tema"):
                    if len(st.session_state.temas_estudo) > 1:
                        st.session_state.temas_estudo.pop(i)
                        st.rerun()
                    else:
                        st.session_state.temas_estudo[0] = novo_tema()
                        st.rerun()
                t["descricao"] = st.text_area(
                    "Descrição (contexto adicional sobre esse tema, opcional)",
                    value=t["descricao"], height=60, key=f"tema_desc_{i}",
                )
        if st.button("➕ Adicionar tema"):
            st.session_state.temas_estudo.append(novo_tema())
            st.rerun()

        st.session_state.diretrizes_extra = st.text_area(
            "Observações adicionais para a IA (opcional)",
            value=st.session_state.diretrizes_extra, height=70,
        )

    st.session_state.gerar_relatorio_auto = st.checkbox(
        "Gerar também a análise aprofundada (relatório dissertativo, lido em blocos e depois "
        "sintetizado — mais fiel aos comentários do que um resumo com amostra pequena)",
        value=st.session_state.gerar_relatorio_auto,
    )
    st.session_state.buscar_metricas_posts = st.checkbox(
        "📊 Buscar métricas dos posts do Instagram (curtidas, comentários, views) e calcular a "
        "'saudabilidade' de cada post com o Gemini",
        value=st.session_state.buscar_metricas_posts,
        help=(
            "As métricas usam o Actor de Posts/Perfis do Instagram (`" + ACTOR_INSTAGRAM_POSTS + "`) — "
            f"1 chamada extra, cobrada à parte a ${RATE_PER_1000['instagram_posts']:.2f} por mil itens. "
            "Só funciona para links do Instagram (o Instagram não expõe compartilhamentos via "
            "scraping). A saudabilidade roda para todos os posts (Instagram e TikTok), usando o "
            "sentimento + as métricas quando disponíveis."
        ),
    )

    if not is_apify_configured() and st.session_state.fonte == FONTES[0]:
        st.warning("Apify não configurado — vai gerar comentários de demonstração.")
    if not is_gemini_configured():
        st.warning("Gemini não configurado — sentimento/relatório serão simulados (modo demonstração).")

    job_ativo = st.session_state.job is not None and st.session_state.job["fase"] not in ("concluido", "cancelado")

    # ---------------- ETA prévio (antes de clicar em rodar) ----------------
    if not job_ativo:
        if st.session_state.fonte == FONTES[0]:
            links_previa = extrair_urls_redes(st.session_state.links_input)
            n_ig = sum(1 for l in links_previa if detectar_plataforma(l) == "instagram")
            n_tk = sum(1 for l in links_previa if detectar_plataforma(l) == "tiktok")
            n_estimado = n_ig * st.session_state.ig_limit + n_tk * st.session_state.tk_limit
            n_plataformas = (1 if n_ig else 0) + (1 if n_tk else 0)
        else:
            n_estimado = 2000 if arquivo_pulsar is not None else 0  # placeholder até ler o arquivo de verdade
            n_plataformas = 0

        if n_estimado or n_plataformas:
            precisa_classificar = (
                True if st.session_state.fonte == FONTES[0] else st.session_state.reclassificar_pulsar_gemini
            )
            eta = estimar_segundos(
                n_estimado, n_plataformas, precisa_classificar, st.session_state.gerar_relatorio_auto,
                st.session_state.buscar_metricas_posts,
            )
            legenda_eta = f"⏱️ Tempo estimado: ~{formatar_duracao(eta)} (estimativa aproximada, atualizada durante a execução)."
            if st.session_state.fonte == FONTES[0] and st.session_state.buscar_metricas_posts and n_ig:
                custo_metricas_estimado = (n_ig / 1000) * RATE_PER_1000["instagram_posts"]
                legenda_eta += f" Custo extra estimado das métricas do Instagram: ~${custo_metricas_estimado:.2f}."
            st.caption(legenda_eta)

        rodar = st.button("🚀 Rodar análise completa", type="primary", use_container_width=True)

        if rodar:
            if st.session_state.fonte == FONTES[1]:
                mapear_contexto_pulsar()
                if arquivo_pulsar is None:
                    st.warning("Envie a planilha do Pulsar antes de rodar.")
                else:
                    st.session_state.job = criar_job_pulsar(
                        arquivo_pulsar, st.session_state.reclassificar_pulsar_gemini, st.session_state.gerar_relatorio_auto
                    )
                    st.session_state.resultados_df = None
                    st.session_state.relatorio_texto = ""
                    st.rerun()
            else:
                links = extrair_urls_redes(st.session_state.links_input)
                if not links:
                    st.warning("Cole ao menos um link antes de rodar.")
                else:
                    links_por_plataforma = {"instagram": [], "tiktok": [], "desconhecido": []}
                    for l in links:
                        links_por_plataforma[detectar_plataforma(l)].append(l)
                    st.session_state.job = criar_job_apify(links_por_plataforma, st.session_state.gerar_relatorio_auto)
                    st.session_state.resultados_df = None
                    st.session_state.relatorio_texto = ""
                    st.rerun()

    # ---------------- JOB EM ANDAMENTO: progresso + ETA + cancelar ----------------
    if job_ativo:
        job = st.session_state.job
        st.divider()

        rotulos_fase = {
            "coleta_apify": "Coletando comentários (Apify)",
            "ler_pulsar": "Lendo a planilha do Pulsar",
            "preparar_classificacao": "Preparando classificação",
            "classificacao": "Classificando sentimento (Gemini, em lotes)",
            "preparar_metricas_ig": "Preparando busca de métricas dos posts (Instagram)",
            "metricas_ig": "Buscando métricas dos posts no Instagram (curtidas, comentários, views)",
            "preparar_saudabilidade": "Preparando cálculo de saudabilidade",
            "saudabilidade": "Calculando a saudabilidade de cada post (Gemini)",
            "preparar_analise": "Preparando análise aprofundada",
            "analise_blocos": "Analisando comentários em blocos (Gemini)",
            "sintese": "Sintetizando o relatório final",
        }
        st.info(f"⏳ {rotulos_fase.get(job['fase'], job['fase'])}...")

        fracao = min(job["passos_concluidos"] / max(job["total_passos"], 1), 0.99)
        st.progress(fracao, text=f"Passo {job['passos_concluidos']} de ~{job['total_passos']}")

        if job["tempos_passo"]:
            media = sum(job["tempos_passo"]) / len(job["tempos_passo"])
            restantes = max(job["total_passos"] - job["passos_concluidos"], 0)
            st.caption(f"⏱️ Tempo estimado restante: ~{formatar_duracao(media * restantes)}")

        if job.get("erro_msg"):
            st.warning(job["erro_msg"])

        cancelar = st.button("🛑 Cancelar processamento")
        if cancelar:
            job["fase"] = "cancelado"
            log("Processamento cancelado pelo usuário.")
            st.session_state.job = job
            st.rerun()
        else:
            t0 = time.time()
            processar_passo_job(job)
            job["tempos_passo"].append(time.time() - t0)
            job["passos_concluidos"] += 1
            st.session_state.job = job
            time.sleep(0.05)
            st.rerun()

    # ---------------- JOB CONCLUÍDO: monta resultados_df/relatorio a partir do job ----------------
    if st.session_state.job is not None and st.session_state.job["fase"] == "concluido":
        job = st.session_state.job
        linhas = job["linhas_prontas"]
        if linhas:
            resultados = pd.DataFrame(linhas)
            resultados["origem"] = st.session_state.origem
            resultados["campanha"] = st.session_state.campanha
            resultados["marca"] = st.session_state.marca
            resultados["mother_brand"] = st.session_state.mother_brand
            resultados["nucleo"] = st.session_state.nucleo

            metricas = job.get("metricas_posts_ig") or {}
            if metricas:
                campos_extra = [
                    "likes_post", "comentarios_post", "views_post", "plays_post",
                    "compartilhamentos_post", "tipo_midia", "is_reel",
                    "duracao_video_seg", "data_publicacao_post",
                ]
                for campo in campos_extra:
                    resultados[campo] = resultados["post_link"].map(
                        lambda l: metricas.get(l, {}).get(campo)
                    )

            saudabilidade = job.get("saudabilidade_por_post") or {}
            if saudabilidade:
                for campo in ("saudabilidade_score", "saudabilidade_classificacao", "saudabilidade_resumo"):
                    resultados[campo] = resultados["post_link"].map(
                        lambda l: saudabilidade.get(l, {}).get(campo)
                    )

            st.session_state.resultados_df = resultados
            st.session_state.relatorio_texto = job["relatorio_final"]
            msg = (
                f"Pronto! {len(resultados)} comentários processados em "
                f"{formatar_duracao(time.time() - job['inicio'])}."
            )
            if job["custo_total"]:
                msg += f" Custo total (coleta + métricas): **${job['custo_total']:.2f}** (registrado em 💰 Gastos)."
            st.success(msg)
        else:
            st.warning("Nenhum comentário processado.")
        st.session_state.job = None

    if st.session_state.job is not None and st.session_state.job["fase"] == "cancelado":
        st.warning("Processamento cancelado. Nenhum resultado foi salvo desta execução.")
        st.session_state.job = None

    # ---------------- RESULTADOS (aparecem assim que existirem) ----------------
    resultados = st.session_state.resultados_df
    if resultados is not None and not resultados.empty:
        st.divider()
        st.subheader("Resultado")

        total = len(resultados)
        pos = int(resultados["sentimento"].eq("Positivo").sum())
        neg = int(resultados["sentimento"].eq("Negativo").sum())
        neu = total - pos - neg
        c1, c2, c3 = st.columns(3)
        c1.metric("😊 Positivos", pos, f"{pos/total:.0%}")
        c2.metric("😐 Neutros", neu, f"{neu/total:.0%}")
        c3.metric("☹️ Negativos", neg, f"{neg/total:.0%}")

        contagem = resultados["sentimento"].value_counts().reset_index()
        contagem.columns = ["sentimento", "quantidade"]
        c1, c2 = st.columns(2)
        with c1:
            st.plotly_chart(
                px.pie(contagem, names="sentimento", values="quantidade", color="sentimento",
                       color_discrete_map=SENTIMENT_COLORS, title="Distribuição de sentimentos"),
                use_container_width=True,
            )
        with c2:
            por_plataforma = resultados.groupby(["plataforma", "sentimento"]).size().reset_index(name="quantidade")
            fig = px.bar(por_plataforma, x="plataforma", y="quantidade", color="sentimento",
                         color_discrete_map=SENTIMENT_COLORS, title="Sentimento por plataforma", barmode="stack")
            st.plotly_chart(fig, use_container_width=True)

        tem_alvo = "alvo" in resultados.columns
        if tem_alvo:
            st.markdown("##### 🎯 Análise por alvo (apenas Apify)")
            st.caption(
                "Sobre o que os comentários falam — conteúdo, marca, produto/serviço ou preço. "
                "Os percentuais consideram só Positivo + Negativo por alvo (Neutros não entram na contagem)."
            )
            avaliaveis = resultados[resultados["sentimento"].isin(["Positivo", "Negativo"])]
            linhas_alvo = []
            for alvo_nome, grupo in avaliaveis.groupby("alvo"):
                p = int(grupo["sentimento"].eq("Positivo").sum())
                n = int(grupo["sentimento"].eq("Negativo").sum())
                base = p + n
                linhas_alvo.append({
                    "alvo": alvo_nome, "positivos": p, "negativos": n,
                    "% positivo": f"{p/base:.0%}" if base else "-",
                    "% negativo": f"{n/base:.0%}" if base else "-",
                    "neutros (fora da contagem)": int((resultados["alvo"] == alvo_nome).sum() - base),
                })
            st.dataframe(pd.DataFrame(linhas_alvo), use_container_width=True)

            fig_alvo = px.bar(
                resultados.groupby(["alvo", "sentimento"]).size().reset_index(name="quantidade"),
                x="alvo", y="quantidade", color="sentimento", color_discrete_map=SENTIMENT_COLORS,
                title="Sentimento por alvo", barmode="stack",
            )
            st.plotly_chart(fig_alvo, use_container_width=True)

        if "saudabilidade_score" in resultados.columns or "likes_post" in resultados.columns:
            st.markdown("##### 📊 Resumo por post (métricas + saudabilidade)")
            resumo_post = montar_resumo_por_post(resultados)
            if "saudabilidade_score" in resumo_post.columns and resumo_post["saudabilidade_score"].notna().any():
                media_saude = resumo_post["saudabilidade_score"].astype(float).mean()
                cc1, cc2 = st.columns(2)
                cc1.metric("Saudabilidade média (0-100)", f"{media_saude:.0f}")
                cc2.metric("Posts analisados", len(resumo_post))
            st.dataframe(resumo_post, use_container_width=True)
            st.caption(
                "💡 'saudabilidade_score' e 'classificação' são geradas pelo Gemini a partir da "
                "distribuição de sentimento + métricas do post. 'compartilhamentos_post' fica vazio "
                "porque nenhum Actor atual retorna esse dado do Instagram/TikTok."
            )

        st.markdown("##### Consultar comentários")
        if tem_alvo:
            c1, c2 = st.columns(2)
            filtro_sent = c1.multiselect(
                "Mostrar sentimentos", ["Positivo", "Neutro", "Negativo"],
                default=["Positivo", "Neutro", "Negativo"],
            )
            alvos_disponiveis = sorted(resultados["alvo"].dropna().unique().tolist())
            filtro_alvo = c2.multiselect("Mostrar alvos", alvos_disponiveis, default=alvos_disponiveis)
            mascara = resultados["sentimento"].isin(filtro_sent) & resultados["alvo"].isin(filtro_alvo)
        else:
            filtro_sent = st.multiselect(
                "Mostrar sentimentos", ["Positivo", "Neutro", "Negativo"],
                default=["Positivo", "Neutro", "Negativo"],
            )
            mascara = resultados["sentimento"].isin(filtro_sent)
        st.dataframe(resultados[mascara], use_container_width=True)

        st.markdown("##### 📋 Exemplos de comentários Neutros (para levar a clientes)")
        neutros = resultados[resultados["sentimento"] == "Neutro"]
        if neutros.empty:
            st.caption("Nenhum comentário classificado como Neutro nesta rodada.")
        else:
            colunas_neutros = ["post_link", "autor", "comentario", "justificativa"]
            if tem_alvo:
                colunas_neutros.insert(3, "alvo")
            st.dataframe(neutros[colunas_neutros], use_container_width=True)
            st.download_button(
                "⬇️ Baixar exemplos neutros (CSV)",
                data=neutros.to_csv(index=False).encode("utf-8"),
                file_name="exemplos_neutros.csv", mime="text/csv",
            )

        if st.session_state.relatorio_texto:
            st.markdown("##### 📄 Análise aprofundada")
            st.markdown(st.session_state.relatorio_texto)
            st.download_button(
                "⬇️ Baixar relatório (Markdown)",
                data=st.session_state.relatorio_texto.encode("utf-8"),
                file_name="analise_aprofundada.md", mime="text/markdown",
            )

        st.markdown("##### 💾 Salvar / exportar (post_comments)")
        st.caption(
            "Exportação completa: comentário + sentimento + justificativa + saudabilidade do "
            "post (Gemini) + métricas (curtidas, comentários do post, views, plays e "
            "compartilhamentos — este último fica vazio, pois nenhum Actor atual retorna esse "
            "dado) + origem/campanha/marca/núcleo."
        )
        resumo_post_export = montar_resumo_por_post(resultados)
        c1, c2 = st.columns(2)
        with c1:
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                resultados.to_excel(writer, sheet_name="post_comments", index=False)
                resumo_post_export.to_excel(writer, sheet_name="resumo_por_post", index=False)
            st.download_button(
                "⬇️ Baixar Excel completo (comentários + resumo por post)",
                data=buffer.getvalue(), file_name="post_comments_completo.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
            )
            st.caption(
                "Aba 'post_comments' = 1 linha por comentário. Aba 'resumo_por_post' = 1 linha "
                "por post, já agregada (sentimento + métricas + saudabilidade)."
            )
        with c2:
            buffer_nucleo = io.BytesIO()
            with pd.ExcelWriter(buffer_nucleo, engine="openpyxl") as writer:
                grupos = resultados.groupby(resultados["nucleo"].replace("", "sem_nucleo").fillna("sem_nucleo"))
                for nome, grupo in grupos:
                    aba = str(nome)[:31] or "sem_nucleo"
                    grupo.to_excel(writer, sheet_name=aba, index=False)
            st.download_button(
                "⬇️ Baixar Excel por núcleo",
                data=buffer_nucleo.getvalue(), file_name="post_comments_por_nucleo.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

        if not is_bq_configured():
            st.caption(
                "🟡 BigQuery ainda não configurado — por enquanto o fluxo oficial é exportar em "
                "Excel (acima). Quando estiver pronto, o botão abaixo passa a gravar direto na tabela "
                f"`{st.session_state.bq_table_post_comments}`."
            )
        if st.button("💾 Salvar também no BigQuery (post_comments)", disabled=not is_bq_configured()):
            with st.spinner("Salvando no BigQuery..."):
                try:
                    table_id = salvar_df_bigquery(resultados, st.session_state.bq_table_post_comments)
                    log(f"{len(resultados)} linhas salvas em {table_id}.")
                    st.success(f"Salvo em `{table_id}`.")
                except Exception as e:
                    st.error(f"Erro ao salvar no BigQuery: {e}")

# ----------------------------------------------------------------------
# 📸 POSTS & PERFIS (INSTAGRAM) — consulta avulsa, fora do fluxo de campanha
# ----------------------------------------------------------------------
with tabs["📸 Posts & Perfis (IG)"]:
    st.subheader("Números de posts e reels do Instagram")
    st.caption(
        "Cole o link do post/reel (pode colar com título junto, tipo copiar de uma busca — "
        "o app extrai a URL sozinho) e receba curtidas, comentários e views. Dá pra colar "
        "vários links de uma vez, um por linha. Também dá pra puxar links de **perfil** "
        "(ex.: `instagram.com/usuario/`) pra trazer os últimos posts/reels dele, ou só os "
        "**dados do perfil** (seguidores, bio, verificado...). ⚠️ Compartilhamentos não "
        "aparecem porque o Instagram não expõe esse número via scraping — nenhuma "
        "ferramenta de coleta automática consegue puxar isso hoje."
    )

    c1, c2 = st.columns([2, 1])
    with c1:
        st.session_state.ig_posts_links_input = st.text_area(
            "Links do Instagram — um por linha (funciona colar com título junto)",
            value=st.session_state.ig_posts_links_input,
            height=130,
            placeholder=(
                "[Mariana Menezes (@marimenezees_) • Reel do Instagram](https://www.instagram.com/p/DaZJ741tEWf/)\n"
                "https://www.instagram.com/p/DZxvMgyH8yR/\n"
                "https://www.instagram.com/humansofny/"
            ),
            key="ig_posts_links_area",
        )

    links_preview = extrair_urls_instagram(st.session_state.ig_posts_links_input)
    tem_link_perfil = any(not eh_link_post_especifico(u) for u in links_preview)

    with c2:
        tipo_busca_label = st.selectbox(
            "O que buscar",
            ["Posts + Reels", "Dados do perfil (seguidores, bio...)"],
            key="ig_posts_tipo_busca",
        )
        st.session_state.ig_posts_results_type = (
            "posts" if tipo_busca_label.startswith("Posts") else "details"
        )
        if st.session_state.ig_posts_results_type == "posts":
            if tem_link_perfil:
                st.session_state.ig_posts_limit = st.number_input(
                    "Máx. de posts/reels por perfil colado",
                    min_value=1, max_value=200, value=st.session_state.ig_posts_limit,
                    help=(
                        "Só vale pra link(s) de perfil (ex.: instagram.com/usuario/). Link de "
                        "post/reel específico sempre traz só aquele item, esse número não afeta."
                    ),
                )
            elif links_preview:
                st.caption("✅ Só post(s)/reel(s) específico(s) — traz exatamente os itens colados, sem precisar de limite.")

    limite_efetivo = st.session_state.ig_posts_limit if tem_link_perfil else 1

    if links_preview and st.session_state.ig_posts_results_type == "posts":
        custo_estimado_posts = (len(links_preview) * limite_efetivo / 1000) * RATE_PER_1000["instagram_posts"]
        st.caption(
            f"💸 Custo máx. estimado desta busca: **${custo_estimado_posts:.2f}** "
            f"(${RATE_PER_1000['instagram_posts']:.2f} por mil itens do Actor de posts/perfis)."
        )

    if not is_apify_configured():
        st.warning("Apify não configurado — vai mostrar dados de demonstração.")

    if st.button("🚀 Buscar no Instagram", type="primary", key="btn_buscar_posts_ig"):
        links_posts = extrair_urls_instagram(st.session_state.ig_posts_links_input)
        if not links_posts:
            st.warning("Cole ao menos um link.")
        else:
            limite_busca = st.session_state.ig_posts_limit if any(not eh_link_post_especifico(u) for u in links_posts) else 1
            with st.spinner("Buscando no Instagram..."):
                items = []
                if is_apify_configured():
                    try:
                        items = coletar_posts_ig(
                            links_posts,
                            results_type=st.session_state.ig_posts_results_type,
                            limit=limite_busca,
                        )
                        if items:
                            custo_real = registrar_gasto("instagram_posts", len(items))
                            log(f"Apify (posts/perfis IG) retornou {len(items)} registro(s) — custo: ${custo_real:.4f}.")
                        else:
                            log("Apify (posts/perfis IG) não retornou registros.")
                    except Exception as e:
                        st.error(f"Erro ao chamar Apify: {e}")
                else:
                    time.sleep(0.5)
                    items = (
                        DEMO_POSTS_SAMPLE
                        if st.session_state.ig_posts_results_type == "posts"
                        else DEMO_PERFIL_SAMPLE
                    )
                    log("[DEMO] Dados fictícios de posts/perfil do Instagram gerados.")

                if not items:
                    st.warning("Nada retornado para esses links.")
                else:
                    if st.session_state.ig_posts_results_type == "posts":
                        linhas = [normalizar_item_post(i) for i in items if "shortCode" in i]
                    else:
                        linhas = [normalizar_item_perfil(i) for i in items]
                    st.session_state.ig_posts_df = pd.DataFrame(linhas)
                    st.success(f"{len(linhas)} registro(s) encontrado(s).")

    df_posts = st.session_state.ig_posts_df
    if df_posts is not None and not df_posts.empty:
        if "likes_post" in df_posts.columns:
            if len(df_posts) > 1:
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Posts/Reels", len(df_posts))
                c2.metric("Curtidas (total)", int(df_posts["likes_post"].fillna(0).sum()))
                c3.metric("Comentários (total)", int(df_posts["comentarios_post"].fillna(0).sum()))
                c4.metric("Views (total)", int(df_posts["views_post"].fillna(0).sum()))
                st.divider()

            st.markdown("##### 🔢 Números por post")
            for _, linha in df_posts.iterrows():
                with st.container(border=True):
                    titulo = f"@{linha.get('owner_username','')}" if linha.get("owner_username") else linha.get("link", "")
                    st.markdown(f"**{titulo}** — {linha.get('link','')}")
                    cc1, cc2, cc3, cc4 = st.columns(4)
                    cc1.metric("❤️ Curtidas", f"{int(linha['likes_post']):,}".replace(",", ".") if pd.notna(linha.get("likes_post")) else "—")
                    cc2.metric("💬 Comentários", f"{int(linha['comentarios_post']):,}".replace(",", ".") if pd.notna(linha.get("comentarios_post")) else "—")
                    views_val = linha.get("views_post") if pd.notna(linha.get("views_post")) else linha.get("plays_post")
                    cc3.metric("👁️ Views", f"{int(views_val):,}".replace(",", ".") if pd.notna(views_val) else "—")
                    cc4.metric("🔁 Compartilhamentos", "Indisponível", help="O Instagram não expõe compartilhamentos via scraping — nenhum Actor atual retorna esse número.")

            with st.expander("📋 Ver tabela completa (todos os campos)"):
                fig_metricas = px.bar(
                    df_posts.sort_values("likes_post", ascending=False).head(20),
                    x="shortcode", y=["likes_post", "comentarios_post"],
                    title="Curtidas x Comentários por post/reel (top 20)", barmode="group",
                )
                fig_metricas.update_xaxes(tickangle=45)
                st.plotly_chart(fig_metricas, use_container_width=True)
                st.dataframe(df_posts, use_container_width=True)
        elif "seguidores" in df_posts.columns:
            c1, c2, c3 = st.columns(3)
            c1.metric("Perfis coletados", len(df_posts))
            c2.metric("Seguidores (soma)", int(df_posts["seguidores"].fillna(0).sum()))
            c3.metric("Posts publicados (soma)", int(df_posts["qtd_posts"].fillna(0).sum()))
            st.dataframe(df_posts, use_container_width=True)

        c1, c2 = st.columns(2)
        with c1:
            st.download_button(
                "⬇️ Baixar CSV",
                data=df_posts.to_csv(index=False).encode("utf-8"),
                file_name="posts_perfis_instagram.csv", mime="text/csv",
            )
        with c2:
            buffer_posts = io.BytesIO()
            with pd.ExcelWriter(buffer_posts, engine="openpyxl") as writer:
                df_posts.to_excel(writer, sheet_name="posts_perfis_ig", index=False)
            st.download_button(
                "⬇️ Baixar Excel",
                data=buffer_posts.getvalue(), file_name="posts_perfis_instagram.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

        if not is_bq_configured():
            st.caption("🟡 BigQuery não configurado — use os downloads acima por enquanto.")
        elif st.button("💾 Salvar no BigQuery", key="btn_salvar_posts_bq"):
            try:
                table_id = salvar_df_bigquery(df_posts, st.session_state.bq_table_posts_ig)
                st.success(f"Salvo em `{table_id}`.")
                log(f"{len(df_posts)} linhas de posts/perfis IG salvas em {table_id}.")
            except Exception as e:
                st.error(f"Erro ao salvar no BigQuery: {e}")

# ----------------------------------------------------------------------
# GASTOS
# ----------------------------------------------------------------------
with tabs["💰 Gastos"]:
    st.subheader("Histórico de gastos com extração de comentários (Apify)")
    st.caption(
        f"Instagram (comentários): ${RATE_PER_1000['instagram']:.2f} por mil · "
        f"TikTok (comentários): ${RATE_PER_1000['tiktok']:.2f} por mil · "
        f"Instagram (posts/perfis): ${RATE_PER_1000['instagram_posts']:.2f} por mil itens. "
        "Planilhas do Pulsar não geram custo de coleta (só de métricas/saudabilidade, se ativado)."
    )
    if not st.session_state.gastos:
        st.caption("Nenhum gasto registrado ainda.")
    else:
        df_gastos = pd.DataFrame(st.session_state.gastos)
        total_geral = df_gastos["custo_usd"].sum()
        c1, c2, c3 = st.columns(3)
        c1.metric("Total gasto", f"${total_geral:.2f}")
        c2.metric("Comentários coletados", int(df_gastos["comentarios_coletados"].sum()))
        c3.metric("Execuções", len(df_gastos))

        c1, c2 = st.columns(2)
        with c1:
            por_usuario = df_gastos.groupby("usuario")["custo_usd"].sum().reset_index().sort_values("custo_usd", ascending=False)
            st.plotly_chart(px.bar(por_usuario, x="usuario", y="custo_usd", title="Gasto por usuário (USD)"), use_container_width=True)
        with c2:
            por_plataforma = df_gastos.groupby("plataforma")["custo_usd"].sum().reset_index()
            st.plotly_chart(px.pie(por_plataforma, names="plataforma", values="custo_usd", title="Gasto por plataforma"), use_container_width=True)

        st.dataframe(df_gastos.sort_values("data_hora", ascending=False), use_container_width=True)
        st.download_button(
            "⬇️ Baixar histórico de gastos (CSV)",
            data=df_gastos.to_csv(index=False).encode("utf-8"),
            file_name="gastos.csv", mime="text/csv",
        )
    st.caption("O histórico acima é desta sessão. Configure o BigQuery para manter salvo entre sessões.")

# ----------------------------------------------------------------------
# CONFIGURAÇÕES (somente admin / usuários com permissão)
# ----------------------------------------------------------------------
if "⚙️ Configurações" in tabs:
    with tabs["⚙️ Configurações"]:
        st.subheader("Configurações — restrito")
        st.caption(f"Visível só para **{ADMIN_EMAIL}** e para quem ele liberar abaixo.")

        subtab_apis, subtab_prompts, subtab_usuarios = st.tabs(
            ["🔑 Chaves de API", "🧠 Prompts (Gemini)", "👥 Acesso de usuários"]
        )

        with subtab_apis:
            st.markdown("#### Apify")
            if st.session_state.get("apify_from_secrets"):
                st.caption("🔐 Carregado automaticamente de `secrets.toml`.")
            st.session_state.apify_token = st.text_input(
                "Apify API Token", value=st.session_state.apify_token, type="password"
            )
            st.caption(
                f"Actors fixos: Instagram Comments Scraper (`{ACTOR_INSTAGRAM}`), "
                f"TikTok Comments Scraper (`{ACTOR_TIKTOK}`) e Instagram Posts/Perfis "
                f"(`{ACTOR_INSTAGRAM_POSTS}`)."
            )
            c1, c2 = st.columns(2)
            st.session_state.ig_limit = c1.number_input(
                "Limite de comentários por post — Instagram", min_value=1, max_value=2000, value=st.session_state.ig_limit
            )
            st.session_state.tk_limit = c2.number_input(
                "Comentários por post — TikTok", min_value=1, max_value=2000, value=st.session_state.tk_limit
            )
            if st.session_state.apify_token and st.button("🗑️ Revogar token do Apify"):
                st.session_state.apify_token = ""
                log("Token do Apify revogado pela interface.")
                st.rerun()

            st.divider()
            st.markdown("#### Google Gemini")
            if st.session_state.get("gemini_from_secrets"):
                st.caption("🔐 Carregada automaticamente de `secrets.toml`.")
            st.session_state.gemini_key = st.text_input(
                "Gemini API Key", value=st.session_state.gemini_key, type="password"
            )
            st.session_state.gemini_model = st.text_input("Modelo", value=st.session_state.gemini_model)
            if st.session_state.gemini_key and st.button("🗑️ Revogar chave do Gemini"):
                st.session_state.gemini_key = ""
                log("Chave do Gemini revogada pela interface.")
                st.rerun()

            st.divider()
            st.markdown("#### BigQuery")
            if st.session_state.get("bq_from_secrets"):
                st.caption("🔐 Credencial carregada automaticamente de `secrets.toml`.")
            st.session_state.bq_project = st.text_input("Project ID", value=st.session_state.bq_project)
            st.session_state.bq_dataset = st.text_input("Dataset", value=st.session_state.bq_dataset)
            st.session_state.bq_table_post_comments = st.text_input(
                "Tabela unificada (comentários + sentimento)", value=st.session_state.bq_table_post_comments
            )
            st.session_state.bq_table_gastos = st.text_input("Tabela de gastos", value=st.session_state.bq_table_gastos)
            st.session_state.bq_table_usuarios = st.text_input(
                "Tabela de usuários permitidos", value=st.session_state.bq_table_usuarios
            )
            st.session_state.bq_table_posts_ig = st.text_input(
                "Tabela de posts/perfis do Instagram", value=st.session_state.bq_table_posts_ig
            )
            cred_file = st.file_uploader("Service Account (JSON)", type=["json"], key="bq_upload")
            if cred_file is not None:
                st.session_state.bq_credentials_json = json.load(cred_file)
                st.success("Credencial carregada nesta sessão.")
            if st.session_state.bq_credentials_json and st.button("🗑️ Revogar credencial do BigQuery"):
                st.session_state.bq_credentials_json = None
                log("Credencial do BigQuery revogada pela interface.")
                st.rerun()

            st.caption(
                "Chaves preenchidas aqui ficam só nesta sessão do navegador. Para "
                "deixar fixo em produção, use o `secrets.toml` (veja o README.md)."
            )

        with subtab_prompts:
            st.caption(
                "A análise completa roda em 3 etapas de prompt: 1) classifica o sentimento em "
                "lotes de comentários, 2) lê cada bloco de comentários e extrai um resumo fiel, "
                "3) sintetiza todos os resumos num relatório final único."
            )
            st.markdown("#### 1a. Prompt de sentimentalização — Pulsar (reclassificação)")
            st.caption(
                f"Usado só quando a fonte é Pulsar e a caixa 'Reclassificar com Gemini' está marcada. "
                f"Classifica um lote de até {TAMANHO_LOTE_CLASSIFICACAO} comentários por chamada. "
                "Placeholders: `{{BRIEFING}}`, `{{DIRETRIZES}}`, `{{COMENTARIOS_NUMERADOS}}`."
            )
            st.session_state.prompt_sentimento = st.text_area(
                "Prompt de sentimentalização (Pulsar)", value=st.session_state.prompt_sentimento, height=200
            )
            c1, c2 = st.columns(2)
            if c1.button("💾 Salvar prompt de sentimentalização (Pulsar)"):
                salvar_prompts()
                log("Prompt de sentimentalização (Pulsar) atualizado.")
                st.success("Salvo.")
            if c2.button("↩️ Restaurar prompt padrão (Pulsar)"):
                st.session_state.prompt_sentimento = DEFAULT_PROMPT_SENTIMENTO
                salvar_prompts()
                st.rerun()

            st.divider()
            st.markdown("#### 1b. Prompt de sentimentalização — Apify (com alvo, emoção e pertinência)")
            st.caption(
                "Usado só para comentários coletados via Apify (Campanha BR / SIC). Além do "
                "sentimento, classifica o **alvo** do comentário (conteúdo/marca/produto-serviço/"
                "preço), a **emoção** predominante e a **pertinência** com a campanha — dá pra "
                "filtrar as amostras por essas categorias depois. Placeholders: `{{CAMPANHA}}`, "
                "`{{MARCA}}`, `{{BRIEFING}}`, `{{DIRETRIZES}}`, `{{COMENTARIOS_NUMERADOS}}`."
            )
            st.session_state.prompt_sentimento_apify = st.text_area(
                "Prompt de sentimentalização (Apify)", value=st.session_state.prompt_sentimento_apify, height=260
            )
            c1, c2 = st.columns(2)
            if c1.button("💾 Salvar prompt de sentimentalização (Apify)"):
                salvar_prompts()
                log("Prompt de sentimentalização (Apify) atualizado.")
                st.success("Salvo.")
            if c2.button("↩️ Restaurar prompt padrão (Apify)"):
                st.session_state.prompt_sentimento_apify = DEFAULT_PROMPT_SENTIMENTO_APIFY
                salvar_prompts()
                st.rerun()

            st.divider()
            st.markdown("#### 2. Prompt de leitura por bloco (análise aprofundada)")
            st.caption(
                f"Lê um bloco de até {TAMANHO_CHUNK_ANALISE} comentários e extrai um resumo fiel — "
                "essa é a etapa que garante que o relatório final não fuja do que está realmente "
                "escrito nos comentários. Placeholders: `{{CONTEXTO}}`, `{{TOTAL_BLOCO}}`, "
                "`{{COMENTARIOS_BLOCO}}`."
            )
            st.session_state.prompt_analise_bloco = st.text_area(
                "Prompt de análise por bloco", value=st.session_state.prompt_analise_bloco, height=200
            )
            c1, c2 = st.columns(2)
            if c1.button("💾 Salvar prompt de análise por bloco"):
                salvar_prompts()
                log("Prompt de análise por bloco atualizado.")
                st.success("Salvo.")
            if c2.button("↩️ Restaurar prompt padrão de análise por bloco"):
                st.session_state.prompt_analise_bloco = DEFAULT_PROMPT_ANALISE_BLOCO
                salvar_prompts()
                st.rerun()

            st.divider()
            st.markdown("#### 3. Prompt de síntese (relatório final)")
            st.caption(
                "Junta todos os resumos de blocos + o resumo quantitativo num relatório final único. "
                "Placeholders: `{{CAMPANHA}}`, `{{MARCA}}`, `{{MOTHER_BRAND}}`, `{{NUCLEO}}`, "
                "`{{BRIEFING}}`, `{{DIRETRIZES}}`, `{{RESUMO_QUANTITATIVO}}`, `{{RESUMOS_DOS_BLOCOS}}`, "
                "`{{AMOSTRA_COMENTARIOS}}`."
            )
            st.session_state.prompt_relatorio = st.text_area(
                "Prompt do relatório aprofundado (síntese)", value=st.session_state.prompt_relatorio, height=260
            )
            c1, c2 = st.columns(2)
            if c1.button("💾 Salvar prompt do relatório"):
                salvar_prompts()
                log("Prompt do relatório aprofundado atualizado.")
                st.success("Salvo.")
            if c2.button("↩️ Restaurar prompt padrão do relatório"):
                st.session_state.prompt_relatorio = DEFAULT_PROMPT_RELATORIO
                salvar_prompts()
                st.rerun()

            st.divider()
            st.markdown("#### 4. Prompt de saudabilidade dos posts")
            st.caption(
                "Roda em lote (até "
                f"{TAMANHO_LOTE_SAUDABILIDADE} posts por chamada), a partir da distribuição de "
                "sentimento já calculada + métricas do post (quando disponíveis). Placeholders: "
                "`{{MARCA}}`, `{{CAMPANHA}}`, `{{DIRETRIZES}}`, `{{POSTS_NUMERADOS}}`."
            )
            st.session_state.prompt_saudabilidade = st.text_area(
                "Prompt de saudabilidade", value=st.session_state.prompt_saudabilidade, height=220
            )
            c1, c2 = st.columns(2)
            if c1.button("💾 Salvar prompt de saudabilidade"):
                salvar_prompts()
                log("Prompt de saudabilidade atualizado.")
                st.success("Salvo.")
            if c2.button("↩️ Restaurar prompt padrão de saudabilidade"):
                st.session_state.prompt_saudabilidade = DEFAULT_PROMPT_SAUDABILIDADE
                salvar_prompts()
                st.rerun()

        with subtab_usuarios:
            st.markdown("#### Quem pode acessar o app")
            st.caption(
                f"Login sempre exige e-mail **@{ALLOWED_DOMAIN}**. **{ADMIN_EMAIL}** sempre "
                "tem acesso total e não aparece nesta lista."
            )
            usuarios = st.session_state.usuarios_permitidos
            if not usuarios:
                st.caption("Nenhum usuário adicionado ainda.")
            else:
                for i, u in enumerate(usuarios):
                    with st.container(border=True):
                        c1, c2, c3, c4 = st.columns([3, 1.3, 1.6, 0.8])
                        c1.markdown(f"**{u.get('email','')}**")
                        u["ativo"] = c2.checkbox("Acesso ao app", value=u.get("ativo", False), key=f"ativo_{i}")
                        u["pode_configurar"] = c3.checkbox(
                            "Pode configurar", value=u.get("pode_configurar", False), key=f"config_{i}"
                        )
                        if c4.button("🗑️", key=f"remover_{i}", help="Remover usuário"):
                            usuarios.pop(i)
                            salvar_usuarios(usuarios)
                            log(f"Usuário removido: {u.get('email','')}.")
                            st.rerun()
                if st.button("💾 Salvar alterações de acesso", type="primary"):
                    salvar_usuarios(usuarios)
                    log("Permissões de usuários atualizadas.")
                    st.success("Alterações salvas.")

            st.divider()
            st.markdown("#### Adicionar novo usuário")
            with st.form("novo_usuario_form", clear_on_submit=True):
                novo_email = st.text_input(f"E-mail (@{ALLOWED_DOMAIN})")
                c1, c2 = st.columns(2)
                novo_ativo = c1.checkbox("Conceder acesso ao app", value=True)
                novo_config = c2.checkbox("Conceder acesso às configurações", value=False)
                enviar = st.form_submit_button("➕ Adicionar / conceder acesso")

            if enviar:
                novo_email = novo_email.strip().lower()
                if not novo_email.endswith("@" + ALLOWED_DOMAIN):
                    st.error(f"O e-mail precisa terminar em @{ALLOWED_DOMAIN}.")
                elif usuario_e_admin(novo_email):
                    st.warning(f"{ADMIN_EMAIL} já é administrador por padrão.")
                elif buscar_permissao(novo_email) is not None:
                    st.warning("Esse usuário já está na lista — edite as permissões acima.")
                else:
                    usuarios.append(
                        {
                            "email": novo_email,
                            "ativo": novo_ativo,
                            "pode_configurar": novo_config,
                            "adicionado_por": st.session_state.usuario_atual,
                            "data": datetime.now().isoformat(timespec="seconds"),
                        }
                    )
                    salvar_usuarios(usuarios)
                    log(f"Usuário adicionado: {novo_email}.")
                    st.success(f"{novo_email} adicionado.")
                    st.rerun()

            if not is_bq_configured():
                st.caption(
                    "⚠️ Sem BigQuery configurado, essa lista é salva em arquivo local no "
                    "servidor — funciona bem local/self-hosted, mas pode resetar em "
                    "provedores de deploy com disco temporário."
                )

# ----------------------------------------------------------------------
# LOG
# ----------------------------------------------------------------------
with tabs["🪵 Log"]:
    st.subheader("Log de execução")
    if not st.session_state.log:
        st.caption("Nenhuma ação registrada ainda.")
    else:
        st.code("\n".join(st.session_state.log), language=None)

    if st.button("🔄 Limpar resultado atual (comentários, sentimento e relatório)"):
        for key in ("links_input", "resultados_df", "relatorio_texto", "log", "job"):
            st.session_state[key] = DEFAULTS[key]
        st.rerun()
