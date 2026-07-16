"""
Painel de Análise de Sentimentos em Comentários (SIC + Campanhas BR)
Pipeline: Links/contexto -> Apify (coleta) -> Gemini (sentimentalização automática)
          -> Sentimentalização (visão geral) -> Relatório aprofundado -> Salvar/Exportar
Inclui: gastos por extração/usuário, login Google restrito ao domínio, menu de
Configurações restrito ao admin (chaves de API, prompts do Gemini e acesso de usuários).
"""

import io
import json
import os
import random
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

RATE_PER_1000 = {"instagram": 1.90, "tiktok": 0.50}
SENTIMENT_COLORS = {"Positivo": "#22C55E", "Neutro": "#94A3B8", "Negativo": "#EF4444"}
ORIGENS = ["Campanha BR (Apify)", "SIC - Reels", "SIC - TikTok"]

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
USERS_FILE = os.path.join(DATA_DIR, "usuarios_permitidos.json")
PROMPTS_FILE = os.path.join(DATA_DIR, "prompts.json")

DEFAULT_PROMPT_SENTIMENTO = """Você é um analista de social listening de uma agência de marketing.
Classifique o sentimento do comentário abaixo em uma destas categorias: Positivo, Negativo ou Neutro.

Briefing do conteúdo: {{BRIEFING}}
Diretrizes da marca para esta análise: {{DIRETRIZES}}

Comentário: "{{COMENTARIO}}"

Responda SOMENTE em JSON, neste formato exato:
{"sentimento": "Positivo", "justificativa": "explicação curta em português"}"""

DEFAULT_PROMPT_RELATORIO = """Você é um analista sênior de social listening e antropologia digital, \
escrevendo um relatório para apresentar a clientes de uma agência de marketing.

Campanha/conteúdo: {{CAMPANHA}}
Marca: {{MARCA}} | Mother brand: {{MOTHER_BRAND}} | Núcleo: {{NUCLEO}}
Briefing do conteúdo: {{BRIEFING}}
Diretrizes da marca: {{DIRETRIZES}}

Resumo quantitativo dos comentários analisados:
{{RESUMO_QUANTITATIVO}}

Amostra de comentários por sentimento:
{{AMOSTRA_COMENTARIOS}}

Escreva uma análise aprofundada, técnica e dissertativa (não apenas bullets soltos) cobrindo,
nesta ordem:
1. Panorama geral do engajamento e do sentimento do público.
2. Insights sobre o comportamento e a percepção do público.
3. Oportunidades para a marca.
4. Riscos e pontos de atenção.
5. Uma leitura antropológica/cultural do que os comentários revelam sobre esse público.
6. Conclusão, amarrando os pontos acima em forma de storytelling.

Escreva em português, tom técnico mas acessível, como material de estudo entregável ao cliente."""

STAGES = [
    {"label": "Links + contexto", "color": "#FDE68A"},
    {"label": "Apify (coleta)", "color": "#FDE68A"},
    {"label": "Gemini (sentimentalização)", "color": "#FBCFA6"},
    {"label": "Sentimentalização", "color": "#BFDBFE"},
    {"label": "Relatório aprofundado", "color": "#BFDBFE"},
    {"label": "Salvar / Exportar", "color": "#BFDBFE"},
]

# ----------------------------------------------------------------------
# ESTADO DA SESSÃO
# ----------------------------------------------------------------------
DEFAULTS = {
    "links_input": "",
    "links_list": [],
    "origem": ORIGENS[0],
    "campanha": "",
    "marca": "",
    "mother_brand": "",
    "nucleo": "",
    "briefing": "",
    "diretrizes_marca": "",
    "comentarios_df": None,
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
    "bq_credentials_json": None,
    "current_stage": 0,
    "log": [],
    "gastos": [],
    "usuario_atual": "convidado",
    "ig_limit": 50,
    "tk_limit": 50,
    "pode_configurar": False,
    "usuarios_permitidos": None,
    "prompt_sentimento": DEFAULT_PROMPT_SENTIMENTO,
    "prompt_relatorio": DEFAULT_PROMPT_RELATORIO,
    "prompts_loaded": False,
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


def montar_prompt_sentimento(comentario: str) -> str:
    return (
        st.session_state.prompt_sentimento.replace(
            "{{BRIEFING}}", st.session_state.briefing or "Não informado."
        )
        .replace("{{DIRETRIZES}}", st.session_state.diretrizes_marca or "Nenhuma diretriz específica.")
        .replace("{{COMENTARIO}}", str(comentario))
    )


def classificar_demo(texto: str) -> str:
    negativos = ["não gostei", "péssima", "quebrado", "ruim"]
    positivos = ["adorei", "incrível", "excelente", "recomendo"]
    t = str(texto).lower()
    if any(p in t for p in negativos):
        return "Negativo"
    if any(p in t for p in positivos):
        return "Positivo"
    return "Neutro"


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
# GESTÃO DOS PROMPTS (editáveis pelo admin, persistidos em disco/BQ)
# ----------------------------------------------------------------------
def carregar_prompts():
    if os.path.exists(PROMPTS_FILE):
        try:
            with open(PROMPTS_FILE, "r", encoding="utf-8") as f:
                dados = json.load(f)
            st.session_state.prompt_sentimento = dados.get(
                "prompt_sentimento", DEFAULT_PROMPT_SENTIMENTO
            )
            st.session_state.prompt_relatorio = dados.get(
                "prompt_relatorio", DEFAULT_PROMPT_RELATORIO
            )
        except Exception:
            pass


def salvar_prompts():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(PROMPTS_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "prompt_sentimento": st.session_state.prompt_sentimento,
                    "prompt_relatorio": st.session_state.prompt_relatorio,
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
# CABEÇALHO / STEPPER
# ----------------------------------------------------------------------
st.title("🤖 Painel de Análise de Sentimentos em Comentários")
st.caption("SIC (Reels/TikTok) + Campanhas BR → Apify → Gemini (automático) → Relatório → Exportar")

cols = st.columns(len(STAGES))
for i, (col, stage) in enumerate(zip(cols, STAGES)):
    active = i <= st.session_state.current_stage
    bg = stage["color"] if active else "#F3F4F6"
    text_color = "#111827" if active else "#9CA3AF"
    col.markdown(
        f"""<div style="background-color:{bg};color:{text_color};border-radius:10px;
        padding:10px 6px;text-align:center;font-size:12px;font-weight:600;
        border:1px solid #d1d5db;min-height:55px;display:flex;align-items:center;
        justify-content:center;">{stage['label']}</div>""",
        unsafe_allow_html=True,
    )
st.write("")

tab_labels = [
    "1️⃣ Links + contexto",
    "2️⃣ Coletar e analisar",
    "3️⃣ Sentimentalização",
    "4️⃣ Relatório aprofundado",
    "5️⃣ Salvar / Exportar",
    "💰 Gastos",
]
if st.session_state.pode_configurar:
    tab_labels.append("⚙️ Configurações")
tab_labels.append("🪵 Log")

tab_objects = st.tabs(tab_labels)
tabs = dict(zip(tab_labels, tab_objects))

# ----------------------------------------------------------------------
# 1) LINKS + CONTEXTO (origem, campanha, marca, briefing, diretrizes)
# ----------------------------------------------------------------------
with tabs["1️⃣ Links + contexto"]:
    st.subheader("Links e contexto da coleta")
    st.caption(
        "Cada rodada é um lote: cole os links dessa campanha/conteúdo e preencha o "
        "contexto — isso é usado tanto para a coleta quanto para enriquecer a "
        "sentimentalização no Gemini."
    )

    c1, c2 = st.columns([1, 2])
    with c1:
        st.session_state.origem = st.selectbox(
            "Origem", ORIGENS, index=ORIGENS.index(st.session_state.origem)
        )
    with c2:
        st.session_state.links_input = st.text_area(
            "Links — um por linha (Instagram e/ou TikTok, detectado automaticamente)",
            value=st.session_state.links_input,
            height=140,
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
    st.session_state.nucleo = c4.text_input(
        "Núcleo (ex: Beauty, Alimentos...)", value=st.session_state.nucleo
    )

    st.session_state.briefing = st.text_area(
        "Briefing do conteúdo (do que se trata esse post — dá mais contexto pra IA)",
        value=st.session_state.briefing,
        height=90,
        placeholder="Ex: Reels de lançamento do produto X, tom bem-humorado, foco em público jovem...",
    )
    st.session_state.diretrizes_marca = st.text_area(
        "Diretrizes da marca para a sentimentalização (critérios específicos desse cliente)",
        value=st.session_state.diretrizes_marca,
        height=90,
        placeholder="Ex: comentários pedindo suporte técnico devem contar como Neutro, não Negativo...",
    )

    if st.button("Confirmar links", type="primary"):
        links = [l.strip() for l in st.session_state.links_input.splitlines() if l.strip()]
        if not links:
            st.warning("Cole ao menos um link.")
        else:
            st.session_state.links_list = links
            log(f"{len(links)} link(s) confirmado(s) — origem: {st.session_state.origem}.")
            st.success(f"{len(links)} link(s) confirmado(s). Vá para a aba 'Coletar e analisar'.")

    if st.session_state.links_list:
        df_links = pd.DataFrame({"link": st.session_state.links_list})
        df_links["plataforma"] = df_links["link"].apply(detectar_plataforma)
        st.dataframe(df_links, use_container_width=True)
        esperado = "instagram" if "Reels" in st.session_state.origem else (
            "tiktok" if "TikTok" in st.session_state.origem else None
        )
        if esperado and (df_links["plataforma"] != esperado).any():
            st.warning(
                f"A origem selecionada é '{st.session_state.origem}', mas nem todos os "
                f"links parecem ser do {esperado}. Confira antes de coletar."
            )

# ----------------------------------------------------------------------
# 2) COLETAR E ANALISAR (Apify + Gemini automático, num único botão)
# ----------------------------------------------------------------------
with tabs["2️⃣ Coletar e analisar"]:
    st.subheader("Coleta automática de comentários + sentimentalização")
    if not st.session_state.links_list:
        st.info("Confirme os links na aba anterior primeiro.")
    else:
        links_por_plataforma = {"instagram": [], "tiktok": [], "desconhecido": []}
        for l in st.session_state.links_list:
            links_por_plataforma[detectar_plataforma(l)].append(l)

        c1, c2, c3 = st.columns(3)
        c1.metric("Links Instagram", len(links_por_plataforma["instagram"]))
        c2.metric("Links TikTok", len(links_por_plataforma["tiktok"]))
        custo_estimado = (
            (len(links_por_plataforma["instagram"]) * st.session_state.ig_limit / 1000) * RATE_PER_1000["instagram"]
            + (len(links_por_plataforma["tiktok"]) * st.session_state.tk_limit / 1000) * RATE_PER_1000["tiktok"]
        )
        c3.metric("Custo máx. estimado", f"${custo_estimado:.2f}")

        if not is_apify_configured():
            st.warning("Apify não configurado — vai gerar comentários de demonstração.")
        if not is_gemini_configured():
            st.warning("Gemini não configurado — sentimento vai ser classificado por simulação (demo).")

        if st.button("🚀 Coletar e analisar automaticamente", type="primary"):
            todos_rows = []
            custo_total_execucao = 0.0

            for plataforma in ("instagram", "tiktok"):
                links = links_por_plataforma[plataforma]
                if not links:
                    continue
                with st.spinner(f"Coletando comentários — {plataforma}..."):
                    rows = []
                    if is_apify_configured():
                        try:
                            from apify_client import ApifyClient

                            client = ApifyClient(st.session_state.apify_token)
                            if plataforma == "instagram":
                                run_input = {
                                    "directUrls": links,
                                    "resultsLimit": st.session_state.ig_limit,
                                    "includeNestedComments": False,
                                }
                                actor_id = ACTOR_INSTAGRAM
                            else:
                                run_input = {
                                    "postURLs": links,
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
                                dataset_id = getattr(run, "default_dataset_id", None) or getattr(
                                    run, "defaultDatasetId", None
                                )
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
                            st.error(f"Erro ao chamar Apify ({plataforma}): {e}")
                    else:
                        time.sleep(0.5)
                        exemplos = [
                            "Adorei esse conteúdo, muito útil!",
                            "Não gostei do produto, veio quebrado.",
                            "Achei ok, nada de mais.",
                            "Excelente atendimento, super recomendo!",
                            "Poderia ser melhor explicado.",
                            "Péssima experiência, não volto mais.",
                            "Simplesmente incrível, superou minhas expectativas!",
                            "Comentário neutro sobre o assunto.",
                            "Alguém sabe se ainda tem em estoque?",
                        ]
                        for link in links:
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
                        todos_rows.extend(rows)
                        custo = registrar_gasto(plataforma, len(rows))
                        custo_total_execucao += custo

            if not todos_rows:
                st.warning("Nenhum comentário coletado.")
            else:
                df = pd.DataFrame(todos_rows)
                st.session_state.comentarios_df = df
                st.session_state.current_stage = max(st.session_state.current_stage, 1)
                log(f"Coleta finalizada: {len(df)} comentários. Custo: ${custo_total_execucao:.2f}.")

                with st.spinner("Rodando sentimentalização automática no Gemini..."):
                    resultados = df.copy()
                    if is_gemini_configured():
                        try:
                            import google.generativeai as genai

                            genai.configure(api_key=st.session_state.gemini_key)
                            model = genai.GenerativeModel(st.session_state.gemini_model)
                            sentimentos, justificativas = [], []
                            progress = st.progress(0)
                            for i, comentario in enumerate(resultados["comentario"]):
                                prompt = montar_prompt_sentimento(comentario)
                                resp = model.generate_content(prompt)
                                try:
                                    parsed = json.loads(resp.text.strip().strip("```json").strip("```"))
                                    sentimentos.append(parsed.get("sentimento", "Neutro"))
                                    justificativas.append(parsed.get("justificativa", ""))
                                except Exception:
                                    sentimentos.append("Neutro")
                                    justificativas.append(resp.text[:120])
                                progress.progress((i + 1) / len(resultados))
                            resultados["sentimento"] = sentimentos
                            resultados["justificativa"] = justificativas
                            log(f"Gemini analisou {len(resultados)} comentários automaticamente.")
                        except Exception as e:
                            st.error(
                                f"Erro ao chamar Gemini: {e} — aplicando classificação "
                                "simulada como contingência para não travar o fluxo."
                            )
                            resultados["sentimento"] = resultados["comentario"].apply(classificar_demo)
                            resultados["justificativa"] = "[FALLBACK] Gemini falhou nesta rodada — classificação simulada."
                            log(f"Erro no Gemini, fallback aplicado: {e}")
                    else:
                        time.sleep(0.8)
                        resultados["sentimento"] = resultados["comentario"].apply(classificar_demo)
                        resultados["justificativa"] = "[DEMO] classificação simulada por palavras-chave"
                        log(f"[DEMO] {len(resultados)} comentários classificados (simulado).")

                    # Metadados do lote (SIC/BR, campanha, marca, mother brand, núcleo)
                    resultados["origem"] = st.session_state.origem
                    resultados["campanha"] = st.session_state.campanha
                    resultados["marca"] = st.session_state.marca
                    resultados["mother_brand"] = st.session_state.mother_brand
                    resultados["nucleo"] = st.session_state.nucleo

                    st.session_state.resultados_df = resultados
                    st.session_state.current_stage = max(st.session_state.current_stage, 3)

                st.success(
                    f"{len(df)} comentários coletados e classificados automaticamente. "
                    f"Custo da coleta: **${custo_total_execucao:.2f}**."
                )

    if st.session_state.resultados_df is not None:
        st.dataframe(st.session_state.resultados_df, use_container_width=True)
    elif st.session_state.comentarios_df is not None:
        st.dataframe(st.session_state.comentarios_df, use_container_width=True)

# ----------------------------------------------------------------------
# 3) SENTIMENTALIZAÇÃO - VISÃO GERAL + EXEMPLOS NEUTROS
# ----------------------------------------------------------------------
with tabs["3️⃣ Sentimentalização"]:
    st.subheader("Visão geral dos sentimentos")
    resultados = st.session_state.resultados_df
    if resultados is None or resultados.empty:
        st.info("Rode a coleta + análise na aba anterior primeiro.")
    else:
        st.session_state.current_stage = max(st.session_state.current_stage, 3)
        contagem = resultados["sentimento"].value_counts().reset_index()
        contagem.columns = ["sentimento", "quantidade"]

        total = len(resultados)
        pos = int(resultados["sentimento"].eq("Positivo").sum())
        neg = int(resultados["sentimento"].eq("Negativo").sum())
        neu = total - pos - neg
        c1, c2, c3 = st.columns(3)
        c1.metric("😊 Positivos", pos, f"{pos/total:.0%}")
        c2.metric("😐 Neutros", neu, f"{neu/total:.0%}")
        c3.metric("☹️ Negativos", neg, f"{neg/total:.0%}")

        c1, c2 = st.columns(2)
        with c1:
            fig_pizza = px.pie(
                contagem, names="sentimento", values="quantidade",
                color="sentimento", color_discrete_map=SENTIMENT_COLORS,
                title="Distribuição de sentimentos",
            )
            st.plotly_chart(fig_pizza, use_container_width=True)
        with c2:
            por_post = resultados.groupby(["post_link", "sentimento"]).size().reset_index(name="quantidade")
            fig_barras = px.bar(
                por_post, x="post_link", y="quantidade", color="sentimento",
                color_discrete_map=SENTIMENT_COLORS, title="Sentimento por post", barmode="stack",
            )
            fig_barras.update_xaxes(tickangle=45)
            st.plotly_chart(fig_barras, use_container_width=True)

        st.markdown("##### Filtrar / consultar comentários")
        filtro = st.multiselect(
            "Mostrar sentimentos", ["Positivo", "Neutro", "Negativo"],
            default=["Positivo", "Neutro", "Negativo"],
        )
        st.dataframe(resultados[resultados["sentimento"].isin(filtro)], use_container_width=True)

        st.markdown("##### 📋 Exemplos de comentários Neutros (para levar a clientes)")
        neutros = resultados[resultados["sentimento"] == "Neutro"]
        if neutros.empty:
            st.caption("Nenhum comentário classificado como Neutro nesta rodada.")
        else:
            st.dataframe(
                neutros[["post_link", "autor", "comentario", "justificativa"]],
                use_container_width=True,
            )
            st.download_button(
                "⬇️ Baixar exemplos neutros (CSV)",
                data=neutros.to_csv(index=False).encode("utf-8"),
                file_name="exemplos_neutros.csv",
                mime="text/csv",
            )

# ----------------------------------------------------------------------
# 4) RELATÓRIO APROFUNDADO
# ----------------------------------------------------------------------
with tabs["4️⃣ Relatório aprofundado"]:
    st.subheader("Análise aprofundada do social da campanha")
    st.caption(
        "Gera um texto dissertativo, técnico e antropológico sobre o que os "
        "comentários revelam — pensado para material de estudo entregável ao cliente."
    )
    resultados = st.session_state.resultados_df
    if resultados is None or resultados.empty:
        st.info("Rode a coleta + análise primeiro.")
    else:
        if not is_gemini_configured():
            st.warning("Gemini não configurado — vai gerar um relatório-modelo simplificado (demo).")

        if st.button("📄 Gerar análise aprofundada", type="primary"):
            with st.spinner("Gemini está lendo os comentários e montando a análise..."):
                contagem = resultados["sentimento"].value_counts()
                total = len(resultados)
                resumo = "\n".join(
                    f"- {s}: {int(contagem.get(s,0))} comentários ({contagem.get(s,0)/total:.0%})"
                    for s in ["Positivo", "Neutro", "Negativo"]
                )
                amostra_partes = []
                for s in ["Positivo", "Neutro", "Negativo"]:
                    exemplos = resultados[resultados["sentimento"] == s]["comentario"].head(8).tolist()
                    if exemplos:
                        amostra_partes.append(f"{s}:\n" + "\n".join(f'- "{c}"' for c in exemplos))
                amostra = "\n\n".join(amostra_partes)

                if is_gemini_configured():
                    try:
                        import google.generativeai as genai

                        genai.configure(api_key=st.session_state.gemini_key)
                        model = genai.GenerativeModel(st.session_state.gemini_model)
                        prompt = (
                            st.session_state.prompt_relatorio.replace("{{CAMPANHA}}", st.session_state.campanha or "Não informado")
                            .replace("{{MARCA}}", st.session_state.marca or "Não informado")
                            .replace("{{MOTHER_BRAND}}", st.session_state.mother_brand or "Não informado")
                            .replace("{{NUCLEO}}", st.session_state.nucleo or "Não informado")
                            .replace("{{BRIEFING}}", st.session_state.briefing or "Não informado")
                            .replace("{{DIRETRIZES}}", st.session_state.diretrizes_marca or "Nenhuma diretriz específica")
                            .replace("{{RESUMO_QUANTITATIVO}}", resumo)
                            .replace("{{AMOSTRA_COMENTARIOS}}", amostra)
                        )
                        resp = model.generate_content(prompt)
                        st.session_state.relatorio_texto = resp.text
                        log("Relatório aprofundado gerado pelo Gemini.")
                    except Exception as e:
                        st.error(f"Erro ao chamar Gemini: {e}")
                else:
                    st.session_state.relatorio_texto = (
                        f"# [DEMO] Análise aprofundada — {st.session_state.campanha or 'campanha sem nome'}\n\n"
                        f"**Marca:** {st.session_state.marca or '-'} · "
                        f"**Mother brand:** {st.session_state.mother_brand or '-'} · "
                        f"**Núcleo:** {st.session_state.nucleo or '-'}\n\n"
                        f"## Panorama geral\n\n{resumo}\n\n"
                        "## Observação\n\nEste é um relatório simulado (modo demonstração) — "
                        "configure a Gemini API Key para gerar a análise dissertativa completa, "
                        "com insights, oportunidades, riscos e leitura antropológica do público."
                    )
                    log("[DEMO] Relatório-modelo gerado (Gemini não configurado).")

        if st.session_state.relatorio_texto:
            st.markdown(st.session_state.relatorio_texto)
            st.download_button(
                "⬇️ Baixar relatório (Markdown)",
                data=st.session_state.relatorio_texto.encode("utf-8"),
                file_name="analise_aprofundada.md",
                mime="text/markdown",
            )

# ----------------------------------------------------------------------
# 5) SALVAR / EXPORTAR (post_comments unificado)
# ----------------------------------------------------------------------
with tabs["5️⃣ Salvar / Exportar"]:
    st.subheader("Salvar / exportar resultado final (post_comments)")
    resultados = st.session_state.resultados_df
    if resultados is None or resultados.empty:
        st.info("Rode a coleta + análise primeiro.")
    else:
        st.caption(
            "Tabela única (mesma estrutura pensada para o BigQuery no futuro) com "
            "comentários + sentimento + origem (SIC/BR) + campanha + marca + núcleo."
        )
        st.dataframe(resultados, use_container_width=True)

        c1, c2 = st.columns(2)
        with c1:
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                resultados.to_excel(writer, sheet_name="post_comments", index=False)
            st.download_button(
                "⬇️ Baixar Excel único (post_comments)",
                data=buffer.getvalue(),
                file_name="post_comments.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        with c2:
            buffer_nucleo = io.BytesIO()
            with pd.ExcelWriter(buffer_nucleo, engine="openpyxl") as writer:
                grupos = resultados.groupby(
                    resultados["nucleo"].replace("", "sem_nucleo").fillna("sem_nucleo")
                )
                for nome, grupo in grupos:
                    aba = str(nome)[:31] or "sem_nucleo"
                    grupo.to_excel(writer, sheet_name=aba, index=False)
            st.download_button(
                "⬇️ Baixar Excel por núcleo (uma aba por núcleo)",
                data=buffer_nucleo.getvalue(),
                file_name="post_comments_por_nucleo.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

        st.divider()
        if not is_bq_configured():
            st.caption(
                "🟡 BigQuery ainda não configurado — por enquanto o fluxo oficial é "
                "exportar em Excel (acima) e integrar com o PowerBI a partir dele. "
                "Assim que o BigQuery estiver pronto, o botão abaixo passa a gravar "
                f"direto na tabela `{st.session_state.bq_table_post_comments}`."
            )
        if st.button("💾 Salvar também no BigQuery (post_comments)", disabled=not is_bq_configured()):
            with st.spinner("Salvando no BigQuery..."):
                try:
                    table_id = salvar_df_bigquery(resultados, st.session_state.bq_table_post_comments)
                    log(f"{len(resultados)} linhas salvas em {table_id}.")
                    st.success(f"Salvo em `{table_id}`.")
                    st.session_state.current_stage = 5
                except Exception as e:
                    st.error(f"Erro ao salvar no BigQuery: {e}")

# ----------------------------------------------------------------------
# 6) GASTOS
# ----------------------------------------------------------------------
with tabs["💰 Gastos"]:
    st.subheader("Histórico de gastos com extração de comentários")
    st.caption(
        f"Instagram: ${RATE_PER_1000['instagram']:.2f} por mil comentários · "
        f"TikTok: ${RATE_PER_1000['tiktok']:.2f} por mil comentários."
    )
    if not st.session_state.gastos:
        st.caption("Nenhum gasto registrado ainda — rode uma coleta na aba 2.")
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
            file_name="gastos.csv",
            mime="text/csv",
        )
    st.caption(
        "O histórico acima é desta sessão. Configure o BigQuery para manter o "
        "histórico salvo entre sessões e entre usuários diferentes."
    )

# ----------------------------------------------------------------------
# 7) CONFIGURAÇÕES (somente admin / usuários com permissão)
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
                f"Actors fixos: Instagram Comments Scraper (`{ACTOR_INSTAGRAM}`) "
                f"e TikTok Comments Scraper (`{ACTOR_TIKTOK}`)."
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
            st.markdown("#### Prompt de sentimentalização")
            st.caption(
                "Usado para classificar cada comentário. Placeholders disponíveis: "
                "`{{BRIEFING}}`, `{{DIRETRIZES}}`, `{{COMENTARIO}}`."
            )
            st.session_state.prompt_sentimento = st.text_area(
                "Prompt de sentimentalização", value=st.session_state.prompt_sentimento, height=220
            )
            c1, c2 = st.columns(2)
            if c1.button("💾 Salvar prompt de sentimentalização"):
                salvar_prompts()
                log("Prompt de sentimentalização atualizado.")
                st.success("Salvo.")
            if c2.button("↩️ Restaurar prompt padrão de sentimentalização"):
                st.session_state.prompt_sentimento = DEFAULT_PROMPT_SENTIMENTO
                salvar_prompts()
                st.rerun()

            st.divider()
            st.markdown("#### Prompt do relatório aprofundado")
            st.caption(
                "Placeholders: `{{CAMPANHA}}`, `{{MARCA}}`, `{{MOTHER_BRAND}}`, `{{NUCLEO}}`, "
                "`{{BRIEFING}}`, `{{DIRETRIZES}}`, `{{RESUMO_QUANTITATIVO}}`, `{{AMOSTRA_COMENTARIOS}}`."
            )
            st.session_state.prompt_relatorio = st.text_area(
                "Prompt do relatório aprofundado", value=st.session_state.prompt_relatorio, height=260
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

    if st.button("🔄 Reiniciar fluxo (limpar comentários, resultados e relatório)"):
        for key in (
            "links_input", "links_list", "comentarios_df", "resultados_df",
            "relatorio_texto", "current_stage", "log",
        ):
            st.session_state[key] = DEFAULTS[key]
        st.rerun()
