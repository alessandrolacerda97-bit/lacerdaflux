# LacerdaFlux — MVP WMS

**O fluxo da sua operação sob controle.**

MVP funcional de um sistema de gestao de estoque para pequenas empresas.

## Recursos

- Login de acesso
- Dashboard operacional
- Cadastro de produtos e enderecos
- Entradas, saidas e ajustes de estoque
- Inventario com divergencia automatica
- Leitura de posição e SKU pela câmera do celular
- Interface responsiva para celular
- Banco SQLite com dados de demonstracao

## Executar

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Abra `http://127.0.0.1:5055`.

Credenciais iniciais:

- E-mail: `admin@lacerdaflux.local`
- Senha: `admin123`

Para recriar os dados de demonstracao, apague `instance/wms.db` e reinicie a aplicacao.

## Publicar no Render

O projeto inclui `render.yaml`. No Render, conecte o repositório e use o Blueprint
ou configure um Web Service com `gunicorn app:app` como comando de inicialização.
