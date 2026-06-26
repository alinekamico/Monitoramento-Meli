# Regras de contribuicao

## Arquivos protegidos - nunca altere sem aprovacao da tecnologia

### Auth e usuarios (zona restrita)
- src/auth/ - toda a pasta
- templates/login.html
- templates/usuarios.html
- templates/trocar_senha.html
- templates/esqueci_senha.html
- templates/resetar_senha.html
- templates/sem_permissao.html

### Infraestrutura
- systemd/ - configuracao dos servicos EC2
- .env - nunca commitar credenciais

## Variaveis de ambiente

O ambiente de producao (EC2) usa variaveis especificas que diferem do ambiente de desenvolvimento local.
Nunca altere os nomes das variaveis de ambiente dentro do codigo.
Cada ambiente tem seu proprio .env - ajuste apenas o seu .env local.

## Processo de deploy

Todo deploy na EC2 e feito pela equipe de tecnologia.
O colaborador sobe para o GitHub; a tecnologia revisa o diff e aplica seletivamente.
Deploy direto na EC2 sem revisao nao e permitido.
