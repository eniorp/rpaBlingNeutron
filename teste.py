import cv2
import numpy as np
from PIL import Image
import io
from pathlib import Path

from posicaoCarimboPDF import encontrar_area_pdf_na_tela, encontrar_espaco_em_branco_no_recorte


EXTENSOES_IMAGEM = {'.png', '.jpg', '.jpeg', '.bmp', '.webp', '.tif', '.tiff'}

def percorrer_imgs_encontrar_area_pdf(
    dir_entrada: str = r"c:\temp\img",
    dir_saida: str = r"c:\temp\img\resultado",
    salvar_debug: bool = True,
) -> list[dict]:
    """
    Percorre todas as imagens em dir_entrada e aplica encontrar_area_pdf_na_tela.
    Retorna lista com nome do arquivo e área detectada.
    """
    pasta = Path(dir_entrada)
    if not pasta.is_dir():
        raise FileNotFoundError(f"Diretório não encontrado: {dir_entrada}")

    if salvar_debug:
        Path(dir_saida).mkdir(parents=True, exist_ok=True)

    resultados = []
    arquivos = sorted(
        f for f in pasta.iterdir()
        if f.is_file() and f.suffix.lower() in EXTENSOES_IMAGEM
    )

    if not arquivos:
        print(f"Nenhuma imagem encontrada em {dir_entrada}")
        return resultados

    print(f"Processando {len(arquivos)} imagem(ns) em {dir_entrada}\n")

    for arquivo in arquivos:
        img = cv2.imread(str(arquivo))
        if img is None:
            print(f"⚠️  Não foi possível ler: {arquivo.name}")
            continue

        area_pdf = encontrar_area_pdf_na_tela(img)
        resultado = {'arquivo': arquivo.name, 'area_pdf': area_pdf}
        resultados.append(resultado)

        print("🔎 Buscando espaço em branco...")
        regiao = encontrar_espaco_em_branco_no_recorte(
            img, area_pdf,
            preferir_lado='direita',
            debug_path=None
        )
        
        
        if regiao is None:
            print("❌ Sem espaço em branco — abortando.",arquivo.name)
            return False
        else:
            print('achou branco',arquivo.name,regiao)    
    
        cx = regiao['centro_x_tela']
        cy = regiao['centro_y_tela']


        print(f"📄 {arquivo.name}")
        print(f"   Área PDF: x={area_pdf['x']}, y={area_pdf['y']}, "
              f"{area_pdf['largura']}x{area_pdf['altura']}px")

        if salvar_debug:
            debug = img.copy()
            x, y = area_pdf['x'], area_pdf['y']
            w, h = area_pdf['largura'], area_pdf['altura']
            cv2.rectangle(debug, (x, y), (x + w, y + h), (0, 200, 0), 3)
            cv2.putText(debug, "PDF", (x + 10, y + 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 200, 0), 2)
            saida = Path(dir_saida) / f"area_{arquivo.stem}.png"
            cv2.imwrite(str(saida), debug)
            print(f"   Debug salvo em: {saida}")

        print()

    return resultados


percorrer_imgs_encontrar_area_pdf(r'c:\temp\img', r'c:\temp\img\resultado', True)