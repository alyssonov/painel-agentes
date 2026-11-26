// Assistente de IA: envia a pergunta sem recarregar a página e mostra a resposta.
// A resposta vem do servidor já convertida em HTML seguro (app/web/markdown_lite.py).
// Sem script inline (CSP 'self'). Sem JavaScript, o formulário funciona do jeito normal.
(function () {
  "use strict";

  function addBubble(log, role, html, isText) {
    var div = document.createElement("div");
    div.className = "asst-msg asst-" + role;
    if (isText) div.textContent = html; else div.innerHTML = html;
    var end = log.querySelector("#fim");
    log.insertBefore(div, end);
    log.scrollTop = log.scrollHeight;
    return div;
  }

  function send(box) {
    var form = box.querySelector("[data-asst-form]");
    var log = box.querySelector("[data-asst-log]");
    var area = form.querySelector("textarea");
    var question = area.value.trim();
    if (!question || form.dataset.busy) return;
    form.dataset.busy = "1";
    var empty = box.querySelector("[data-asst-empty]");
    if (empty) empty.remove();
    addBubble(log, "user", question, true);
    var wait = addBubble(log, "assistant", "Pensando… (consultando os dados da loja)", true);
    wait.classList.add("asst-wait");
    var data = new FormData(form);
    area.value = "";
    fetch(form.action, {
      method: "POST", body: data, credentials: "same-origin",
      headers: { "X-Requested-With": "fetch", "X-CSRF-Token": data.get("csrf_token") }
    }).then(function (r) {
      return r.json().catch(function () { return { erro: "Resposta inesperada do servidor." }; });
    }).then(function (json) {
      wait.classList.remove("asst-wait");
      if (json.erro) {
        wait.classList.add("asst-error");
        wait.textContent = json.erro;
        return;
      }
      form.querySelector("[data-asst-conversa]").value = json.conversa;
      try { sessionStorage.setItem("asst-conversa", json.conversa); } catch (e) { /* sem armazenamento: tudo bem */ }
      var r = json.resposta;
      wait.innerHTML = r.html + (r.tools ? '<p class="asst-meta"></p>' : "");
      if (r.tools) wait.querySelector(".asst-meta").textContent = "consultou: " + r.tools;
      log.scrollTop = log.scrollHeight;
    }).catch(function () {
      wait.classList.remove("asst-wait");
      wait.classList.add("asst-error");
      wait.textContent = "Sem conexão com o painel. Tente de novo.";
    }).then(function () {
      delete form.dataset.busy;
      area.focus();
    });
  }

  // A bolinha cresce e vira o chat (e volta a ser bolinha ao fechar).
  function openPop() {
    var pop = document.getElementById("asst-pop");
    var fab = document.querySelector("[data-asst-abrir]");
    if (!pop) return;
    pop.hidden = false;
    void pop.offsetWidth; // força o navegador a desenhar antes da animação
    pop.classList.add("is-open");
    if (fab) { fab.classList.add("is-hidden"); fab.setAttribute("aria-expanded", "true"); }
    try {
      var saved = sessionStorage.getItem("asst-conversa");
      if (saved) pop.querySelector("[data-asst-conversa]").value = saved;
    } catch (e) { /* sem armazenamento */ }
    pop.querySelector("textarea").focus();
  }

  function closePop() {
    var pop = document.getElementById("asst-pop");
    var fab = document.querySelector("[data-asst-abrir]");
    if (!pop || pop.hidden) return;
    pop.classList.remove("is-open");
    if (fab) { fab.classList.remove("is-hidden"); fab.setAttribute("aria-expanded", "false"); }
    var done = false;
    function hide() { if (!done) { done = true; pop.hidden = true; } }
    pop.addEventListener("transitionend", hide, { once: true });
    setTimeout(hide, 350); // sem animação (preferência do sistema): esconde mesmo assim
    if (fab) fab.focus();
  }

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closePop();
  });

  document.addEventListener("submit", function (event) {
    var box = event.target.closest("[data-asst]");
    if (!box || !event.target.matches("[data-asst-form]") || !window.fetch) return;
    event.preventDefault();
    send(box);
  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Enter" || event.shiftKey || !event.target.closest) return;
    var form = event.target.closest("[data-asst-form]");
    if (!form || event.target.tagName !== "TEXTAREA") return;
    event.preventDefault();
    send(form.closest("[data-asst]"));
  });

  document.addEventListener("click", function (event) {
    var sug = event.target.closest("[data-asst-sugestao]");
    if (sug) {
      var box = sug.closest("[data-asst]");
      box.querySelector("textarea").value = sug.textContent;
      send(box);
      return;
    }
    var open = event.target.closest("[data-asst-abrir]");
    var close = event.target.closest("[data-asst-fechar]");
    if (open) openPop(); else if (close) closePop();
  });
})();
