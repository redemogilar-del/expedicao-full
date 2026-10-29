# Expedição Mogilar

Painel de rotina de expedição. Primeira aba: **Envios Full** — a pessoa sobe o *Relatório de Planejamento de envios* do Mercado Livre e o painel diz quais produtos enviar e quantas unidades, e gera a planilha de envio e o resumo por SKU para a produção.

Nada é gravado no servidor e nada é alterado no Mercado Livre: o painel só lê os arquivos enviados e devolve planilhas.

## Como usar (quem está na expedição)
1. Baixe no Mercado Livre o **Relatório de Planejamento de envios** (Excel).
2. Entre no painel → **Envios Full** → passo 1: escolha o relatório.
3. Passo 2 (só na primeira vez ou quando mudar frete/custo): envie a planilha de controle (aba `anuncios`, com Frete e Custo). A base fica guardada neste navegador.
4. Passo 3: confira as regras e clique em **Calcular envios**.
5. Os produtos de **envio-teste** (sem estoque e sem vendas no Full) começam desmarcados: marque os que o Mercado Livre sugeriu.
6. Ajuste quantidades se precisar e baixe **a planilha de envio** e **o resumo por SKU**.

## Regras (iguais às da planilha de controle)
- Necessidade = vendas dos últimos 30 dias × meses de estoque (padrão 1,5) − recebimento pendente − aptas para venda, arredondado para cima.
- Produto sem nenhum estoque no Full (aptas + pendente = 0): envia no mínimo o envio-teste (padrão 3). Se o ML sugere menos que isso, arredonda para o teste.
- Frete ÷ custo precisa ser **menor** que o limite (padrão 45%). Acima disso o produto não é enviado (mas dá para marcar manualmente).
- Frete ou custo ausente nunca vira zero: o produto fica de fora com o motivo.
- O ML pode impedir o envio (restrição, dados fiscais, anúncio duplicado): aparece como "Impedido".

## Publicar no Render
1. Render → **New → Blueprint** → escolha este repositório (o `render.yaml` já configura tudo).
2. Preencha as três variáveis quando pedir: `EXP_SECRET_KEY`, `EXP_LOGIN_USER`, `EXP_LOGIN_PASSWORD_HASH`.
3. Para trocar a senha: `python gerar_hash.py` e coloque o resultado em `EXP_LOGIN_PASSWORD_HASH`.

## Desenvolvimento
```bash
pip install -r requisitos.txt
EXP_SECRET_KEY=x EXP_LOGIN_USER=admin EXP_LOGIN_PASSWORD_HASH="$(python -c "from werkzeug.security import generate_password_hash as g;print(g('minha-senha'))")" python app.py
python -m unittest discover -s tests -v
```
