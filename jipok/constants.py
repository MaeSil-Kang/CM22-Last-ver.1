"""기준표(2026.4.5 개정) 상수 — 지폭스케줄 계산용.

출처: 기초/스케줄 학습.xls '기준표' 시트 + 사용자 인터뷰 확정.
일반 감열지(SF/PF) 기준. 특감(SFH/SL)은 teukgam=True 분기(값은 잠정, 추후 검증).
"""

HEUPSEUP = 300            # 흡습길이: 전 CP 고정 (사용자 확정)

CP_WEIGHT_MAX = 33.3      # 완스풀 상한 (절대 우선순위 1). 2026-08 CM2최종학습(108LOT)에서
                          # 정확히 33.0이 아니라 ~33.3까지 답지가 N을 올려 씀을 재확인(예 15000m
                          # N=8/9 경계 사례들) — 33.0→33.3으로 상향.
CP_WEIGHT_MIN = 11.0      # 하한

MIN_WIDTH = 4880          # PM23 최소지폭(일반)
MIN_WIDTH_TEUKGAM = 4820  # 특감
SHORT_LEN = 10000         # 미만이면 보상 120 고정 + 권취 짝수화(합권)

# 재권취 보상길이(현행 19.12.12)
BOSANG_ROLL = 120         # 롤포장(기본)
BOSANG_LEN = 240          # 길이변경
BOSANG_WIDTH = 170        # 폭변경


def kota_loss(cp: int, teukgam: bool = False) -> int:
    """코타 손실지폭(mm)."""
    if teukgam:
        return 70
    return 40 if cp <= 48 else 30


def single_trim(teukgam: bool = False) -> int:
    """싱글 트림량(mm)."""
    return 80 if teukgam else 45


def choji_service(cp: int) -> int:
    """초지 서비스길이(m). CP44·48=700, 55이상=600."""
    return 700 if cp <= 48 else 600


def cm2_loss(cp: int, teukgam: bool = False) -> int:
    """CM2 LOSS 길이(m)."""
    if teukgam:
        return 2600        # 특감 완스플(2021.8); 2등분 2200
    if cp >= 70:
        return 1800
    return 1500
