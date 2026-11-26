// Calculadora "Saia do Prejuízo" (Precificação). Criada pelo dono do painel e adaptada à
// identidade visual. Só faz contas no navegador: nada é enviado nem salvo.
//
// Descobrir o preço de venda:
//   com margem:  preço = (custo + taxa fixa + outros) ÷ (1 − tarifa − imposto − publicidade − margem)
//   com markup:  preço = ((1 + markup) × custo + taxa fixa) ÷ (1 − tarifa)
//                (markup = (valor recebido − custo) ÷ custo; valor recebido = preço × (1 − tarifa) − taxa fixa)
// Descobrir a margem: usa o preço que a pessoa cobra hoje.
// Lucro = preço − custo − outros − tarifa − taxa fixa − imposto − publicidade.
// Desconto no anúncio: preço "de" = preço ÷ (1 − desconto), para o comprador pagar o preço calculado.
(function () {
  "use strict";
  var form = document.getElementById("sdp-form");
  if (!form) return;

  var el = function (id) { return document.getElementById(id); };
  var brl = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
  var pct = function (n) { return (n * 100).toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + "%"; };
  var NAMES = ["Preço de custo", "Tarifa de venda", "Taxa fixa", "Publicidade", "Imposto", "Outros custos", "Lucro líquido"];
  var modo = "calc";
  var tipo = "margem";

  function num(id) {
    var s = el(id).value.trim().replace(/\s|R\$|%/g, "");
    if (s.indexOf(",") >= 0) s = s.replace(/\./g, "").replace(",", ".");
    var n = parseFloat(s);
    return isFinite(n) ? n : 0;
  }

  function numero(v) {
    return (v < -0.005 ? "-" : "") + brl.format(Math.abs(v)).replace(/^R\$\s*/, "");
  }

  function erro(msg) {
    var box = el("priceBox");
    box.classList.add("is-error");
    box.classList.remove("is-neg");
    el("preco").textContent = msg;
    ["vMargem", "vMarkup", "recebe", "liquido", "cheio"].forEach(function (k) { el(k).textContent = "—"; });
    ["r-custo", "r-outros", "r-ads", "r-tarifa", "r-fixa", "r-imposto", "r-total", "r-lucro"].forEach(function (k) {
      el(k).textContent = "—";
    });
    document.querySelectorAll("#bar i").forEach(function (b) { b.style.width = "0"; b.removeAttribute("data-tip"); });
  }

  function calc() {
    var custo = num("custo"), fixa = num("fixa"), outros = num("outros");
    var t = num("tarifa") / 100, i = num("imposto") / 100, m = num("margem") / 100, k = num("markup") / 100;
    var a = num("ads") / 100;
    var base = custo + fixa + outros;
    var denom = 1 - t - i - a - m, denomMin = 1 - t - i - a;
    var box = el("priceBox");
    var usaMarkup = tipo === "markup";
    var modoAtual = modo === "atual";

    el("heroLbl").textContent = modoAtual ? "Sua margem" : "Preço de venda";
    el("rMargem").hidden = modoAtual || !usaMarkup;
    el("rMarkup").hidden = !modoAtual && usaMarkup;
    // "De ... por" e o desconto sempre ocupam o espaço (só ficam invisíveis): nada pula ao digitar.
    el("rDesc").style.visibility = modoAtual ? "hidden" : "";
    el("deLinha").style.visibility = "hidden";

    var preco;
    if (modoAtual) {
      preco = num("atual");
      if (preco <= 0) return erro("Informe o preço de venda que você cobra hoje.");
    } else if (usaMarkup) {
      if (denomMin <= 0) return erro("Tarifa + imposto + publicidade passam de 100%. Reduza algum deles.");
      if (t >= 1) return erro("A tarifa precisa ser menor que 100%.");
      preco = ((1 + k) * custo + fixa) / (1 - t);
    } else {
      if (denom <= 0) return erro("Tarifa + imposto + publicidade + margem passam de 100%. Reduza algum deles.");
      preco = base / denom;
    }
    box.classList.remove("is-error");

    var vTarifa = preco * t, vImposto = preco * i, vAds = preco * a;
    var total = custo + outros + vTarifa + fixa + vImposto + vAds;
    var lucro = preco - total;
    var recebido = preco - vTarifa - fixa;
    var neg = lucro < -0.005;

    box.classList.toggle("is-neg", neg);
    el("preco").textContent = modoAtual ? pct(lucro / preco) : brl.format(preco);
    el("vMargem").textContent = pct(lucro / preco);
    el("vMarkup").textContent = custo > 0 ? pct((recebido - custo) / custo) : "—";

    var d = num("desconto") / 100;
    if (!modoAtual && d > 0) {
      el("deLinha").style.visibility = "";
      el("cheio").textContent = d >= 1 ? "desconto < 100%" : brl.format(Math.ceil(preco / (1 - d) * 100 - 1e-6) / 100);
    }
    el("recebe").textContent = brl.format(recebido);
    el("liquido").textContent = brl.format(lucro);
    el("liquido").classList.toggle("is-neg", neg);

    // Detalhamento: % à esquerda, "R$" e o número alinhados em todas as linhas.
    var linhas = { "r-custo": custo, "r-tarifa": vTarifa, "r-fixa": fixa, "r-ads": vAds, "r-imposto": vImposto,
                   "r-outros": outros, "r-total": total, "r-lucro": lucro };
    var largura = 0;
    Object.keys(linhas).forEach(function (id) {
      var v = linhas[id], n = numero(v), cell = el(id);
      largura = Math.max(largura, n.length);
      cell.textContent = "";
      var share = document.createElement("span");
      share.className = "sdp-share";
      share.textContent = preco > 0 ? pct(v / preco) : "";
      var cur = document.createElement("span");
      cur.className = "sdp-cur";
      cur.textContent = "R$";
      var amt = document.createElement("span");
      amt.className = "sdp-amt";
      amt.textContent = n;
      cell.appendChild(share); cell.appendChild(cur); cell.appendChild(amt);
    });
    el("r-custo").closest("table").style.setProperty("--sdp-amtw", largura + "ch");
    el("profitRow").classList.toggle("is-neg", neg);

    var parts = [custo, vTarifa, fixa, vAds, vImposto, outros, Math.max(lucro, 0)];
    document.querySelectorAll("#bar i").forEach(function (b, idx) {
      var w = preco > 0 ? parts[idx] / preco * 100 : 0;
      b.style.width = w + "%";
      if (w > 0) b.setAttribute("data-tip", NAMES[idx] + ": " + brl.format(parts[idx]) + " (" + pct(parts[idx] / preco) + ")");
      else b.removeAttribute("data-tip");
    });
  }

  // O último campo fica sempre no mesmo lugar: só troca o texto e qual caixa de valor aparece.
  function atualizarCampo() {
    var at = modo === "atual", mk = !at && tipo === "markup", mg = !at && !mk;
    el("tiposMini").style.visibility = at ? "hidden" : "";
    el("in-margem").hidden = !mg; el("in-markup").hidden = !mk; el("in-atual").hidden = !at;
    el("hintMargem").hidden = !mg; el("hintMarkup").hidden = !mk; el("hintAtual").hidden = !at;
    el("lblMargem").textContent = at ? "Preço de venda atual" : mk ? "Markup desejado" : "Margem de lucro desejada";
    el("lblMargem").setAttribute("for", at ? "atual" : mk ? "markup" : "margem");
    calc();
  }

  function setModo(m) {
    modo = m;
    el("modo-calc").setAttribute("aria-pressed", m === "calc");
    el("modo-atual").setAttribute("aria-pressed", m === "atual");
    atualizarCampo();
  }

  function setTipo(tp) {
    tipo = tp;
    el("tipo-margem").setAttribute("aria-pressed", tp !== "markup");
    el("tipo-markup").setAttribute("aria-pressed", tp === "markup");
    atualizarCampo();
  }

  el("tipo-margem").addEventListener("click", function () { setTipo("margem"); });
  el("tipo-markup").addEventListener("click", function () { setTipo("markup"); });
  el("modo-calc").addEventListener("click", function () { setModo("calc"); });
  el("modo-atual").addEventListener("click", function () { setModo("atual"); });
  ["custo", "tarifa", "fixa", "imposto", "outros", "ads", "margem", "markup", "atual", "desconto"].forEach(function (id) {
    el(id).addEventListener("input", calc);
  });
  form.addEventListener("submit", function (e) { e.preventDefault(); });
  atualizarCampo();
})();
