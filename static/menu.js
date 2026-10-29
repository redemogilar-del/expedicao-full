"use strict";
// Menu lateral que encolhe. A escolha fica guardada só neste navegador (se o navegador não deixar, simplesmente não lembra).
(function(){
  const botao = document.getElementById('menuBotao'), body = document.body, CHAVE = 'exp_menu_fechado';
  function aplicar(fechado){
    body.classList.toggle('menu-fechado', fechado);
    botao.textContent = fechado ? '»' : '«'; botao.setAttribute('aria-expanded', String(!fechado));
  }
  let inicial = false;
  try { inicial = localStorage.getItem(CHAVE) === '1'; } catch (e) {}
  aplicar(inicial);
  botao.onclick = () => {
    const fechado = !body.classList.contains('menu-fechado'); aplicar(fechado);
    try { localStorage.setItem(CHAVE, fechado ? '1' : '0'); } catch (e) {}
  };
})();
