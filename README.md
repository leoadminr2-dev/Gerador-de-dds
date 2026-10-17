# Gerador de DDS — versão consolidada 1.2.2

Esta versão preserva as funções existentes e utiliza como base visual o PDF institucional enviado para revisão: **Lista de Presença — FR-SGQ-001/AD-CO, Revisão 03**. O gerador preenche os campos dinâmicos sobre o próprio formulário para preservar o posicionamento das tabelas e linhas.

## Regras do PDF
- Cabeçalho, logo, caixa vazia para Treinamento/DDSSMA/Evento/Reunião/Outros, fontes, bordas, títulos e proporções seguem o formulário de referência.
- Local/Cliente: **GALEÃO**. Empresa: **TOP SERVICE - LIMPEZA**. Área: **LIMPEZA**.
- Unidade obrigatória selecionável: **GIG, TECA, UNITED ou GATE GOURMET**. A lista inclui somente colaboradores da unidade e do turno selecionados.
- Turno apresentado somente para a opção escolhida: Manhã/Diurno, Tarde/Vespertino ou Madrugada/Noturno. Responsáveis aplicáveis: Diurno — Orlando Barbosa e Evelyn Montovani; Vespertino — Kayo Lessa e Bruna Fraga; Noturno — Maria do Socorro.
- Nº Participantes sempre em branco; Duração sempre **01:00**. O campo de responsabilidade contém apenas os nomes do turno escolhido; QSSMA, Operação, Outros e Rubrica ficam disponíveis para preenchimento manual.
- A lista contém os nomes do turno/unidade escolhidos + **cinco linhas extras vazias**, numeradas em sequência. A paginação cresce conforme o necessário.
- Todas as páginas têm “PROIBIDA a reprodução não autorizada” e “Página X de N” no rodapé.

## Execução
Extraia o ZIP inteiro para uma pasta local e execute `run_windows.bat`, ou escolha um dos inicializadores de compatibilidade incluídos. Os dados são mantidos na pasta local da aplicação.

## Validação
O pacote foi conferido com 41 testes automatizados, verificações sintáticas do Python e JavaScript e geração de PDFs de teste. O uso e a impressão devem ser confirmados no Windows de destino antes da operação real.


## Geração em massa (1.2.2)

A geração em massa aceita vários temas da biblioteca, turnos e unidades e reúne os documentos em um único PDF. A data é opcional e fica em branco no formulário quando não informada.
No formulário principal, clique em **Gerar DDS em massa**. Marque um ou mais turnos e unidades, ou use “Marcar todos”/“Todas”. A aplicação cria um PDF separado por combinação selecionada que tenha colaboradores cadastrados; combinações vazias são listadas como ignoradas. Tema, data e conteúdo são compartilhados no lote. Abra e confira cada PDF antes de imprimir.


## Hospedagem web (1.2.2)
O pacote inclui `render.yaml`, `Procfile` e `DEPLOY_HOSTING.md` para preparar uma publicação protegida. A hospedagem ainda precisa ser criada na conta do responsável; este ZIP, sozinho, não publica o sistema. A configuração proposta utiliza autenticação administrativa obrigatória e armazenamento persistente. Revise os custos do provedor antes de ativar o serviço.

A ação de pesquisa tenta consultar novamente MTE/Fundacentro e, quando pertinente, Anvisa. Se não conseguir obter fontes atuais, informa erro e não retorna conteúdo offline como se estivesse validado naquela sessão.

## Painel Administrativo Central
Consulte `ROADMAP_PAINEL_ADMIN_CENTRAL.md` para o plano de gestão conjunta do Gerador de DDS, Missão Em Dia, Super Gerador e outros projetos. A integração real do painel ainda será construída por módulos, sem simular métricas ou conexões que não estejam ativas.
