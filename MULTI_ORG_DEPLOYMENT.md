# Acesso multi-organização — implantação inicial

## Recursos provisionados
- Projeto Supabase dedicado: `GERADOR DE DDS` (`iqxdkmcwztcpdzvdnsts`), região São Paulo, estimativa consultada: US$ 0/mês no momento da criação.
- Edge Function `org-access`, JWT obrigatório, versão 2.
- Esquema: organizações, membros, convites, auditoria; convite com hash de token, uso único, e-mail normalizado e expiração; aceite transacional.
- RLS ativado e políticas de leitura limitadas à organização. Escritas privilegiadas feitas pela Edge Function com service role; a chave de serviço não deve ser enviada ao navegador.
- Projeto Missão Em Dia e serviço Render de produção não foram modificados.

## Limite importante
Esta é a base de infraestrutura. A aplicação Flask atual ainda usa autenticação Basic compartilhada e grava colaboradores/histórico em `state.json`/SQLite. Ela ainda não associa os dados operacionais a `organization_id`, não oferece tela de cadastro/aceite de convite e não consome a Edge Function. Portanto, **não habilitar usuários externos nem anunciar multi-organização como concluído** até completar a integração do app e os testes ponta a ponta.

## Ações da Edge Function
POST `/functions/v1/org-access` com JWT Supabase:
- `{ "action": "create_org", "name": "Organização Teste" }` — cria a primeira organização do usuário autenticado e atribui owner, se ele ainda não pertence a organização.
- `{ "action": "invite", "organization_id": "...", "email": "...", "role": "member", "valid_days": 7 }` — somente owner/admin; gera token aleatório, armazena hash e usa o fluxo de convite de e-mail do Supabase Auth.
- `{ "action": "accept_invite", "token": "..." }` — exige sessão autenticada e e-mail correspondente ao convite.

## Validação pendente antes de produção
1. Configurar e testar entrega de e-mail e URL de redirecionamento em Auth > URL Configuration.
2. Criar duas contas de teste e duas organizações fictícias; verificar isolamento por usuário e por organização.
3. Integrar autenticação ao Flask, remover Basic Auth compartilhada para modo multi-tenant, implementar UI de convite/aceite e associar todos os dados (colaboradores, temas privados, histórico, configurações e arquivos) a `organization_id`.
4. Garantir que geração de PDF e downloads validem organização e associação do recurso no servidor.
5. Automatizar testes de convite válido, reutilizado, expirado, revogado, e-mail divergente, usuário não-admin, acesso cruzado e rollback.
6. Executar advisories de segurança/performance e corrigir alertas relevantes.
7. Somente após aprovação dos testes, configurar deploy de preview; não mesclar esta branch automaticamente em `main`.

## Segurança e custo
- Não guardar service-role key em GitHub, HTML, navegador ou variáveis expostas ao cliente.
- A cotação inicial do projeto foi US$ 0/mês no momento da criação. Confirmar a página de uso antes de aumentar recursos; não contratar plano pago sem autorização.
- Não há usuários externos convidados nesta etapa. O envio real de convite ainda depende da configuração/validação do provedor de e-mail do projeto.
