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



## Biblioteca de temas DDS (catálogo ampliado)

A biblioteca online é carregada pela API `/api/topics` a partir do catálogo incluído no código. O catálogo consolidado contém **163 títulos únicos**, incluindo prevenção de acidentes, limpeza aeroportuária, produtos químicos, EPI, ergonomia, circulação operacional, riscos biológicos, emergência, saúde ocupacional, fatores psicossociais e temas relacionados às Normas Regulamentadoras.

Os títulos foram organizados com referência temática às Normas Regulamentadoras vigentes do Ministério do Trabalho e Emprego e às publicações de Segurança e Saúde no Trabalho da Fundacentro. Referências oficiais para consulta e atualização:
- https://www.gov.br/trabalho-e-emprego/pt-br/assuntos/inspecao-do-trabalho/seguranca-e-saude-no-trabalho/ctpp-nrs/normas-regulamentadoras-nrs
- https://www.gov.br/fundacentro/pt-br/centrais-de-conteudo/biblioteca/publicacoes-institucionais

Os DDS são rascunhos de orientação geral: a liderança deve conferir os procedimentos locais, rótulos/FDS, avaliação de riscos e regras operacionais antes de utilizar ou imprimir. A biblioteca inicial é recriada pelo aplicativo após a inicialização; temas cadastrados manualmente continuam sujeitos à persistência do armazenamento configurado na hospedagem.

## Hospedagem web (1.2.2)
Esta branch prepara autenticação por conta individual no modo hospedado, período de teste de 30 dias, painel administrativo e avisos de vencimento. A publicação ainda depende de configurar os segredos `DDS_ADMIN_EMAIL`, `DDS_ADMIN_PASSWORD` e `DDS_SESSION_SECRET` no provedor e de configurar SMTP para enviar e-mails. O serviço usa `/var/data` para persistência; não remover o disco nem mudar o caminho sem backup. Confirme o status do deploy, a proteção HTTPS, os testes de acesso e os custos vigentes antes do uso operacional. A ferramenta continua sujeita à validação da liderança, TI e Segurança/QSSMA e não deve ser apresentada como homologada sem aprovação formal.

A ação de pesquisa tenta consultar novamente MTE/Fundacentro e, quando pertinente, Anvisa. Se não conseguir obter fontes atuais, informa erro e não retorna conteúdo offline como se estivesse validado naquela sessão.

## Painel Administrativo Central
Consulte `ROADMAP_PAINEL_ADMIN_CENTRAL.md` para o plano de gestão conjunta do Gerador de DDS, Missão Em Dia, Super Gerador e outros projetos. A integração real do painel ainda será construída por módulos, sem simular métricas ou conexões que não estejam ativas.
