# Expedição Mogilar

Painel de rotina de expedição. Primeira aba: **Envios Full** — a pessoa sobe o *Relatório de Planejamento de envios* do Mercado Livre, escolhe a conta (ML19 ou ML44) e o painel busca sozinho o **frete** (API do Mercado Livre) e o **custo** (planilha de custos) e diz quais produtos enviar e quantas unidades, e gera a planilha de envio e o resumo por SKU para a produção.

Nada é gravado no servidor e nada é alterado no Mercado Livre: o painel só lê (relatório enviado, planilha de custos e cotação de frete) e devolve planilhas.

## Como usar (quem está na expedição)
1. Baixe no Mercado Livre o **Relatório de Planejamento de envios** (Excel).
2. Entre no painel → **Envios Full** → passo 1: escolha o relatório.
3. Passo 2: escolha a **conta** do Mercado Livre do relatório (ML19 ou ML44).
4. Passo 3: confira as regras e clique em **Calcular envios**. O painel busca custo e frete sozinho (pode levar alguns minutos em relatórios grandes).
5. Os produtos de **envio-teste** (sem estoque, sem vendas ou com sugestão do ML de 1 ou 2 unidades) já vêm marcados, com a quantidade do teste (3 por padrão, campo "Unidades do envio-teste"). Para mudar uma quantidade, digite na própria linha.
6. Ajuste quantidades se precisar e baixe **a planilha de envio** e **o resumo por SKU**.

## Aba Candidatos ao Full
Lê os pedidos pagos do período (ML19, ML44 ou as duas), soma por anúncio e mostra os que **venderam fora do Full**, com unidades, faturamento, preço médio, vendas/dia, custo (planilha de custos) e estoque. Dá para somar por SKU, filtrar, ordenar e baixar a planilha. O "fora do Full" usa o tipo de envio que o anúncio tem **hoje**: anúncio que já é Full não aparece. Só leitura.

## Aptos para Full
A aba **Aptos Para FULL** da planilha "Banco de Dados ML Hub" (coluna A, SKU) define o que é apto. O painel separa o resultado em *Aptos* e *Não aptos* (comparando o SKU sem diferenciar maiúsculas), tem o botão **Marcar todos os aptos para Full** e baixa um relatório de cada grupo. A planilha de envio leva tudo que estiver marcado (aptos e não aptos incluídos à mão), com a coluna "Apto para Full". Precisa do trecho `apps_script/aptos_full.gs` no Apps Script da planilha.

## Planilhas geradas
Em todas as abas, a **coluna A é o MLB** (o primeiro anúncio do produto; os demais ficam na coluna "MLBs") e a **coluna B é a quantidade**, com cor diferente e a quantidade em negrito, para copiar e colar no painel do Mercado Livre. No resumo por SKU, A é o SKU. O relatório de aptos (e o de não aptos) também traz a aba "Resumo por SKU" com o que está marcado no grupo.

## Aba Vendas do dia
Puxa os pedidos pagos do dia (ML19, ML44 ou as duas) com **tarifa do ML** e **frete do vendedor** de cada item e calcula o lucro:
`lucro = venda − tarifa − frete − imposto (venda × imposto%) − custo × quantidade` e `margem = lucro ÷ custo`. Custo e margem mínima de cada SKU vêm da planilha (abas DadosBling e MargensML; Full usa a coluna de margem Full). Quem não tem margem na planilha usa a **margem mínima padrão** (marcada com `*`). Imposto (8%) e margem padrão (20%) são editáveis na tela e recalculam na hora. Dado que falta (custo, tarifa, frete, SKU) nunca vira zero: a venda fica **Sem dados**.
Cada venda é classificada em *Dentro da margem*, *Abaixo da margem*, *Prejuízo* ou *Sem dados*, e separada em *A enviar* (não Full, ainda não despachado), *Já enviado* e *Full*. O operador marca o que **não deve ser enviado** (há botão para marcar todos abaixo da margem) e baixa a lista "Não enviar". O botão **Salvar relatório do dia** grava na planilha as abas `Vendas AAAA-MM-DD`, `Não enviar AAAA-MM-DD` e uma linha em `Desempenho diário`. Precisa do trecho `apps_script/relatorio_vendas.gs` (V8) no Apps Script. Só leitura no Mercado Livre. Pedidos de carrinho dividem o frete igualmente entre os pedidos do carrinho (aviso na linha).

## Regras (iguais às da planilha de controle)
- Necessidade = vendas dos últimos 30 dias × meses de estoque (padrão 1,5) − recebimento pendente − aptas para venda, arredondado para cima.
- Produto sem nenhum estoque no Full (aptas + pendente = 0): envia no mínimo o envio-teste (padrão 3). Se o ML sugere menos que isso, arredonda para o teste.
- Frete ÷ custo precisa ser **menor** que o limite (padrão 45%). Acima disso o produto não é enviado (mas dá para marcar manualmente).
- Frete = custo de frete do vendedor cotado na API do ML para o **primeiro anúncio ativo** do produto (tenta até 3). Custo = coluna Custo da planilha de custos (DadosBling), por SKU.
- Frete ou custo ausente nunca vira zero: o produto fica como "Sem dados" com o motivo.
- O ML pode impedir o envio (restrição, dados fiscais, anúncio duplicado): aparece como "Impedido".

## Publicar no Render
1. Render → **New → Blueprint** → escolha este repositório (o `render.yaml` já configura tudo).
2. Preencha as variáveis quando pedir: `EXP_SECRET_KEY`, `EXP_LOGIN_USER`, `EXP_LOGIN_PASSWORD_HASH`, `EXP_SHEETS_URL`, `EXP_SHEETS_KEY` (mesmos valores de `MLH_SHEETS_URL`/`MLH_SHEETS_KEY` do painel antigo), `EXP_TOKEN_URL_ML19`, `EXP_TOKEN_URL_ML44` (Apps Scripts que devolvem o access_token de cada conta).
3. Para trocar a senha: `python gerar_hash.py` e coloque o resultado em `EXP_LOGIN_PASSWORD_HASH`.

## Desenvolvimento
```bash
pip install -r requisitos.txt
EXP_SECRET_KEY=x EXP_LOGIN_USER=admin EXP_LOGIN_PASSWORD_HASH="$(python -c "from werkzeug.security import generate_password_hash as g;print(g('minha-senha'))")" python app.py
python -m unittest discover -s tests -v
```
