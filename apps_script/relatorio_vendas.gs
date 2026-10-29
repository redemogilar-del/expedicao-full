// Acréscimo ao Apps Script "ML HUB MOGILAR — GOOGLE SHEETS" (V9): relatório da aba "Vendas do dia" e histórico de preços.
//
// 1) No switch (b.acao) do doPost, acrescente (junto do 'aptos_full'):
//
//      case 'vendas_relatorio':
//        r = vendasRelatorio_(b);
//        break;
//      case 'vendas_precos':
//        r = vendasPrecos_(b);
//        break;
//
//    (se você já tinha o 'vendas_relatorio' da V8, só acrescente o 'vendas_precos' e SUBSTITUA as funções antigas por estas)
// 2) Cole TODAS as funções abaixo no final do Código.gs (apague as versões antigas de vendasRelatorio_ e vrEscrever_).
// 3) Implantar > Gerenciar implantações > lápis > Nova versão > Implantar (a URL não muda).
//
// O que a planilha passa a ter, para cada conta (ML19 e ML44):
//   "Base ML19"                 todas as vendas já analisadas. A venda é reconhecida pela chave (conta|pedido|MLB|SKU):
//                               salvar de novo ATUALIZA a linha, nunca repete.
//   "Desempenho ML19"           uma linha por dia da venda (prejuízo R$, margem não atingida R$, quantidades) + 2 gráficos.
//                               É recalculado da Base a cada salvamento.
//   "Prejuízos ML19"            só as vendas com prejuízo (vem da Base, sem repetir).
//   "Abaixo da margem ML19"     só as vendas abaixo da margem (vem da Base, sem repetir).
//   "Preços atualizados ML19"   cada anúncio que teve o preço alterado pelo site (não repete o mesmo anúncio, preço e dia).
// Continuam: "Vendas AAAA-MM-DD", "Não enviar AAAA-MM-DD" e "Desempenho diário" (como na V8).

var VR_CONTAS_ = ['ML19', 'ML44'];
var VR_COL_ = { chave: 0, data: 1, classe: 2, conta: 3, pedido: 4, valor: 18 };   // posições na linha da Base (começa em 0)

function vendasRelatorio_(b) {
  const dia = String(b.dia || '');

  if (!/^\d{4}-\d{2}-\d{2}$/.test(dia)) {
    throw Error('Dia inválido.');
  }

  const lock = LockService.getScriptLock();
  lock.waitLock(30000);

  try {
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

    // 3) uma linha por dia no desempenho geral
    vrDesempenhoDiario_(b, dia);
    abas.push('Desempenho diário');

    // 4) por conta: base sem repetição, desempenho com gráficos, prejuízos e abaixo da margem
    if (b.base && b.base.length && b.cab_base) {
      VR_CONTAS_.forEach(function (conta) {
        const novas = b.base.filter(function (l) { return l[VR_COL_.conta] === conta; });
        if (!novas.length) return;
        vrContaAtualizar_(ss, conta, b.cab_base, novas).forEach(function (n) { abas.push(n); });
      });
    }

    return { ok: true, abas: abas };
  } finally {
    lock.releaseLock();
  }
}

function vrDesempenhoDiario_(b, dia) {
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
}

// Junta as vendas novas na Base da conta (sem repetir) e refaz as abas derivadas.
function vrContaAtualizar_(ss, conta, cab, novas) {
  const nomeBase = 'Base ' + conta;
  let base = ss.getSheetByName(nomeBase);

  if (!base) {
    base = ss.insertSheet(nomeBase);
  }

  // lê o que já existe (chave na coluna A)
  const existentes = [];
  const ult = base.getLastRow();

  if (ult >= 2) {
    base.getRange(2, 1, ult - 1, cab.length).getValues().forEach(function (l) {
      if (String(l[0]).trim() !== '') existentes.push(l);
    });
  }

  const porChave = {};
  existentes.forEach(function (l, i) { porChave[String(l[0])] = i; });

  let atualizadas = 0;
  let adicionadas = 0;

  novas.forEach(function (l) {
    const k = String(l[0]);
    const linha = l.map(function (v) { return (v === null || v === undefined) ? '' : v; });

    if (porChave.hasOwnProperty(k)) {
      existentes[porChave[k]] = linha;       // mesma venda: só atualiza
      atualizadas++;
    } else {
      porChave[k] = existentes.length;
      existentes.push(linha);
      adicionadas++;
    }
  });

  // mais recentes primeiro
  existentes.sort(function (a, b) {
    if (a[VR_COL_.data] !== b[VR_COL_.data]) return a[VR_COL_.data] < b[VR_COL_.data] ? 1 : -1;
    return String(a[VR_COL_.pedido]) < String(b[VR_COL_.pedido]) ? 1 : -1;
  });

  vrGravar_(base, cab, existentes, '#14243a');

  const nomes = [nomeBase];
  nomes.push(vrDesempenhoConta_(ss, conta, existentes));

  const semChave = function (l) { return l.slice(1); };
  const cabSem = cab.slice(1);

  const prej = existentes.filter(function (l) { return l[VR_COL_.classe] === 'Prejuízo'; })
    .sort(function (a, b) { return Number(b[VR_COL_.valor]) - Number(a[VR_COL_.valor]); })
    .map(semChave);

  const abaixo = existentes.filter(function (l) { return l[VR_COL_.classe] === 'Abaixo da margem'; })
    .sort(function (a, b) { return Number(b[VR_COL_.valor]) - Number(a[VR_COL_.valor]); })
    .map(semChave);

  nomes.push(vrDerivada_(ss, 'Prejuízos ' + conta, cabSem, prej, '#b4232a'));
  nomes.push(vrDerivada_(ss, 'Abaixo da margem ' + conta, cabSem, abaixo, '#a8641a'));

  return nomes;
}

// Escreve uma tabela inteira (cabeçalho + linhas) com colunas de identificação como texto, para o Sheets não estragar pedido, data, SKU e MLB.
function vrGravar_(sh, cab, linhas, cor) {
  sh.clear();
  sh.getRange(1, 1, 1, cab.length).setValues([cab]);

  if (linhas.length) {
    const area = sh.getRange(2, 1, linhas.length, cab.length);
    const textos = [];

    cab.forEach(function (nome, i) {
      if (['Chave', 'Data', 'Classe', 'Conta', 'Pedido', 'SKU', 'MLB', 'Status do envio', 'Produto', 'Tipo de envio', 'Atualizado em'].indexOf(nome) >= 0) textos.push(i + 1);
    });

    textos.forEach(function (c) { sh.getRange(2, c, linhas.length, 1).setNumberFormat('@'); });
    area.setValues(linhas);
  }

  sh.setFrozenRows(1);

  sh.getRange(1, 1, 1, cab.length)
    .setFontWeight('bold')
    .setBackground(cor)
    .setFontColor('#ffffff');
}

function vrDerivada_(ss, nome, cab, linhas, cor) {
  let sh = ss.getSheetByName(nome);

  if (!sh) {
    sh = ss.insertSheet(nome);
  }

  vrGravar_(sh, cab, linhas, cor);
  return nome;
}

// Uma linha por dia da venda, calculada da Base (por isso nunca conta a mesma venda duas vezes) + gráficos.
function vrDesempenhoConta_(ss, conta, base) {
  const nome = 'Desempenho ' + conta;
  let sh = ss.getSheetByName(nome);

  if (!sh) {
    sh = ss.insertSheet(nome);
  }

  const dias = {};

  base.forEach(function (l) {
    const d = String(l[VR_COL_.data]);
    if (!dias[d]) dias[d] = { prej: 0, abaixo: 0, total: 0, nPrej: 0, nAbaixo: 0, nSem: 0 };
    const x = dias[d];
    const v = Number(l[VR_COL_.valor]) || 0;
    x.total++;

    if (l[VR_COL_.classe] === 'Prejuízo') { x.prej += v; x.nPrej++; }
    else if (l[VR_COL_.classe] === 'Abaixo da margem') { x.abaixo += v; x.nAbaixo++; }
    else if (l[VR_COL_.classe] === 'Sem dados') { x.nSem++; }
  });

  const cab = ['Data da venda', 'Prejuízo R$', 'Margem não atingida R$', 'Vendas analisadas', 'Vendas com prejuízo', 'Vendas abaixo da margem', 'Vendas sem dados'];

  const linhas = Object.keys(dias).sort().map(function (d) {
    const x = dias[d];
    return [d, Math.round(x.prej * 100) / 100, Math.round(x.abaixo * 100) / 100, x.total, x.nPrej, x.nAbaixo, x.nSem];
  });

  sh.getCharts().forEach(function (c) { sh.removeChart(c); });
  sh.clear();
  sh.getRange(1, 1, 1, cab.length).setValues([cab]);
  sh.getRange(2, 1, linhas.length, 1).setNumberFormat('@');
  sh.getRange(2, 1, linhas.length, cab.length).setValues(linhas);
  sh.getRange(2, 2, linhas.length, 2).setNumberFormat('R$ #,##0.00');
  sh.setFrozenRows(1);

  sh.getRange(1, 1, 1, cab.length)
    .setFontWeight('bold')
    .setBackground('#14243a')
    .setFontColor('#ffffff');

  const n = linhas.length + 1;

  const grafico = function (coluna, titulo, linhaTopo) {
    return sh.newChart()
      .setChartType(Charts.ChartType.LINE)
      .addRange(sh.getRange(1, 1, n, 1))
      .addRange(sh.getRange(1, coluna, n, 1))
      .setNumHeaders(1)
      .setOption('title', titulo + ' — ' + conta)
      .setOption('legend', { position: 'none' })
      .setOption('width', 560)
      .setOption('height', 300)
      .setPosition(linhaTopo, 9, 0, 0)
      .build();
  };

  if (linhas.length) {
    sh.insertChart(grafico(2, 'Prejuízo diário', 2));
    sh.insertChart(grafico(3, 'Margem não atingida', 18));
  }

  return nome;
}

// Anota os preços alterados pelo site. Não repete: mesmo anúncio, mesmo preço anterior/novo, no mesmo dia.
function vendasPrecos_(b) {
  const conta = String(b.conta || '');

  if (VR_CONTAS_.indexOf(conta) < 0) {
    throw Error('Conta inválida.');
  }

  const itens = b.itens || [];
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);

  try {
    const ss = wb_();
    const nome = 'Preços atualizados ' + conta;
    const cab = ['Chave', 'Data e hora', 'Conta', 'MLB', 'SKU', 'Produto', 'Preço anterior (R$)', 'Preço novo (R$)', 'Variação (%)', 'Vendas (pedidos) que motivaram', 'Situação'];

    let sh = ss.getSheetByName(nome);
    const existentes = [];

    if (!sh) {
      sh = ss.insertSheet(nome);
    } else if (sh.getLastRow() >= 2) {
      sh.getRange(2, 1, sh.getLastRow() - 1, cab.length).getValues().forEach(function (l) {
        if (String(l[0]).trim() !== '') existentes.push(l);
      });
    }

    const chaves = {};
    existentes.forEach(function (l) { chaves[String(l[0])] = true; });

    const agora = new Date();
    const dia = Utilities.formatDate(agora, 'America/Sao_Paulo', 'yyyy-MM-dd');
    const hora = Utilities.formatDate(agora, 'America/Sao_Paulo', 'dd/MM/yyyy HH:mm');

    let novos = 0;
    let repetidos = 0;

    itens.forEach(function (x) {
      const k = [conta, x.mlb, x.preco_anterior, x.preco_novo, dia].join('|');

      if (chaves[k]) {
        repetidos++;
        return;
      }

      chaves[k] = true;
      novos++;

      existentes.unshift([
        k, hora, conta, String(x.mlb || ''), String(x.sku || ''), String(x.produto || ''),
        Number(x.preco_anterior), Number(x.preco_novo), Number(x.variacao_pct),
        (x.pedidos || []).join(', '), 'Atualizado e confirmado no Mercado Livre'
      ]);
    });

    if (novos) {
      sh.clear();
      sh.getRange(1, 1, 1, cab.length).setValues([cab]);
      [1, 2, 3, 4, 5, 6, 10, 11].forEach(function (c) { sh.getRange(2, c, existentes.length, 1).setNumberFormat('@'); });
      sh.getRange(2, 1, existentes.length, cab.length).setValues(existentes);
      sh.getRange(2, 7, existentes.length, 2).setNumberFormat('R$ #,##0.00');
      sh.setFrozenRows(1);

      sh.getRange(1, 1, 1, cab.length)
        .setFontWeight('bold')
        .setBackground('#1b6e3a')
        .setFontColor('#ffffff');
    }

    return { ok: true, aba: nome, novos: novos, repetidos: repetidos };
  } finally {
    lock.releaseLock();
  }
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
