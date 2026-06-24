# -*- coding: utf-8 -*-
"""
RPA Bling - Atualizar/criar produtos a partir de Nota Fiscal de Entrada
=======================================================================

Fluxo automatizado:
  1. Abre https://www.bling.com.br/
  2. Clica em "Login" / "Acessar"
  3. Digita o usuario (e-mail)
  4. Solicita a senha via prompt (nao fica salva no codigo)
  5. Faz login
  6. Abre o menu "Estoque"
  7. Abre "Notas Fiscais de Entrada"
  8. Clica na primeira nota fiscal da lista
  9. Rola ate a secao "Itens da nota fiscal"
 10. Clica no icone em forma de clips com o hint
     "atualizar ou criar produtos a partir da nota"

Como o DOM do Bling pode variar, cada passo tenta varias estrategias de
seletor antes de falhar, e tudo e registrado no log para facilitar o ajuste.

Uso:
    python rpa_bling.py
    python rpa_bling.py --headless        (sem janela - nao recomendado p/ login)
    python rpa_bling.py --user outro@mail.com
"""

import argparse
import getpass
import logging
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    ElementClickInterceptedException,
    StaleElementReferenceException,
)

# --------------------------------------------------------------------------- #
# Configuracao
# --------------------------------------------------------------------------- #
URL_BLING = "https://www.bling.com.br/"
USUARIO_PADRAO = "zanjinhasouza@gmail.com"
TIMEOUT = 30  # segundos para esperas explicitas

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("rpa_bling")


# --------------------------------------------------------------------------- #
# Helpers de interacao robusta
# --------------------------------------------------------------------------- #
def montar_driver(headless: bool) -> webdriver.Chrome:
    """Cria o WebDriver do Chrome. Selenium Manager baixa o driver sozinho."""
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--start-maximized")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    driver = webdriver.Chrome(options=opts)
    driver.maximize_window()
    return driver


def esperar(driver, condicao, timeout: int = TIMEOUT):
    return WebDriverWait(driver, timeout).until(condicao)


def primeiro_elemento(driver, seletores, timeout: int = TIMEOUT, visivel: bool = True):
    """
    Tenta uma lista de seletores (By, valor) e retorna o primeiro que aparecer.
    Levanta TimeoutException se nenhum funcionar dentro do timeout total.
    """
    fim = time.time() + timeout
    ultimo_erro = None
    while time.time() < fim:
        for by, valor in seletores:
            try:
                elems = driver.find_elements(by, valor)
                for el in elems:
                    if not visivel or el.is_displayed():
                        return el
            except (NoSuchElementException, StaleElementReferenceException) as e:
                ultimo_erro = e
        time.sleep(0.5)
    raise TimeoutException(
        f"Nenhum dos seletores encontrado: {seletores} ({ultimo_erro})"
    )


def clicar(driver, elemento):
    """Clica de forma resiliente: scroll ate o elemento, click normal e fallback JS."""
    driver.execute_script(
        "arguments[0].scrollIntoView({block:'center'});", elemento
    )
    time.sleep(0.3)
    try:
        elemento.click()
    except (ElementClickInterceptedException, StaleElementReferenceException):
        driver.execute_script("arguments[0].click();", elemento)


def dump_navegacao(driver, prefixo: str = "diagnostico"):
    """
    Despeja toda a estrutura clicavel da pagina atual (links, botoes, itens de
    menu) num arquivo .txt, alem de salvar o HTML e um screenshot. Serve para
    descobrir os seletores reais quando um passo falha.
    """
    arquivo = f"{prefixo}_menu.txt"
    script = r"""
    const sels = "a, button, [role='menuitem'], [role='button'], [class*='menu'] *[onclick], li[onclick]";
    const out = [];
    document.querySelectorAll(sels).forEach(el => {
        const txt = (el.innerText || el.textContent || "").trim().replace(/\s+/g, " ");
        const title = el.getAttribute("title") || "";
        const aria = el.getAttribute("aria-label") || "";
        const href = el.getAttribute("href") || "";
        const cls = el.getAttribute("class") || "";
        if (txt || title || aria || href) {
            out.push([
                el.tagName.toLowerCase(),
                "txt=" + txt.slice(0, 60),
                title ? "title=" + title : "",
                aria ? "aria=" + aria : "",
                href ? "href=" + href : "",
                cls ? "class=" + cls.slice(0, 80) : ""
            ].filter(Boolean).join(" | "));
        }
    });
    return out.join("\n");
    """
    try:
        conteudo = driver.execute_script(script)
        with open(arquivo, "w", encoding="utf-8") as f:
            f.write(f"URL: {driver.current_url}\n")
            f.write(f"TITULO: {driver.title}\n")
            f.write("=" * 70 + "\n")
            f.write(conteudo or "(nada encontrado)")
        log.info("Diagnostico de navegacao salvo em %s", arquivo)
    except Exception as e:
        log.warning("Falha ao gerar dump de navegacao: %s", e)
    try:
        with open(f"{prefixo}_pagina.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        driver.save_screenshot(f"{prefixo}_tela.png")
        log.info("HTML e screenshot salvos (%s_pagina.html / %s_tela.png)", prefixo, prefixo)
    except Exception as e:
        log.warning("Falha ao salvar HTML/screenshot: %s", e)


def trocar_para_iframe_com(driver, seletores, timeout: int = 8) -> bool:
    """
    Algumas telas do Bling carregam dentro de iframe. Procura o elemento no
    documento principal; se nao achar, tenta dentro de cada iframe.
    Retorna True se trocou para um iframe que contem o elemento.
    """
    driver.switch_to.default_content()
    for by, valor in seletores:
        if driver.find_elements(by, valor):
            return False  # esta no documento principal
    iframes = driver.find_elements(By.TAG_NAME, "iframe")
    for idx, frame in enumerate(iframes):
        try:
            driver.switch_to.frame(frame)
            for by, valor in seletores:
                if driver.find_elements(by, valor):
                    log.info("Elemento encontrado dentro do iframe #%s", idx)
                    return True
            driver.switch_to.default_content()
        except Exception:
            driver.switch_to.default_content()
    driver.switch_to.default_content()
    return False


# --------------------------------------------------------------------------- #
# Passos do fluxo
# --------------------------------------------------------------------------- #
def passo_abrir_site(driver):
    log.info("1) Abrindo %s", URL_BLING)
    driver.get(URL_BLING)
    esperar(driver, EC.presence_of_element_located((By.TAG_NAME, "body")))


def passo_clicar_login(driver):
    log.info("2) Clicando em Login / Acessar")
    seletores = [
        (By.XPATH, "//a[contains(translate(., 'ACESSARLOGIN', 'acessarlogin'), 'acessar')]"),
        (By.XPATH, "//a[contains(translate(., 'LOGIN', 'login'), 'login')]"),
        (By.XPATH, "//button[contains(translate(., 'LOGINACESSARENTRAR', 'loginacessarentrar'), 'login')]"),
        (By.XPATH, "//*[contains(translate(., 'ENTRAR', 'entrar'), 'entrar')][self::a or self::button]"),
        (By.LINK_TEXT, "Login"),
        (By.PARTIAL_LINK_TEXT, "Acessar"),
    ]
    try:
        botao = primeiro_elemento(driver, seletores, timeout=15)
        clicar(driver, botao)
    except TimeoutException:
        # fallback: ir direto para a pagina de login
        log.warning("Botao de login nao encontrado, indo direto para /login")
        driver.get("https://www.bling.com.br/login")


def passo_digitar_usuario(driver, usuario: str):
    log.info("3) Digitando usuario: %s", usuario)
    seletores = [
        (By.CSS_SELECTOR, "input[type='email']"),
        (By.CSS_SELECTOR, "input[name='login']"),
        (By.CSS_SELECTOR, "input[name='username']"),
        (By.CSS_SELECTOR, "input[name='email']"),
        (By.ID, "username"),
        (By.ID, "login"),
        (By.CSS_SELECTOR, "input[autocomplete='username']"),
    ]
    campo = primeiro_elemento(driver, seletores)
    campo.clear()
    campo.send_keys(usuario)

    # Algumas versoes pedem "Continuar" antes da senha
    try:
        continuar = primeiro_elemento(
            driver,
            [
                (By.XPATH, "//button[contains(translate(., 'CONTINUAR', 'continuar'), 'continuar')]"),
                (By.XPATH, "//button[contains(translate(., 'PROXIMO', 'proximo'), 'proximo')]"),
            ],
            timeout=4,
        )
        log.info("   Clicando em 'Continuar'")
        clicar(driver, continuar)
        time.sleep(1.5)
    except TimeoutException:
        pass  # fluxo de pagina unica (usuario+senha juntos)


def passo_digitar_senha(driver, senha: str):
    log.info("4) Digitando senha")
    seletores = [
        (By.CSS_SELECTOR, "input[type='password']"),
        (By.CSS_SELECTOR, "input[name='password']"),
        (By.CSS_SELECTOR, "input[name='senha']"),
        (By.ID, "password"),
        (By.ID, "senha"),
    ]
    campo = primeiro_elemento(driver, seletores)
    campo.clear()
    campo.send_keys(senha)

    log.info("5) Enviando login")
    seletores_btn = [
        (By.XPATH, "//button[@type='submit']"),
        (By.XPATH, "//button[contains(translate(., 'ENTRARLOGINACESSAR', 'entrarloginacessar'), 'entrar')]"),
        (By.XPATH, "//input[@type='submit']"),
    ]
    try:
        botao = primeiro_elemento(driver, seletores_btn, timeout=8)
        clicar(driver, botao)
    except TimeoutException:
        campo.send_keys(Keys.ENTER)


def passo_aguardar_login(driver):
    log.info("   Aguardando carregar o painel apos login...")
    # Espera sumir o campo de senha ou aparecer algo do painel.
    fim = time.time() + TIMEOUT
    while time.time() < fim:
        if not driver.find_elements(By.CSS_SELECTOR, "input[type='password']"):
            log.info("   Login concluido (campo de senha desapareceu).")
            time.sleep(2)
            return
        time.sleep(0.5)
    log.warning("   Nao confirmei o login automaticamente. Verifique a janela "
                "(pode haver 2FA/captcha). Continuando mesmo assim...")


def passo_menu_estoque(driver):
    log.info("6) Abrindo menu 'Estoque'")
    seletores = [
        # XPath real informado: 'Estoque' e uma <div> no header/nav
        (By.XPATH, "/html/body/div[1]/header/div[1]/div[2]/nav/div[4]"),
        (By.XPATH, "//header//nav/div[4]"),
        (By.XPATH, "//header//nav//div[contains(translate(., 'ESTOQUE', 'estoque'), 'estoque')]"),
        (By.XPATH, "//*[@title='Estoque' or @aria-label='Estoque']"),
        (By.XPATH, "//a[contains(translate(., 'ESTOQUE', 'estoque'), 'estoque')]"),
        (By.XPATH, "//button[contains(translate(., 'ESTOQUE', 'estoque'), 'estoque')]"),
        (By.PARTIAL_LINK_TEXT, "Estoque"),
    ]
    # O painel do Bling pode carregar dentro de iframe.
    trocar_para_iframe_com(driver, seletores)
    try:
        # 1a tentativa: elemento visivel
        item = primeiro_elemento(driver, seletores, timeout=12, visivel=True)
    except TimeoutException:
        try:
            # 2a tentativa: existe no DOM mas pode estar em menu colapsado
            log.info("   Item visivel nao achado; tentando item oculto/colapsado.")
            item = primeiro_elemento(driver, seletores, timeout=6, visivel=False)
        except TimeoutException:
            log.error("   Menu 'Estoque' nao encontrado. Gerando diagnostico...")
            driver.switch_to.default_content()
            dump_navegacao(driver, "estoque")
            raise
    clicar(driver, item)
    time.sleep(1.5)


def passo_item_menu(driver,labelPesquisa):
    log.info("7) Abrindo 'Notas Fiscais de Entrada'")
    seletores = [(By.PARTIAL_LINK_TEXT, labelPesquisa)]
    #seletores = [
    #    (By.XPATH, "//a[contains(translate(., 'NOTAS FISCAIS DE ENTRADA', 'notas fiscais de entrada'), 'notas fiscais de entrada')]"),
    #    (By.XPATH, "//*[contains(translate(., 'NOTAS FISCAIS DE ENTRADA', 'notas fiscais de entrada'), 'notas fiscais de entrada')][self::a or self::span or self::button]"),
    #    (By.PARTIAL_LINK_TEXT, "Notas Fiscais de Entrada"),
    #    (By.PARTIAL_LINK_TEXT, "Notas de Entrada"),
    #]
    try:
        item = primeiro_elemento(driver, seletores, timeout=12)
    except TimeoutException:
        log.error("   'Notas Fiscais de Entrada' nao encontrada. Gerando diagnostico...")
        dump_navegacao(driver, "notas")
        raise
    clicar(driver, item)
    # Espera a tabela de notas carregar
    esperar(
        driver,
        EC.presence_of_element_located((By.XPATH, "//table | //tbody | //tr")),
    )
    time.sleep(2)

def passo_pesquisar_item_lancEstoque(driver,idItem:str):    
    log.info("8) Pesquisando a nota fiscal numero '%s'", idItem)
    xpath_busca = "/html/body/div[7]/div[2]/div[3]/div[1]/div[1]/div/div[1]/input"

    # Digita o numero na barra de pesquisa
    seletores_busca = [
        (By.XPATH, xpath_busca),
        (By.CSS_SELECTOR, "input[type='search']"),
        (By.CSS_SELECTOR, "input[placeholder*='esquis']"),
        (By.CSS_SELECTOR, "input[placeholder*='uscar']"),
    ]
    try:
        campo = primeiro_elemento(driver, seletores_busca, timeout=15)
    except TimeoutException:
        log.error("   Barra de pesquisa nao encontrada. Gerando diagnostico...")
        dump_navegacao(driver, "busca_nota")
        raise
    campo.clear()
    campo.send_keys(idItem)
    campo.send_keys(Keys.ENTER)
    log.info("   Aguardando resultado da pesquisa...")
    time.sleep(3)
    log.info("   clicando botão incluir lançamento...") 
    xpath_busca = "/html/body/div[7]/div[2]/div[6]/div[1]/button"
    seletores_busca = [
          (By.XPATH, xpath_busca),
          (By.CSS_SELECTOR, "input[type='search']"),
          (By.CSS_SELECTOR, "input[placeholder*='esquis']"),
          (By.CSS_SELECTOR, "input[placeholder*='uscar']"),
      ]
    try:
        campo = primeiro_elemento(driver, seletores_busca, timeout=15)
    except TimeoutException:
        log.error("   Barra de pesquisa nao encontrada. Gerando diagnostico...")
        dump_navegacao(driver, "busca_nota")
        raise

    campo.send_keys(Keys.ENTER)
       



def passo_pesquisar_e_abrir_nota(driver, numero_nota: str):
    log.info("8) Pesquisando a nota fiscal numero '%s'", numero_nota)
    xpath_busca = "/html/body/div[7]/div[11]/div[2]/div[1]/div[1]/div/div[1]/input"
    xpath_resultado = "/html/body/div[7]/div[11]/div[2]/div[9]/table/tbody/tr/td[2]"

    # Digita o numero na barra de pesquisa
    seletores_busca = [
        (By.XPATH, xpath_busca),
        (By.CSS_SELECTOR, "input[type='search']"),
        (By.CSS_SELECTOR, "input[placeholder*='esquis']"),
    ]
    try:
        campo = primeiro_elemento(driver, seletores_busca, timeout=15)
    except TimeoutException:
        log.error("   Barra de pesquisa nao encontrada. Gerando diagnostico...")
        dump_navegacao(driver, "busca_nota")
        raise
    campo.clear()
    campo.send_keys(numero_nota)
    campo.send_keys(Keys.ENTER)
    log.info("   Aguardando resultado da pesquisa...")
    time.sleep(3)

    # Clica na nota fiscal retornada
    seletores_resultado = [
        (By.XPATH, xpath_resultado),
        (By.XPATH, "(//table//tbody//tr)[1]/td[2]"),
        (By.XPATH, "(//table//tbody//tr)[1]//a"),
        (By.XPATH, "(//table//tbody//tr)[1]"),
    ]
    try:
        resultado = primeiro_elemento(driver, seletores_resultado, timeout=15)
    except TimeoutException:
        log.error("   Nota nao encontrada na pesquisa. Gerando diagnostico...")
        dump_navegacao(driver, "resultado_nota")
        raise
    clicar(driver, resultado)
    time.sleep(2.5)


def passo_rolar_itens(driver):
    log.info("9) Rolando ate a secao 'Itens da nota fiscal'")
    trocar_para_iframe_com(
        driver,
        [(By.XPATH, "//*[contains(translate(., 'ITENS DA NOTA', 'itens da nota'), 'itens da nota')]")],
    )
    try:
        secao = primeiro_elemento(
            driver,
            [
                (By.XPATH, "//*[contains(translate(., 'ITENS DA NOTA FISCAL', 'itens da nota fiscal'), 'itens da nota fiscal')]"),
                (By.XPATH, "//*[contains(translate(., 'ITENS DA NOTA', 'itens da nota'), 'itens da nota')]"),
                (By.XPATH, "//*[contains(translate(., 'ITENS', 'itens'), 'itens')]"),
            ],
            timeout=15,
        )
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", secao)
        time.sleep(1.5)
    except TimeoutException:
        log.warning("   Titulo 'Itens da nota fiscal' nao localizado; rolando a pagina.")
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight*0.6);")
        time.sleep(1.5)


def passo_clicar_clips(driver):
    log.info("10) Clicando no icone de clips "
             "(hint: 'atualizar ou criar produtos a partir da nota')")
    # O hint geralmente fica em title, aria-label ou data-original-title.
    frase = "atualizar ou criar produtos a partir da nota"
    seletores = [
        (By.XPATH, f"//*[contains(translate(@title,'ABCDEFGHIJKLMNOPQRSTUVWXYZÁÉÍÓÚÃÕÂÊÔÇ','abcdefghijklmnopqrstuvwxyzaeiouaoaeoc'),'{frase}')]"),
        (By.XPATH, f"//*[contains(translate(@aria-label,'ABCDEFGHIJKLMNOPQRSTUVWXYZÁÉÍÓÚÃÕÂÊÔÇ','abcdefghijklmnopqrstuvwxyzaeiouaoaeoc'),'{frase}')]"),
        (By.XPATH, f"//*[contains(translate(@data-original-title,'ABCDEFGHIJKLMNOPQRSTUVWXYZÁÉÍÓÚÃÕÂÊÔÇ','abcdefghijklmnopqrstuvwxyzaeiouaoaeoc'),'{frase}')]"),
        (By.XPATH, "//*[contains(@title,'criar produtos') or contains(@aria-label,'criar produtos')]"),
        # fallback por icone de clips (classes comuns de bibliotecas de icones)
        (By.XPATH, "//*[contains(@class,'clip') or contains(@class,'paperclip') or contains(@class,'fa-paperclip')]"),
        (By.XPATH, "//i[contains(@class,'icon')][contains(@class,'clip')]"),
    ]
    icone = primeiro_elemento(driver, seletores)
    # O elemento clicavel pode ser o pai do icone
    clicar(driver, icone)
    log.info("   Clique no clips realizado.")
    time.sleep(2)


# Seletor CSS unico usado tanto para localizar quanto para clicar os botoes.
_CSS_REVERTER = (
    "button[original-title='Reverter conciliação'],"
    "button.button-conciliation-done,"
    "button.botaoConciliacao.fa-check-circle,"
    "button[onclick*='reverterConciliarProduto']"
)

# JS que clica em TODOS os botoes visiveis numa unica passada sincrona
# (filtra visibilidade com offsetParent, sem round-trips por elemento) e
# retorna quantos clicou. Como o loop e sincrono, todos os handlers disparam
# antes de qualquer re-render assincrono do AJAX.
_JS_CLICAR_REVERTER = """
var sel = arguments[0];
var n = 0;
document.querySelectorAll(sel).forEach(function (b) {
    if (b.offsetParent !== null) { b.click(); n++; }
});
return n;
"""


def passo_reverter_conciliacoes(driver):
    log.info("11) Clicando em todos os botoes 'Reverter conciliacao'")
    # Garante o contexto certo (a tela pode estar dentro de um iframe).
    trocar_para_iframe_com(driver, [(By.CSS_SELECTOR, _CSS_REVERTER)])

    total = 0
    PASSADAS = 30  # backstop; normalmente termina em 1-2 passadas
    for _ in range(PASSADAS):
        try:
            n = driver.execute_script(_JS_CLICAR_REVERTER, _CSS_REVERTER)
        except Exception as e:
            log.warning("   Erro ao clicar via JS: %s", e)
            break
        # Trata eventual confirmacao (alert/confirm do navegador), se houver.
        try:
            driver.switch_to.alert.accept()
        except Exception:
            pass
        if not n:
            break  # nao restam botoes visiveis
        total += n
        log.info("   %d botao(oes) clicado(s) nesta passada.", n)
        time.sleep(1.0)  # deixa o AJAX/re-render terminar antes de reconferir
    log.info("   %d conciliacao(oes) revertida(s) no total.", total)


# Caixa de pesquisa de produto de cada item "nao encontrado" na conciliacao.
_CSS_BUSCA_PRODUTO = (
    "input[name='produtoConciliacaoSearch'],"
    "input[id^='produtoConciliacaoSearchconciliation_not_found_']"
)

# Tabela de itens da conciliacao (full XPath informado pelo usuario).
_XPATH_TBODY = "/html/body/div[7]/div[10]/div/div[2]/table/tbody"
# Posicao do codigo dentro de cada linha (relativo a tr[N]).
_XPATH_CODIGO_REL = "td[1]/div/div[3]/div[4]"

# Itens da lista de resultados que aparece ao digitar na caixa de pesquisa.
_SELETORES_RESULTADO = [
    (By.CSS_SELECTOR, ".InputSearch-results li"),
    (By.CSS_SELECTOR, ".InputSearch-options li"),
    (By.CSS_SELECTOR, "[class*='InputSearch'] li"),
    (By.CSS_SELECTOR, "[class*='InputSearch'] [class*='option']"),
    (By.CSS_SELECTOR, "[class*='InputSearch'] [class*='result']"),
    (By.CSS_SELECTOR, "[class*='autocomplete'] li"),
    (By.CSS_SELECTOR, "ul[class*='result'] li"),
    (By.CSS_SELECTOR, "[class*='dropdown'] li"),
]


def passo_conciliar_nao_encontrados(driver):
    log.info("12) Conciliando itens da tabela (leitura do codigo por full XPath)")
    trocar_para_iframe_com(driver, [(By.XPATH, _XPATH_TBODY + "/tr")])

    # Conta as linhas da tabela (tr[1]..tr[N]); N e o ultimo (ex.: 149).
    linhas = driver.find_elements(By.XPATH, _XPATH_TBODY + "/tr")
    total = len(linhas)
    if total == 0:
        log.warning("   Nenhuma linha encontrada na tabela. Gerando diagnostico...")
        dump_navegacao(driver, "conciliar_tabela")
        return
    log.info("   %d linha(s) na tabela.", total)

    conciliados = 0
    for n in range(1, total + 1):
        # Re-localiza tudo pelo XPath a cada iteracao (a tela re-renderiza).
        code_xpath = f"{_XPATH_TBODY}/tr[{n}]/{_XPATH_CODIGO_REL}"
        code_els = driver.find_elements(By.XPATH, code_xpath)
        if not code_els:
            log.info("   [%d/%d] codigo nao encontrado (sem caixa de busca?); pulando.",
                     n, total)
            continue
        # textContent pega o texto mesmo se o elemento nao estiver "visivel".
        codigo = (driver.execute_script(
            "return (arguments[0].textContent || '').trim();", code_els[0]))
        if not codigo:
            log.info("   [%d/%d] codigo vazio; pulando.", n, total)
            continue

        # Caixa de pesquisa dentro da MESMA linha.
        input_xpath = (f"{_XPATH_TBODY}/tr[{n}]"
                       "//input[@name='produtoConciliacaoSearch' or "
                       "starts-with(@id,'produtoConciliacaoSearch')]")
        campos = driver.find_elements(By.XPATH, input_xpath)
        if not campos or not campos[0].is_displayed():
            log.info("   [%d/%d] codigo '%s' sem caixa de pesquisa visivel; pulando.",
                     n, total, codigo)
            continue
        campo = campos[0]

        log.info("   [%d/%d] pesquisando codigo '%s'", n, total, codigo)
        try:
            campo.clear()
        except Exception:
            pass
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", campo)
        campo.send_keys(codigo)

        # Aguarda a lista de resultados popular e seleciona o primeiro item
        # com seta para baixo + Enter (em vez de clicar no elemento).
        time.sleep(2.0)
        campo.send_keys(Keys.ARROW_DOWN)
        time.sleep(0.3)
        campo.send_keys(Keys.ENTER)
        conciliados += 1
        log.info("   [%d/%d] item selecionado (seta baixo + Enter).", n, total)
        time.sleep(1.0)  # deixa o re-render terminar antes do proximo
    log.info("   %d item(ns) conciliado(s) no total.", conciliados)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    parser = argparse.ArgumentParser(description="RPA Bling - produtos a partir de NF de entrada")
    parser.add_argument("--user", default=USUARIO_PADRAO, help="E-mail de login")
    parser.add_argument("--nota", default=None, help="Numero da nota fiscal a pesquisar")
    parser.add_argument("--headless", action="store_true", help="Rodar sem janela")
    parser.add_argument("--keep-open", action="store_true", default=True,
                        help="Manter o navegador aberto ao final (padrao)")
    args = parser.parse_args()

    # Numero da nota fiscal (pergunta no inicio se nao veio por argumento)
    numero_nota = args.nota or input("Numero da nota fiscal a pesquisar: ").strip()
    if not numero_nota:
        log.error("Numero da nota vazio. Abortando.")
        sys.exit(1)

    # Senha via prompt seguro (nao aparece na tela, nao fica no codigo)
    #senha = getpass.getpass(f"Senha do Bling para {args.user}: ")
    senha = "El14151617.$"
    if not senha:
        log.error("Senha vazia. Abortando.")
        sys.exit(1)

    driver = montar_driver(args.headless)
    try:
        passo_abrir_site(driver)
        passo_clicar_login(driver)
        passo_digitar_usuario(driver, args.user)
        passo_digitar_senha(driver, senha)
        passo_aguardar_login(driver)
        passo_menu_estoque(driver)
        passo_item_menu(driver,"Notas fiscais de entrada")
        #passo_item_menu(driver,"Lançamentos de estoque")
        passo_pesquisar_e_abrir_nota(driver, numero_nota)
        #passo_pesquisar_item_lancEstoque(driver,"510801-G")
        passo_rolar_itens(driver)
        passo_clicar_clips(driver)
        passo_reverter_conciliacoes(driver)
        passo_conciliar_nao_encontrados(driver)
        log.info("FLUXO CONCLUIDO. Verifique a janela do navegador.")
    except Exception as e:
        log.exception("Falha durante o RPA: %s", e)
        try:
            driver.save_screenshot("erro_rpa_bling.png")
            log.info("Screenshot do erro salvo em erro_rpa_bling.png")
        except Exception:
            pass
    finally:
        input("\nPressione ENTER para encerrar e fechar o navegador...")
        driver.quit()


if __name__ == "__main__":
    main()
