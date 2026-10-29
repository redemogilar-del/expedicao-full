// Acréscimo ao Apps Script "ML HUB MOGILAR — GOOGLE SHEETS" (V8): grava o relatório diário da aba "Vendas do dia".
//
// 1) No switch (b.acao) do doPost, acrescente (junto do 'aptos_full'):
//
//      case 'vendas_relatorio':
//        r = vendasRelatorio_(b);
//        break;
//
// 2) Cole as funções abaixo no final do Código.gs.
// 3) Implantar > Gerenciar implantações > lápis > Nova versão > Implantar (a URL não muda).
//
// Cria/atualiza na planilha: "Vendas AAAA-MM-DD" (todas as vendas do dia), "Não enviar AAAA-MM-DD" (só se houver marcados)
// e "Desempenho diário" (uma linha por dia). Rodar de novo no mesmo dia substitui o que já estava lá.

function vendasRelatorio_(b) {
  const dia = String(b.dia || '');

  if (!/^\d{4}-\d{2}-\d{2}$/.test(dia)) {
    throw Error('Dia inválido.');
  }

  const abas = [];
  const ss = wb_();

  // 1) todas as vendas do dia
  abas.push(vrEscrever_(ss, 'Vendas ' + dia, b.cab, b.linhas, '#14243a'));

  // 2) o que não deve ser enviado
  const nomeNao = 'Não enviar ' + dia;
  const velha = ss.getSheetByName(nomeNao);

  if (b.nao_enviar && b.nao_enviar.length) {
    abas.push(vrEscrever_(ss, nomeNao, b.cab_nao_enviar, b.nao_enviar, '#b4232a'));
  } else if (velha) {
    ss.deleteSheet(velha);
  }

  // 3) uma linha por dia no desempenho
  const cab = [
    'Data', 'Período', 'Itens', 'Unidades', 'Dentro da margem', 'Abaixo da margem',
    'Prejuízo', 'Sem dados', 'Vendido (R$)', 'Lucro (R$)', 'Prejuízo total (R$)', 'Deixou de ganhar (R$)', 'Não enviar',
    'Imposto (%)', 'Margem padrão (%)', 'Atualizado em'
  ];

  const sh = aba_('Desempenho diário', cab);
  const r = b.resumo || {};
  const p = b.parametros || {};

  const linha = [
    dia, String(b.periodo || ''), r.itens, r.unidades, r.dentro, r.abaixo,
    r.prejuizo, r.sem_dados, r.venda, r.lucro, r.prejuizo_rs, r.deixou_rs, r.nao_enviar,
    p.imposto_pct, p.margem_padrao,
    Utilities.formatDate(new Date(), 'America/Sao_Paulo', 'dd/MM/yyyy HH:mm')
  ];

  const n = sh.getLastRow();
  let alvo = n + 1;

  if (n >= 2) {
    const datas = sh.getRange(2, 1, n - 1, 1).getDisplayValues();

    for (let i = 0; i < datas.length; i++) {
      if (String(datas[i][0]).trim() === dia) {
        alvo = i + 2;
        break;
      }
    }
  }

  sh.getRange(alvo, 1, 1, 1).setNumberFormat('@');
  sh.getRange(alvo, 1, 1, linha.length).setValues([linha]);
  abas.push('Desempenho diário');

  return { ok: true, abas: abas };
}

function vrEscrever_(ss, nome, cab, linhas, cor) {
  let sh = ss.getSheetByName(nome);

  if (sh) {
    sh.clear();
  } else {
    sh = ss.insertSheet(nome);
  }

  const todas = [cab].concat(linhas || []);

  sh.getRange(1, 1, todas.length, cab.length).setValues(
    todas.map(l => l.map(v => (v === null || v === undefined) ? '' : v))
  );

  sh.setFrozenRows(1);

  sh.getRange(1, 1, 1, cab.length)
    .setFontWeight('bold')
    .setBackground(cor)
    .setFontColor('#ffffff');

  return nome;
}
