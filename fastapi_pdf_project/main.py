from typing import Optional, List, Tuple

from fastapi import FastAPI, Request, Form, Body, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from pydantic import BaseModel

from weasyprint import HTML, CSS
from fontTools.ttLib import TTFont

from pathlib import Path
import base64
import io
import os
import re


# ================== FastAPI metadata (shows in Swagger) ==================
app = FastAPI(
    title="HTML → PDF API (WeasyPrint, Arabic Fonts)",
    description=(
        "تحويل HTML إلى PDF مع دعم الخطوط العربية. "
        "يمكن الإرسال كـ JSON أو كـ Form-Data. "
        "جرّب من /docs."
    ),
    version="1.4.0",
    contact={"name": "Mahmood-Shaker"}
)

# ================== Paths & Templates ==================
BASE_DIR = Path(__file__).parent.resolve()
templates = Jinja2Templates(directory=BASE_DIR / "templates")

# Windows Fonts folder
WINDOWS_FONTS_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"


# ================== Helpers: fonts & rendering ==================
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
    """Return Arabic-supporting fonts: filename + internal family name."""
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
                            family_name = (
                                record.toUnicode()
                                if hasattr(record, "toUnicode")
                                else record.toStr()
                            )
                            break
                    tt.close()
                    if family_name:
                        fonts.append({"filename": f.name, "family": family_name})
                except Exception:
                    continue
    return sorted(fonts, key=lambda x: x["family"].lower())


def get_family_from_fontfile(font_path: Path) -> Optional[str]:
    """Extract the internal family name from a font file."""
    try:
        tt = TTFont(str(font_path))
        family_name = None
        for record in tt["name"].names:
            if record.nameID == 1:
                family_name = (
                    record.toUnicode()
                    if hasattr(record, "toUnicode")
                    else record.toStr()
                )
                break
        tt.close()
        return family_name
    except Exception:
        return None


def build_css(font_family: Optional[str], font_path_uri: Optional[str]) -> CSS:
    """
    Build CSS for A4 pages with header/footer space and optional user-selected font.
    """
    # --- نصوص الهيدر والفوتر الافتراضية ---
    HEADER_TEXT = ""
    FOOTER_LEFT = ""
    FOOTER_RIGHT = ""

    # الهوامش بالملليمتر
    MARGIN_TOP_MM = 40
    MARGIN_RIGHT_MM = 10
    MARGIN_BOTTOM_MM = 40
    MARGIN_LEFT_MM = 10

    page_css = f"""
        @page {{
            size: A4;
            margin: {MARGIN_TOP_MM}mm {MARGIN_RIGHT_MM}mm {MARGIN_BOTTOM_MM}mm {MARGIN_LEFT_MM}mm;

            @top-center {{
                content: "{HEADER_TEXT}";
                font-size: 14pt;
            }}

            @bottom-left {{
                content: "{FOOTER_LEFT}";
                font-size: 11pt;
            }}

            @bottom-center {{
                content: "صفحة " counter(page) " من " counter(pages);
                font-size: 11pt;
            }}

            @bottom-right {{
                content: "{FOOTER_RIGHT}";
                font-size: 11pt;
            }}
        }}
    """

    if font_family and font_path_uri:
        css_text = f"""
            @font-face {{
                font-family: '{font_family}';
                src: url('{font_path_uri}');
            }}
            {page_css}
            body {{
                direction: rtl;
                font-family: '{font_family}', serif;
                line-height: 1.7;
            }}
        """
    else:
        css_text = f"""
            {page_css}
            body {{
                direction: rtl;
                line-height: 1.7;
            }}
        """

    return CSS(string=css_text)


def render_pdf_bytes(html: str, css: CSS) -> bytes:
    pdf_io = io.BytesIO()
    HTML(string=html, base_url=str(BASE_DIR)).write_pdf(pdf_io, stylesheets=[css])
    return pdf_io.getvalue()


# ================== Font extraction from HTML ==================
FONTFAMILY_REGEXES = [
    re.compile(r'font-family\s*:\s*(?:(["\'])(?P<q>[^"\']+)\1|(?P<nq>[^;"}]+))', re.IGNORECASE),
]

def extract_font_family_candidates(html: str) -> List[str]:
    if not html:
        return []
    candidates: List[str] = []
    for rx in FONTFAMILY_REGEXES:
        for m in rx.finditer(html):
            fam = m.group('q') if m.group('q') else m.group('nq')
            if not fam:
                continue
            fam = fam.split(',')[0].strip().strip('"').strip("'")
            if fam and fam not in candidates:
                candidates.append(fam)
    return candidates


def choose_font_from_html(html: str) -> Tuple[Optional[str], Optional[str]]:
    mapping = {f["family"].strip().lower(): f["filename"] for f in get_font_display_list()}
    for fam in extract_font_family_candidates(html):
        key = fam.strip().lower()
        if key in mapping:
            return fam, mapping[key]

    for fallback in ["Traditional Arabic", "Arial", "Tahoma", "Noto Kufi Arabic", "Segoe UI"]:
        key = fallback.lower()
        if key in mapping:
            return fallback, mapping[key]

    return None, None


# ================== UI ==================
@app.get("/", summary="نموذج HTML للاختبار", tags=["UI"])
async def form_page(request: Request):
    fonts = get_font_display_list()
    return templates.TemplateResponse("index.html", {"request": request, "fonts": fonts})


# ================== Fonts listing ==================
@app.get("/api/fonts", tags=["Info"], summary="قائمة الخطوط الداعمة للعربية")
def list_fonts():
    return get_font_display_list()


# ================== convert-html (returns Base64) ==================
@app.post("/api/convert-html", tags=["Conversion"])
async def convert_html(
    html_content: str = Form(None),
    font_filename: str = Form(None),
    font_family: str = Form(None),
    json_data: dict = Body(None)
):
    if json_data:
        html_content = json_data.get("html_content")
        font_filename = json_data.get("font_filename")
        font_family = json_data.get("font_family")

    if not html_content:
        raise HTTPException(status_code=400, detail="Missing html_content.")

    chosen_family, chosen_filename = None, None
    if not font_filename or not font_family:
        chosen_family, chosen_filename = choose_font_from_html(html_content)

    font_family = font_family or chosen_family
    font_filename = font_filename or chosen_filename

    if font_family and font_filename:
        font_path = (WINDOWS_FONTS_DIR / font_filename).resolve()
        if not font_path.exists():
            css = build_css(None, None)
        else:
            css = build_css(font_family, font_path.as_uri())
    else:
        css = build_css(None, None)

    try:
        pdf_bytes = render_pdf_bytes(html_content, css)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {e}")

    pdf_base64 = base64.b64encode(pdf_bytes).decode("utf-8")
    return JSONResponse({"pdf_base64": pdf_base64})


# ================== savepdf (returns application/pdf) ==================
class SavePdfPayload(BaseModel):
    HtmlContent: str
    FileName: Optional[str] = None
    FontFileName: Optional[str] = None
    FontFamily: Optional[str] = None


@app.post("/api/pdf/savepdf", tags=["Conversion"])
async def save_pdf(payload: SavePdfPayload):
    html = payload.HtmlContent
    if not html:
        raise HTTPException(status_code=400, detail="HtmlContent is required.")

    file_name = payload.FileName or "document.pdf"
    font_family = payload.FontFamily
    font_filename = payload.FontFileName

    if not font_family or not font_filename:
        auto_family, auto_filename = choose_font_from_html(html)
        font_family = font_family or auto_family
        font_filename = font_filename or auto_filename

    if font_family and font_filename:
        font_path = (WINDOWS_FONTS_DIR / font_filename).resolve()
        if not font_path.exists():
            css = build_css(None, None)
        else:
            css = build_css(font_family, font_path.as_uri())
    else:
        css = build_css(None, None)

    try:
        pdf_bytes = render_pdf_bytes(html, css)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {e}")

    headers = {"Content-Disposition": f'attachment; filename="{file_name}"'}
    return StreamingResponse(io.BytesIO(pdf_bytes), media_type="application/pdf", headers=headers)
