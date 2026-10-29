// Trecho para o Apps Script da planilha "Banco de Dados ML Hub".
// Faz o painel conseguir ler a aba "Aptos Para FULL" (coluna A, SKU, a partir da linha 2).
//
// PASSO A PASSO
// 1. No Código.gs, ache a função "doPost" que já existe e troque o nome dela para "doPostAntigo".
// 2. Cole TUDO abaixo no final do Código.gs e salve.
// 3. Implantar > Gerenciar implantações > lápis (editar) > Versão: "Nova versão" > Implantar.
//    (A URL continua a mesma; não precisa mudar nada no Render.)

function doPost(e) {
  try {
    var d = JSON.parse(e.postData.contents);
    if (d && d.acao === 'aptos_full') {
      // Confere a chave usando a própria validação do script antigo (não expõe a chave aqui).
      var teste = doPostAntigo({postData: {contents: JSON.stringify({chave: d.chave, acao: 'catalogo_custos', skus: ['__teste__']})}});
      var r = JSON.parse(teste.getContent());
      if (!r || r.ok !== true) return saidaAptos_({ok: false, error: 'Chave inválida'});
      var aba = SpreadsheetApp.getActive().getSheetByName('Aptos Para FULL');
      if (!aba) return saidaAptos_({ok: false, error: 'Aba Aptos Para FULL não encontrada'});
      var n = aba.getLastRow();
      var skus = n < 2 ? [] : aba.getRange(2, 1, n - 1, 1).getValues()
        .map(function (l) { return String(l[0]).trim(); })
        .filter(function (s) { return s; });
      return saidaAptos_({ok: true, skus: skus});
    }
  } catch (err) {
    return saidaAptos_({ok: false, error: String(err)});
  }
  return doPostAntigo(e);
}

function saidaAptos_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
