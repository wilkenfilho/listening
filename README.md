# Painel de Análise de Sentimentos em Comentários

Interface em Streamlit que segue o fluxo:

`Links de posts → Apify → Banco (comentários) → API Gemini → Sentimentalização → Banco (resultado final)`

O app funciona em **modo demonstração** automaticamente enquanto Apify, Gemini
e BigQuery não estiverem configurados — assim dá pra visualizar o fluxo
inteiro antes de conectar tudo de verdade.

**Novidades desta versão:**
- **Pipeline unificado SIC + Campanhas BR**: na aba 1, você escolhe a origem
  (`Campanha BR (Apify)`, `SIC - Reels` ou `SIC - TikTok`) e preenche
  campanha, marca, mother brand, núcleo, briefing do conteúdo e diretrizes
  específicas da marca — tudo isso alimenta o prompt do Gemini.
- **Coleta + sentimentalização num único botão** (aba 2): assim que os
  comentários são extraídos do Apify, o Gemini já classifica automaticamente
  cada um, sem passo manual separado.
- **Detecta automaticamente Instagram vs TikTok** e roda o Actor certo
  (`Instagram Comments Scraper` / `TikTok Comments Scraper`).
- **Calcula o custo de cada coleta** ($1,90/mil comentários no Instagram,
  $0,50/mil no TikTok) e registra no histórico da aba 💰 Gastos, por usuário.
- **Categoria Neutro em destaque**: aba própria de exemplos neutros, prontos
  pra exportar e levar como exemplo pra clientes.
- **Relatório aprofundado** (aba 4): o Gemini lê todos os comentários e o
  contexto (briefing + diretrizes da marca) e escreve uma análise
  dissertativa — panorama, insights, oportunidades, riscos, leitura
  antropológica e conclusão — pronta pra usar como material de estudo.
- **Tabela única `post_comments`** (aba 5): mesma estrutura pensada para o
  BigQuery no futuro, com comentários + sentimento + origem + campanha +
  marca + núcleo. Por enquanto exporta em **Excel** (arquivo único ou uma
  aba por núcleo) — o botão de salvar no BigQuery já existe, só fica
  desabilitado até vocês configurarem.
- **Login com Google restrito ao domínio `@br-mediagroup.com`** — quem não
  tiver e-mail da agência não consegue usar o app.
- **Menu "⚙️ Configurações" restrito** — só aparece para
  `wilken.perez@br-mediagroup.com` (administrador fixo) e para quem ele
  liberar. Tem três partes: chaves de API, os **prompts do Gemini**
  (sentimentalização e relatório aprofundado, editáveis com placeholders) e
  o controle de acesso de usuários.

## Estrutura

```
agencia-sentimento/
├── app.py
├── requirements.txt
├── .gitignore
└── .streamlit/
    └── secrets.toml.example
```

## Rodando localmente

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

## Subindo no GitHub

```bash
git init
git add .
git commit -m "Painel de análise de sentimentos"
git branch -M main
git remote add origin https://github.com/SEU_USUARIO/SEU_REPO.git
git push -u origin main
```

⚠️ O arquivo `.streamlit/secrets.toml` (com suas chaves reais) **não** deve
ir para o GitHub — ele já está no `.gitignore`. Suba apenas o
`secrets.toml.example` como referência.

## Deploy gratuito (Streamlit Community Cloud)

1. Acesse https://share.streamlit.io e conecte sua conta do GitHub.
2. Escolha o repositório e o arquivo `app.py`.
3. Em **Settings → Secrets**, cole o conteúdo do seu `secrets.toml` real
   (Apify token, Gemini key e credenciais do BigQuery).
4. Deploy. Pronto — sua agência acessa por um link público.

## Configurando as credenciais

Você pode preencher as chaves de duas formas:

1. **Direto na interface** (aba "⚙️ Configurações" na barra lateral) — fica
   salvo só durante a sessão do navegador, ótimo para testar rapidamente.
2. **Via `secrets.toml`** — recomendado para produção. Copie
   `.streamlit/secrets.toml.example` para `.streamlit/secrets.toml` e
   preencha com os valores reais (token do Apify, Actor ID, Gemini API Key,
   projeto/dataset do BigQuery e o JSON da service account).

### Apify
- Token: em https://console.apify.com/account/integrations
- Os Actor IDs do Instagram (`SbK00X0JYCPblD2wp`) e do TikTok
  (`BDec00yAmCm1QbMEI`) já estão fixos no `app.py` — não precisa configurar.

### Gemini
- API Key: em https://aistudio.google.com/apikey

### Login com Google (restrito a `@br-mediagroup.com`)

O app usa a autenticação nativa do Streamlit (`st.login`), que fala OpenID
Connect com o Google. Passo a passo:

1. No [Google Cloud Console](https://console.cloud.google.com/apis/credentials),
   crie um **OAuth 2.0 Client ID** do tipo "Web application" (pode ser no
   mesmo projeto GCP do BigQuery ou em outro).
2. Em "Authorized redirect URIs", adicione:
   - `http://localhost:8501/oauth2callback` (para testar local)
   - `https://SEU-APP.streamlit.app/oauth2callback` (depois do deploy)
3. Copie o `client_id` e o `client_secret` gerados.
4. No `.streamlit/secrets.toml`, preencha a seção `[auth]` (veja o
   `secrets.toml.example`) com esses valores e um `cookie_secret` aleatório
   (qualquer string longa e única serve).
5. Pronto — ao abrir o app, ele pede login com Google. Depois do login, o
   próprio app confere se o e-mail termina em `@br-mediagroup.com`; se não
   terminar, mostra erro e bloqueia o acesso automaticamente.

Enquanto a seção `[auth]` não estiver preenchida, o app cai em modo
demonstração: pede só um nome de usuário em texto livre, sem exigir login
real — útil para testar localmente antes de configurar o Google.

> O Google não oferece um jeito de restringir a tela de login só ao domínio
> pelo lado do OAuth padrão (isso é um recurso do Google Workspace chamado
> "hd", que nem todo fluxo aceita) — por isso a checagem do domínio é feita
> pelo próprio app depois do login, que é a forma robusta de garantir isso
> aqui.

### BigQuery
- Crie uma service account no seu projeto GCP com permissão de
  `BigQuery Data Editor` no dataset de destino, gere a chave JSON, e
  faça upload dela na aba de configurações (ou cole no `secrets.toml`).

## Menu de Configurações (restrito)

Só aparece na barra de abas para `wilken.perez@br-mediagroup.com` e para
usuários que ele liberar. Tem duas partes:

**🔑 Chaves de API** — mesmos campos que antes ficavam na barra lateral
(Apify, Gemini, BigQuery), agora só editáveis por quem tem permissão. Cada
chave preenchida tem um botão "🗑️ Revogar" ao lado.

**👥 Acesso de usuários** — lista de e-mails `@br-mediagroup.com` com dois
controles por pessoa:
- *Acesso ao app*: se desmarcado, a pessoa é bloqueada no login.
- *Pode configurar*: se marcado, essa pessoa também enxerga e edita esta
  tela de Configurações (chaves de API + outros usuários).

O administrador (`wilken.perez@br-mediagroup.com`) sempre tem acesso total
e não precisa (nem pode) ser removido dessa lista.

**Onde isso fica salvo:** se o BigQuery estiver configurado, a lista de
usuários é salva na tabela `usuarios_permitidos` (nome configurável em
`BQ_TABLE_USUARIOS`) e persiste entre sessões e deploys. Sem BigQuery, cai
num arquivo local (`data/usuarios_permitidos.json`) — funciona bem rodando
localmente ou em servidor próprio, mas em provedores com sistema de
arquivos temporário (como o Streamlit Community Cloud) esse arquivo pode
ser perdido a cada reinício. Assim que o BigQuery de vocês estiver pronto,
recomendo migrar para lá.

## Controle de gastos

Cada vez que a extração roda, o app calcula o custo com base na quantidade
de comentários realmente coletados e registra: usuário, data/hora,
plataforma, quantidade e custo em USD. Isso fica salvo:

- Na sessão atual sempre (aba **💰 Gastos**, com gráficos por usuário e por
  plataforma, e exportação em CSV).
- No BigQuery também, se estiver configurado — assim o histórico persiste
  entre sessões e entre usuários diferentes (tabela `gastos`, configurável
  em `BQ_TABLE_GASTOS`).

Os valores usados são $1,90 a cada 1.000 comentários no Instagram e $0,50 a
cada 1.000 no TikTok — ajuste as constantes `RATE_PER_1000` no topo do
`app.py` se o preço do Apify mudar.

## Sobre a automação

Hoje a coleta e a sentimentalização rodam com um clique no botão "🚀 Coletar
e analisar automaticamente" (aba 2) — como o Streamlit só executa enquanto
alguém tem o app aberto no navegador, não existe agendamento nativo. Quando
fizer sentido, dá pra evoluir isso para automação de verdade (roda sozinho,
sem precisar abrir o app) com Cloud Scheduler + Cloud Function/Run
disparando o mesmo pipeline direto no BigQuery — o código já está separado
em funções (`salvar_df_bigquery`, `montar_prompt_sentimento` etc.) pensando
nisso.

## Próximos ajustes recomendados

- **Registro de conteúdos do SIC**: hoje os links de `SIC - Reels` e
  `SIC - TikTok` são colados manualmente na aba 1, igual aos de campanha.
  Quando o cadastro de conteúdos do SIC estiver no BigQuery (ou em outro
  sistema), dá pra puxar os links automaticamente de lá em vez de colar.
- **Campanha / marca / mother_brand / briefing**: hoje são campos de texto
  livre preenchidos a cada rodada. Quando existir uma tabela de campanhas
  no BigQuery, dá pra trocar por um seletor que já vem com briefing e
  diretrizes preenchidos automaticamente.

- Trocar o parsing genérico dos itens retornados pelo Apify (campos
  `text`/`ownerUsername`) pelos campos exatos que o Actor escolhido retorna.
- Ajustar o schema da tabela `post_comments` no BigQuery antes do primeiro
  carregamento — o app cria a tabela automaticamente a partir do
  DataFrame, mas o ideal é ter o schema definido no datalake.
