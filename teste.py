import cv2
import numpy as np
from pathlib import Path
from typing import Tuple, Dict, Optional

def encontrar_imagem_em_png(
    caminho_png: str, 
    caminho_template: str, 
    limiar_confianca: float = 0.8,
    metodo: str = 'TM_CCOEFF_NORMED'
) -> Dict[str, any]:
    # Validar arquivos
    
    if not Path(caminho_png).exists():
        print('aqui')
        raise FileNotFoundError(f"Arquivo PNG não encontrado: {caminho_png}")
    if not Path(caminho_template).exists():
        raise FileNotFoundError(f"Template não encontrado: {caminho_template}")
    
    # Carregar imagens
    imagem_principal = cv2.imread(caminho_png)
    template = cv2.imread(caminho_template)
    
    if imagem_principal is None:
        raise ValueError(f"Não foi possível ler: {caminho_png}")
    if template is None:
        raise ValueError(f"Não foi possível ler: {caminho_template}")
    
    # Converter para escala de cinza (melhora o matching)
    img_cinza = cv2.cvtColor(imagem_principal, cv2.COLOR_BGR2GRAY)
    template_cinza = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    
    # Validar dimensões
    if template_cinza.shape[0] > img_cinza.shape[0] or \
       template_cinza.shape[1] > img_cinza.shape[1]:
        return {
            'encontrado': False,
            'confianca': 0.0,
            'posicao': None,
            'area': None,
            'motivo': 'Template maior que a imagem principal'
        }
    
    # Selecionar método
    metodos_disponiveis = {
        'TM_CCOEFF_NORMED': cv2.TM_CCOEFF_NORMED,
        'TM_SQDIFF_NORMED': cv2.TM_SQDIFF_NORMED,
        'TM_CCORR_NORMED': cv2.TM_CCORR_NORMED,
    }
    
    metodo_cv = metodos_disponiveis.get(metodo, cv2.TM_CCOEFF_NORMED)
    
    # Fazer template matching
    resultado = cv2.matchTemplate(img_cinza, template_cinza, metodo_cv)
    
    # Encontrar melhor match
    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(resultado)
    
    # Para TM_SQDIFF_NORMED, valores menores são melhores
    if metodo == 'TM_SQDIFF_NORMED':
        confianca = 1 - min_val
        posicao = min_loc
    else:
        confianca = max_val
        posicao = max_loc
    
    # Obter dimensões do template
    altura_template, largura_template = template_cinza.shape
    
    # Verificar se confiança atende ao limiar
    encontrado = confianca >= limiar_confianca
    
    return {
        'encontrado': encontrado,
        'confianca': round(confianca, 4),
        'posicao': posicao if encontrado else None,
        'area': (largura_template, altura_template),
        'limiar_usado': limiar_confianca
    }


def encontrar_multiplas_imagens(
    caminho_png: str,
    caminho_template: str,
    limiar_confianca: float = 0.8
) -> Dict[str, any]:
    """
    Encontra TODAS as ocorrências de uma imagem dentro do PNG.
    
    Args:
        caminho_png: Caminho do arquivo PNG principal
        caminho_template: Caminho da imagem a ser procurada
        limiar_confianca: Valor mínimo de confiança
    
    Returns:
        Dict com lista de todas as ocorrências encontradas
    """
    
    imagem_principal = cv2.imread(caminho_png)
    template = cv2.imread(caminho_template)
    
    if imagem_principal is None or template is None:
        raise ValueError("Erro ao carregar imagens")
    
    img_cinza = cv2.cvtColor(imagem_principal, cv2.COLOR_BGR2GRAY)
    template_cinza = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    
    resultado = cv2.matchTemplate(img_cinza, template_cinza, cv2.TM_CCOEFF_NORMED)
    
    # Encontrar todos os matches acima do limiar
    localizacoes = np.where(resultado >= limiar_confianca)
    
    matches = []
    altura_template, largura_template = template_cinza.shape
    
    for pt in zip(*localizacoes[::-1]):
        confianca = resultado[pt[1], pt[0]]
        matches.append({
            'posicao': pt,
            'confianca': round(float(confianca), 4),
            'area': (largura_template, altura_template)
        })
    
    return {
        'total_encontrado': len(matches),
        'matches': matches,
        'limiar_usado': limiar_confianca
    }


#resultado = encontrar_imagem_em_png(
#    "recorte1.png",
#    "carimbo.png",
#    limiar_confianca=0.3
#)

#print(resultado)

#resultado = encontrar_imagem_em_png(
#    "recorte2.png",
#    "identificacaoPdfNaPagina.png",
#    limiar_confianca=0.3
#)



#resultado = encontrar_imagem_em_png(
#    "recorte.png",
#    "identificacaoPdfNaPagina.png",
#    limiar_confianca=0.6
#)
#print(resultado)
#
#resultado = encontrar_imagem_em_png(
#    "recorteManual.png",
#    "identificacaoPdfNaPagina.png",
#    limiar_confianca=0.6
#)
#print(resultado['encontrado'])

# {'encontrado': True, 'confianca': 0.9234, 'posicao': (100, 50), ...}