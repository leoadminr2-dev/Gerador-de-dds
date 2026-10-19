# Acesso individual e fase de testes — Gerador de DDS

## Estado desta entrega
Esta branch prepara a implementação sem modificar a branch `main`. O acesso atual da aplicação hospedada usa uma única credencial HTTP Basic; isso não é cadastro individual e não deve ser apresentado como tal.

## Requisitos obrigatórios
- Contas individuais por pessoa, com identificação única e senha armazenada apenas como hash forte.
- Papéis separados: `admin` e `tester`. O cadastro público não pode criar administradores.
- Convite de teste criado por administrador; token aleatório, de uso único, armazenado somente como hash e com expiração.
- Período de teste de 30 dias contado da ativação da conta, em UTC.
- Avisos de vencimento em 5, 3 e 1 dia, no máximo uma vez por marco. Não depender de o usuário abrir a página para disparar avisos.
- Após vencer: bloquear as funções operacionais do testador, mas preservar todos os dados e permitir tela de status/contato para regularização. Administradores continuam com acesso.
- Desativação e reativação registradas em log de auditoria, com quem fez a alteração, quando e motivo.
- Sessões seguras: cookies HttpOnly, Secure em HTTPS, SameSite, proteção CSRF nas alterações, limite de tentativas de login e encerramento de sessão.
- Não guardar senhas, tokens de convite ou segredos em texto puro, logs, frontend ou repositório.
- Não excluir dados ao expirar ou desativar uma conta.
- Todas as permissões verificadas no servidor, nunca apenas escondendo botões na interface.

## Modelo de dados sugerido
Usar SQLite apenas se houver um único processo/instância e armazenamento persistente; para múltiplas instâncias, usar banco gerenciado. Tabelas mínimas:
- `users`: id, email normalizado único, password_hash, role, status, created_at, activated_at, trial_started_at, trial_ends_at, disabled_at.
- `invitations`: id, email opcional, token_hash, role fixo como tester, expires_at, used_at, created_by.
- `trial_notifications`: user_id, milestone_days (5, 3 ou 1), sent_at; chave única por usuário/marco.
- `audit_log`: actor_user_id, action, target_user_id, timestamp, metadata sem segredos.
- `sessions` se as sessões não forem armazenadas no mecanismo de sessão seguro já escolhido.

## Regras de vencimento
- `trial_ends_at = trial_started_at + 30 dias`.
- Enviar aviso quando restarem 5, 3 ou 1 dia, com comparação de data em UTC e idempotência no banco.
- Considerar vencido quando `now >= trial_ends_at`.
- O teste de acesso deve ocorrer antes de cada rota/API operacional protegida.
- A tela de vencimento e a rota de logout permanecem disponíveis; não expor dados operacionais após o vencimento.

## Painel administrativo mínimo
- Listar usuários com email, papel, estado, início/fim do teste e último acesso, sem exibir hash/token.
- Criar convite, copiar link, revogar convite, desativar/reativar usuário e estender teste com motivo obrigatório.
- Pesquisar por email e filtrar por ativo, em teste, vencido e desativado.
- Mostrar contagem real do banco; não simular métricas nem ações não implementadas.

## Critérios de aceite antes de publicar
1. Cadastro público não consegue elevar privilégios.
2. Convite expirado, usado ou adulterado é recusado.
3. Senhas nunca aparecem no banco ou nos logs em texto puro.
4. Usuário tester não acessa endpoints operacionais após o vencimento, inclusive chamando a API diretamente.
5. Avisos 5/3/1 são idempotentes e não duplicam em execuções repetidas.
6. Administrador permanece acessível e consegue regularizar o teste.
7. Missões/dados/arquivos existentes não são apagados pela expiração.
8. Testes automatizados para autenticação, autorização, vencimento, avisos, CSRF e recuperação de falhas.
9. Revisão do custo e configuração de HTTPS/segredos antes de ativar hospedagem.

## Implantação segura
Não ativar cadastro individual parcialmente nem remover a autenticação administrativa atual até todos os critérios passarem. Primeiro implementar em branch, testar com banco temporário, revisar migração/backup e só então abrir pull request para revisão. A hospedagem e qualquer recurso pago dependem de autorização do titular.
