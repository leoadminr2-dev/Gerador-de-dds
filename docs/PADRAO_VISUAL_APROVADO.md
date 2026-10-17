# Padrão visual aprovado — Gerador de DDS

**Versão de referência:** 1.2.3  
**Aprovado pelo responsável do projeto:** 17/10/2026

## Cabeçalho principal
- Exibir somente a marca **DDS** no canto superior esquerdo da barra lateral.
- Remover a identificação da operação (por exemplo, “Operação Galeão · GIG”) da área de marca.
- No cabeçalho principal, mostrar **“Gerador de DDS”** em tamanho grande e negrito.
- Logo abaixo, exibir **“Facilitando seu dia.”** em fonte menor.
- Não mostrar a palavra “Operação” como breadcrumb no topo.
- Remover o cartão decorativo “Diálogo Diário de Segurança” da tela principal.

## Indicador de ambiente
- Se a aplicação conseguir consultar o endpoint `/api/health`, mostrar **“Ambiente Online Ativado”** com indicador verde.
- Se o navegador estiver sem conexão ou o endpoint não responder, mostrar **“Ambiente Offline Ativado”** com indicador vermelho.
- Atualizar o indicador quando os eventos de conexão do navegador mudarem.
- O indicador descreve a disponibilidade da aplicação/servidor; não garante que cada serviço externo de pesquisa esteja disponível.

## Consistência e preservação
- Manter a navegação, os formulários, os recursos de geração de PDF e as demais funcionalidades existentes.
- Aplicar o mesmo padrão responsivo no computador e no celular.
- Não reintroduzir a operação no cabeçalho nem o cartão removido sem autorização explícita.


## Regra inviolável do documento PDF do DDS
- O conteúdo do DDS deve permanecer estritamente dentro da área útil e do espaço destinado a cada campo.
- Nenhum texto, título, tabela, lista, assinatura ou outro conteúdo pode ultrapassar as margens, invadir áreas reservadas ou ser cortado.
- Não alterar, deslocar, redimensionar, redesenhar ou desconfigurar margens, cabeçalho, rodapé, identificação visual, linhas, quadros, campos, paginação ou qualquer parte da estrutura do modelo institucional aprovado.
- Preservar integralmente o layout original do modelo DDS. A geração deve adaptar e ajustar o conteúdo ao espaço existente — nunca adaptar o modelo para caber o conteúdo.
- Se o conteúdo não couber no espaço disponível, ajustar a composição do conteúdo dentro das regras do modelo ou distribuí-lo de forma controlada em páginas adicionais, sem sobrepor nem deformar a estrutura institucional.
- Antes de disponibilizar o PDF, validar visualmente que todo o conteúdo está dentro das margens e que o modelo permanece intacto.
- Esta regra tem prioridade sobre qualquer melhoria estética, formatação automática ou expansão de conteúdo.


## Personalização permitida aos usuários — estrutura protegida
- A estrutura visual do modelo DDS é fixa e não editável pelos usuários comuns.
- A interface de personalização deverá oferecer somente controles autorizados: (1) selecionar/enviar a logo da empresa ou setor e (2) escolher entre temas predefinidos.
- A logo do usuário deve ocupar exclusivamente a área reservada no canto superior esquerdo do cabeçalho do DDS. Deve respeitar limites de tamanho e proporção, sem deslocar, cobrir ou redimensionar os demais elementos.
- Os temas podem alterar somente as propriedades visuais explicitamente permitidas e previamente definidas. Não podem alterar margens, dimensões, posições, fontes estruturais, cabeçalho, rodapé, campos, linhas, tabelas, identificação institucional ou paginação.
- Não oferecer editor livre de layout, arrastar e soltar elementos, edição de margens, fontes estruturais, posições, quadros ou rodapé.
- A geração do PDF deve usar um modelo controlado pelo sistema e validar o conteúdo e a logo dentro das áreas permitidas antes de gerar o arquivo.
- A personalização não pode alterar o modelo-base compartilhado com outros usuários; as preferências devem ser específicas de cada usuário/instalação.
- Nota de segurança: em uma versão distribuída para download, código e arquivos locais podem ser modificados por alguém com conhecimento técnico. A interface pode bloquear alterações comuns, mas a proteção mais forte exige geração/validação no servidor ou distribuição de componentes compilados e assinados. Não prometer impossibilidade absoluta de modificação em uma cópia local.
