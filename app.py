"""
Painel de Análise de Sentimentos em Comentários (Apify: SIC + Campanhas BR | Pulsar: planilha)
Fluxo em uma tela só: escolhe a fonte, preenche o contexto, clica em um botão e
recebe coleta/leitura + sentimentalização + relatório aprofundado de uma vez.
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
ORIGENS_APIFY = ["Campanha BR (Apify)", "SIC - Reels", "SIC - TikTok"]
FONTES = ["Apify (links de Instagram/TikTok)", "Pulsar (planilha exportada)"]
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


def classificar_comentarios_gemini(df: pd.DataFrame) -> pd.DataFrame:
    """Roda a sentimentalização (Gemini, ou fallback demo) sobre a coluna 'comentario'."""
    resultados = df.copy()
    if is_gemini_configured():
        try:
            import google.generativeai as genai

            genai.configure(api_key=st.session_state.gemini_key)
            model = genai.GenerativeModel(st.session_state.gemini_model)
            sentimentos, justificativas = [], []
            progress = st.progress(0)
            total = len(resultados)
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
                if total:
                    progress.progress((i + 1) / total)
            resultados["sentimento"] = sentimentos
            resultados["justificativa"] = justificativas
            log(f"Gemini analisou {len(resultados)} comentários automaticamente.")
        except Exception as e:
            st.error(
                f"Erro ao chamar Gemini: {e} — aplicando classificação simulada como contingência."
            )
            resultados["sentimento"] = resultados["comentario"].apply(classificar_demo)
            resultados["justificativa"] = "[FALLBACK] Gemini falhou nesta rodada — classificação simulada."
            log(f"Erro no Gemini, fallback aplicado: {e}")
    else:
        time.sleep(0.5)
        resultados["sentimento"] = resultados["comentario"].apply(classificar_demo)
        resultados["justificativa"] = "[DEMO] classificação simulada por palavras-chave"
        log(f"[DEMO] {len(resultados)} comentários classificados (simulado).")
    return resultados


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


def gerar_relatorio(resultados: pd.DataFrame) -> str:
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
            log("Relatório aprofundado gerado pelo Gemini.")
            return resp.text
        except Exception as e:
            st.error(f"Erro ao gerar relatório com Gemini: {e}")
            log(f"Erro ao gerar relatório: {e}")
            return ""
    else:
        log("[DEMO] Relatório-modelo gerado (Gemini não configurado).")
        return (
            f"# [DEMO] Análise aprofundada — {st.session_state.campanha or 'campanha sem nome'}\n\n"
            f"**Marca:** {st.session_state.marca or '-'} · "
            f"**Mother brand:** {st.session_state.mother_brand or '-'} · "
            f"**Núcleo:** {st.session_state.nucleo or '-'}\n\n"
            f"## Panorama geral\n\n{resumo}\n\n"
            "## Observação\n\nEste é um relatório simulado (modo demonstração) — "
            "configure a Gemini API Key para gerar a análise dissertativa completa."
        )


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
            st.session_state.prompt_relatorio = dados.get("prompt_relatorio", DEFAULT_PROMPT_RELATORIO)
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
# CABEÇALHO
# ----------------------------------------------------------------------
st.title("🤖 Painel de Análise de Sentimentos em Comentários")
st.caption("Escolha a fonte, preencha o contexto e rode tudo com um clique.")

tab_labels = ["🚀 Análise", "💰 Gastos"]
if st.session_state.pode_configurar:
    tab_labels.append("⚙️ Configurações")
tab_labels.append("🪵 Log")

tab_objects = st.tabs(tab_labels)
tabs = dict(zip(tab_labels, tab_objects))

# ----------------------------------------------------------------------
# 🚀 ANÁLISE — tela única
# ----------------------------------------------------------------------
with tabs["🚀 Análise"]:
    st.session_state.fonte = st.radio("Fonte dos dados", FONTES, horizontal=True)

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
        "Gerar também a análise aprofundada (relatório dissertativo)",
        value=st.session_state.gerar_relatorio_auto,
    )

    if not is_apify_configured() and st.session_state.fonte == FONTES[0]:
        st.warning("Apify não configurado — vai gerar comentários de demonstração.")
    if not is_gemini_configured():
        st.warning("Gemini não configurado — sentimento/relatório serão simulados (modo demonstração).")

    rodar = st.button("🚀 Rodar análise completa", type="primary", use_container_width=True)

    if rodar:
        resultados = None

        # ---------------- FONTE: PULSAR ----------------
        if st.session_state.fonte == FONTES[1]:
            mapear_contexto_pulsar()
            if arquivo_pulsar is None:
                st.warning("Envie a planilha do Pulsar antes de rodar.")
            else:
                with st.spinner("Lendo a planilha do Pulsar..."):
                    try:
                        base = ler_planilha_pulsar(arquivo_pulsar)
                        log(f"Planilha do Pulsar lida: {len(base)} comentários.")
                    except Exception as e:
                        st.error(f"Erro ao ler a planilha do Pulsar: {e}")
                        base = None

                if base is not None and not base.empty:
                    if st.session_state.reclassificar_pulsar_gemini:
                        with st.spinner("Reclassificando com Gemini..."):
                            resultados = classificar_comentarios_gemini(base.drop(columns=["sentimento", "justificativa"]))
                    else:
                        resultados = base
                        log("Usando a classificação de sentimento original do Pulsar.")

        # ---------------- FONTE: APIFY ----------------
        else:
            links = [l.strip() for l in st.session_state.links_input.splitlines() if l.strip()]
            if not links:
                st.warning("Cole ao menos um link antes de rodar.")
            else:
                links_por_plataforma = {"instagram": [], "tiktok": [], "desconhecido": []}
                for l in links:
                    links_por_plataforma[detectar_plataforma(l)].append(l)

                todos_rows = []
                custo_total_execucao = 0.0
                for plataforma in ("instagram", "tiktok"):
                    plinks = links_por_plataforma[plataforma]
                    if not plinks:
                        continue
                    with st.spinner(f"Coletando comentários — {plataforma}..."):
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
                            todos_rows.extend(rows)
                            custo = registrar_gasto(plataforma, len(rows))
                            custo_total_execucao += custo

                if todos_rows:
                    df = pd.DataFrame(todos_rows)
                    log(f"Coleta finalizada: {len(df)} comentários. Custo: ${custo_total_execucao:.2f}.")
                    with st.spinner("Rodando sentimentalização automática no Gemini..."):
                        resultados = classificar_comentarios_gemini(df)
                    st.info(f"Custo desta coleta: **${custo_total_execucao:.2f}** (registrado em 💰 Gastos).")
                else:
                    st.warning("Nenhum comentário coletado.")

        # ---------------- PÓS-PROCESSAMENTO COMUM ----------------
        if resultados is not None and not resultados.empty:
            resultados["origem"] = st.session_state.origem
            resultados["campanha"] = st.session_state.campanha
            resultados["marca"] = st.session_state.marca
            resultados["mother_brand"] = st.session_state.mother_brand
            resultados["nucleo"] = st.session_state.nucleo
            st.session_state.resultados_df = resultados

            if st.session_state.gerar_relatorio_auto:
                with st.spinner("Gerando a análise aprofundada..."):
                    st.session_state.relatorio_texto = gerar_relatorio(resultados)
            else:
                st.session_state.relatorio_texto = ""

            st.success(f"Pronto! {len(resultados)} comentários processados.")

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

        st.markdown("##### Consultar comentários")
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
            st.dataframe(neutros[["post_link", "autor", "comentario", "justificativa"]], use_container_width=True)
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
        c1, c2 = st.columns(2)
        with c1:
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                resultados.to_excel(writer, sheet_name="post_comments", index=False)
            st.download_button(
                "⬇️ Baixar Excel único (post_comments)",
                data=buffer.getvalue(), file_name="post_comments.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
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
# GASTOS
# ----------------------------------------------------------------------
with tabs["💰 Gastos"]:
    st.subheader("Histórico de gastos com extração de comentários (Apify)")
    st.caption(
        f"Instagram: ${RATE_PER_1000['instagram']:.2f} por mil comentários · "
        f"TikTok: ${RATE_PER_1000['tiktok']:.2f} por mil comentários. Planilhas do Pulsar não geram custo aqui."
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
                "Usado para classificar cada comentário. Placeholders: "
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

    if st.button("🔄 Limpar resultado atual (comentários, sentimento e relatório)"):
        for key in ("links_input", "resultados_df", "relatorio_texto", "log"):
            st.session_state[key] = DEFAULTS[key]
        st.rerun()
