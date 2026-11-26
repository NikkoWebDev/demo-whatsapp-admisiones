#!/usr/bin/env python3
"""
Script: extract_pdfs.py
Descripción: Extrae texto y tablas de los archivos PDF oficiales ubicados en `raw_pdfs/`
y genera el archivo consolidado en Markdown `data/conocimiento_2027.md` con tablas limpias
y encabezados por documento fuente.
"""

import os
import sys
from pathlib import Path
import pdfplumber

# Resolver rutas relativas a la raíz del proyecto
BASE_DIR = Path(__file__).resolve().parent.parent
RAW_PDFS_DIR = BASE_DIR / "raw_pdfs"
OUTPUT_FILE = BASE_DIR / "data" / "conocimiento_2027.md"


def table_to_markdown(table: list[list[str]]) -> str:
    """Convierte una matriz 2D de celdas a una tabla en formato Markdown limpio."""
    if not table or not any(row for row in table):
        return ""

    # Normalizar número de columnas y limpiar celdas
    cleaned_rows = []
    max_cols = max(len(row) for row in table)
    if max_cols == 0:
        return ""

    for row in table:
        cleaned_row = []
        for i in range(max_cols):
            cell = row[i] if i < len(row) else ""
            if cell is None:
                cell = ""
            # Reemplazar saltos de línea dentro de la celda y escapar pipes
            clean_cell = str(cell).replace("\n", " ").replace("|", "\\|").strip()
            cleaned_row.append(clean_cell)
        cleaned_rows.append(cleaned_row)

    # Si la primera fila está vacía, no es una tabla útil
    if not any(cleaned_rows[0]):
        return ""

    header = cleaned_rows[0]
    separator = ["---"] * len(header)
    data_rows = cleaned_rows[1:]

    lines = []
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join(separator) + " |")
    for row in data_rows:
        lines.append("| " + " | ".join(row) + " |")

    return "\n".join(lines)


def process_pdf(pdf_path: Path) -> str:
    """Extrae el contenido de un archivo PDF estructurándolo con tablas y texto."""
    content_parts = []
    filename = pdf_path.name
    content_parts.append(f"# {filename}\n")

    print(f"  [+] Procesando: {filename}...")
    with pdfplumber.open(pdf_path) as pdf:
        total_pages = len(pdf.pages)
        for page_idx, page in enumerate(pdf.pages, start=1):
            content_parts.append(f"## {filename} - Página {page_idx} de {total_pages}\n")

            # 1. Extraer tablas
            tables = page.extract_tables()
            table_markdowns = []
            for t_idx, table in enumerate(tables):
                md_table = table_to_markdown(table)
                if md_table:
                    table_markdowns.append(md_table)

            # 2. Extraer texto plano
            # Intentamos extraer texto filtrando áreas de tablas si es posible, o el texto completo
            page_text = page.extract_text(layout=False) or ""
            page_text = page_text.strip()

            if page_text:
                content_parts.append(page_text + "\n")

            if table_markdowns:
                content_parts.append("\n### Tablas extraídas:\n")
                for md_table in table_markdowns:
                    content_parts.append(md_table + "\n\n")

            content_parts.append("\n---\n")

    return "\n".join(content_parts)


def main():
    print("=" * 60)
    print(" Extractor de PDFs a Markdown - Admisiones 2027")
    print("=" * 60)

    if not RAW_PDFS_DIR.exists():
        print(f"[!] Creando directorio faltante: {RAW_PDFS_DIR}")
        RAW_PDFS_DIR.mkdir(parents=True, exist_ok=True)

    pdf_files = sorted(RAW_PDFS_DIR.glob("*.pdf"))

    if not pdf_files:
        print(f"[!] No se encontraron archivos .pdf en: {RAW_PDFS_DIR}")
        print("    Por favor coloca tus archivos allí (ej: 'PROSPECTO 2027.pdf', 'Costos_LCS_2027.pdf', etc.)")
        print(f"    El archivo actual de conocimiento en '{OUTPUT_FILE}' no ha sido modificado.")
        sys.exit(0)

    print(f"[*] Se encontraron {len(pdf_files)} archivo(s) PDF para procesar:")
    for f in pdf_files:
        print(f"    - {f.name}")

    consolidated_sections = [
        "<!-- ARCHIVO GENERADO AUTOMÁTICAMENTE POR extract_pdfs.py -->\n",
        f"<!-- Total de documentos procesados: {len(pdf_files)} -->\n\n"
    ]

    for pdf_file in pdf_files:
        try:
            pdf_markdown = process_pdf(pdf_file)
            consolidated_sections.append(pdf_markdown)
        except Exception as e:
            print(f"[ERROR] Error extrayendo {pdf_file.name}: {e}")

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    final_content = "\n".join(consolidated_sections)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(final_content)

    print(f"\n[OK] Base de conocimiento generada exitosamente en:\n     {OUTPUT_FILE}")
    print(f"     Tamaño: {len(final_content.encode('utf-8'))} bytes")
    print("=" * 60)


if __name__ == "__main__":
    main()
