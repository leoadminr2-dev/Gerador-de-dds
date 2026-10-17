# Registro geral dos projetos — 17/10/2026

## Decisão principal
O Gerador de DDS é prioridade imediata. A versão local que o usuário confirma funcionar no Windows 7 deve ser preservada; não recomeçar nem remover recursos funcionais. O foco é hospedagem web para acesso remoto e correção da validação online de conteúdo dos temas.

## Gerador de DDS — escopo imediato
- Nome do produto: **Gerador de DDS**.
- Idealizador indicado na página Sobre: Leonardo Moreira.
- Preservar geração do PDF institucional, importação da planilha de colaboradores, filtros por unidade/turno, histórico, backup/restauração e geração em massa.
- Unidades atuais: GIG, TECA, UNITED e GATE GOURMET.
- Responsáveis por unidade informados pelo usuário: UNITED — Wilker Maria; GATE GOURMET — Márcia Faustino; TECA/Terminal de Carga — Pedro Paulo. GIG continua a confirmar.
- Os dados de colaboradores serão atualizados quando o usuário fornecer a nova planilha. Não inventar nem substituir nomes sem a planilha.
- Fontes primárias de conteúdo: Ministério do Trabalho e Emprego (MTE) e Fundacentro; Anvisa como fonte complementar quando pertinente a saneantes, limpeza, saúde e aeroportos.
- Cada ação “Pesquisar na internet e gerar conteúdo” deve tentar consulta ao vivo. Se a pesquisa falhar ou não houver evidência textual, informar falha e não apresentar cache/rascunho genérico como conteúdo verificado naquela sessão.
- A publicação não deve ocorrer sem autenticação administrativa, HTTPS e armazenamento persistente.
- Pacote de trabalho preparado: versão 1.2.2-web-ready; `render.yaml`, `Procfile`, `DEPLOY_HOSTING.md` e armazenamento esperado em `/var/data`.
- Estado real: preparado para deploy, mas **ainda não publicado**. Precisa de conta/serviço de hospedagem, repositório autorizado, senha administrativa forte configurada como segredo e smoke tests no endereço HTTPS.
- A configuração Render proposta usa plano Starter e disco persistente; revisar custo antes de ativar.

## Painel Administrativo Central — decisão do usuário
Criar um único painel administrativo para acompanhar e gerenciar todos os aplicativos, sem misturar os dados operacionais dos produtos.

### Módulos previstos
1. Gerador de DDS: publicação, versão, estado do serviço, fontes/pesquisa, importação da planilha e falhas técnicas.
2. Missão Em Dia: divulgação, famílias em teste, período de 30 dias, alertas 5/3/1 dias antes do vencimento, estado dos acessos e regularização. Após vencer, responsável deve manter acesso para consulta/regularização e apenas funções protegidas do perfil infantil ficam limitadas; preservar missões, evidências, histórico e XP.
3. Super Gerador: versões, builds, testes e pendências.
4. Fotos em Dia: progresso, testes e decisões de segurança para backup/exclusão.
5. Novos aplicativos: adicionar módulos independentes conforme os projetos avancem.

### Regras do painel
- Uma visão geral de status, pendências, versões e links por projeto.
- Autenticação forte, permissões mínimas, segredos fora do frontend/repositório, logs de ações e backups antes de alterações destrutivas.
- Não exibir métricas falsas nem alegar integração antes de ligar e testar a fonte de dados real.
- Integrar um módulo por vez; primeiro DDS e estado do período de teste do Missão Em Dia.

## Estado de testes do pacote 1.2.2
- `python -m py_compile app.py`: aprovado.
- `node --check web/app.js`: aprovado.
- Testes: 46 executados, zero falhas/erros, 1 teste de stress de paginação ignorado nesta execução por exceder o limite de tempo do ambiente; o teste permanece no pacote para validação local completa.
- Acesso real no provedor e pesquisa online ao vivo não foram validados porque ainda não há deploy.

## Próximas etapas
1. Autorizar o provedor de hospedagem e o repositório para publicação do Gerador de DDS.
2. Configurar a senha secreta do administrador e testar saúde, autenticação, importação, geração PDF, backup/restauração e pesquisa online.
3. Atualizar a planilha com os colaboradores do TECA, UNITED e GATE GOURMET.
4. Construir o Painel Administrativo Central com módulos isolados e integrar primeiro DDS e Missão Em Dia.

## Princípio de trabalho
Preservar recursos já validados, não reabrir problemas resolvidos sem evidência, avançar em etapas e distinguir claramente entre “preparado”, “implementado”, “testado” e “publicado”.
