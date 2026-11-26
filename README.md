# Imob Pato Branco — Agregador & Motor de Busca Unificado

API REST (**FastAPI** + **SQLAlchemy 2.0** + **MySQL 8.4**) e **painel web público de consulta, sem login**, para buscar, comparar e acompanhar imóveis para **aluguel** e **compra/venda** em fontes públicas de **Pato Branco - PR**. São **54 fontes cadastradas com coletores configurados**. Cadastro e testes locais não comprovam coleta completa nem funcionamento atual de cada site.

Na versão 0.2.0, a instalação passa a usar Docker Compose e um banco MySQL novo, sem migração do SQLite. A sincronização manual e as operações de escrita não ficam disponíveis no site.

---

## Principais Recursos

1. **Fontes cadastradas**:
   - **Widesys (25 sites)**: Arcari, Ederson, Realiza PB, Casa Mais, Rafa Imóveis, Imobilli, Valmir, Solar, Tomazini, Morada 46, Pato Imóveis, Domeneguini, Famex, Aliança PB, Exclusive Gold, Fávero, Esatta, Tradição, Cattuci, Veronese, Duo, Mattia (venda), Negociar (venda), Magcen (venda) e Morosini.
   - **Imonov / Si9 Sistemas (18 sites)**: Assmann, Trento, Invest, Bela Morada, Habitar (com coordenadas GPS Leaflet), Conceito PB, Sbaraini, Ciro, Cagol & Chaves, Evidence, Famma, América, Acacio, Imperador, Mazorana, Paulo Reis, Popiel e CAD Imobiliária (`/venda.asp?p=1` com paginação por query string).
   - **Apresenta.me**: Imobiliária Uniq (`POST /busca` + endereço estruturado, condomínio e IPTU).
   - **WordPress MyHome REST**: Moretti Imobiliária (`/wp-json/myhome/v1/listings`).
   - **Apolar REST API**: Apolar Imóveis (`POST /properties/search/main` filtrado exclusivamente para `"city": "Pato Branco"`).
   - **Catálogos HTML/JS (7 sites)**: Valen (`POST /pesquisa` com cidade Pato Branco), Cristian (grade paginada da página inicial), Familiar (`/busca/`), Álvaro (`/imoveis/1-venda` e `/imoveis/2-locacao`), Carlos (`/imovel/` com finalidade e cidade), Mariely Cattoni (`POST /imoveis` com paginação e filtro de Pato Branco) e Junior Gonçalves (`/imoveis-data.js`). Leitura de cartões e links de paginação publicados no mesmo domínio.
   - **Agil REST API**: Fortun (`POST /company/{id}/search`), com paginação nativa e consulta separada de venda, locação anual e temporada. Detalhes que o site marca como protegidos exigem identificação no site original.
2. **Validação e Reconciliação Local dos Dados**:
   - **Filtro estrito de cidade**: descarta anúncios de outras cidades (ex.: Vitorino, Mariópolis, Francisco Beltrão, Pato Bragado, litoral de SC).
   - **Correção de finalidade e categoria**: detecta anúncios cadastrados incorretamente pelas imobiliárias (ex.: imóvel `"à venda"` de R$ 350 mil jogado na aba de locação; `Sobrado` cadastrado como `Casa`; `Kitnet/Studio` ou `Cobertura` cadastrado como `Apartamento`; `Sala Comercial` ou `Barracão`).
   - **Padronização de bairros de Pato Branco**: unifica grafias (`Jd. Floresta` → `Jardim Floresta`, `Dal Ross` → `Dall Ross`, `Lasalle` → `La Salle`, etc.).
3. **Sincronização programada**:
   - Atualiza o catálogo às **06:00 e às 18:00 (America/Sao_Paulo)**, com limites de requisições por domínio e proteção contra desativação em massa.
   - Nenhuma coleta é disparada ao iniciar o servidor. Horários perdidos enquanto ele está desligado não são recuperados.
   - A sincronização manual fica disponível apenas para operadores, pela CLI.
4. **Inteligência Financeira & Curadoria Pessoal**:
   - Cálculo automático de **Custo Mensal Estimado** (`Aluguel + Condomínio + IPTU + Seguro`) e **R$/m²**.
   - **Histórico de preços** (`price_history`) e filtro direto de **quedas de preço** (`only_price_drops=true`).
   - **Detecção e agrupamento de duplicados** entre imobiliárias diferentes (`deduplicate=true` mostra o menor valor anunciado e lista os concorrentes lado a lado).
   - **Favoritos, descartados e anotações** ficam neste navegador. O IMOB limita seu armazenamento a 256 KiB, não apaga itens para abrir espaço e avisa se não conseguir salvar. Os filtros de favoritos e descartados enviam apenas identificadores de anúncios à busca; as anotações nunca são enviadas.

---

## Como Usar

### Ampliação do cadastro em 09/11/2026

Foram acrescentadas as 16 fontes do levantamento e a
[Mattia Corretores](https://mattiacorretores.com.br/), que também estava ausente.
As 17 novas fontes foram verificadas por coleta HTTP real, seguindo as páginas
disponíveis dentro do limite de 60 páginas por seção. A
[verificação por fonte](docs/catalog-verification-2026-11-09.json) registra as
requisições, anúncios lidos e observações aceitas pelo filtro de cidade.
O teto dos filtros Imonov de América e Mazorana foi ampliado para não repetir os
atalhos restritos encontrados nos sites. A inclusão de anúncios sem preço precisa
ser conferida também no comportamento desses filtros remotos.

As seis fontes que estavam pendentes agora usam `public_html` (cinco) e `agil`
(Fortun), sem serviço intermediário ou nova dependência. O coletor HTML lê links de imóveis e os dados dos cartões,
segue a próxima página publicada, preserva anúncios sem preço e rejeita
localizações desconhecidas ou fora de Pato Branco. O endereço comercial da empresa
no rodapé não serve como localização do imóvel.

O coletor sinaliza resultado parcial em caso de limite de páginas, paginação
repetida ou carregamento adicional por JavaScript sem URL navegável. Na Fortun,
a API pública resolve a busca que o site carrega por JavaScript, incluindo os
anúncios além dos destaques. A Familiar retornou **Nenhum imóvel encontrado** na
consulta atual; isso não é tratado como uma integração pendente.

A venda da Duo usa o formulário público `/localizar/localizar-imovel`, pois o
atalho `/imoveis-venda` retornou HTTP 500. O paginador preserva os filtros desse
formulário. Paulo Reis usa os parâmetros completos do link publicado pelo site.

Uma página inicial com HTML não reconhecido agora informa erro, em vez de parecer
uma sincronização vazia bem-sucedida. Etiquetas explícitas de vendido/locado nos
cartões Widesys impedem a entrada desses cartões como imóveis disponíveis sem
interromper a paginação. As proteções existentes contra desativação em massa continuam
aplicadas; isso não substitui a validação do catálogo completo.

### Subir com Docker Compose

Use Docker Engine com Docker Compose **v2.24 ou mais recente**. No servidor compartilhado, use um checkout separado dos projetos GoGon e cookbook. Antes de subir, confira que `docker compose ls` não mostra outro projeto `imob` e que `ss -ltn 'sport = :8090'` não mostra a porta em uso. Altere `IMOB_PORT` se necessário.

Em uma instalação nova, na raiz do checkout (não sobrescreva um `.env` existente):

```bash
(umask 077; set -o noclobber; sed '/^IMOB_MYSQL_PASSWORD=/d' imob-env.example > .env &&
  printf 'IMOB_MYSQL_PASSWORD=%s\n' "$(openssl rand -hex 24)" >> .env)
chmod 600 .env
docker compose config --quiet
docker compose up -d --build --wait
docker compose ps
curl -fsS http://127.0.0.1:8090/api/health
```

O painel fica em <http://127.0.0.1:8090/> no host. O MySQL não publica porta. O catálogo começa vazio; aguarde o próximo horário ou execute uma primeira coleta em um horário aprovado:

```bash
docker compose run --rm imob-ops imob sync --force
docker compose run --rm imob-ops imob stats
```

Para testar uma única fonte, adicione `--agency apolar` ao sync. Para executar apenas fontes cujo TTL expirou (6 horas por padrão), omita `--force`. Consulte `docker compose logs --tail 100 imob imob-caddy mysql` e `/api/sync/status` para acompanhar o serviço. O status da coleta por fonte também aparece no painel.

Para atualizar, use `docker compose up -d --build --wait` com o checkout atualizado. `docker compose down` para a aplicação, mas preserva os volumes `imob_imob-mysql` e `imob_imob-maps`. Não há backup automático. Não use `down -v` em manutenção: ele apaga o catálogo, seu histórico e os mapas importados. As credenciais iniciais do MySQL são aplicadas apenas a um volume novo; mudar `.env` sozinho não altera a conta existente.

### Exposição pública no IP compartilhado

O IMOB tem Caddyfile e contêiner próprios. Só `imob-caddy` publica uma porta no host, em `127.0.0.1:8090` por padrão. Ele atende por HTTP; o Caddy que já serve o IP da VPS continua sendo o único responsável pelas portas 80 e 443 e pelo certificado HTTPS. O app e o MySQL não publicam portas.

Se o Caddy público **roda no host**, use o Compose padrão do IMOB e a porta de loopback. Se ele **roda em contêiner**, como no cookbook, `127.0.0.1` aponta para o próprio contêiner. Nesse caso, crie uma rede compartilhada de proxy uma única vez e suba o IMOB com o arquivo adicional:

```bash
docker network create --internal imob_proxy
docker compose -f compose.yaml -f compose.public.yaml up -d --build --wait
```

Reutilize os dois `-f` nas próximas atualizações do IMOB. No Compose do proxy público, declare `imob_proxy: {external: true}` em `networks` e adicione `imob_proxy: {}` às redes do serviço `caddy`, sem remover as redes atuais. Só os dois proxies precisam dessa rede; não conecte o MySQL nem o app a ela.

No site HTTPS **já configurado para o IP**, adicione esta rota ao Caddyfile do proxy público, sem criar outro bloco para o mesmo IP. O `handle_path` remove `/imob` antes de encaminhar e o redirecionamento mantém a query string. O exemplo abaixo é para o **Caddy público em contêiner**:

```caddyfile
redir /imob /imob/?{query} 308
handle_path /imob/* {
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        -Server
    }
    reverse_proxy imob-caddy:8080
}
```

Para um **Caddy público no host**, troque a linha `reverse_proxy imob-caddy:8080` por `reverse_proxy 127.0.0.1:8090` (ajuste se mudou `IMOB_PORT`). Não use a rede compartilhada nesse caso.

Se for o Caddy do cookbook, insira o trecho no snippet `cookbook_app` de `web/caddy/site.caddy`. Mova o `encode`, o `import security_headers` e os três blocos `handle` atuais do cookbook para dentro de um `handle` de fallback; mantenha `handle_errors` no nível do snippet. Assim a política de conteúdo do cookbook não substitui a do IMOB, que permite fotos externas e visualizar PDFs.

O endereço público será `https://IP_DA_VPS/imob/`. Os apps mantêm chaves distintas no `localStorage`, mas compartilhar o mesmo IP e origem também compartilha acesso ao armazenamento; o caminho `/imob/` não isola os dados. Para isolar, use um hostname dedicado.

Valide a configuração do proxy público antes de aplicá-la e guarde a anterior para reversão. Se o proxy for o do cookbook, o snippet faz parte da imagem e a alteração exige reconstruir seu serviço Caddy. Confira o painel, `/imob/api/health`, CSS/JS, PDFs importados e as rotas já atendidas pelo proxy público. O Compose do IMOB não altera o proxy público, o certificado nem o DNS.

O pacote portátil do Windows com SQLite foi descontinuado na versão 0.2.0.

---

## Principais Endpoints da API REST

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/api/health` | Status do serviço |
| `GET` | `/api/stats` | Totais de anúncios ativos, aluguel, venda, quedas de preço e duplicados |
| `GET` | `/api/facets` | Contadores dinâmicos de tipos, bairros e imobiliárias (`?purpose=aluguel\|venda`) |
| `GET` | `/api/agencies` | Lista fontes cadastradas, coletores e status do último sync |
| `GET` | `/api/properties` | Busca paginada, incluindo busca textual MySQL FULLTEXT |
| `GET` | `/api/properties/{id}` | Ficha completa, histórico de preços e anúncios equivalentes em outras imobiliárias |
| `POST` | `/api/properties/search` | Busca somente de leitura, com filtros no corpo e listas opcionais `include_keys` e `exclude_keys` |
| `GET` | `/api/sync/status` | Progresso e próximo horário da sincronização automática |

### Exemplo de Consulta via API (`curl`)

```bash
curl "http://127.0.0.1:8090/api/properties?purpose=aluguel&types=apartamento,sobrado&neighborhoods=centro,fraron&max_monthly_total=3200&min_bedrooms=2&only_with_price=true&deduplicate=true&sort=menor_custo_mensal"
```

---

## Variáveis de Ambiente (`IMOB_*`)

| Variável | Padrão | Descrição |
|---|---|---|
| `IMOB_MYSQL_HOST` | `mysql` no Docker | Host do MySQL (`127.0.0.1` fora do Docker) |
| `IMOB_MYSQL_PORT` | `3306` | Porta do MySQL |
| `IMOB_MYSQL_USER`, `IMOB_MYSQL_DATABASE` | `imob` no Docker | Conta e banco do IMOB |
| `IMOB_MYSQL_PASSWORD` | obrigatório | Senha exclusiva da conta MySQL |
| `IMOB_PORT` | `8090` no Docker | Porta HTTP publicada apenas no loopback |
| `IMOB_AUTO_SYNC_ENABLED` | `true` | Ativa o agendamento automático |
| `IMOB_SYNC_TIMES` | `06:00,18:00` | Horários locais da sincronização automática |
| `IMOB_SYNC_TIMEZONE` | `America/Sao_Paulo` | Fuso horário IANA do agendamento |
| `IMOB_SYNC_INTERVAL_HOURS` | `6.0` | TTL da sincronização manual sem `--force` |
| `IMOB_PER_DOMAIN_DELAY_SECONDS` | `0.45` | Atraso mínimo entre requisições para o mesmo domínio |
| `IMOB_MAX_CONCURRENT_AGENCIES` | `6` | Máximo de imobiliárias sincronizadas simultaneamente |

---

## Qualidade de Código e Testes

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run python -m mypy src tests
uv run python -m pytest -v
node --test tests/web/

# Testes de integração: MySQL descartável em loopback, nunca o banco de produção.
docker compose -f compose.test.yaml up -d --wait
IMOB_TEST_MYSQL_URL=mysql+pymysql://root:imob-test-only@127.0.0.1:33306 \
  IMOB_REQUIRE_MYSQL=1 uv run python -m pytest -v
docker compose -f compose.test.yaml down

# Opcional: verifica as 17 inclusões nos sites reais e salva diagnósticos em /tmp/imob-live-catalogs/
uv run python -m pytest -q tests/live_catalogs.py
```

## Similaridade, negociação e cobertura

A detecção distingue **grupos prováveis** de **pares para revisar**. Apenas os
prováveis entram em `deduplicate=true`. Pares para revisar permanecem como anúncios
independentes, com motivos, campos ausentes e divergências nos detalhes. O filtro
**Com pares semelhantes para revisar** (`only_similarity_candidates=true`) localiza
esses anúncios. O contador de revisão representa pares, não imóveis nem duplicados
confirmados. A ficha apresenta os 20 pares com mais evidências e informa o total.

**Mesmo edifício e mesma unidade identificam diretamente o imóvel**, mesmo sem
área ou características adicionais. Diferenças em quartos, banheiros, vagas e áreas
são exibidas como inconsistências de cadastro e não anulam essa identificação.
Endereços, edifícios ou unidades conflitantes continuam impedindo a associação;
contradições internas entre título/cadastro e descrição exigem revisão.

Nos demais casos, um grupo provável exige **3 ou mais características independentes idênticas**,
incluindo uma referência de localização (rua ou edifício/condomínio), sem divergências
conhecidas. Contam rua/edifício (uma vez), número do endereço, unidade, metragem,
quartos, suítes, banheiros e vagas. Rua e edifício não contam duas vezes pela mesma
localização; metragem/tamanho e várias modalidades de área também contam uma só vez.
Número ou unidade sem contexto de rua/edifício não identificam um imóvel.

A metragem conta como idêntica somente se o valor numérico do mesmo tipo de área
coincidir. Áreas apenas próximas apoiam a revisão, sem aumentar a contagem de
igualdades. Bairro, tipo, finalidade, preço e textos genéricos não aumentam a contagem.
Os detalhes mostram quantas características coincidiram e quais são elas.

Dados ausentes não são iguais nem divergentes. Informações explícitas nos títulos e
descrições disponíveis podem complementar características ausentes, incluindo
endereço, número, edifício, unidade, áreas e contagens. A descrição do imóvel é lida
por inteiro até uma seção explícita de contato/assinatura, evitando usar o endereço
comercial da imobiliária. Dados estruturados não são silenciosamente substituídos
por um endereço ou unidade divergente no texto: o conflito exige revisão. Prefixos de edifícios e
bairros ao final do nome são normalizados. Contagens explícitas como "duas suítes +
um quarto" são reconciliadas na comparação, sem alterar o cadastro original.

Endereço ou unidade conhecidos e conflitantes impedem a associação. Divergências de
quartos, suítes, banheiros, vagas e áreas conservam o candidato para revisão quando
há outras evidências, mas impedem o agrupamento automático por semelhança. A
identificação direta por edifício + unidade prevalece sobre essas divergências
de características. Áreas são comparadas
apenas com o mesmo tipo: até 3% apoiam a associação; acima de 8% geram divergência.
Casos sem identificação direta e abaixo de três igualdades ou com divergências
ficam para revisar. Mesmo com
três igualdades, unidades distintas de um edifício podem ter a mesma configuração;
por isso os grupos são identificados como prováveis. Características genéricas
isoladas não geram grupos.
Todos os membros de um grupo precisam ser compatíveis entre si, evitando cadeias
A–B–C quando A e C divergem. A pontuação serve somente para ordenar evidências;
não é probabilidade calibrada.

O preço **não participa** da identificação. Alterações, diferenças ou ausência do
valor não aproximam nem separam anúncios. Números monetários também são excluídos
da comparação de termos. A comparação visual das fotos ainda não está implementada.

A tabela derivada `property_matches` guarda os grupos e pares, recalculados a partir
 dos dados do MySQL na inicialização e após a atualização de cada catálogo. Uma nova
coleta não é necessária para rever dados já armazenados. O parser Imonov reconhece mais atributos com rótulos e ícones
e corrige a leitura de áreas com `m²`; esses dados adicionais entram no próximo sync.
O catálogo pode estar incompleto: baixa cobertura de características não comprova
ausência de duplicados, mesmo quando o sincronizador retorna sucesso.

As tags **Aceita permuta** e **Estuda proposta** aparecem nos cartões e detalhes
quando indicadas nos dados estruturados suportados ou na descrição coletada.
Ausência da tag não significa recusa. A leitura de texto considera negações;
novos termos ficam disponíveis na próxima sincronização.
Clique numa miniatura da galeria para ampliar, navegar pelas setas e fechar com Esc.

Anúncios sem preço são incluídos por padrão, inclusive ao limpar filtros.
`only_with_price=true` restringe a busca quando desejado. A opção de incluir descartados
usa o estado deste navegador; `include_inactive=true` inclui anúncios inativos.
Limites de preço/área ainda podem excluir anúncios que não informam esses valores.

O banco contém apenas o que foi coletado dos catálogos públicos e aceito pelo filtro
de cidade. Imóveis identificados como fora de Pato Branco são descartados antes da
persistência; incluir inativos não os recupera. Status de sincronização bem-sucedida
não comprova que todo o estoque interno da imobiliária foi publicado. Um dump de
todo o estoque, incluindo anúncios não publicados, exige exportação autorizada do
CRM de cada imobiliária. A coleta atual não acessa esse estoque privado.

## Biblioteca de mapas e quadras

O botão **Mapas e quadras** organiza os links municipais em cinco faixas de 1 a
2500. Consulte uma quadra para abrir a pasta oficial ou ver seu PDF quando já
importado. Cada arquivo tem limite de 20 MiB.
Os links das pastas não significam que todos os PDFs já foram baixados.

Para importar mapas públicos baixados do acervo municipal, crie `maps-import/` na raiz
do checkout e copie os PDFs para lá. A pasta e os arquivos devem ser legíveis pelo
UID 10001 no contêiner. Só o serviço de importação monta essa pasta, em modo de leitura:

```bash
docker compose run --rm imob-maps-import
```

O importador reconhece arquivos como `0928.pdf` ou `Quadra_0928.pdf`, inclusive em
subpastas. Nomes ambíguos são informados como não importados; duas entradas para a
mesma quadra interrompem a importação antes de gravar. Os arquivos são armazenados
por quadra, sem criar base de proprietários ou consultar cadastros de IPTU.

Endpoints: `GET /api/maps?quadra=928` e `GET /api/maps/quadras/928/pdf`. Não há upload
pela API nem consulta automática
por proprietário, CPF, débitos ou enumeração de cadastros municipais. Quadra, lote,
cadastro imobiliário e matrícula são identificadores diferentes; nenhuma fórmula
entre eles foi confirmada. No formulário municipal observado em novembro de 2026,
`tipo_filtro=1` é CPF/CNPJ, `1001` é cadastro imobiliário e `1002` é cadastro econômico;
o formulário não oferece filtro por quadra/lote. O limite de 15 caracteres do campo
não demonstra ordem, distribuição ou limite numérico dos cadastros existentes.
