# Digital Book Reader

Aplicación de escritorio mínima para Windows, con una biblioteca local, lector integrado y motor de formatos en Python. El repositorio incluye la interfaz y el código fuente; todavía no incluye un instalador de Windows.

## Formatos

- **EPUB 2 y 3** (spine, índice, metadatos y portadas).
- **MOBI, PRC, AZW y AZW3 sin DRM**, mediante KindleUnpack (`mobi`). Los libros con DRM no se pueden abrir.
- **PDF**, renderizado por página bajo demanda.
- **FB2**.
- **TXT**, con detección de codificación habitual y saltos CRLF/LF.
- **HTML, HTM y XHTML**, eliminando scripts y contenido activo.
- **Markdown** (`.md`, `.markdown`), convertido a HTML seguro.
- **RTF**.
- **DOCX** (texto, títulos, tablas e imágenes incrustadas).
- **ODT** (texto, títulos, listas, tablas e imágenes incrustadas).
- **CBZ/ZIP, CBR/RAR y 7Z** con imágenes; las páginas se leen/renderizan bajo demanda.

Los formatos de texto ambiguos también se pueden detectar por contenido aunque el archivo no tenga extensión. Para Markdown se recomienda conservar `.md` o `.markdown`.

## Instalación de dependencias

Requiere Python 3.10 o posterior. En Windows, desde PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Notas de dependencias:

- CBR/RAR requiere `UnRAR.exe`/WinRAR disponible para `rarfile`. La aplicación busca `tools\UnRAR.exe` y las rutas habituales de WinRAR. No se incluye el ejecutable propietario.
- MOBI/AZW está limitado a archivos sin DRM. Los archivos convertidos se guardan en la caché del usuario para que imágenes y PDF de páginas puedan seguir abriéndose.
- PyMuPDF y py7zr se instalan desde los requisitos. PySide6 proporciona la ventana de escritorio y Qt WebEngine.

## Iniciar la aplicación de escritorio

Desde la raíz del repositorio, con el entorno virtual activado:

```powershell
python -m src.app
```

También puedes abrir un libro directamente al iniciar:

```powershell
python -m src.app "C:\Books\novel.epub"
```

La ventana ofrece una biblioteca local, importación y apertura de libros, índice/tabla de contenidos, búsqueda en el texto, marcadores y restauración de la última ubicación al volver a abrir un libro. También puedes arrastrar archivos a la ventana. La aplicación es todavía una versión mínima de código fuente: **no hay ejecutable ni instalador de Windows**.

## Uso del motor

```python
from src.core.formats import parse

book = parse(r"C:\Books\novel.epub")
print(book.title, book.author, book.total_pages)
print(book.html)  # HTML de lectura; los scripts se eliminan de documentos importados
```

Para PDF y cómics, `book.page_source` es un `PageSource` que entrega imágenes de forma diferida. En una integración con Qt WebEngine se puede usar el servidor local:

```python
from src.core.page_server import get_page_server

server = get_page_server()
server.start()
server.register("42", book.page_source)
base_url = server.base_url_for("42")
# Cargar book.html en WebEngine usando base_url como URL base del documento.
```

## Datos de usuario

En Windows, biblioteca, ajustes y caché se guardan bajo `%LOCALAPPDATA%\Digital Book Reader`. En otros sistemas se usan las carpetas XDG del usuario.

## Licencia

El código original del proyecto se ofrece bajo **GNU AGPL v3.0 únicamente** (`AGPL-3.0-only`); consulta [`LICENSE`](LICENSE). Las dependencias conservan sus propias licencias y avisos, resumidos en [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md). Antes de distribuir una compilación, verifica también las versiones exactas y las dependencias transitivas instaladas.

## Pruebas

```powershell
python -m unittest discover -v
```

Las pruebas crean libros de muestra temporales y cubren los parsers, el enrutamiento por formato, la migración de la base de datos, el progreso y los marcadores, y la caché de páginas.
