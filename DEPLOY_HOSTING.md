# Hospedagem web — Gerador de DDS

## Estado desta branch
Esta configuração prepara a hospedagem com **contas individuais e período de teste controlado pelo servidor**. Por segurança, o blueprint começa com `DDS_ACCOUNT_AUTH=0`, mantendo o modo administrativo existente até os segredos obrigatórios estarem configurados. A branch de teste não significa que o serviço está publicado. Não ativar em produção até revisar custo, acesso, backup e SMTP.

## Como funciona o acesso
- `DDS_ACCOUNT_AUTH=1` ativa autenticação individual. O aplicativo falha na inicialização se os segredos administrativos obrigatórios não estiverem configurados.
- O administrador inicial é criado com `DDS_ADMIN_EMAIL` e `DDS_ADMIN_PASSWORD`. A senha tem no mínimo 16 caracteres e é a credencial de recuperação definida no provedor; em cada inicialização ela é sincronizada com a conta administrativa.
- `DDS_SESSION_SECRET` deve conter pelo menos 32 caracteres aleatórios e assinar as sessões. Não reutilize a senha administrativa como segredo de sessão.
- Cada pessoa entra com sua própria conta. Cadastro público sem convite não é permitido.
- Convite de testador tem uso único e vence em 7 dias. A contagem de 30 dias começa quando a pessoa conclui o cadastro.
- O sistema verifica o prazo no servidor em todas as rotas protegidas. Após expirar, o testador não gera nem consulta operações protegidas, mas sua conta e seus dados permanecem guardados. O administrador pode prorrogar o teste.
- São registrados logins e alterações administrativas. Senhas ficam em hash; tokens de convite e redefinição são armazenados somente como hash.
- O administrador pode gerar link de redefinição de senha de uso único, válido por uma hora, e entregá-lo à pessoa por canal seguro.
- Alterações de senha e de status invalidam sessões anteriores, exigindo novo login.
- As contas controlam quem pode entrar, mas o espaço operacional atual (planilha de colaboradores, temas, histórico e PDFs) é compartilhado entre as contas autorizadas. Convide somente pessoas confiáveis da mesma equipe/organização; não use este desenho como isolamento entre empresas ou clientes.

## Alertas de vencimento
O serviço verifica os marcos de 5, 3 e 1 dia e registra avisos com chave única por conta/marco. Para enviar e-mails, configure no provedor:
- `DDS_SMTP_HOST`
- `DDS_SMTP_PORT` (normalmente 587 com STARTTLS ou 465 com TLS implícito)
- `DDS_SMTP_USER`
- `DDS_SMTP_PASSWORD`
- `DDS_SMTP_FROM`

Sem SMTP, os avisos ficam no painel como pendentes e não são apresentados como enviados. Teste uma entrega real antes de convidar usuários. A rotina de avisos está preparada para a configuração atual de um único worker Gunicorn; não aumente workers/instâncias sem migrar a rotina para um agendador/banco que coordene envios.

## Variáveis do blueprint
- `DDS_HOSTED=1`
- `DDS_DATA_DIR=/var/data`
- `DDS_ACCOUNT_AUTH=0` no primeiro deploy; mudar para `1` somente após salvar os segredos obrigatórios
- `DDS_ADMIN_EMAIL` — e-mail da conta administradora
- `DDS_ADMIN_PASSWORD` — segredo, mínimo 16 caracteres
- `DDS_SESSION_SECRET` — segredo aleatório, mínimo 32 caracteres
- `DDS_PUBLIC_BASE_URL` — origem HTTPS da aplicação, se o provedor não fornecer `RENDER_EXTERNAL_URL`/ `RENDER_EXTERNAL_HOSTNAME`
- Variáveis SMTP, conforme a seção acima, para alertas por e-mail

Não coloque senhas, tokens, banco de contas ou dados dos colaboradores no repositório. O repositório está público no momento; mantenha os segredos fora dos arquivos versionados e considere restringir a visibilidade se o código e os detalhes operacionais não forem destinados à divulgação.

## Procedimento de publicação
1. Confirmar o plano e o custo vigente do serviço e do disco com o titular da conta. Não iniciar nem reconfigurar recurso pago sem autorização.
2. Configurar `DDS_ADMIN_EMAIL`, `DDS_ADMIN_PASSWORD` e `DDS_SESSION_SECRET` no painel do provedor. Não enviar segredos por chat ou commit.
3. Manter `DDS_ACCOUNT_AUTH=0` até salvar e conferir os segredos; só então alterar para `1` e reiniciar/deployar o serviço.
4. Confirmar que o disco persistente de 1 GB está montado em `/var/data`. A aplicação guarda ali os dados operacionais e `accounts.sqlite3`.
5. Implantar primeiro em ambiente de teste com HTTPS. Conferir login administrativo, convite, cadastro, expiração, prorrogação, desativação, redefinição de senha e envio real de e-mail.
6. Importar uma cópia de teste da planilha e validar pesquisa, geração de PDF institucional, formatação e histórico.
7. Fazer backup de `/var/data` antes de atualizar ou mudar o disco. O banco de contas contém hashes e metadados pessoais; restrinja o acesso ao backup.
8. Só depois aprovar a integração e publicar. Não alterar a branch `main` antes dos testes passarem.

## Pesquisa de temas e homologação
A pesquisa tenta consultar fontes públicas oficiais no momento da solicitação. Quando a busca falha, o sistema deve informar a falha e não apresentar material antigo como se fosse pesquisa verificada. A ferramenta e os PDFs precisam ser validados com a liderança, TI e Segurança/QSSMA; não afirmar homologação formal sem aprovação.

## Limites de conclusão
O código e os testes podem ser preparados nesta branch, mas a publicação real ainda depende de configurar os segredos do titular, decidir o custo, configurar SMTP válido e testar o deploy real. Um commit no GitHub não prova que o site foi implantado ou está acessível.
