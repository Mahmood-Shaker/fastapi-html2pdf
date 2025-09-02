from fastapi import FastAPI, Request, Form, Body, HTTPException
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from weasyprint import HTML, CSS
from fontTools.ttLib import TTFont
from pathlib import Path
import base64
import io
import os

# ===== FastAPI metadata (يظهر في Swagger) =====
app = FastAPI(
    title="HTML → PDF API (WeasyPrint, Arabic Fonts)",
    description=(
        "تحويل HTML إلى PDF مع دعم الخطوط العربية. "
        "يمكن الإرسال كـ JSON أو كـ Form-Data. "
        "جرّب من /docs."
    ),
    version="1.0.0",
    contact={"name": "Mahmood-Shaker"}
)

# ===== المسارات والقوالب =====
BASE_DIR = Path(__file__).parent.resolve()
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# مجلد خطوط ويندوز
WINDOWS_FONTS_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"


def font_supports_arabic(font_path: Path) -> bool:
    """تحقق إن كان الخط يحتوي على نطاقات يونيكود العربية."""
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
    """قائمة بالخطوط الداعمة للعربية: الاسم الداخلي واسم الملف."""
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
                        fonts.append({"filename": f.name, "family": family_name})
                except Exception:
                    continue
    return sorted(fonts, key=lambda x: x["family"].lower())


@app.get("/", summary="نموذج HTML للاختبار", tags=["UI"])
async def form_page(request: Request):
    """يعرض صفحة HTML فيها اختيار خطوط ويندوز المتوفرة (دعم عربي)."""
    fonts = get_font_display_list()
    return templates.TemplateResponse("index.html", {"request": request, "fonts": fonts})


@app.post(
    "/api/convert-html",
    summary="تحويل HTML إلى PDF",
    description=(
        "أرسل المحتوى كـ JSON أو كـ Form-Data.\n\n"
        "- JSON مثال:\n"
        "```json\n"
        "{\n"
        "  \"html_content\": \"<h1>مرحبا</h1>\",\n"
        "  \"font_filename\": \"TRADBDO.TTF\",\n"
        "  \"font_family\": \"Traditional Arabic\"\n"
        "}\n"
        "```\n\n"
        "- Form-Data مفاتيح: html_content, font_filename, font_family\n"
    ),
    tags=["Conversion"],
    responses={
        200: {"description": "نجاح: يرجع PDF Base64"},
        400: {"description": "خطأ في الإدخال"},
        500: {"description": "فشل إنشاء PDF (WeasyPrint/الخطوط)"}
    },
)
async def convert_html(
    html_content: str = Form(None, description="محتوى HTML كاملاً"),
    font_filename: str = Form(None, description="اسم ملف الخط داخل C:\\Windows\\Fonts"),
    font_family: str = Form(None, description="اسم العائلة الداخلية للخط"),
    json_data: dict = Body(None, description="بديل للإرسال على شكل JSON")
):
    """
    إذا أرسلت JSON سيتم استخدامه بدل الـ Form.
    يجب أن تتوافق `font_filename` مع `font_family` كما هو موجود في الخط.
    """
    # إن وُجد JSON نستخدمه
    if json_data:
        html_content = json_data.get("html_content")
        font_filename = json_data.get("font_filename")
        font_family = json_data.get("font_family")

    if not html_content or not font_filename or not font_family:
        raise HTTPException(status_code=400, detail="Missing required data.")

    # التحقق من صحة الخط المُختار
    valid_fonts = {f["filename"]: f["family"] for f in get_font_display_list()}
    if font_filename not in valid_fonts or valid_fonts[font_filename] != font_family:
        raise HTTPException(status_code=400, detail="Invalid font selection.")

    font_path = (WINDOWS_FONTS_DIR / font_filename).resolve()
    if not font_path.exists():
        raise HTTPException(status_code=400, detail="Font file not found.")

    font_uri = font_path.as_uri()

    # CSS يدعم العربية مع الخط المختار
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
