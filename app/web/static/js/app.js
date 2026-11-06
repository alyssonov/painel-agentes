// Pequenos comportamentos do painel (sem scripts inline, por causa da CSP).
// Botões "Editar" e "Responder eu mesmo" da fila de perguntas.
document.addEventListener("click", function (event) {
  var button = event.target.closest("[data-acao]");
  if (!button) return;
  var card = button.closest("[data-card]");
  if (!card) return;
  var box = card.querySelector("textarea");
  if (!box) return;
  if (button.dataset.acao === "editar") {
    box.readOnly = false;
    box.focus();
    box.setSelectionRange(box.value.length, box.value.length);
  } else if (button.dataset.acao === "eu-mesmo") {
    box.readOnly = false;
    box.value = "";
    box.focus();
  }
  card.classList.add("is-editing");
  var send = card.querySelector("[data-enviar]");
  if (send) send.textContent = "Enviar resposta";
});

// Contador de caracteres das respostas.
document.addEventListener("input", function (event) {
  var box = event.target;
  if (box.tagName !== "TEXTAREA") return;
  var counter = box.closest("[data-card]") && box.closest("[data-card]").querySelector("[data-contador]");
  if (counter) counter.textContent = box.value.length + " / " + box.maxLength;
});

// Botão de olho no login: mostra/esconde a senha.
document.addEventListener("click", function (event) {
  var button = event.target.closest("[data-mostrar-senha]");
  if (!button) return;
  var input = button.parentElement.querySelector("[data-senha]");
  if (!input) return;
  var show = input.type === "password";
  input.type = show ? "text" : "password";
  button.setAttribute("aria-pressed", show ? "true" : "false");
  button.setAttribute("aria-label", show ? "Esconder senha" : "Mostrar senha");
});
