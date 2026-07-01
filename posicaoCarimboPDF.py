import cv2
import numpy as np
from PIL import Image
import io
from pathlib import Path
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
import pyautogui
import time

EXTENSOES_IMAGEM = {'.png', '.jpg', '.jpeg', '.bmp', '.webp', '.tif', '.tiff'}


def screenshot_para_numpy(driver) -> np.ndarray:
    """Captura screenshot da tela inteira como numpy array BGR."""
    png = driver.get_screenshot_as_png()
    img = Image.open(io.BytesIO(png))
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def encontrar_area_pdf_na_tela(img: np.ndarray) -> dict:
    """
    Tenta localizar o painel do PDF automaticamente
    buscando a maior região com fundo branco/cinza claro
    no lado direito da tela.
    
    Retorna: {'x', 'y', 'largura', 'altura'} em pixels da tela
    """
    h, w = img.shape[:2]
    
    # Foca na metade direita da tela
    metade_direita = img[:, w // 2:]
    gray = cv2.cvtColor(metade_direita, cv2.COLOR_BGR2GRAY)
    
    # Detecta fundo claro (área do viewer do PDF)
    _, mask = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
    
    # Encontra o maior bloco contínuo branco
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (50, 50))
    mask_fechada = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    
    contornos, _ = cv2.findContours(
        mask_fechada, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    
    if not contornos:
        # Fallback: assume metade direita inteira
        return {'x': w // 2, 'y': 0, 'largura': w // 2, 'altura': h}
    
    maior = max(contornos, key=cv2.contourArea)
    x, y, larg, alt = cv2.boundingRect(maior)
    
    return {
        'x': x + w // 2,  # ajusta offset da metade direita
        'y': y,
        'largura': larg,
        'altura': alt
    }


def encontrar_espaco_em_branco_no_recorte(
    img: np.ndarray,
    area_pdf: dict,
    largura_min: int = 60,
    altura_min: int = 20,
    preferir_lado: str = 'direita',   # 'direita' | 'esquerda' | 'qualquer'
    threshold_branco: int = 245,
    debug_path: str = None
) -> dict | None:
    """
    Dentro da área do PDF na tela, encontra a melhor
    região em branco para posicionar o carimbo.
    """
    x0 = area_pdf['x']
    y0 = area_pdf['y']
    larg_pdf = area_pdf['largura']
    alt_pdf  = area_pdf['altura']
    
    # Recorta só a área do PDF
    recorte = img[y0:y0 + alt_pdf, x0:x0 + larg_pdf]
    gray    = cv2.cvtColor(recorte, cv2.COLOR_BGR2GRAY)
    
    # Máscara de pixels brancos
    _, mask_branco = cv2.threshold(gray, threshold_branco, 255, cv2.THRESH_BINARY)
    
    # Dilata o conteúdo (texto/linhas) para "bloquear" área em volta
    kernel_bloqueia = cv2.getStructuringElement(cv2.MORPH_RECT, (4, 4))
    mask_conteudo   = cv2.dilate(cv2.bitwise_not(mask_branco), kernel_bloqueia, iterations=3)
    mask_livre      = cv2.bitwise_not(mask_conteudo)
    
    # Erode para garantir que a região tem o tamanho mínimo necessário
    kernel_erosao = cv2.getStructuringElement(cv2.MORPH_RECT, (largura_min, altura_min))
    mask_regioes  = cv2.erode(mask_livre, kernel_erosao)
    
    contornos, _ = cv2.findContours(
        mask_regioes, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    
    regioes = []
    for cnt in contornos:
        x, y, w, h = cv2.boundingRect(cnt)
        if w < largura_min or h < altura_min:
            continue
        regioes.append({
            'x_local': x, 'y_local': y,
            'largura': w, 'altura': h,
            'area': w * h,
            'centro_x_local': x + w // 2,
            'centro_y_local': y + h // 2,
            # Coordenadas absolutas na tela
            'centro_x_tela': x0 + x + w // 2,
            'centro_y_tela': y0 + y + h // 2,
        })
    
    if not regioes:
        print("⚠️  Nenhuma região em branco encontrada no PDF.")
        return None
    
    # Preferência por lado direito (onde o PDF geralmente tem espaço)
    if preferir_lado == 'direita':
        regioes.sort(key=lambda r: (-r['centro_x_local'], -r['area']))
    elif preferir_lado == 'esquerda':
        regioes.sort(key=lambda r: (r['centro_x_local'], -r['area']))
    else:
        regioes.sort(key=lambda r: -r['area'])
    
    melhor = regioes[0]
    
    # Debug: salva imagem anotada
    if debug_path:
        debug = recorte.copy()
        for i, r in enumerate(regioes[:5]):
            cor = (0, 200, 0) if i == 0 else (0, 180, 220)
            cv2.rectangle(debug,
                          (r['x_local'], r['y_local']),
                          (r['x_local'] + r['largura'], r['y_local'] + r['altura']),
                          cor, 2)
            cv2.putText(debug, f"#{i+1}", (r['x_local'] + 5, r['y_local'] + 22),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, cor, 2)
        cv2.imwrite(debug_path, debug)
        print(f"Debug salvo em: {debug_path}")
    
    return melhor


#def clicar_espaco_em_branco_pdf(
#    driver,
#    elemento_id: str = "pageWidgetContainer1",
#    preferir_lado: str = 'direita',
#    debug: bool = True,
#    timeout: int = 15
#) -> bool:
#    """
#    Fluxo completo via screenshot:
#    1. Captura tela
#    2. Localiza painel do PDF
#    3. Encontra região em branco
#    4. Clica usando ActionChains com coordenadas absolutas
#    """
#    print("📸 Capturando screenshot...")
#    img = screenshot_para_numpy(driver)
#    
#    print("🔍 Localizando painel do PDF na tela...")
#    area_pdf = encontrar_area_pdf_na_tela(img)
#    print(f"   Área PDF: x={area_pdf['x']}, y={area_pdf['y']}, "
#          f"{area_pdf['largura']}x{area_pdf['altura']}px")
#    
#    debug_path = "/tmp/debug_carimbo.png" if debug else None
#    print('passou')
#    print("🔎 Buscando espaço em branco...")
#    regiao = encontrar_espaco_em_branco_no_recorte(
#        img, area_pdf,
#        preferir_lado=preferir_lado,
#        debug_path=debug_path
#    )
#    
#    if regiao is None:
#        print("❌ Sem espaço em branco — abortando.")
#        return False
#    
#    cx = regiao['centro_x_tela']
#    cy = regiao['centro_y_tela']
#    print(f"✅ Clicando em ({cx}, {cy}) na tela")
#    
#    # Clica via coordenadas absolutas da tela
#    # (não depende de elemento específico)
#    body = driver.find_element(By.TAG_NAME, "body")
#    ActionChains(driver)\
#        .move_to_element_with_offset(body, 0, 0)\
#        .move_by_offset(cx, cy)\
#        .click()\
#        .perform()
#    
#    return True



def clicar_espaco_em_branco_pdf(
    driver,
    preferir_lado: str = 'direita',
    debug: bool = True,
) -> bool:
    
    
    img = screenshot_para_numpy(driver)    
    cv2.imwrite("/tmp/screenshot_debug.png", img)
    
    
    area_pdf = encontrar_area_pdf_na_tela(img)
    
    debug_path = "debug_carimbo.png"
    
    time.sleep(1)
    
    regiao = encontrar_espaco_em_branco_no_recorte(
        img, area_pdf,
        preferir_lado=preferir_lado,
        debug_path=debug_path
    )
    
    if regiao is None:
        print("❌ Sem espaço em branco — abortando.")
        return False

   
    cx = regiao['centro_x_tela']
    cy = regiao['centro_y_tela']
    
    # ── Ajuste de escala para telas HiDPI/4K ─────────────────────────────
    # Screenshot do Selenium pode ter resolução maior que a tela física
    # (ex: tela 1920px mas screenshot 3840px em displays 2x)
    tela_larg, tela_alt = pyautogui.size()
    screenshot_larg = img.shape[1]
    screenshot_alt  = img.shape[0]
    
    escala_x = tela_larg / screenshot_larg
    escala_y = tela_alt  / screenshot_alt
    
    cx_real = int(cx * escala_x)
    cy_real = int(cy * escala_y)
    
    
    # ── Clique via pyautogui (coordenadas absolutas da tela física) ───────
    pyautogui.moveTo(cx_real, cy_real, duration=0.3)
    time.sleep(0.2)  # aguarda o cursor chegar
    pyautogui.click()
    
    return True




# ── USO ──────────────────────────────────────────────────────────────────────
# clicar_espaco_em_branco_pdf(driver, debug=True)
# percorrer_imgs_encontrar_area_pdf()
