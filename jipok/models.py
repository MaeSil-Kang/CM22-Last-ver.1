"""자료구조.

contribs 형식(2026-08 CM2최종학습 108LOT 반영): [order_idx, jeong, bosang, count].
정길이/보상을 항목별로 들고 있어 한 스풀/행 안에 서로 다른 정길이가 섞인
'교차-정길이 병합'(폭이 같으면 정길이가 달라도 한 스풀에 감는 답지 관행)을 표현할 수 있다.
단일 정길이 스풀에서는 모든 contrib이 같은 jeong/bosang을 반복해 담을 뿐이라 기존 동작과 동일.
"""
from dataclasses import dataclass, field


@dataclass
class Order:
    idx: int            # 순서 번호(1-base). pack.schedule()이 폭 내림차순으로 재부여한다.
    jeong: int           # 정길이 (m)
    kwonchwi: int       # 권취 수
    width: int          # 23호기요청지폭(표준지폭)
    bosang: int         # 보상길이


@dataclass
class Spool:
    jeong: int          # 대표 정길이(단일-정길이 스풀의 정길이. 혼합 스풀도 여유분 계산 등에 폴백으로 씀)
    width: int          # 그룹 내 최대폭
    bosang: int         # 대표 보상(위 jeong과 짝, 여유분 계산 폴백)
    N: int              # 완스풀(스풀당 마끼) — 대표 정길이 기준
    contribs: list = field(default_factory=list)  # [[order_idx, jeong, bosang, count], ...]
    yeoyu: int = 0

    @property
    def filled(self):
        return sum(c[3] for c in self.contribs) + self.yeoyu

    @property
    def mixed(self):
        """이 스풀에 서로 다른 정길이가 섞여 있는가(교차-정길이 병합)."""
        js = {c[1] for c in self.contribs}
        return len(js) > 1


@dataclass
class Row:
    width: int
    jeong: int          # 대표 정길이(단일-정길이 행). 혼합 행은 bigo()가 contrib별 실제 jeong을 출력.
    N: int
    spools: int
    contribs: list      # [[order_idx, jeong, bosang, count], ...] 집계
    yeoyu: int
    bosang: int
    choji_real: int = 0
    saengsan: int = 0

    @property
    def mixed(self):
        js = {c[1] for c in self.contribs}
        return len(js) > 1

    def bigo(self):
        parts = [f"(순서{o}번){j:,}m용*{c}회" for o, j, _b, c in self.contribs]
        if self.yeoyu:
            parts.append(f"여유분 {self.yeoyu}회")
        return "+".join(parts)
