"""질문(스케줄) PDF 렌더 + 보조 추출.

스캔 이미지 PDF라 완전 자동 OCR은 불안정 → 페이지를 렌더해 화면에 보여주고
사용자가 편집표에서 검수/수정하는 흐름을 전제로 한다.

파싱 정확도용 전처리(render_pages_hq): 400 DPI + 기울기 보정(deskew) +
표 영역 크롭(여백 trim). 개별 열은 자르지 않는다(행 오정렬 위험) — 표 전체를
확대해 행·열 맥락은 유지하면서 작은 숫자 해상도만 높인다. 전처리는 실패해도
원본 이미지로 진행(파싱이 절대 안 깨지게).
"""
import fitz
from PIL import Image


def render_pages(pdf_bytes, dpi=200):
    """PDF 바이트 → PIL 이미지 리스트(페이지별)."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    imgs = []
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    for page in doc:
        pix = page.get_pixmap(matrix=mat)
        imgs.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
    return imgs


def _detect_skew(img, max_angle=4.0, step=0.5):
    """투영 프로파일로 기울기 각도(도) 추정. 축소본에서 빠르게 탐색.
    글자 줄이 수평일수록 행 합(projection)의 인접차 제곱합이 커진다."""
    import numpy as np
    g = img.convert("L")
    w, h = g.size
    scale = 1000.0 / max(w, h)
    if scale < 1:
        g = g.resize((max(1, int(w * scale)), max(1, int(h * scale))))
    arr = np.asarray(g, dtype=np.float32)
    thr = arr.mean() - arr.std() * 0.5           # 대략적 이진화 임계
    base = Image.fromarray(((arr < thr) * 255).astype("uint8"))  # 글자=흰
    best_a, best_s = 0.0, -1.0
    a = -max_angle
    while a <= max_angle + 1e-6:
        rot = base.rotate(a, resample=Image.NEAREST, expand=False, fillcolor=0)
        proj = np.asarray(rot, dtype=np.float32).sum(axis=1)
        s = float(((proj[1:] - proj[:-1]) ** 2).sum())
        if s > best_s:
            best_s, best_a = s, float(a)
        a += step
    return best_a


def _deskew(img):
    """기울기 보정. |각도|<0.6°면 무시(노이즈 수준 회전·리샘플 방지)."""
    ang = _detect_skew(img)
    if abs(ang) < 0.6:
        return img
    return img.rotate(ang, resample=Image.BICUBIC, expand=True, fillcolor=(255, 255, 255))


def _autocrop(img, pad=14, min_ratio=0.4):
    """내용(글자) 경계 상자로 여백을 잘라 표를 크게 만든다. 안전장치:
    크롭 결과가 원본의 min_ratio보다 작아지면(이상 크롭) 원본 유지."""
    import numpy as np
    arr = np.asarray(img.convert("L"))
    thr = arr.mean() - arr.std() * 0.4
    ys, xs = np.where(arr < thr)
    if xs.size == 0:
        return img
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    W, H = img.size
    x0 = max(0, x0 - pad); y0 = max(0, y0 - pad)
    x1 = min(W, x1 + pad); y1 = min(H, y1 + pad)
    if (x1 - x0) < W * min_ratio or (y1 - y0) < H * min_ratio:
        return img                               # 과잉 크롭 방지
    return img.crop((x0, y0, x1, y1))


def _cap(img, max_side=4000):
    """가장 긴 변을 max_side로 제한(메모리·페이로드 상한). 확대는 안 함."""
    m = max(img.size)
    if m <= max_side:
        return img
    r = max_side / m
    return img.resize((max(1, int(img.width * r)), max(1, int(img.height * r))),
                      resample=Image.LANCZOS)


def render_pages_hq(pdf_bytes, dpi=400, deskew=True, autocrop=True, max_side=4000):
    """파싱용 고품질 렌더: 고DPI + deskew + 표영역 크롭 + 상한.
    각 단계는 독립적으로 try — 실패하면 그 단계만 건너뛰고 진행한다."""
    steps = []
    if deskew:
        steps.append(_deskew)
    if autocrop:
        steps.append(_autocrop)
    if max_side:
        steps.append(lambda im: _cap(im, max_side))
    out = []
    for im in render_pages(pdf_bytes, dpi):
        for fn in steps:
            try:
                im = fn(im)
            except Exception:
                pass                             # 전처리 실패 → 원본 유지
        out.append(im)
    return out


def try_ocr(pdf_bytes, dpi=300):
    """tesseract가 설치돼 있으면 텍스트 1차 추출(보조용). 없으면 ''."""
    try:
        import pytesseract
    except Exception:
        return ""
    text = []
    for img in render_pages(pdf_bytes, dpi):
        try:
            text.append(pytesseract.image_to_string(img, lang="kor+eng"))
        except Exception:
            return ""
    return "\n".join(text)
