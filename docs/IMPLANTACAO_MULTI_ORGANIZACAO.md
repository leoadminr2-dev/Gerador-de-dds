# Implantação multi-organização — Gerador de DDS

## Estado desta branch
Branch: `feature/multi-org-access`

Esta branch contém uma migração inicial para a estrutura de organizações, membros, convites e auditoria em um projeto Supabase dedicado. **Ainda não é uma implantação funcional ponta a ponta**: não há projeto Supabase dedicado confirmado, endpoints seguros de convite ainda precisam ser implementados, a aplicação Flask ainda usa o modelo de autenticação administrativa atual e o fluxo não está conectado à interface.

## Arquitetura proposta
- Interface web do Gerador de DDS hospedada no Render.
- Supabase Auth para identidade e confirmação do e-mail.
- Banco Supabase dedicado ao Gerador de DDS, separado do projeto MISSÃO EM DIA.
- RLS no PostgreSQL para limitar consultas à organização do usuário.
- Edge Functions autenticadas para criar organizações, enviar convites, aceitar/revogar convites e registrar auditoria.
- E-mail de convite com token aleatório de uso único; persistir somente o hash; expiração máxima de 7 dias.
- A aplicação deve obter a organização ativa a partir do vínculo autenticado, nunca confiar apenas em `organization_id` enviado pelo navegador.

## Migração incluída
`supabase/migrations/202610200001_multi_organization_access.sql`

A migração cria as tabelas com prefixo `dds_`, ativa RLS e impede leitura direta de convites e auditoria pelo navegador. Ela deve ser aplicada **somente a um projeto Supabase dedicado de desenvolvimento**. Não aplicar no projeto MISSÃO EM DIA.

## Critérios de aceite antes de publicar
1. Login e confirmação de e-mail funcionam.
2. Criar organização com prazo de 7 dias e primeiro administrador em transação confiável.
3. Convidar por e-mail; rejeitar endereço diferente, token reutilizado, expirado ou revogado.
4. Testar duas organizações com dados fictícios e o mesmo ID de recurso; não pode haver leitura ou sobrescrita cruzada.
5. Usuário comum não administra membros; administrador só administra sua organização.
6. Após expiração, operações protegidas são bloqueadas sem apagar dados.
7. Falha no envio de e-mail não deixa convite ativo.
8. Executar testes HTTP contra ambiente de desenvolvimento, revisão de políticas RLS e teste de segurança.
9. Preservar o modo local e testar geração de PDF, importação de colaboradores e impressão.
10. Só então preparar merge/publicação; não substituir o serviço operacional automaticamente.

## Bloqueios para implantação real
- Confirmar o workspace Render correto antes de criar/alterar serviços.
- Confirmar a organização Supabase onde o projeto dedicado será criado e conferir o custo cotado.
- Criar e validar projeto Supabase dedicado.
- Implementar e testar as Edge Functions e ligar a autenticação/organização ao Flask e à interface.
- Configurar segredos de serviço no provedor, nunca no repositório.
- Executar os testes ponta a ponta e revisar custos e persistência.

Nenhum banco, segredo, dado real ou serviço de produção foi alterado por esta branch.
