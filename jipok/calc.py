"""결정형 계산 공식 — Case 8(CP44)·Case 1(CP55)로 검증된 체인.

용어
  싱글규격(single_spec) : 최종 재단폭(RE 지폭 아래 숫자)
  표준지폭(std_width=23호기요청지폭) : 싱글규격 + 코타손실 + 싱글트림 (반올림/최소/조정 후)
  정길이(jeong_len)     : RE 지폭 위 길이 (16000 등)
  보상(bosang)          : 롤포장120/폭변경170/길이변경240, <10000m=120
  N                     : 완스풀(스풀당 마끼수) = CP중량<=33 만족 최대 정수
"""
from . import constants as C


def round_half_up(x: float, base: int) -> int:
    return int((x + base / 2) // base) * base


def round100(x: float) -> int:
    """원지실길이 등 10자리에서 반올림(=100단위)."""
    return round_half_up(x, 100)


ADJ_MAX_DROP = 30   # 인접 순서 간 23요청폭 하강 폭 상한(작업자 평활화, LOT339 답지 역산)


def request_width(single_spec: int, cp: int, teukgam: bool = False) -> int:
    """23호기요청지폭(표준지폭). 최소폭 적용. (인접 평활화는 smooth_request_widths 별도)
    raw를 10단위 **내림(버림)**한다(답지 역산: 지폭4863+85=4948→4940, 4870+85=4955→4950).
    """
    raw = single_spec + C.kota_loss(cp, teukgam) + C.single_trim(teukgam)
    w = (raw // 10) * 10
    floor = C.MIN_WIDTH_TEUKGAM if teukgam else C.MIN_WIDTH
    return max(w, floor)


def smooth_request_widths(widths, cap: int = ADJ_MAX_DROP):
    """인접 순서 23요청폭 평활화. 순서대로 받아, 각 값이 직전보다 cap(=30) 넘게
    떨어지지 않도록 올린다(값 ≥ 직전 − cap). 0/빈 값은 그대로 두고 건너뛴다.
    작업자 규칙: 23요청폭이 한 단계에 30 넘게 급락하지 않게 위로 맞춤.
    (예: 4980→4920(급락60)이면 4950으로, 그 다음 4900→4920으로.)"""
    out = list(widths)
    prev = None
    for i, w in enumerate(out):
        if not w or w <= 0:
            continue
        out[i] = w if prev is None else max(w, prev - cap)
        prev = out[i]
    return out


def bosang(jeong_len: int, width_change: bool = False, len_change: bool = False) -> int:
    """보상길이. <10000m는 무조건 120. 폭변경170·길이변경240, 둘 다면 큰쪽(240)."""
    if jeong_len < C.SHORT_LEN:
        return C.BOSANG_ROLL
    vals = [C.BOSANG_ROLL]
    if width_change:
        vals.append(C.BOSANG_WIDTH)
    if len_change:
        vals.append(C.BOSANG_LEN)
    return max(vals)


def decide_bosang(jeong_len: int, gyukyeok, wanjeong, cp: int = 48, teukgam: bool = False) -> int:
    """규칙형 보상길이 판정(작업자 공식 그대로, 코드가 결정).

    인자
      gyukyeok : RE공정 규격1~9 토큰 [{'w': 앞폭, 'n': x뒤 배수}, ...]
      wanjeong : 완정공정(물류입고) 표 [{'garo': 가로, 'gil': 길이}, ...]
      cp, teukgam : 지종 게이팅용(cp는 현재 미사용, teukgam으로 판정).
    규칙
      - **짧은롤(정길이 < 10000) 무조건 120은 특감(SFH/SL)만 예외**로 풀어 폭/길이변경 반영.
        그 외 지종(44/48g·55/65/70g 등)은 원래대로 짧은롤 무조건 120.
      - 규격 배수(n)가 하나라도 2 이상 → 폭변경(170).
      - 규격 앞폭(w)이 완정공정 '가로'와 매칭되는 행을 찾아, 그 행들의 '길이'에
        정길이가 하나도 없으면(= 완정에서 그 길이로 안 나옴) → 길이변경(240).
      - 둘 다면 큰쪽(240). 그 외 롤포장(120).
    """
    if jeong_len < C.SHORT_LEN and not teukgam:
        return C.BOSANG_ROLL                      # 특감(SFH/SL) 외에는 짧은롤 무조건 120
    gy = gyukyeok or []
    wj = wanjeong or []
    width_change = any((g.get("n") or 1) >= 2 for g in gy)
    fronts = {g.get("w") for g in gy if g.get("w")}
    # 길이변경: 규격 앞폭(가로)별로, 그 가로의 완정 길이가 '전부' 정길이와 다르면
    # (= 그 조각이 정길이로는 안 나옴) → 길이변경. 하나라도 정길이면 그 조각은 유지.
    len_change = False
    for f in fronts:
        rows_f = [w for w in wj if w.get("garo") == f]
        if rows_f and all(w.get("gil") != jeong_len for w in rows_f):
            len_change = True
            break
    vals = [C.BOSANG_ROLL]                         # 짧은롤이라도 합권 아니면 폭/길이변경 반영
    if width_change:
        vals.append(C.BOSANG_WIDTH)
    if len_change:
        vals.append(C.BOSANG_LEN)
    return max(vals)


def wonji_jeonsan_items(items, cp: int, teukgam: bool = False) -> int:
    """원지전산길이(혼합-정길이 스풀 지원, 2026-08 CM2최종학습 확정).
    items: [(jeong_len, bosang_len, count), ...] — 스풀을 채운 마끼들을 (정길이,보상)별로
    묶은 구성. 폭이 같으면 정길이가 달라도 한 스풀에 섞이는 '교차-정길이 병합' 행은
    항목이 여러 개(서로 다른 jeong_len)가 된다. 단일 구성이면 기존 공식과 동일:
      Σ(정길이+보상)*count + CM2LOSS + 흡습. (초지서비스 미포함)"""
    s = sum((j + b) * n for j, b, n in items)
    return s + C.cm2_loss(cp, teukgam) + C.HEUPSEUP


def wonji_jeonsan(jeong_len: int, bosang_len: int, N: int, cp: int, teukgam: bool = False) -> int:
    """원지전산길이 = (정길이+보상)*N + CM2LOSS + 흡습. (초지서비스 미포함)
    단일-정길이 스풀 전용 API(하위호환) — wonji_jeonsan_items의 특수형."""
    return wonji_jeonsan_items([(jeong_len, bosang_len, N)], cp, teukgam)


def wonji_real(jeonsan: int, cp: int) -> int:
    """원지실길이 = 원지전산길이 + 초지서비스길이."""
    return jeonsan + C.choji_service(cp)


def choji_saengsan_real_items(items, cp: int, teukgam: bool = False) -> int:
    """초지생산실길이 = round100(원지실길이). 혼합-정길이 스풀 지원(items=wonji_jeonsan_items 인자)."""
    js = wonji_jeonsan_items(items, cp, teukgam)
    return round100(wonji_real(js, cp))


def saengsan_len_items(items, spools: int, cp: int, teukgam: bool = False) -> int:
    """생산길이(행) = round100(원지전산길이) * 스플수. 혼합-정길이 스풀 지원."""
    js = wonji_jeonsan_items(items, cp, teukgam)
    return round100(js) * spools


def choji_saengsan_real(jeong_len: int, bosang_len: int, N: int, cp: int, teukgam: bool = False) -> int:
    """초지생산실길이 = round100(원지실길이). 단일-정길이 전용(하위호환)."""
    return choji_saengsan_real_items([(jeong_len, bosang_len, N)], cp, teukgam)


def saengsan_len(jeong_len: int, bosang_len: int, N: int, spools: int, cp: int, teukgam: bool = False) -> int:
    """생산길이(행) = round100(원지전산길이) * 스플수. 단일-정길이 전용(하위호환)."""
    return saengsan_len_items([(jeong_len, bosang_len, N)], spools, cp, teukgam)


def cp_weight(cp: int, jeonsan: int, std_width: int) -> float:
    """CP중량 = CP/100 * 원지전산/10000 * 표준지폭/1000."""
    return cp / 100 * jeonsan / 10000 * std_width / 1000


def wanspool_N(jeong_len: int, bosang_len: int, cp: int, std_width: int,
               teukgam: bool = False, cap: int = 200) -> int:
    """완스풀 N = CP중량<=33 만족 최대 정수."""
    best = 0
    for n in range(1, cap + 1):
        js = wonji_jeonsan(jeong_len, bosang_len, n, cp, teukgam)
        if cp_weight(cp, js, std_width) <= C.CP_WEIGHT_MAX:
            best = n
        else:
            break
    return best


def min_n_cp11(jeong_len: int, bosang_len: int, cp: int, std_width: int,
               teukgam: bool = False, cap: int = 200) -> int:
    """CP중량>=11 만족 최소 마끼수(스풀당 최소). 절대규칙: 스풀은 CP중량 11~33."""
    for n in range(1, cap + 1):
        js = wonji_jeonsan(jeong_len, bosang_len, n, cp, teukgam)
        if cp_weight(cp, js, std_width) >= C.CP_WEIGHT_MIN:
            return n
    return 1


def extra_needed_for_min(items, extra_jeong, extra_bosang, cp: int, std_width: int,
                          teukgam: bool = False, cap: int = 200) -> int:
    """혼합 스풀에서 CP중량>=11을 채우는 데 필요한 추가 마끼수(여유분용, extra_jeong/bosang
    반복 추가). items는 이미 채운 (jeong,bosang,count) 목록. 이미 충족이면 0."""
    for extra in range(0, cap + 1):
        js = wonji_jeonsan_items(items + [(extra_jeong, extra_bosang, extra)], cp, teukgam)
        if cp_weight(cp, js, std_width) >= C.CP_WEIGHT_MIN:
            return extra
    return 0
