# Guardrails do núcleo local

O MCP não oferece execução SQL, shell, chamadas HTTP, exclusão de arquivos,
conexão a bancos ou encaminhamento para outros MCPs. A descoberta de uma ferramenta
não autoriza nem executa essa ferramenta. Texto de fontes permanece dado não confiável.
Não usamos uma blacklist de palavras SQL como substituto de permissões reais.

## Compartilhamento com o modelo

`ae-agent --root /workspace serve` disponibiliza apenas `scan_environment` e
`environment_status`, com contagens, sem nomes de arquivos, configurações ou conteúdo.
O scan ainda indexa documentos localmente e grava o histórico.

Para autorizar o envio de evidências ao host/modelo e a criação de propostas:

```bash
ae-agent --root /workspace serve --allow-content
```

Essa opção libera `search_sources`, `knowledge_graph`, `revalidation_queue` e
`propose_claim`. A autorização vale para todo o workspace e a vida do processo.
Não há ferramenta MCP para alterar a política ou aprovar regras. Chamadas bloqueadas
por essa política geram eventos locais sem registrar os argumentos.
A CLI continua sendo uma interface local privilegiada; ela pode consultar conteúdo
sem essa opção. Não dê shell irrestrito ao modelo se precisar impedir esse acesso.

## Proteções implementadas

- Redação de padrões conhecidos de credenciais, chaves privadas, emails e CPF
  formatado na ingestão de fontes e na saída da CLI/MCP, incluindo registros antigos.
- Metadados de propostas, inventário e revisão também são filtrados. Evidências
  são comparadas com a versão redigida; filtros podem reduzir a fidelidade do texto.
- Leitura limitada a arquivos regulares de até 1 MB, sem seguir symlinks em qualquer
  componente do caminho. Pastas comuns de credenciais não são varridas.
- Banco local com modo `0600`, diretório novo com `0700`; caminhos de armazenamento
  com symlinks e arquivos com múltiplos hard links são recusados.
- Allowlist fixa de ferramentas, argumentos validados e limitados a 16.384 caracteres,
  respostas de ferramentas limitadas a 128 KB e frames de entrada limitados a
  1 milhão de caracteres. Frame excedido encerra o servidor; erros operacionais MCP
  não incluem conteúdo de exceções.
- Propostas não viram regras validadas automaticamente. Revisão continua na CLI.

## Limites e implantação

Filtros heurísticos não são DLP completo: nomes, telefones, dados comerciais,
credenciais desconhecidas e segredos ofuscados podem passar. `--allow-content`
autoriza a saída desses dados também. Use um workspace com documentos curados,
`.aeignore` e permissões mínimas antes de indexar. Exclusão do scan não apaga histórico;
a redação de saída não remove dados antigos do arquivo SQLite ou seus backups.

A versão atual exige Linux/macOS ou WSL para leitura segura de arquivos. Windows
nativo é recusado; as configurações dos clientes precisam iniciar o processo nesse
ambiente. Não há autenticação de reviewer, ACL por usuário, criptografia em repouso,
isolamento de processos, garantia contra ataques de outro processo local com o mesmo
usuário ou limites globais de tamanho/tempo da varredura e do grafo.

Esses controles se aplicam somente ao nosso núcleo. O harness pode possuir shell,
outros MCPs ou credenciais fora dele. Restrinja essas ferramentas no host e use
credenciais read-only no warehouse. Futuros conectores/executores devem ter escopos
explícitos, autorização independente de instruções do modelo e testes negativos
antes de serem habilitados. O conhecimento aprendido nunca deve ampliar permissões.
