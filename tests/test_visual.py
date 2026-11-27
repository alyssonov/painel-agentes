"""Etapa 2 — identidade visual: tema escuro/claro, menu por módulos e Visão geral do mês."""

from app.web.templating import active_nav
from tests.conftest import csrf_from


def test_menu_marca_o_item_mais_especifico():
    assert active_nav("/") == "/"
    assert active_nav("/margem") == "/margem"
    assert active_nav("/margem/venda") == "/margem"
    assert active_nav("/margem/graficos") == "/margem/graficos"
    assert active_nav("/precificacao/taxas") == "/precificacao"
    assert active_nav("/integracoes/shopee/conectar") == "/integracoes"
    assert active_nav("/login") == ""  # página fora do menu: nada marcado


def test_tema_escuro_por_padrao_e_troca_para_claro(logged_client):
    html = logged_client.get("/").text
    assert 'data-theme="dark"' in html
    for title in ("Operação", "Atendimento", "Financeiro", "Inteligência"):
        assert title in html
    assert "Este mês" in html and "Por marketplace" in html
    token = csrf_from(html)
    r = logged_client.post("/tema", data={"csrf_token": token}, headers={"referer": "http://testserver/margem"},
                           follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/margem"
    assert 'data-theme="light"' in logged_client.get("/").text
    logged_client.post("/tema", data={"csrf_token": token}, follow_redirects=False)
    assert 'data-theme="dark"' in logged_client.get("/").text


def test_tema_nao_redireciona_para_fora_e_exige_csrf(logged_client):
    token = csrf_from(logged_client.get("/").text)
    for ref in ("https://malicioso.com//x", "http://testserver//malicioso.com"):
        r = logged_client.post("/tema", data={"csrf_token": token}, headers={"referer": ref}, follow_redirects=False)
        assert r.headers["location"] == "/"
    assert logged_client.post("/tema", data={}).status_code == 403


def test_tema_invalido_no_cookie_volta_ao_escuro(logged_client):
    logged_client.cookies.set("tema", "rosa")
    assert 'data-theme="dark"' in logged_client.get("/").text


def test_formularios_de_edicao_so_salvam_quando_algo_muda(logged_client):
    """Botão "Salvar" apagado até mudar algo (data-salvar + app.js) nas telas de configuração."""
    for url in ("/integracoes", "/relatorios", "/automacoes", "/agentes", "/precificacao/taxas"):
        assert "data-salvar" in logged_client.get(url).text, url
    js = logged_client.get("/static/js/app.js").text
    assert "form[data-salvar]" in js and "Salvando…" in js


def test_logos_com_versao_para_o_navegador_nao_usar_copia_antiga(client):
    """Logo e ícones levam ?v=… (muda a cada vez que o painel liga): nada de logo antigo guardado no navegador."""
    html = client.get("/login").text
    assert "/static/img/logo-gerente-do-ano.svg?v=" in html and "/static/img/robo-cabeca.svg?v=" in html
