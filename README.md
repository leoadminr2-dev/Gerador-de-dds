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

Os títulos foram organizados com referência temática às Normas Regulamentadoras vigentes do Ministério do Trabalho e Emprego e às publicações de Segurança e Saúde no Trabalho da Fundacentro. Os temas pesquisados passam a exibir a origem e as referências HTTPS aproveitáveis registradas na pesquisa; rascunhos criados quando os buscadores falham são identificados como não verificados, e não são apresentados como pesquisa online confirmada.

Referências oficiais para consulta e atualização:
- https://www.gov.br/trabalho-e-emprego/pt-br/assuntos/inspecao-do-trabalho/seguranca-e-saude-no-trabalho/ctpp-nrs/normas-regulamentadoras-nrs
- https://www.gov.br/fundacentro/pt-br/centrais-de-conteudo/biblioteca/publicacoes-institucionais

Antes de salvar um tema novo ou editado, o aplicativo executa uma pré-conferência usando a mesma rotina de medição do campo de conteúdo do PDF. Se o texto ultrapassar o espaço disponível, o salvamento é recusado para revisão manual; nenhuma frase é cortada automaticamente e o formulário institucional não é alterado. O gerador mantém sua validação final no momento de emitir o PDF.

Os DDS são rascunhos de orientação geral: a liderança deve conferir os procedimentos locais, rótulos/FDS, avaliação de riscos e regras operacionais antes de utilizar ou imprimir. A API informa se a biblioteca está no computador local ou no servidor. A disponibilidade compartilhada e a preservação após reinicializações dependem de configurar o armazenamento persistente da hospedagem (por exemplo, DDS_DATA_DIR=/var/data no Render).

## Hospedagem web (1.2.2)
O serviço web do Gerador de DDS está configurado no Render a partir deste repositório. A aplicação usa autenticação administrativa obrigatória em modo hospedado, verificação de saúde em `/healthz` e armazenamento persistente montado em `/var/data`. A variável `DDS_ADMIN_PASSWORD` deve permanecer definida como segredo no painel do Render e ter pelo menos 16 caracteres; não coloque credenciais no repositório. Antes de uso operacional, confirme a implantação atual, o acesso autorizado, a persistência dos dados e os custos do provedor. A ferramenta continua sujeita à validação da liderança, TI e Segurança/QSSMA e não deve ser apresentada como homologada sem aprovação formal.

A ação de pesquisa tenta consultar novamente MTE/Fundacentro e, quando pertinente, Anvisa. Se não conseguir obter fontes atuais, informa erro e não retorna conteúdo offline como se estivesse validado naquela sessão.

## Painel Administrativo Central
Consulte `ROADMAP_PAINEL_ADMIN_CENTRAL.md` para o plano de gestão conjunta do Gerador de DDS, Missão Em Dia, Super Gerador e outros projetos. A integração real do painel ainda será construída por módulos, sem simular métricas ou conexões que não estejam ativas.

## Padrao oficial do quadro de responsaveis

A numeracao dentro dos quadradinhos à esquerda, o espaçamento dos nomes, os alinhamentos e a ausência de fundo seguem o [padrão aprovado do Gerador de DDS](docs/PADRAO_RESPONSAVEIS_DDS.md), compartilhado por todos os turnos e unidades e protegido por testes automáticos.
