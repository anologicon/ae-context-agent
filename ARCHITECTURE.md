# Arquitetura e plano de evolução

## Objetivo

Um colaborador de Analytics Engineering que conhece capacidades do ambiente, mantém
conhecimento rastreável do negócio e identifica quando esse conhecimento precisa mudar.

## Decisões do MVP

1. **LLM no hospedeiro.** MCP permite começar com agentes existentes e diferentes modelos.
2. **SQLite antes de banco de grafos.** Nós e relações são explícitos; evidências referenciam
   revisões. O armazenamento pode mudar sem alterar o significado dos registros.
3. **Proposta não é validação.** `proposed → validated/rejected`; mudança ou remoção da fonte
   leva propostas/validações a `stale`. Nova evidência gera nova proposta.
4. **Revisões preservadas.** Hashes são calculados sobre texto após redação; não representam
   um checksum do documento remoto original. Datas são UTC.
5. **Descoberta não executa.** A configuração de uma ferramenta não é autorização para usá-la.
6. **Fontes locais isoladas.** Sem acesso de equipe ou ACL remota nesta fase.

## Limites importantes do modelo inicial

- Nós de afirmações aceitam tipos explícitos; afirmações antigas continuam `concept`.
  A migração não tenta adivinhar tipos nem unir nós existentes automaticamente.
- Identidade de nós usa tipo + rótulo + workspace; sinônimos ainda criam nós distintos.
- Cada afirmação tem uma evidência principal; múltiplas evidências e seus conflitos são futuros.
- O evento de mudança marca como stale as afirmações diretamente apoiadas na fonte.
  Dependências revisadas geram uma fila transitiva; afirmações de outras fontes não
  são marcadas como falsas ou obsoletas apenas por estarem no percurso.
- Não existe promoção automática por confiança nem validação independente da regra.
- A busca devolve no máximo 30 fontes e um preview inicial limitado. Paginação e recuperação
  de trechos completos por identificador precisam preceder usos com acervos grandes.
- O schema está na versão 2, com migração aditiva. Eventos antigos não são reprocessados.
- Encerrar a fila não comprova execução de testes: o responsável registra resultado e
  evidência. Ainda não há executor automático de verificações ou reabertura por mudança
  de uma fonte usada apenas como evidência de encerramento.

## Próximas entregas, em ordem

### 1. Revalidação com dependências — primeira versão implementada

Tipos de nós, relações `depends_on`, fila e caminhos explicáveis estão implementados.
Ciclos, isolamento de escopo e substituição de itens pendentes estão testados.
Ainda faltam múltiplas evidências, retirada explícita de dependências antigas e
execução/proposição automática dos testes de revalidação.

### 2. Um conector real com ACL

Escolher Drive ou Confluence. Usar IDs estáveis, revisão do provedor, atualização incremental,
exclusão/tombstones e permissões no registro e na busca. Não indexar dados de acesso restrito
em uma memória compartilhada antes de haver essas garantias.

### 3. Cliente MCP e verificação de capacidades

Conectar apenas servidores autorizados, enumerar ferramentas, separar detected/verified/
unavailable e registrar schema e validade. Começar com catálogo de dados read-only.

### 4. Extração de conceitos pelo agente

Contrato estruturado para conceitos, hipóteses, evidências e conflitos. Corpus de avaliações
com sugestões versus decisões, métricas homônimas e vigências diferentes. Comparar com
baseline sem memória para demonstrar benefício.

### 5. Conversas e aprendizado de procedimentos

Threads selecionadas, decisões confirmadas e responsáveis. Skills com histórico e rollback;
uma alteração só representa melhoria após avaliações. Acesso a conversa não autoriza envio
de mensagens. Nunca promover repetição de uma hipótese a evidência independente.

## Referências estudadas

- nao: descoberta MCP sob demanda e adaptadores de sincronização.
- Wren: conhecimento durável separado de índices e observação de mudanças.
- Hermes: revisão pós-tarefa e histórico reversível de skills.
- dbt Agent Skills: granularidade e critérios de aceitação antes de implementar modelos.

Repositórios de referência:

- https://github.com/getnao/nao
- https://github.com/Canner/WrenAI
- https://github.com/NousResearch/hermes-agent
- https://github.com/dbt-labs/dbt-agent-skills
