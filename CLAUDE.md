# CLAUDE.md

Painel **Expedição Mogilar** (Flask). Código, mensagens e commits em **português**. Quem usa é a equipe de expedição (não técnica): textos simples, erros que dizem o que fazer.

## Comandos
```bash
pip install -r requisitos.txt
python app.py                                   # http://127.0.0.1:5000 (precisa das 3 variáveis EXP_*)
python -m unittest discover -s tests -v
```

## Arquitetura
- `app.py` — login obrigatório (503 se faltar `EXP_SECRET_KEY`, `EXP_LOGIN_USER` ou `EXP_LOGIN_PASSWORD_HASH`), rotas `/envios`, `/api/envios/{base,calcular,exportar}`, geração dos .xlsx.
- `envios.py` — toda a regra de negócio e a leitura dos arquivos (relatório do ML e base de frete/custo). Sem Flask.
- `templates/` + `static/envios.js` — tela. Sem `innerHTML` (CSP `default-src 'self'`; nada de script inline).
- Nada é gravado no servidor. A base de frete/custo fica no `localStorage` do navegador.

## Regras de negócio (não mudar sem pedir)
- Necessidade = ARREDONDA_PARA_CIMA(vendas30 × meses − pendente − aptas), nunca negativa.
- aptas + pendente = 0 ⇒ mínimo = envio-teste (3). Sugestão do ML menor que o teste ⇒ vira o teste.
- Frete/custo < limite (45%), estritamente menor. Dado ausente nunca vira zero.
- Chave do produto = Código ML (o "Número de produto" pode ser `0` em anúncios fora do catálogo).
- Valores com `=`, `+` ou `@` no início são escritos com apóstrofo no Excel (nada vira fórmula).

## Convenções
- Trabalhe em branches e abra PR; quem faz o merge é o dono. Não mexer na `main` direto.
- Variáveis: `EXP_SECRET_KEY`, `EXP_LOGIN_USER`, `EXP_LOGIN_PASSWORD_HASH`, `PORT`, `RENDER`.
