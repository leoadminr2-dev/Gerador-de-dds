# Multi-organização — estado do ambiente de desenvolvimento

Data: 2026-10-20

## Estado confirmado
- Projeto Supabase dedicado: `iqxdkmcwztcpdzvdnsts` (GERADOR DE DDS), região São Paulo, estado ACTIVE_HEALTHY.
- Migrações aplicadas: estrutura de organizações, membros, convites, auditoria e endurecimento de RLS.
- Edge Function `org-access`: JWT obrigatório; versão de desenvolvimento atualizada para validar origem, não usar URL de produção como fallback e recusar envio de convite enquanto `APP_BASE_URL` não estiver configurada.
- Produção Render existente: `https://gerador-de-dds.onrender.com`; não foi alterada nem recebeu deploy.

## Decisões de segurança
- Nenhuma chave service-role é incorporada ao cliente; a Edge Function lê o segredo do ambiente.
- Convites guardam hash SHA-256 do token, têm validade limitada (1–30 dias; padrão 7), verificam papel de administrador e registram auditoria.
- CORS usa origem exata configurada em `APP_BASE_URL`; não há fallback para o site público.
- Se a URL do app não estiver configurada, a ação de convite retorna `503 app_base_url_not_configured` em vez de mandar um link que leve à produção ou a um endereço incorreto.
- Alertas do linter para funções `SECURITY DEFINER` foram analisados: a função de aceite precisa executar sob identidade autenticada e as funções auxiliares são usadas pelas políticas RLS. Manter apenas porque a lógica valida `auth.uid()`, usa `search_path` fixo e não concede acesso aos dados de outra organização por si só; revisar de novo antes da publicação.

## Bloqueios honestos
- O frontend Flask atual ainda usa autenticação administrativa HTTP Basic global e arquivos JSON/SQLite, e seus endpoints de colaboradores, temas, histórico e PDFs não estão todos isolados por `organization_id`. Portanto, não é seguro habilitar multi-organização no site atual.
- Não existe URL de preview grátis confirmada para esta branch e o serviço Render de produção está configurado com deploy automático apenas da branch `main`. Não fazer merge ou deploy antes de integrar tenant context a todos os dados/rotas.
- O fluxo real de e-mail e a aceitação por usuários reais não foram testados porque a URL isolada do aplicativo ainda não foi configurada. Nenhum convite foi enviado.
- O isolamento cross-tenant ponta a ponta ainda precisa de dois usuários autenticados de teste e dados sintéticos em cada organização.

## Próxima sequência obrigatória
1. Hospedar um preview de desenvolvimento sem custo e sem disco pago, ou executar o app localmente para os testes iniciais.
2. Configurar `APP_BASE_URL` para a origem exclusivamente de desenvolvimento e validar o redirect allowlist do Supabase Auth.
3. Implementar organização ativa no login e escopo por organização para colaboradores, temas, configurações, histórico, arquivos e geração de PDF; não usar um JSON/SQLite global compartilhado.
4. Criar duas organizações fictícias e dois usuários de teste; testar leituras, gravações, edição, exclusão, exportação, URLs de arquivos, convites expirados/reutilizados/revogados e tentativa de adulterar organization_id.
5. Rodar testes de regressão existentes e testes de ponta a ponta. Só então solicitar revisão e publicação.

## Comando de regressão estática
`python -m unittest tests.test_multi_org_access_static -v`
