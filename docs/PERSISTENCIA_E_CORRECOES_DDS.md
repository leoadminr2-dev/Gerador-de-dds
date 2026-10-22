# Estado das correções e persistência — Gerador de DDS

Atualizado em 22/10/2026.

## Alterações entregues

- Importação de listas simples: cada nome da aba é importado, com teste de regressão.
- Links de PDF: download autenticado por `fetch` e visualização via `Blob`, com busca no cofre caso o arquivo não esteja mais no Render.
- Turno TODOS: a emissão individual consolida os turnos em um PDF; o servidor usa o endpoint de lote.
- DDS manual: emissão consolidada por turno sem exigir colaboradores cadastrados.
- Backup: cópia no navegador e cofre no Supabase dedicado ao DDS. Ao recarregar o app, nunca sobrescrever automaticamente um backup remoto antigo com estado local.
- O servidor tenta sincronizar backups em cada mutação e PDFs ao emitir, mesmo se a aba fechar. Falhas são registradas e não impedem que o usuário baixe o PDF.
- Licença permanente sem validade final é respeitada no frontend.

## Banco e cofre remoto

- Projeto Supabase exclusivo do Gerador: `iqxdkmcwztcpdzvdnsts`.
- Tabela `public.dds_vault_snapshots`: backups estruturados segregados por identificador de usuário; RLS ativa, sem políticas de acesso direto a usuários comuns.
- Tabela `public.dds_vault_machine_keys`: somente digest da credencial de servidor, nunca o token em claro.
- Bucket privado `dds-pdfs`: PDFs organizados por usuário.
- Edge Function `dds-vault` (deploy com `verify_jwt=false`): verifica manualmente o JWT da Central ou o segredo do servidor, sem expor acesso anônimo. 
- Configuração Render: `DDS_VAULT_ENDPOINT`, `DDS_VAULT_OWNER_TOKEN` (nunca copiar token para o repositório).

## Verificações já realizadas

- Suíte GitHub Actions e testes automáticos de API, PDF e persistência simulada.
- SQL: leitura/escrita transacional da tabela de backups com ROLLBACK; bucket privado presente; digest do proprietário presente.
- Publicação automática no Render.

**Limites importantes de homologação:**
- A validação de ponta a ponta deve verificar `/healthz` retornando `durable_vault_ready: true` no ambiente publicado.
- É necessário efetuar pelo menos um login real e alterar ou emitir um DDS para confirmar uma gravação real no Supabase. Sem uma sessão do titular, não presumir que PDFs históricos tenham sido migrados.
- O modo gratuito do Render ainda suspende a instância após inatividade e não fornece disco persistente; o cofre Supabase é o armazenamento a reter.
- Modelos institucionais personalizados enviados por usuários ainda requerem recuperação de arquivo própria; não estão incluídos no cofre de PDFs emitidos.
- Pagamento Pix e webhook exigem teste real controlado (não simular cobrança ou pagamento).
- Não remover, formatar ou substituir o modelo original `assets/template_gps_galeao.pdf` nem o layout `assets/row_layout.json`.
- Sem política explícita de retenção, não excluir automaticamente PDFs de presença.

## Procedimento seguro para homologar

1. Abrir o Gerador pela Central e entrar com a conta autorizada.
2. Confirmar os colaboradores cadastrados.
3. Gerar um DDS de teste sem dados pessoais reais, baixar e reabrir no histórico.
4. Checar o cofre Supabase: novos backups e objetos no bucket privado.
5. Somente então validar o fluxo de reinício e recuperação em ambiente de teste, sem reiniciar o servidor em produção nem arriscar dados atuais.
