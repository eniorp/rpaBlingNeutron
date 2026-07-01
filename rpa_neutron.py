
from datetime import datetime
from selenium.webdriver.common.action_chains import ActionChains

from selenium.webdriver.common.keys import Keys
import argparse
import random
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

import os
import re
import unicodedata

import cv2
import numpy as np
import pytesseract

from posicaoCarimboPDF import (
    clicar_espaco_em_branco_pdf,
    screenshot_para_numpy,
    encontrar_area_pdf_na_tela,
)
from teste import encontrar_imagem_em_png
from verificaTemAprovado import tem_aprovado

# --------------------------------------------------------------------------- #
# Configuracao
# --------------------------------------------------------------------------- #
URL_NEUTRON = "https://neutron.ufenesp.com.br/Neutron/nclienteweb/Account/Login"
USUARIO_PADRAO = "enio.silveira"
TIMEOUT = 30  # segundos para esperas explicitas

# Caminho do executavel do Tesseract-OCR (necessario para o reconhecimento da
# palavra "APROVADO" no recorte do PDF). Pode ser sobrescrito pela variavel de
# ambiente TESSERACT_CMD. Se nao informado, usa o que estiver no PATH.
TESSERACT_CMD = os.environ.get("TESSERACT_CMD")
if not TESSERACT_CMD:
    for _cand in (
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
    ):
        if os.path.isfile(_cand):
            TESSERACT_CMD = _cand
            break
if TESSERACT_CMD:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD

# Idioma do OCR e pasta de "traineddata". Se existir uma pasta ./tessdata ao
# lado do script (com por.traineddata baixado), ela e usada via --tessdata-dir.
# O idioma "por" e o preferido; se nao estiver disponivel, cai para "eng"
# (a palavra "aprovado" usa apenas letras latinas comuns).
OCR_LANG = os.environ.get("OCR_LANG", "por")
_TESSDATA_LOCAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tessdata")

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
    log.info("1) Abrindo %s", URL_NEUTRON)
    driver.get(URL_NEUTRON)
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
        (By.XPATH, "/html/body/div/div/div/div[1]/div/div/form/div[2]/input")
        
    ]
    try:
        botao = primeiro_elemento(driver, seletores, timeout=15)
        clicar(driver, botao)
    except TimeoutException:
        # fallback: ir direto para a pagina de login
        log.warning("Botao de login nao encontrado, indo direto para /login")
        driver.get("https://www.bling.com.br/login")




def duplo_clique_linha(driver, elemento):
    """Duplo clique na primeira célula da linha (método que funciona com DevExpress)."""
    try:
        from selenium.webdriver.common.action_chains import ActionChains
        primeira_celula = elemento.find_element(By.XPATH, ".//td[1]")
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", primeira_celula)
        ActionChains(driver).double_click(primeira_celula).perform()
        log.info("Duplo clique via double_click na primeira célula")
    except Exception as e:
        log.error(f"Duplo clique falhou: {e}")
        raise

def passo_duplo_clique_tarefas(driver):
    log.info("Iterando linhas da tabela de tarefas e dando duplo clique")

    XPATH_LINHAS = "//tr[contains(@id, 'tarefasGridView_DXDataRow')]"

    def buscar_linhas():
        # Garante que está no contexto principal antes de buscar
        driver.switch_to.default_content()
        return driver.find_elements(By.XPATH, XPATH_LINHAS)

    try:
        driver.switch_to.default_content()
        primeiro_elemento(driver, [(By.XPATH, XPATH_LINHAS)], timeout=15)

        total = len(buscar_linhas())
        log.info(f"Encontradas {total} linhas")
        i = 0
        while total > 0: 
           #for i in range(total):
            try:
                
                # Volta para o documento principal antes de cada iteração
                driver.switch_to.default_content()
                i = i + 1
                linhas = buscar_linhas()

                #if i >= len(linhas):
                #    log.warning(f"Linha {i} não existe mais, encerrando")
                #    break

                #linha = linhas[i]
                linha = linhas[5]
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", linha)

                # Move o iframe para fora do caminho antes de clicar
                driver.execute_script("""
                    var iframe = document.getElementById('webviewer-1');
                    if (iframe) iframe.style.pointerEvents = 'none';
                """)

                time.sleep(0.3)
                duplo_clique_linha(driver, linha)
                log.info(f"Duplo clique na linha 0")


                # Reabilita o iframe após o clique
                driver.execute_script("""
                    var iframe = document.getElementById('webviewer-1');
                    if (iframe) iframe.style.pointerEvents = 'auto';
                """)

                tempo = random.randint(5, 10 )
                print(f'aguardando por {tempo} segundos..')
                time.sleep(tempo)                
                
                passo_menu_abrirCarimbo(driver)
                passo_selecionar_aprovado(driver)
                #clicar_link_pdf(driver)
                time.sleep(2)
                if not palavra_presente(driver, debug=True,imagemCompara='identificacaoPdfNaPagina.png'):
                    if not palavra_presente(driver, debug=True,imagemCompara='identificacaoPdfNaPagina1_1.png'):
                       print('nao achou numeracao de paginas..')
                       i = 1 + 1
                       continue
                print('achou numeracao de paginas..')
                if (clicar_espaco_em_branco_pdf(driver, debug=True)):
                   time.sleep(3)
                   # So salva se a palavra APROVADO for encontrada no PDF.                   
                   achouCarimbo = False
                   while not achouCarimbo:
                    if palavra_presente(driver, debug=True,imagemCompara='carimbo.png'):
                       achouCarimbo = True
                       print('salvou')                       
                       
                    elif palavra_presente(driver, debug=True,imagemCompara='carimboAlternativo.png'):
                       achouCarimbo = True
                       print('salvou - carimbo alternativo')
                    elif palavra_presente(driver, debug=True,imagemCompara='carimboAlternativo1.png'):
                       achouCarimbo = True
                       print('salvou - carimbo alternativo 1')
   
                    else:
                       print('não salvou..palavra APROVADO não encontrada no PDF..')
                       input("Pressione ENTER para continuar...")
                   time.sleep(3)
                   if achouCarimbo:
                     passo_menu_salvar(driver)    
                else:
                    print('não salvou..nao achou pdf na tela..')
                
                
                #clicar_se_existir(driver, By.ID, "pageWidgetContainer1", offset_x_pct=0.80, offset_y_pct=0.85, timeout=15)
                #if clicar_se_existir(driver, By.ID, "pageWidgetContainer1", timeout=15):                   
                #   print('verificar esse elemento pageWidgetContainer1 contem o pdf, ou se existe outro elemento, quando da o problema de nao aparecer o pdf, se existir testar o elemento mas precisa clicar nesse senao vai para o site da prefeitura ')
                   

                total = len(buscar_linhas())
                if i >= 8: 
                    
                    break


            except StaleElementReferenceException:
                log.warning(f"Linha  ainda stale, pulando...")
                driver.switch_to.default_content()
                continue
            except Exception as e:
                log.error(f"Erro na linha : {e}")
                driver.switch_to.default_content()
                continue

    except TimeoutException:
        log.warning("Tabela de tarefas não encontrada")
    finally:
        driver.switch_to.default_content()
        
def passo_digitar_usuario(driver, usuario: str):
    log.info("3) Digitando usuario: %s", usuario)
    seletores = [
        (By.XPATH, "/html/body/div/div/div/div[1]/div/div/form/div[2]/input"),
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
        (By.XPATH,"/html/body/div/div/div/div[1]/div/div/form/div[3]/input"),        
        (By.CSS_SELECTOR, "input[type='password']"),
        (By.CSS_SELECTOR, "input[name='password']"),
        (By.CSS_SELECTOR, "input[name='senha']"),
        (By.ID, "password"),
        (By.ID, "senha"),
    ]
    campo = primeiro_elemento(driver, seletores)
    campo.clear()
    campo.send_keys(senha)
    seletores = [
        (By.XPATH,"/html/body/div/div/div/div[1]/div/div/form/div[4]/input")
    ]
    campo = primeiro_elemento(driver, seletores)
    campo.clear()
    campo.send_keys("008")


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





def passo_menu_selecionarAprovado(driver):
    log.info("6) Abrindo menu 'Estoque'")
    seletores = [
        
        (By.XPATH, "/html/body/div[3]/div/div[2]/div/div[2]/div/div/div/div[1]/input")
        
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
            log.error("   Menu 'passo_menu_selecionarAprovado' nao encontrado. Gerando diagnostico...")
            driver.switch_to.default_content()
            dump_navegacao(driver, "estoque")
            raise
    clicar(driver, item)
    time.sleep(1.5)

def passo_selecionar_aprovado(driver):

    time.sleep(1)
    print('aquardando 5 segundos..')
    driver.switch_to.active_element.send_keys(Keys.TAB)
    time.sleep(0.2)
    driver.switch_to.active_element.send_keys(Keys.TAB)
    time.sleep(0.2)
    ativo = driver.switch_to.active_element

    ativo.send_keys(Keys.ARROW_DOWN)

    time.sleep(0.2)
    driver.switch_to.active_element.send_keys(Keys.TAB)

    # seleciona APROVADO (primeiro item)
    ativo.send_keys(Keys.ENTER)

    log.info("Carimbo APROVADO confirmado")

#def passo_selecionar_aprovado(driver):
#    log.info("Selecionando carimbo APROVADO")
#
#    wait = WebDriverWait(driver, 15)
#
#    # Abre o combo
#    combo = wait.until(
#        EC.element_to_be_clickable((By.ID, "comboCarimbo"))
#    )
#    combo.click()
#
#    # Seleciona APROVADO
#    aprovado = wait.until(
#        EC.element_to_be_clickable(
#            (By.XPATH, "//div[contains(@class,'dx-list-item-content') and normalize-space()='APROVADO']")
#        )
#    )
#    aprovado.click()
#
#    # Clica em Confirmar
#    confirmar = wait.until(
#        EC.element_to_be_clickable(
#            (By.ID, "stampButton")
#        )
#    )
#    confirmar.click()
#
#    log.info("Carimbo APROVADO confirmado com sucesso")


def passo_menu_salvar(driver):
    

    seletores = [
        (By.XPATH, "/html/body/div[1]/div[1]/div[1]/div/button[10]")
    ]

    trocar_para_iframe_com(driver, seletores)

    try:
        try:
            item = primeiro_elemento(driver, seletores, timeout=12, visivel=True)
        except TimeoutException:
            log.info("   Item visivel nao achado; tentando item oculto/colapsado.")
            item = primeiro_elemento(driver, seletores, timeout=6, visivel=False)

        clicar(driver, item)
        print('documento salvo..')
    except TimeoutException:
        log.error("   Menu 'passo_menu_salvar' nao encontrado. Gerando diagnostico...")
        driver.switch_to.default_content()
        dump_navegacao(driver, "estoque")
        raise


def passo_menu_abrirCarimbo(driver):
    

    seletores = [
        (By.XPATH, "//button[@data-element='salvaAnotacoesDocumento']"),
        (By.XPATH, "//button[@aria-label='Carimbos']"),
        (By.XPATH, "//button[contains(@class,'ActionButton') and @aria-label='Carimbos']"),
    ]

    trocar_para_iframe_com(driver, seletores)

    try:
        try:
            item = primeiro_elemento(driver, seletores, timeout=12, visivel=True)
        except TimeoutException:
            log.info("   Item visivel nao achado; tentando item oculto/colapsado.")
            item = primeiro_elemento(driver, seletores, timeout=6, visivel=False)

        clicar(driver, item)

        log.info("Primeiro item selecionado.")

    except TimeoutException:
        log.error("   Menu 'Estoque' nao encontrado. Gerando diagnostico...")
        driver.switch_to.default_content()
        dump_navegacao(driver, "estoque")
        raise

def passo_menu_paraAnalise(driver):
    log.info("6) Abrindo menu 'para Analise'")
    seletores = [
        # XPath real informado: 'Estoque' e uma <div> no header/nav
        (By.XPATH, "/html/body/table/tbody/tr/td[1]/div/table/tbody/tr/td/div/div[4]/div/div/div/div/div/div[2]/div/ul/li/form/h/input")
        
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
def passo_menu_processos(driver):
    log.info("6) Abrindo menu 'Estoque'")
    seletores = [
        # XPath real informado: 'Estoque' e uma <div> no header/nav
        (By.XPATH, "/html/body/table/tbody/tr/td[1]/div/table/tbody/tr/td/div/nav/div/div[2]/ul/li[1]/a")
        
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

def passo_abrir_combo(driver):
    log.info("Abrindo combo de seleção")

    seletores = [
        (By.XPATH, "//input[contains(@class,'dx-texteditor-input')]"),
        (By.XPATH, "//input[contains(@placeholder,'Selecione')]"),
        (By.XPATH, "//input[@role='combobox']"),
        (By.CSS_SELECTOR, "input.dx-texteditor-input"),
    ]    

    try:
        campo = primeiro_elemento(driver, seletores, timeout=12, visivel=True)
        clicar(driver, campo)

        log.info("Combo aberto com sucesso")

    except TimeoutException:
        log.warning("Campo de seleção não encontrado")
        raise


def passo_selecionar_primeiro_item(driver):
    log.info("Selecionando primeiro item da lista")

    try:
        # Abre o combo
        seletores_combo = [
            (By.XPATH, "//input[@placeholder='Selecione ...']"),
            (By.XPATH, "//input[@role='combobox']"),
            (By.XPATH, "//input[contains(@class,'dx-texteditor-input')]"),
        ]

        combo = primeiro_elemento(
            driver,
            seletores_combo,
            timeout=12,
            visivel=True
        )

        clicar(driver, combo)

        # Seleciona o primeiro item
        combo.send_keys(Keys.ARROW_DOWN)
        combo.send_keys(Keys.ENTER)

        log.info("Primeiro item selecionado")

        # Clica no botão Confirmar
        seletores_confirmar = [
            (By.XPATH, "//span[text()='Confirmar']/ancestor::div[contains(@class,'dx-button-content')]"),
            (By.XPATH, "//span[text()='Confirmar']"),
            (By.XPATH, "//div[contains(@class,'dx-button-content')]//span[text()='Confirmar']"),
        ]

        btn_confirmar = primeiro_elemento(
            driver,
            seletores_confirmar,
            timeout=12,
            visivel=True
        )

        clicar(driver, btn_confirmar)

        log.info("Botão Confirmar acionado")

    except TimeoutException:
        log.warning("Não foi possível selecionar o item ou confirmar")
        raise




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


def clicar_se_existir(driver, by, valor, offset_x_pct=0.80, offset_y_pct=0.85, timeout=10):
    """
    Clica em uma posição relativa dentro do elemento, evitando áreas críticas.
    
    offset_x_pct: posição horizontal (0.0 = esquerda, 1.0 = direita)
    offset_y_pct: posição vertical  (0.0 = topo,     1.0 = rodapé)
    """
    try:
        elemento = WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((by, valor))
        )
        
        largura  = elemento.size['width']
        altura   = elemento.size['height']
        
        # Calcula offset relativo ao centro (ActionChains usa offset do centro)
        offset_x = int((offset_x_pct - 0.5) * largura)
        offset_y = int((offset_y_pct - 0.5) * altura)
        
        ActionChains(driver)\
            .move_to_element_with_offset(elemento, offset_x, offset_y)\
            .click()\
            .perform()
        
        print(f"Clicou na posição relativa ({offset_x_pct*100:.0f}%, {offset_y_pct*100:.0f}%) do elemento")
        return True
    except Exception as e:
        print(f"Não foi possível clicar: {e}")
        return False
    
#def clicar_se_existir(driver, by, valor, timeout=10):
#    """Tenta clicar no elemento se ele existir e estiver clicável."""
#    try:
#        # Espera explícita é melhor que time.sleep
#        from selenium.webdriver.support.ui import WebDriverWait
#        from selenium.webdriver.support import expected_conditions as EC
#        
#        elemento = WebDriverWait(driver, timeout).until(
#            EC.element_to_be_clickable((by, valor))
#        )
#        elemento.click()
#        return True
#    except Exception as e:
#        print(f"Não foi possível clicar: {e}")
#        return False

def clicar_link_pdf(driver):
    driver.find_element(By.ID, "pageWidgetContainer1").click()

    log.info("Link clicado com sucesso")


def _normalizar_texto(texto: str) -> str:
    """Minusculas, sem acentos e sem caracteres nao alfanumericos (vira espaco)."""
    texto = unicodedata.normalize("NFKD", texto)
    texto = texto.encode("ascii", "ignore").decode("ascii")
    texto = texto.lower()
    return re.sub(r"[^a-z0-9]+", " ", texto)


def palavra_presente(driver, debug: bool = True, imagemCompara: str = '') -> bool:
    """Verifica, via OCR, se a palavra 'APROVADO' aparece no recorte do PDF."""
    try:
        img = screenshot_para_numpy(driver)
    except Exception as e:
        log.warning("Falha ao capturar a tela para checar o carimbo: %s", e)
        return False
    
    
    # Mesma logica/etapa ja usada no programa para achar a area do PDF.
    area = encontrar_area_pdf_na_tela(img)
    x0, y0 = area['x'], area['y']
    larg, alt = area['largura'], area['altura']

    recorte = img[y0:y0 + alt, x0:x0 + larg]
    nome = f"recorte_{datetime.now():%Y%m%d_%H%M%S}.png"
    cv2.imwrite(nome, recorte)
    
    resultado = encontrar_imagem_em_png(nome,
                                   imagemCompara,
                                   limiar_confianca=0.6)
    return resultado['encontrado']                                   


    
    if recorte.size == 0:
        log.warning("Area do PDF vazia; nao foi possivel checar a palavra.")
        return False
    #return tem_aprovado(recorte,palavra=palavra)
    # Pre-processamento leve: escala de cinza ampliada 2x. Deixamos o proprio
    # Tesseract fazer a binarizacao interna (lida melhor com o texto branco do
    # carimbo do que um threshold global). Tentamos a imagem normal e, so se
    # necessario, a invertida (texto claro sobre fundo escuro).
    cinza = cv2.cvtColor(recorte, cv2.COLOR_BGR2GRAY)
    cinza = cv2.resize(cinza, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
    invertido = cv2.bitwise_not(cinza)

    # Monta o config do OCR: psm 3 (segmentacao automatica de pagina) e, se
    # existir a pasta local com o por.traineddata, aponta o --tessdata-dir.
    config_ocr = "--psm 3"
    if os.path.isdir(_TESSDATA_LOCAL):
        config_ocr += f' --tessdata-dir "{_TESSDATA_LOCAL}"'

    for nome, imagem in (("normal", cinza), ("invertido", invertido)):
        texto = None
        for lang in (OCR_LANG, "eng"):
            try:
                texto = pytesseract.image_to_string(imagem, lang=lang, config=config_ocr)
                break  # OCR funcionou com este idioma
            except pytesseract.TesseractNotFoundError:
                log.error(
                    "Tesseract-OCR nao encontrado. Instale o binario e/ou defina "
                    "a variavel de ambiente TESSERACT_CMD apontando para tesseract.exe."
                )
                return False
            except pytesseract.TesseractError as e:
                # Idioma indisponivel: tenta o proximo (ex.: 'por' -> 'eng').
                log.warning("OCR com lang='%s' falhou (%s); tentando proximo idioma.", lang, e)
                continue
            except Exception as e:
                log.warning("Falha no OCR (%s): %s", nome, e)
                break

        if texto is None:
            continue
        with open("resultado.txt", "w", encoding="utf-8") as arquivo:
           arquivo.write(texto)
        #if palavra in _normalizar_texto(texto):
        if palavra in texto:
            log.info(f"Palavra {palavra} encontrada no recorte (OCR %s).", nome)
            if debug:
                try:
                    cv2.imwrite("debug_aprovado_ocr.png", imagem)
                except Exception:
                    pass
            return True

    log.info(f"Palavra {palavra} NAO encontrada no recorte do PDF.")
    return False
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
    #numero_nota = args.nota or input("Numero da nota fiscal a pesquisar: ").strip()
    #if not numero_nota:
    #    log.error("Numero da nota vazio. Abortando.")
    #    sys.exit(1)

    # Senha via prompt seguro (nao aparece na tela, nao fica no codigo)
    #senha = getpass.getpass(f"Senha do Bling para {args.user}: ")
    senha = "Esilveira008"
    if not senha:
        log.error("Senha vazia. Abortando.")
        sys.exit(1)

    driver = montar_driver(args.headless)
    try:
        passo_abrir_site(driver)
        #passo_clicar_login(driver)
        passo_digitar_usuario(driver, args.user)
        passo_digitar_senha(driver, senha)
        #passo_aguardar_login(driver)
        passo_menu_processos(driver)
        passo_menu_paraAnalise(driver)
        passo_duplo_clique_tarefas(driver)
        #passo_menu_selecionarAprovado(driver)
        #passo_abrir_combo(driver)
        #passo_selecionar_primeiro_item(driver)
        ##passo_item_menu(driver,"Notas fiscais de entrada")
        #passo_item_menu(driver,"Lançamentos de estoque")
        ##passo_pesquisar_e_abrir_nota(driver, numero_nota)
        #passo_pesquisar_item_lancEstoque(driver,"510801-G")
        #passo_rolar_itens(driver)
        #passo_clicar_clips(driver)
        #passo_reverter_conciliacoes(driver)
        #passo_conciliar_nao_encontrados(driver)
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
