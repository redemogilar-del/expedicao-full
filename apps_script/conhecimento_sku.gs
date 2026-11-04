// Acréscimo ao Apps Script DESTA planilha ("Atualizar Preco ML 44", o que tem o menu "Perguntas ML").
// Faz o painel Expedição Mogilar LER as abas "Regras Gerais", "Informações por sku" e "Base de Conhecimento"
// para a IA sugerir respostas. É SOMENTE LEITURA: nada é alterado na planilha.
//
// 1) Cole TUDO abaixo (menos este bloco de comentários) num arquivo novo do Apps Script (+ > Script), por exemplo "Conhecimento".
// 2) Troque a senha na linha CHAVE_SITE_ por uma senha longa e inventada por você (a mesma vai para EXP_CONHECIMENTO_KEY no Render).
// 3) No doPost(e) que já existe, logo na PRIMEIRA linha de dentro, cole:
//
//      const b__ = JSON.parse((e.postData && e.postData.contents) || '{}');
//      if (b__.acao === 'conhecimento_sku') return conhecimentoSkuResposta_(b__);
//
//    (se o seu doPost já usa o nome b__, troque por outro nome nestas duas linhas)
// 4) Implantar > Gerenciar implantações > lápis > Nova versão > Implantar (o endereço não muda).

const CHAVE_SITE_ = 'TROQUE-POR-UMA-SENHA-LONGA';

function conhecimentoJson_(o) {
  return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON);
}

function conhecimentoN_(s) {
  return String(s == null ? '' : s).normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/\s+/g, ' ').trim();
}

function conhecimentoAba_(nome) {
  const sh = SpreadsheetApp.getActiveSpreadsheet().getSheets().find(x => conhecimentoN_(x.getName()) === conhecimentoN_(nome));
  if (!sh) throw Error('Aba "' + nome + '" não encontrada.');
  return sh.getDataRange().getDisplayValues();
}

function conhecimentoColuna_(v, i) {
  return v.slice(1).map(l => String(l[i] || '').trim()).filter(x => x);
}

function conhecimentoSkuResposta_(b) {
  if (CHAVE_SITE_ === 'TROQUE-POR-UMA-SENHA-LONGA' || b.chave !== CHAVE_SITE_) {
    return conhecimentoJson_({ ok: false, error: 'Chave inválida.' });
  }
  try {
    return conhecimentoJson_(conhecimentoSku_(b.skus));
  } catch (e) {
    return conhecimentoJson_({ ok: false, error: String((e && e.message) || e) });
  }
}

function conhecimentoSku_(skus) {
  if (!Array.isArray(skus) || skus.length > 10) throw Error('Lista de SKUs inválida.');

  const g = conhecimentoAba_('Regras Gerais');
  const geral = { prompt: conhecimentoColuna_(g, 0).join('\n'), regras: conhecimentoColuna_(g, 1), respostas: conhecimentoColuna_(g, 2) };

  const t = conhecimentoAba_('Informações por sku');
  const cab = t[0].map(conhecimentoN_);
  const cSku = cab.indexOf('sku'), cFam = cab.indexOf('familia'), cReg = cab.indexOf('regras especiais');
  if (cSku < 0 || cFam < 0 || cReg < 0) throw Error('A aba "Informações por sku" precisa das colunas SKU, Familia e Regras Especiais.');

  const k = conhecimentoAba_('Base de Conhecimento');
  const familias = k[0].map(conhecimentoN_);

  const saida = {};
  skus.forEach(sku => {
    const linha = t.slice(1).find(l => conhecimentoN_(l[cSku]) === conhecimentoN_(sku));
    if (!linha) { saida[sku] = { encontrado: false }; return; }
    const familia = String(linha[cFam] || '').trim();
    const col = familia ? familias.indexOf(conhecimentoN_(familia)) : -1;
    const base = col < 1 ? [] : k.slice(1).map(l => ({ topico: String(l[0] || '').trim(), texto: String(l[col] || '').trim() })).filter(x => x.texto);
    saida[sku] = { encontrado: true, familia, regras_especiais: String(linha[cReg] || '').trim(), base_encontrada: col >= 1, base };
  });
  return { ok: true, geral, skus: saida };
}
