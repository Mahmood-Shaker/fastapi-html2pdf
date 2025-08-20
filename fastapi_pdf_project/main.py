from fastapi import FastAPI, Request, Form, Body, HTTPException
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from weasyprint import HTML, CSS
from fontTools.ttLib import TTFont
from pathlib import Path
import base64
import io
import os

app = FastAPI()

BASE_DIR = Path(__file__).parent.resolve()
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Windows Fonts directory
WINDOWS_FONTS_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"


def font_supports_arabic(font_path: Path) -> bool:
    """Check if font contains Arabic Unicode blocks."""
    try:
        tt = TTFont(str(font_path))
        for table in tt["cmap"].tables:
            for codepoint in table.cmap.keys():
                if (
                    0x0600 <= codepoint <= 0x06FF or
                    0x0750 <= codepoint <= 0x077F or
                    0x08A0 <= codepoint <= 0x08FF or
                    0xFB50 <= codepoint <= 0xFDFF or
                    0xFE70 <= codepoint <= 0xFEFF
                ):
                    tt.close()
                    return True
        tt.close()
    except Exception:
        pass
    return False


def get_font_display_list():
    """Return only Arabic-supporting fonts with filename and internal family name."""
    fonts = []
    if not WINDOWS_FONTS_DIR.exists():
        return fonts

    for f in WINDOWS_FONTS_DIR.iterdir():
        if f.is_file() and f.suffix.lower() in {".ttf", ".otf"}:
            if font_supports_arabic(f):
                try:
                    tt = TTFont(str(f))
                    family_name = None
                    for record in tt["name"].names:
                        if record.nameID == 1:  # Font Family name
                            family_name = record.toStr()
                            break
                    tt.close()
                    if family_name:
                        fonts.append({
                            "filename": f.name,
                            "family": family_name
                        })
                except Exception:
                    continue
    return sorted(fonts, key=lambda x: x["family"].lower())


@app.get("/")
async def form_page(request: Request):
    fonts = get_font_display_list()
    return templates.TemplateResponse("index.html", {"request": request, "fonts": fonts})


@app.post("/api/convert-html")
async def convert_html(
    html_content: str = Form(None),
    font_filename: str = Form(None),
    font_family: str = Form(None),
    json_data: dict = Body(None)
):
    # If JSON payload provided, override values
    if json_data:
        html_content = json_data.get("html_content")
        font_filename = json_data.get("font_filename")
        font_family = json_data.get("font_family")

    if not html_content or not font_filename or not font_family:
        raise HTTPException(status_code=400, detail="Missing required data.")

    # Validate font choice
    valid_fonts = {f["filename"]: f["family"] for f in get_font_display_list()}
    if font_filename not in valid_fonts or valid_fonts[font_filename] != font_family:
        raise HTTPException(status_code=400, detail="Invalid font selection.")

    font_path = (WINDOWS_FONTS_DIR / font_filename).resolve()
    if not font_path.exists():
        raise HTTPException(status_code=400, detail="Font file not found.")

    font_uri = font_path.as_uri()

    # Build CSS for Arabic + user font
    custom_css = CSS(string=f"""
        @font-face {{
            font-family: '{font_family}';
            src: url('{font_uri}');
        }}
        body {{
            direction: rtl;
            font-family: '{font_family}', serif;
            line-height: 1.7;
        }}
    """)

    try:
        pdf_io = io.BytesIO()
        HTML(string=html_content, base_url=str(BASE_DIR)).write_pdf(pdf_io, stylesheets=[custom_css])
        pdf_bytes = pdf_io.getvalue()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {e}")

    pdf_base64 = base64.b64encode(pdf_bytes).decode("utf-8")
    return JSONResponse({"pdf_base64": pdf_base64})