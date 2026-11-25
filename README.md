# Imob Pato Branco — Agregador & Motor de Busca Unificado

API REST (**FastAPI** + **SQLAlchemy 2.0** + **SQLite WAL + FTS5**) e **Painel Web Local** para buscar, validar, comparar e acompanhar imóveis para **aluguel** e **compra/venda** em fontes públicas de **Pato Branco - PR**. São **54 fontes cadastradas com coletores configurados**. Cadastro e testes locais não comprovam coleta completa nem funcionamento atual de cada site.

---

## Principais Recursos

1. **Fontes cadastradas**:
   - **Widesys (25 sites)**: Arcari, Ederson, Realiza PB, Casa Mais, Rafa Imóveis, Imobilli, Valmir, Solar, Tomazini, Morada 46, Pato Imóveis, Domeneguini, Famex, Aliança PB, Exclusive Gold, Fávero, Esatta, Tradição, Cattuci, Veronese, Duo, Mattia (venda), Negociar (venda), Magcen (venda) e Morosini.
   - **Imonov / Si9 Sistemas (18 sites)**: Assmann, Trento, Invest, Bela Morada, Habitar (com coordenadas GPS Leaflet), Conceito PB, Sbaraini, Ciro, Cagol & Chaves, Evidence, Famma, América, Acacio, Imperador, Mazorana, Paulo Reis, Popiel e CAD Imobiliária (`/venda.asp?p=1` com paginação por query string).
   - **Apresenta.me**: Imobiliária Uniq (`POST /busca` + endereço estruturado, condomínio e IPTU).
   - **WordPress MyHome REST**: Moretti Imobiliária (`/wp-json/myhome/v1/listings`).
   - **Apolar REST API**: Apolar Imóveis (`POST /properties/search/main` filtrado exclusivamente para `"city": "Pato Branco"`).
   - **Catálogos HTML/JS (7 sites)**: Valen (`POST /pesquisa` com cidade Pato Branco), Cristian (grade paginada da página inicial), Familiar (`/busca/`), Álvaro (`/imoveis/1-venda` e `/imoveis/2-locacao`), Carlos (`/imovel/` com finalidade e cidade), Mariely Cattoni (`POST /imoveis` com paginação e filtro de Pato Branco) e Junior Gonçalves (`/imoveis-data.js`). Leitura de cartões e links de paginação publicados no mesmo domínio; enriquecimento individual somente sob demanda.
   - **Agil REST API**: Fortun (`POST /company/{id}/search`), com paginação nativa e consulta separada de venda, locação anual e temporada. Detalhes que o site marca como protegidos exigem identificação no site original.
2. **Validação e Reconciliação Local dos Dados**:
   - **Filtro estrito de cidade**: descarta anúncios de outras cidades (ex.: Vitorino, Mariópolis, Francisco Beltrão, Pato Bragado, litoral de SC).
   - **Correção de finalidade e categoria**: detecta anúncios cadastrados incorretamente pelas imobiliárias (ex.: imóvel `"à venda"` de R$ 350 mil jogado na aba de locação; `Sobrado` cadastrado como `Casa`; `Kitnet/Studio` ou `Cobertura` cadastrado como `Apartamento`; `Sala Comercial` ou `Barracão`).
   - **Padronização de bairros de Pato Branco**: unifica grafias (`Jd. Floresta` → `Jardim Floresta`, `Dal Ross` → `Dall Ross`, `Lasalle` → `La Salle`, etc.).
3. **Sincronização Incremental de Baixo Custo & Timer Periódico**:
   - **Inicialização instantânea**: `uv run imob serve` abre o banco local `data/imob.db` na hora, **sem disparar requisições externas ao subir**.
   - **Varredura por catálogo/JSON paginado**: coleta dezenas/centenas de imóveis por requisição, **nunca** varrendo imóvel por imóvel individualmente.
   - **Timer padrão em background (`6h` TTL)**: verifica periodicamente quais imobiliárias venceram o TTL e atualiza de forma educada (rate-limit por domínio + circuit-breaker contra desativação em massa caso algum site oscile).
   - **Enriquecimento sob demanda (`POST /api/properties/{id}/enrich`)**: busca a descrição longa, taxas ocultas de condomínio/IPTU e galeria completa apenas quando você clicar no imóvel de interesse.
4. **Inteligência Financeira & Curadoria Pessoal**:
   - Cálculo automático de **Custo Mensal Estimado** (`Aluguel + Condomínio + IPTU + Seguro`) e **R$/m²**.
   - **Histórico de preços** (`price_history`) e filtro direto de **quedas de preço** (`only_price_drops=true`).
   - **Detecção e agrupamento de duplicados** entre imobiliárias diferentes (`deduplicate=true` mostra o menor valor anunciado e lista os concorrentes lado a lado).
   - **Favoritos (`★`), Descartados (`✕`) e Anotações Pessoais**, preservados entre sincronizações.

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

Reinicie o servidor para carregar as novas configurações. Os arquivos do projeto e o
pacote portátil do Windows (`dist/ImobPatoBranco-Windows-x64.zip`) foram atualizados.

Todos os comandos devem ser executados dentro da pasta `imob/`:

```bash
cd /home/htxzz77/Projects/general_topics/imob
```

### 1. Subir a API REST + Painel Web Local

```bash
uv run imob serve
```

- **Painel Web com Filtros**: <http://127.0.0.1:8000/>
- **Documentação Interativa OpenAPI (Swagger)**: <http://127.0.0.1:8000/docs>

Opções úteis:
```bash
uv run imob serve --port 8080          # Escolher outra porta
uv run imob serve --no-timer           # Desativar o timer automático em background
```

### 2. Sincronizar via Linha de Comando (Opcional)

```bash
# Atualiza apenas imobiliárias cujo cache TTL (padrão 6h) expirou
uv run imob sync

# Força atualização imediata das fontes cadastradas
uv run imob sync --force

# Sincroniza apenas imobiliárias específicas pelo slug
uv run imob sync --agency apolar --agency uniq --force
```

### 3. Ver Estatísticas do Banco Local

```bash
uv run imob stats
```

### 4. Gerar Versão Portátil para Windows (`.zip` + `.exe`)

Para empacotar o aplicativo como uma pasta portátil para Windows 10/11 (`dist/ImobPatoBranco-Windows-x64.zip`, já contendo `ImobPatoBranco.exe` e o banco inicial populado sem favoritos/anotações pessoais):

```bash
uv run python packaging/windows/build_windows_portable.py
```

Quem receber o `.zip` só precisa extrair a pasta `ImobPatoBranco` e dar **duplo clique** em `ImobPatoBranco.exe` (o servidor local inicia automaticamente e abre o painel no navegador padrão).

---

## Principais Endpoints da API REST

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/api/health` | Status do serviço |
| `GET` | `/api/stats` | Totais ativos, aluguel, venda, quedas de preço, duplicados e favoritos |
| `GET` | `/api/facets` | Contadores dinâmicos de tipos, bairros e imobiliárias (`?purpose=aluguel\|venda`) |
| `GET` | `/api/agencies` | Lista fontes cadastradas, coletores e status do último sync |
| `GET` | `/api/properties` | Busca paginada com todos os filtros combináveis + busca textual FTS5 |
| `GET` | `/api/properties/{id}` | Ficha completa, histórico de preços e anúncios equivalentes em outras imobiliárias |
| `POST` | `/api/properties/{id}/enrich` | Raspa sob demanda a página individual do imóvel (descrição completa, taxas e fotos) |
| `PATCH` | `/api/properties/{id}/state` | Salva `is_favorite`, `is_hidden` (descartado) e `user_notes` |
| `GET` | `/api/sync/status` | Progresso em tempo real da sincronização e configuração do timer |
| `POST` | `/api/sync` | Dispara sincronização em background (`{"agencies": [], "force": false}`) |

### Exemplo de Consulta via API (`curl`)

```bash
curl "http://127.0.0.1:8000/api/properties?purpose=aluguel&types=apartamento,sobrado&neighborhoods=centro,fraron&max_monthly_total=3200&min_bedrooms=2&only_with_price=true&deduplicate=true&sort=menor_custo_mensal"
```

---

## Variáveis de Ambiente (`IMOB_*`)

| Variável | Padrão | Descrição |
|---|---|---|
| `IMOB_DB_PATH` | `data/imob.db` | Caminho do banco SQLite |
| `IMOB_AUTO_SYNC_ENABLED` | `true` | Ativa/desativa o timer periódico em background no `imob serve` |
| `IMOB_SYNC_INTERVAL_HOURS` | `6.0` | TTL (horas) mínimo antes de ressincronizar automaticamente uma imobiliária |
| `IMOB_TIMER_CHECK_MINUTES` | `15.0` | Intervalo de verificação do timer em background (minutos) |
| `IMOB_PER_DOMAIN_DELAY_SECONDS` | `0.45` | Atraso mínimo entre requisições para o mesmo domínio |
| `IMOB_MAX_CONCURRENT_AGENCIES` | `6` | Máximo de imobiliárias sincronizadas simultaneamente |

---

## Qualidade de Código e Testes

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src tests
uv run pytest -v

# Opcional: verifica as 17 inclusões nos sites reais e salva diagnósticos em /tmp/imob-live-catalogs/
uv run pytest -q tests/live_catalogs.py
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

A migração `0003` cria a tabela derivada `property_matches`; grupos e pares são
recalculados com os dados locais na inicialização, após cada catálogo e após
qualquer enriquecimento. Uma nova coleta não é necessária para rever os dados já
armazenados. O parser Imonov também reconhece mais atributos com rótulos e ícones
e corrige a leitura de áreas com `m²`; esses dados adicionais entram no próximo sync.
O catálogo pode estar incompleto: baixa cobertura de características não comprova
ausência de duplicados, mesmo quando o sincronizador retorna sucesso.

As tags **Aceita permuta** e **Estuda proposta** aparecem nos cartões e detalhes
quando indicadas nos dados estruturados suportados ou na descrição coletada.
Ausência da tag não significa recusa. A leitura de texto considera negações;
novos termos ficam disponíveis na próxima sincronização ou no enriquecimento.
Clique numa miniatura da galeria para ampliar, navegar pelas setas e fechar com Esc.

Anúncios sem preço são incluídos por padrão, inclusive ao limpar filtros.
`only_with_price=true` restringe a busca quando desejado. As opções de incluir
anúncios descartados (`include_hidden=true`) e inativos (`include_inactive=true`)
permitem consultar o histórico local. Limites de preço/área ainda podem excluir
anúncios que não informam esses valores.

O banco contém apenas o que foi coletado dos catálogos públicos e aceito pelo filtro
de cidade. Imóveis identificados como fora de Pato Branco são descartados antes da
persistência; incluir inativos não os recupera. Status de sincronização bem-sucedida
não comprova que todo o estoque interno da imobiliária foi publicado. Um dump de
todo o estoque, incluindo anúncios não publicados, exige exportação autorizada do
CRM de cada imobiliária. A coleta atual não acessa esse estoque privado.

## Biblioteca de mapas e quadras

O botão **Mapas e quadras** organiza os links municipais em cinco faixas de 1 a
2500. Consulte uma quadra para abrir a pasta oficial correspondente e adicionar seu
PDF. Os mapas adicionados ficam em `data/maps/quadras/0928.pdf`, por exemplo, e
podem ser visualizados dentro do painel. Cada arquivo tem limite de 20 MB.
Os links das pastas não significam que todos os PDFs já foram baixados.

Para importar uma pasta de mapas públicos baixados do acervo municipal:

```bash
uv run imob maps-import /caminho/para/pasta-de-mapas
```

O importador reconhece arquivos como `0928.pdf` ou `Quadra_0928.pdf`, inclusive em
subpastas. Nomes ambíguos são informados como não importados; duas entradas para a
mesma quadra interrompem a importação antes de gravar. Os arquivos são armazenados
por quadra, sem criar base de proprietários ou consultar cadastros de IPTU.

Endpoints: `GET /api/maps?quadra=928`, `GET /api/maps/quadras/928/pdf` e
`PUT /api/maps/quadras/928/pdf` (corpo `application/pdf`). Não há consulta automática
por proprietário, CPF, débitos ou enumeração de cadastros municipais. Quadra, lote,
cadastro imobiliário e matrícula são identificadores diferentes; nenhuma fórmula
entre eles foi confirmada. No formulário municipal observado em novembro de 2026,
`tipo_filtro=1` é CPF/CNPJ, `1001` é cadastro imobiliário e `1002` é cadastro econômico;
o formulário não oferece filtro por quadra/lote. O limite de 15 caracteres do campo
não demonstra ordem, distribuição ou limite numérico dos cadastros existentes.
