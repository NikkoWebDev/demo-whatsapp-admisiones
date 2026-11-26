# Carpeta para PDFs Oficiales

Coloca aquí los archivos PDF oficiales de admisiones del colegio:
- `PROSPECTO 2027.pdf`
- `Costos_LCS_2027.pdf`
- `Costos educativos Ciudad de Sol.pdf`

Luego ejecuta el script extractor desde la raíz del proyecto:
```bash
python scripts/extract_pdfs.py
```
El script procesará los PDFs y actualizará automáticamente `data/conocimiento_2027.md`.
