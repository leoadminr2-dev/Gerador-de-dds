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
