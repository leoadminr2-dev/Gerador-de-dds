# Painel Administrativo Central — plano mestre dos aplicativos

## Decisão de arquitetura
Manter um painel administrativo único para visão geral, com módulos separados por aplicativo. Não misturar bancos operacionais nem dados pessoais de um produto com outro. Cada integração deve ter credenciais próprias, permissões mínimas, logs e controles de acesso.

## Módulos planejados
1. **Gerador de DDS** — estado da hospedagem, versão, último deploy, pesquisa oficial, disponibilidade, colaboradores/unidades, histórico de geração e alertas técnicos. A planilha de colaboradores e os PDFs ficam no armazenamento privado do próprio serviço.
2. **Missão Em Dia** — divulgação, famílias em teste, início/fim dos 30 dias, avisos de 5/3/1 dia, estado de acesso e regularização. Após expirar, manter o responsável com acesso para consultar/regularizar e limitar somente recursos protegidos da criança; preservar missões, evidências, histórico e XP.
3. **Super Gerador** — versões, build/testes, bloqueios e próximas tarefas.
4. **Fotos em Dia** — fase de desenvolvimento, testes e decisões de segurança para backup/exclusão.
5. **Projetos futuros** — cadastro independente com nome, status, responsável, versão e link.

## Tela inicial do painel
- Resumo dos projetos: Em desenvolvimento, Em teste, Publicado, Atenção necessária.
- Próximas pendências prioritárias e data da última atualização.
- Indicadores específicos por projeto, sem inventar métricas quando não houver integração real.
- Links diretos para cada aplicativo e respectivo painel de operação.

## Regras de segurança
- Autenticação forte e acesso administrativo restrito.
- Segredos exclusivamente em variáveis de ambiente ou cofre de segredos, nunca no frontend/repositório.
- Separação de dados por aplicativo e menor privilégio.
- Registro de alterações administrativas e backups antes de mudanças destrutivas.
- O painel mostra apenas dados realmente conectados; não simular que métricas/ações estão integradas antes de implementá-las e testá-las.

## Próxima sequência
1. Hospedar e validar o Gerador de DDS.
2. Definir o contrato de integração do painel com o serviço DDS e com Supabase do Missão Em Dia.
3. Construir o painel central com autenticação e módulos isolados.
4. Integrar um módulo por vez, começando por status/deploy do DDS e status do período de teste do Missão Em Dia.
