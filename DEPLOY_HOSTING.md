# Hospedagem web — Gerador de DDS

## Estado
O pacote está preparado para deploy em um serviço Python que suporte disco persistente e variáveis de ambiente. O arquivo `render.yaml` descreve um serviço Render com disco persistente de 1 GB. **Ainda não está publicado**: a publicação exige que o titular autorize a conexão do repositório e configure a senha administrativa no provedor.

## Segurança
- `DDS_HOSTED=1` ativa o modo hospedado.
- `DDS_ADMIN_USER` define o usuário de acesso (padrão `admin`).
- `DDS_ADMIN_PASSWORD` é obrigatório e deve ter no mínimo 16 caracteres; o processo se recusa a iniciar sem ele.
- A aplicação usa autenticação HTTP Basic e deve ser servida exclusivamente por HTTPS do provedor.
- `/healthz` é a única rota pública, para verificação técnica de saúde.
- O disco persistente em `/var/data` guarda planilha/estado, temas e PDFs. Não remover o disco nem mudar o caminho sem backup.
- Um único worker Gunicorn é configurado para evitar concorrência indevida em arquivos SQLite locais.

## Passos do deploy
1. Colocar o diretório do projeto em um repositório Git privado.
2. No provedor, criar um novo Web Service usando o blueprint `render.yaml`.
3. Criar e guardar uma senha administrativa forte (mínimo 16 caracteres) como variável secreta `DDS_ADMIN_PASSWORD`; não colocar a senha no código ou em mensagens.
4. Confirmar `DDS_HOSTED=1`, `DDS_DATA_DIR=/var/data`, `DDS_ADMIN_USER=admin`.
5. Autorizar o serviço, aguardar o build e confirmar `/healthz`.
6. Entrar no endereço HTTPS e autenticar com as credenciais administrativas.
7. Importar a planilha atualizada e validar geração de PDF, importação, exportação, histórico, upload de planilha e pesquisa de tema.
8. Criar backup dos dados antes de qualquer alteração de infraestrutura.

## Custo e autorização
A configuração usa um serviço `starter` com disco persistente. Pode haver cobrança conforme os preços e condições atuais do provedor. Não iniciar o serviço sem revisar o custo e confirmar o titular da conta.

## Pesquisa de temas
A ação de pesquisa tenta consultar novamente fontes oficiais a cada solicitação. Prioriza MTE e Fundacentro; Anvisa é fonte complementar quando o assunto envolve saneantes, limpeza, saúde ou aeroportos. Se a pesquisa falhar, o sistema retorna erro explícito e não apresenta conteúdo antigo ou genérico como se tivesse sido verificado naquele momento.
