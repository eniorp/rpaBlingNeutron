"""
Função OCR para verificar se a palavra "APROVADO" existe em uma imagem.

Requisitos:
    pip install easyocr numpy Pillow
"""

import numpy as np
from PIL import Image
import re

# Inicializa o leitor OCR globalmente para reutilização (melhor performance)
_reader = None

def _get_reader():
    """Retorna o leitor EasyOCR inicializado (lazy loading)."""
    global _reader
    if _reader is None:
        import easyocr
        # 'pt' = português, cobre bem o alfabeto usado em "APROVADO"
        # gpu=False para funcionar em CPU (mais compatível)
        _reader = easyocr.Reader(['pt'], gpu=False, verbose=False)
    return _reader


def tem_aprovado(img, case_sensitive=False, partial_match=False,palavra=""):

    # --- 1. Normaliza a entrada para numpy array ---
    if isinstance(img, Image.Image):
        # PIL Image → numpy array (RGB)
        img_array = np.array(img)
    elif isinstance(img, np.ndarray):
        # Já é numpy array (provavelmente do OpenCV em BGR)
        img_array = img
    elif isinstance(img, str):
        # Caminho do arquivo → abre com PIL
        img_array = np.array(Image.open(img))
    elif isinstance(img, bytes):
        # Bytes → abre com PIL
        from io import BytesIO
        img_array = np.array(Image.open(BytesIO(img)))
    else:
        raise TypeError(
            f"Tipo não suportado: {type(img)}. "
            "Aceita PIL.Image, numpy.ndarray, str (caminho) ou bytes."
        )

    # --- 2. Executa OCR ---
    reader = _get_reader()
    # detail=0 retorna apenas as strings (mais rápido)
    resultados = reader.readtext(img_array, detail=0)

    # --- 3. Verifica se "APROVADO" está no texto ---
    palavra_busca = "APROVADO" if case_sensitive else "aprovado"

    for texto in resultados:
        texto_normalizado = texto if case_sensitive else texto.lower()

        if partial_match:
            # Match parcial: "APROVADOS" conta
            if palavra_busca in texto_normalizado:
                return True
        else:
            # Match exato por palavra (remove pontuação)
            palavras = re.findall(r'\b\w+\b', texto_normalizado)
            if palavra_busca in palavras:
                return True

    return False
# Com OpenCV (numpy array)
#import cv2
#img_cv = cv2.imread("recorte.png")
#print(tem_aprovado(img_cv))  # True ou False
#
#img_cv = cv2.imread("recorte1.png")
#print(tem_aprovado(img_cv))  # True ou False

      # Só aceita "APROVADO" exato
