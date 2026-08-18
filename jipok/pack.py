"""스케줄러(패커) v3 — 권취를 스풀로 묶고 인접 병합/균등분할/여유분 처리.

규칙(답지 LOT 2260600339 등 역산 + 2026-08 CM2최종학습 108LOT 전수 대조로 갱신):
  - **주문은 항상 23요청지폭 내림차순으로 재번호매김한 뒤 패킹한다**(rank_orders, 신규).
    답지의 '(순서N번)' 라벨은 PDF 원본 표의 순서 컬럼과 무관하게 이 규칙(폭 내림차순
    순위, 동률은 입력 순서 유지)을 따른다 — 108LOT 전수 검증으로 확정.
  - 각 주문은 원칙적으로 '자기 순서'로 스풀을 만든다(순번 건너뛰기 병합 금지, mode=loss/convenience).
  - answer/answer_o1: 같은 정길이는 다른 정길이를 건너뛰어도 병합(skip-merge)한다.
  - **answer_v2(신규)**: 폭이 같으면 정길이가 달라도 한 스풀에 섞는 '교차-정길이 병합'을
    지원한다(자투리만, forward로 순서(폭) 인접에 따라 누적). 108LOT에서 반복 확인된
    핵심 패턴(예 LOT2260200347·2260201111·2260600337 등).
  - 권취가 완스풀 N을 넘으면 ceil(권취/N) 개 스풀로 '균등' 분할(욕심껏 N 채우기 X).
      예) 13마끼·N9 → 7+6,  21마끼·N7 → 7+7+7.
  - 모든 스풀은 CP중량 11~33.3(2026-08 상향). 부족분은 여유분(min_n)으로 채워 확정.
  - 같은 (폭,정길이,실채움) 스풀들을 한 행으로 집계.
  - 총생산길이는 초지공정길이 이상이 되도록 여유분으로 보충(상한은 느슨).
주의: 여유분 정밀배치는 작업자 판단 영역 → '유효하고 답지 구조에 일치'를 목표.
"""
import math
from . import calc
from . import constants as C
from .models import Order, Spool, Row

WIDTH_TOL = 20          # 병합 허용 폭차(기본). 평균이하 폭은 50.
LONG_ROLL_N = 7         # 완스풀 N이 이 값 이하인 '긴 롤'은 단독 부분스풀을 완스풀까지 채움


def rank_orders(orders):
    """주문을 23요청지폭 내림차순으로 재정렬하고 순서번호(idx)를 1..N으로 다시 부여한다.
    동률(폭이 같음)은 입력 순서를 그대로 유지한다(원표 등장순 — 108LOT 중 일부는 반대로도
    보였으나 다수는 이 순서를 따름, [[jipok-cm2-learning-progress]] 참고).
    2026-08 CM2최종학습(108LOT) 확정 규칙: 답지의 '(순서N번)' 라벨은 PDF 원본 표의 순서
    컬럼과 무관하게 이 규칙(폭 내림차순 순위)으로 매겨진다 — 패킹 전에 항상 이 순서로
    정렬해야 인접 병합·forward carry 로직이 답지 구조와 일치한다."""
    ranked = sorted(orders, key=lambda o: -o.width)   # stable sort: 동률은 입력 순서 유지
    return [Order(i + 1, o.jeong, o.kwonchwi, o.width, o.bosang)
            for i, o in enumerate(ranked)]


def _N(jeong, width, bosang, cp, teukgam):
    return calc.wanspool_N(jeong, bosang, cp, width, teukgam) or 1


def _count(sp):
    return sum(c[3] for c in sp.contribs)


def _add(sp, order_idx, jeong, bosang, n):
    """contribs=[order_idx, jeong, bosang, count]. 같은 주문(=같은 jeong)이 연속이면 합친다."""
    if sp.contribs and sp.contribs[-1][0] == order_idx and sp.contribs[-1][1] == jeong:
        sp.contribs[-1][3] += n
    else:
        sp.contribs.append([order_idx, jeong, bosang, n])


def _spool_items(s):
    """스풀의 실제 (정길이,보상,수량) 구성 — 여유분은 스풀 대표 jeong/bosang으로 포함."""
    items = [(j, b, c) for _, j, b, c in s.contribs]
    if s.yeoyu:
        items.append((s.jeong, s.bosang, s.yeoyu))
    return items


def _spool_saengsan(s, cp, teukgam):
    return calc.round100(calc.wonji_jeonsan_items(_spool_items(s), cp, teukgam))


def _balanced(total, k):
    """total을 k조각으로 균등 분할. 앞쪽이 큼. 예) (13,2)->[7,6], (21,3)->[7,7,7]."""
    base, rem = divmod(total, k)
    return [base + 1 if i < rem else base for i in range(k)]


def make_spools(orders, cp, teukgam):
    """주문 리스트 -> 스풀 리스트.

    핵심(답지 학습):
      - 순번 건너뛰기 병합 금지: carry는 '바로 다음 순서'에서만 합쳐진다.
      - 작은 주문(권취<CP중량11 최소)만 인접 병합 대상. 그 외엔 자기 스풀.
      - 큰 주문은 ceil(권취/N) 스풀로 균등 분할.
    """
    spools = []
    widths = [o.width for o in orders] or [0]
    avg = (max(widths) + min(widths)) / 2

    def tol(w1, w2):
        return 50 if min(w1, w2) < avg else WIDTH_TOL

    def Nof(j, w, b):
        return _N(j, w, b, cp, teukgam)

    def minof(j, w, b):
        return calc.min_n_cp11(j, b, cp, w, teukgam)

    def finalize(s):
        """자체 스풀 확정: CP중량>=11 되도록 여유분 채움."""
        need = minof(s.jeong, s.width, s.bosang)
        if _count(s) + s.yeoyu < need:
            s.yeoyu = need - _count(s)

    carry = None   # 바로 다음 순서와만 합칠 부분스풀(<min). 절대 건너뛰지 않음.
    for i, o in enumerate(orders):
        # 1) 들어온 carry(직전 순서 잔량)와 결합 시도 — 호환되면 합치고, 아니면 단독 확정
        group = []
        gj, gw, gb = o.jeong, o.width, o.bosang
        if carry is not None:
            if carry.jeong == o.jeong and abs(carry.width - o.width) <= tol(carry.width, o.width):
                group = [list(c) for c in carry.contribs]
                gw = max(gw, carry.width)
                gb = max(gb, carry.bosang)
            else:
                finalize(carry); spools.append(carry)   # 인접 불호환 → 단독 확정
            carry = None

        _add_group(group, o.idx, gj, gb, o.kwonchwi)
        M = sum(c[3] for c in group)
        if M == 0:
            continue
        N = Nof(gj, gw, gb)
        mn = minof(gj, gw, gb)

        # 2) 너무 작아 홀로 설 수 없으면(권취<CP중량11) → 바로 다음 순서로만 이월
        if M < mn:
            nxt = orders[i + 1] if i + 1 < len(orders) else None
            if nxt is not None and nxt.jeong == gj and abs(gw - nxt.width) <= tol(gw, nxt.width):
                carry = Spool(gj, gw, gb, N, group, 0)
                continue
            sp = Spool(gj, gw, gb, N, group, 0)
            finalize(sp); spools.append(sp)
            continue

        # 3) 완스풀 N 기준 ceil 분할(균등). 가장 작은 조각도 CP중량11 보장.
        k = max(1, math.ceil(M / N))
        while k > 1 and (M // k) < mn:
            k -= 1
        sizes = _balanced(M, k)
        gi, gcnt = 0, group[0][3]
        for s in sizes:
            sp = Spool(gj, gw, gb, N, [], 0)
            need = s
            while need > 0:
                if gcnt == 0:
                    gi += 1
                    gcnt = group[gi][3]
                take = min(need, gcnt)
                _add(sp, group[gi][0], group[gi][1], group[gi][2], take)
                gcnt -= take
                need -= take
            spools.append(sp)

    if carry is not None:
        finalize(carry); spools.append(carry)
    return spools


def _add_group(group, order_idx, jeong, bosang, n):
    if group and group[-1][0] == order_idx and group[-1][1] == jeong:
        group[-1][3] += n
    else:
        group.append([order_idx, jeong, bosang, n])


def spools_to_rows(spools, cp, teukgam):
    """연속한 동일 (폭,정길이,실채움) 스풀을 한 행으로 집계.
    길이는 완스풀 N이 아니라 '실제 채운 마끼수(실채움=마끼+여유)'로 계산.
    생산길이 계산은 각 행의 '대표 스풀(그룹을 처음 만든 스풀) 1개'의 실제 (정길이,보상)
    구성으로 하고, 이후 병합되는 스풀 수만큼 스플수(spools)를 늘린다(혼합-정길이 스풀도
    정확히 계산됨 — bigo() 표시용 contribs 누적과 별개)."""
    rows = []
    rep_items = {}   # id(row) -> 그 그룹을 만든 첫 스풀 1개의 (jeong,bosang,count) 구성
    for s in spools:
        filled = _count(s) + s.yeoyu
        key = (s.width, s.jeong, filled)
        if rows and (rows[-1].width, rows[-1].jeong, rows[-1].N) == key:
            r = rows[-1]
            r.spools += 1
            for oi, j, b, c in s.contribs:
                if r.contribs and r.contribs[-1][0] == oi and r.contribs[-1][1] == j:
                    r.contribs[-1][3] += c
                else:
                    r.contribs.append([oi, j, b, c])
            r.yeoyu += s.yeoyu
            r.bosang = max(r.bosang, s.bosang)
        else:
            r = Row(s.width, s.jeong, filled, 1, [list(c) for c in s.contribs], s.yeoyu, s.bosang)
            rows.append(r)
            rep_items[id(r)] = _spool_items(s)
    for r in rows:
        items = rep_items[id(r)]
        r.choji_real = calc.choji_saengsan_real_items(items, cp, teukgam)
        r.saengsan = calc.saengsan_len_items(items, r.spools, cp, teukgam)
    return rows


def topup_yeoyu(spools, cp, teukgam, choji_len, lo=1000, hi=60000, concentrate=False):
    """총생산길이를 초지공정길이 이상으로 끌어올리는 여유분 보충(상한은 느슨).
    답지 학습: 순서1은 '필수 여유분 1회'만 받고, 추가 보충 대상은 아래 우선순위로 고른다.
      1) 재단량 비율(=권취 생산량 비중)이 높은 순서의 부분스풀 우선
      2) 같으면 지폭(생산폭)이 넓은 곳, 그다음 긴 정길이
    (순서1은 필수 1회 외 추가 보충에서 제외. 이미 +lo 충족이면 추가 안 함.)
    concentrate=True(순서1 여유분 모드): 추가 여유분을 '대형 권취 순서 한 곳'에 몰아주고
    소형 순서 부분스풀은 건드리지 않는다(예 순5 5/5 유지, 순3에 여유분 집중)."""
    if not choji_len:
        return
    total = lambda: sum(_spool_saengsan(s, cp, teukgam) for s in spools)
    target_lo, target_hi = choji_len + lo, choji_len + hi
    o1 = spools[0].contribs[0][0] if spools and spools[0].contribs else None

    # 재단량(권취=생산량) 비중: 순서별 실제 권취 총합(여유 제외). 스풀 점수는 그 스풀에
    # 기여한 순서들 중 최대 권취량(=생산량 큰 순서에 속한 스풀일수록 우선).
    prod = {}
    for s in spools:
        for oi, _j, _b, c in s.contribs:
            prod[oi] = prod.get(oi, 0) + c

    def prod_weight(s):
        return max((prod.get(oi, 0) for oi, _j, _b, _c in s.contribs), default=0)

    def under(s):
        return (_count(s) + s.yeoyu) < s.N

    def is_o1(s):
        return s.contribs and s.contribs[0][0] == o1

    def delta(s):
        items = _spool_items(s)
        plus = [(j, b, c) for j, b, c in items]
        plus.append((s.jeong, s.bosang, 1))
        return calc.round100(calc.wonji_jeonsan_items(plus, cp, teukgam)) - \
               calc.round100(calc.wonji_jeonsan_items(items, cp, teukgam))

    # 순서1 필수 여유분(규칙4-4): 순서1 부분스풀이 있으면 1회만 채운다.
    o1_part = next((s for s in spools if is_o1(s) and under(s)), None)
    if o1_part is not None:
        o1_part.yeoyu += 1

    if concentrate:
        # 추가 여유분을 '대형 권취 순서 한 곳'에 몰아준다. 단 대상은 '부분스풀(미완)이 있는'
        # 순서 중 권취 최대(순서1 제외). 완벽 완스풀로 자투리가 없는 대형 순서(예 순4=180)나
        # 소형 마지막 순서(지폭·권취 작음)에는 여유분을 몰지 않는다(순수 여유 행 방지).
        part_orders = {oi for s in spools if under(s) for oi, _j, _b, _c in s.contribs}
        cand = {oi: p for oi, p in prod.items() if oi != o1 and oi in part_orders}
        if not cand:                    # 부분스풀 없음(전부 완스풀) → 부득이 대형 순서 사용
            cand = {oi: p for oi, p in prod.items() if oi != o1}
        co = max(cand, key=cand.get) if cand else o1
        tmpl = next((s for s in spools if any(c[0] == co for c in s.contribs)), None)
        guard = 0
        while total() < target_lo and guard < 4000:
            # co 순서가 든 미완 스풀 우선 → 없으면 co 폭의 순수 여유 스풀(재사용/새로)
            sink = next((s for s in spools if under(s) and s.contribs
                         and any(c[0] == co for c in s.contribs)), None)
            if sink is None and tmpl is not None:
                sink = next((s for s in spools if under(s) and not s.contribs
                             and s.width == tmpl.width and s.jeong == tmpl.jeong), None)
            if sink is None and tmpl is not None:
                sink = Spool(tmpl.jeong, tmpl.width, tmpl.bosang, tmpl.N, [], 0)
                spools.append(sink)
            if sink is None:
                break
            sink.yeoyu += 1
            guard += 1
        # 대형 순서의 '꼬리 부분스풀 + 순수 여유 스풀'을 같은 마끼수 부분스풀로 재분배한다.
        # (완스풀까지 억지로 안 채우고 답지처럼 '순3×1+여유13'을 2스풀(7,7)로 보이게.)
        if tmpl is not None:
            Nb = tmpl.N
            pool = [s for s in spools if s.jeong == tmpl.jeong and s.width == tmpl.width
                    and not (_count(s) == Nb and s.yeoyu == 0)]   # 완스풀 bulk 제외
            if pool:
                flat = []
                for s in pool:
                    for oi, j, b, c in s.contribs:
                        flat += [(oi, j, b)] * c
                tot = sum(_count(s) + s.yeoyu for s in pool)
                for s in pool:
                    spools.remove(s)
                mn = calc.min_n_cp11(tmpl.jeong, tmpl.bosang, cp, tmpl.width, teukgam)
                k = max(1, math.ceil(tot / Nb))
                while k > 1 and math.ceil(tot / k) < mn:
                    k -= 1
                size = math.ceil(tot / k)
                pos = 0
                for _ in range(k):
                    ids = flat[pos:pos + size]; pos += size
                    sp = Spool(tmpl.jeong, tmpl.width, tmpl.bosang, Nb, [], 0)
                    for oi, j, b in ids:
                        _add(sp, oi, j, b, 1)
                    if _count(sp) < size:
                        sp.yeoyu = size - _count(sp)
                    if _count(sp) + sp.yeoyu < mn:      # CP중량 최소 보장
                        sp.yeoyu = mn - _count(sp)
                    spools.append(sp)
        return

    guard = 0
    while total() < target_lo and guard < 2000:
        cur = total()
        # 추가 보충은 순서1 제외, 넓은 폭 부분스풀 우선
        cand = [s for s in spools if under(s) and not is_o1(s)]
        if not cand:
            cand = [s for s in spools if under(s)]   # 순서1만 남으면 부득이 사용
        if not cand:
            # 모두 꽉 참(N 이상, 자기 완스풀 배수로 딱 떨어짐) → 더 채울 '자연스러운' 미완
            # 스풀이 없다. 2026-08 답지 전수조사(전체 1,169행)로 확인: 어떤 행도
            # "(순서N번)..." 없이 여유분만 단독으로 나오지 않는다(주문 없는 빈 스풀을
            # 새로 만들면 안 됨). 그렇다고 기존 완스풀에 여유분을 그냥 얹으면 그 스풀
            # 하나의 CP중량이 N을 넘어 상한(33.3)을 위반한다. **절대 우선순위(CP중량
            # 11~33.3이 총생산≥초지보다 위)**를 지키기 위해 이 구간에서는 멈춘다 —
            # target_lo(=초지+1,000)는 소프트 목표라 여기까지만 채우고, 진짜 하한(초지)
            # 미만이면 UI가 "총생산 부족" 경고로 보여줘 검수자가 직접 확인하게 한다.
            break
        safe = [s for s in cand if cur + delta(s) <= target_hi]
        pool = safe if safe else cand
        # 재단량 비율(권취 생산량) 큰 순서 우선 → 같으면 넓은 폭 → 그다음 긴 정길이
        pool.sort(key=lambda s: (-prod_weight(s), -s.width, -s.jeong))
        pool[0].yeoyu += 1
        guard += 1


def pad_long_rolls(spools):
    """긴 롤(완스풀 N<=LONG_ROLL_N, 예 15,000m·N7) 부분스풀을 완스풀까지 여유분으로 채움.
    답지 학습: 작업자는 15,000m 같은 긴 롤을 부분으로 돌리지 않고 완스풀(N)까지 완성한다.
    단, '주문 전량이 한 스풀에 든 단독 스풀'만 대상(균등 분할된 스풀은 그대로 둠).
    혼합-정길이 스풀(answer_v2)은 N이 단일값이 아니라 대상에서 제외한다.
    topup(길이 보충) 이후에 호출 → 순서별 여유분과 별개로 긴 롤만 추가 패딩."""
    glob = {}
    for s in spools:
        for o, _j, _b, c in s.contribs:
            glob[o] = glob.get(o, 0) + c
    for s in spools:
        if s.mixed or s.N > LONG_ROLL_N or (_count(s) + s.yeoyu) >= s.N:
            continue
        orders_in = {o for o, _j, _b, _c in s.contribs}
        # 단일스풀(분할 아님)이고, 채울 여유분이 현재 권취 이하일 때만 완스풀까지 채운다.
        # (소형 주문을 2배 이상 뻥튀기하지 않음: 예 18,500m·권취3 → 3마끼 그대로 둠.)
        if (orders_in and sum(glob[o] for o in orders_in) <= s.N
                and 2 * _count(s) >= s.N):
            s.yeoyu = s.N - _count(s)


def enforce_even_short(spools, hapgwon=True):
    """<10,000m 합권은 스풀당 총 마끼(권취+여유)가 짝수여야 한다 → 홀수면 여유분 +1.
    특감(SFH/SL)만 예외 → hapgwon=False(특감)면 짝수화 안 함, 그 외 지종은 적용.
    혼합-정길이 스풀은 어느 정길이 기준인지 모호해 대상에서 제외한다."""
    if not hapgwon:
        return
    for s in spools:
        if s.mixed:
            continue
        if s.jeong < C.SHORT_LEN and (_count(s) + s.yeoyu) % 2 == 1:
            s.yeoyu += 1


def merge_for_convenience(spools, cp, teukgam):
    """작업편리성 모드: 인접 스풀을 합쳐 스풀 수를 줄인다(완스풀 위주).
    조건: 같은 정길이 & 폭차<=tol & 합산 마끼 <= 완스풀N. **인접 스풀끼리만**(순번
    건너뛰기 없음). 손율최소 결과에 한 번 더 패스 → 멀쩡한 인접 주문도 한 스풀에 합쳐
    스풀 수↓(대신 좁은 주문이 넓은 폭으로 가 폭손실 소폭↑). topup 이전에 호출."""
    if len(spools) <= 1:
        return spools
    widths = [s.width for s in spools]
    avg = (max(widths) + min(widths)) / 2

    def tol(w1, w2):
        return 50 if min(w1, w2) < avg else WIDTH_TOL

    out = [spools[0]]
    for s in spools[1:]:
        p = out[-1]
        w = max(p.width, s.width); b = max(p.bosang, s.bosang)
        n = _N(p.jeong, w, b, cp, teukgam)
        if (p.jeong == s.jeong and abs(p.width - s.width) <= tol(p.width, s.width)
                and _count(p) + _count(s) <= n):
            p.width, p.bosang, p.N = w, b, n
            for oi, j, bb, c in s.contribs:
                _add(p, oi, j, bb, c)
            p.yeoyu += s.yeoyu
        else:
            out.append(s)
    return out


def make_spools_streamed(orders, cp, teukgam):
    """답지 방식: 같은 정길이(폭 호환) 주문을 하나의 스트림으로 이어붙여 완스풀 N마끼로
    연속 충전. 중간에 낀 다른 정길이 주문은 건너뛴다(건너뛰기 병합). 스트림 끝 자투리가
    CP중량 최소 미만이면 직전 스풀과 합쳐 균등 분할(작은 orphan 방지)."""
    if not orders:
        return []
    widths = [o.width for o in orders]
    avg = (max(widths) + min(widths)) / 2

    def tol(w1, w2):
        return 50 if min(w1, w2) < avg else WIDTH_TOL

    # 1) 스트림 구성 — 정길이 같고 폭 호환인 주문을 순서 유지해 모음(다른 정길이는 건너뜀)
    used = [False] * len(orders)
    streams = []
    for i in range(len(orders)):
        if used[i]:
            continue
        o = orders[i]; used[i] = True
        contribs = [[o.idx, o.kwonchwi]]; w = o.width; b = o.bosang; j = o.jeong
        for k in range(i + 1, len(orders)):
            if used[k]:
                continue
            o2 = orders[k]
            if o2.jeong == j and abs(w - o2.width) <= tol(w, o2.width):
                used[k] = True
                contribs.append([o2.idx, o2.kwonchwi])
                w = max(w, o2.width); b = max(b, o2.bosang)
        streams.append((j, w, b, contribs))

    # 2) 스트림별 완스풀 연속 충전 + 자투리 균등화. 스풀 폭/보상은 '그 스풀 기여 주문'의 최대.
    wmap = {o.idx: o.width for o in orders}
    bmap = {o.idx: o.bosang for o in orders}
    spools = []
    for j, w, b, contribs in streams:
        N = _N(j, w, b, cp, teukgam)                # 스트림 최대폭 기준 완스풀(가장 보수적)
        mn = calc.min_n_cp11(j, b, cp, w, teukgam)
        flat = []
        for oid, cnt in contribs:
            flat.extend([oid] * cnt)
        M = len(flat)
        full, rem = divmod(M, N)
        sizes = [N] * full + ([rem] if rem else [])
        if not sizes:
            sizes = [M]
        if len(sizes) >= 2 and (sizes[-1] < mn or sizes[-1] <= N // 2):
            tot = sizes.pop() + sizes.pop()         # 작은 자투리 → 직전과 합쳐 균등분할
            sizes += [(tot + 1) // 2, tot // 2]
        pos = 0
        for s in sizes:
            ids = flat[pos:pos + s]; pos += s
            sw = max(wmap[o] for o in ids); sb = max(bmap[o] for o in ids)
            sp = Spool(j, sw, sb, _N(j, sw, sb, cp, teukgam), [], 0)
            for oid in ids:
                _add(sp, oid, j, bmap[oid], 1)
            if _count(sp) < calc.min_n_cp11(j, sb, cp, sw, teukgam):
                sp.yeoyu = calc.min_n_cp11(j, sb, cp, sw, teukgam) - _count(sp)
            spools.append(sp)
    return spools


def make_spools_perorder(orders, cp, teukgam):
    """새 방식(①②④, 답지 LOT2260702206 역산):
      ② 같은 정길이 순서는 각자 완스풀(floor(권취/N))을 '단일 순서'로 만든다
         (한 완스풀 안에 여러 순서 안 섞음 — 답지처럼 순6·순7 각자 완스풀).
      ① 자투리(권취%N)는 '대형 권취 순서'를 기준으로 두 구역으로 처리한다.
         - 대형 순서까지(앞): 자투리를 이어붙여 완스풀(N)을 완성하고, 대형 순서의
           마지막 잔여는 '꼬리 부분스풀'로 격리 → topup 여유분이 그쪽으로 몰린다.
         - 대형 순서 뒤: 인접 자투리를 한 폭(최댓값)으로 '균등 등분'해 한 행으로 묶는다
           (부족분은 여유분으로 채워 같은 마끼수 → 답지처럼 순6×6+순7×5 한 행).
      ④ 단독이며 소형(같은 정길이 1개 & 권취<=2N)인 순서는 완스풀 대신 균등 등분한다
         (지폭·재단량이 작아 여유분을 다른 곳에 쓰기 어려운 순서 → 예 순5 10→5+5).
    같은 정길이는 중간에 다른 정길이가 껴 있어도 한 그룹(자투리 건너뛰기 병합 허용)."""
    if not orders:
        return []
    wmap = {o.idx: o.width for o in orders}
    bmap = {o.idx: o.bosang for o in orders}

    def _flat(contribs):
        f = []
        for oid, cnt in contribs:
            f += [oid] * cnt
        return f

    def fill_complete(jeong, contribs):
        """자투리를 이어붙여 완스풀(N)을 채우고 마지막 잔여는 부분스풀(꼬리)로 남긴다.
        스풀 폭/보상=그 스풀 기여 주문의 최대. 마지막 부분스풀은 CP중량 최소까지만 보정."""
        flat = _flat(contribs)
        M = len(flat)
        if M == 0:
            return []
        rw = max(wmap[o] for o in flat); rb = max(bmap[o] for o in flat)
        No = _N(jeong, rw, rb, cp, teukgam)
        out, pos = [], 0
        while pos < M:
            ids = flat[pos:pos + No]; pos += No
            sw = max(wmap[o] for o in ids); sb = max(bmap[o] for o in ids)
            sp = Spool(jeong, sw, sb, _N(jeong, sw, sb, cp, teukgam), [], 0)
            for oid in ids:
                _add(sp, oid, jeong, bmap[oid], 1)
            need = calc.min_n_cp11(jeong, sb, cp, sw, teukgam)
            if _count(sp) < need:
                sp.yeoyu = need - _count(sp)
            out.append(sp)
        return out

    def merge_even(jeong, contribs):
        """인접 자투리를 한 폭(run 최댓값)으로 균등 등분. 부족분은 여유분으로 채워
        모든 스풀 같은 마끼수 → spools_to_rows에서 한 행으로 묶임(순6×6+순7×5)."""
        flat = _flat(contribs)
        M = len(flat)
        if M == 0:
            return []
        rw = max(wmap[o] for o in flat); rb = max(bmap[o] for o in flat)
        No = _N(jeong, rw, rb, cp, teukgam)
        mn = calc.min_n_cp11(jeong, rb, cp, rw, teukgam)
        k = max(1, math.ceil(M / No))
        while k > 1 and math.ceil(M / k) < mn:
            k -= 1
        size = math.ceil(M / k)
        out, pos = [], 0
        for _ in range(k):
            ids = flat[pos:pos + size]; pos += size
            sp = Spool(jeong, rw, rb, No, [], 0)      # 같은 폭(run 최댓값)
            for oid in ids:
                _add(sp, oid, jeong, bmap[oid], 1)
            if _count(sp) < size:                     # 균등화(부족분 여유분)
                sp.yeoyu = size - _count(sp)
            if _count(sp) + sp.yeoyu < mn:             # CP중량 최소 보장
                sp.yeoyu = mn - _count(sp)
            out.append(sp)
        return out

    # 정길이별 그룹(첫 등장 순서 유지)
    groups = []
    for o in orders:
        g = next((x for x in groups if x[0] == o.jeong), None)
        if g is None:
            groups.append([o.jeong, [o]])
        else:
            g[1].append(o)

    spools = []
    for jeong, gords in groups:
        wmax = max(o.width for o in gords); bmax = max(o.bosang for o in gords)
        N = _N(jeong, wmax, bmax, cp, teukgam)
        # ④ 단독 소형(권취<=2N) → 완스풀 대신 균등 등분
        if len(gords) == 1 and gords[0].kwonchwi <= 2 * N:
            spools += merge_even(jeong, [[gords[0].idx, gords[0].kwonchwi]])
            continue
        # ② 각 순서 완스풀(단일순서) + 자투리 수집
        rem = []
        for o in gords:
            No = _N(jeong, o.width, o.bosang, cp, teukgam)
            full = o.kwonchwi // No
            for _ in range(full):
                sp = Spool(jeong, o.width, o.bosang, No, [], 0)
                _add(sp, o.idx, jeong, o.bosang, No)
                spools.append(sp)
            r = o.kwonchwi - full * No
            if r > 0:
                rem.append([o.idx, r])
        if not rem:
            continue
        # ① 대형 권취 순서(자기 완스풀 bulk가 있는 순서)를 '구분선'으로 앞/뒤 자투리를
        #    따로 처리한다(대형 순서를 건너뛰어 병합하지 않음).
        #    - 대형 순서에 자투리가 있으면: 앞 구역은 완스풀 완성 + 대형 순서 꼬리 격리.
        #    - 대형 순서가 완벽 완스풀(자투리 없음)이면: 앞/뒤 구역을 각각 균등병합.
        #    - 대형 순서 자체가 없으면(그룹 전부 권취<N): 자투리 통째 완스풀 완성(순4+순8).
        sink = max(gords, key=lambda o: o.kwonchwi)
        if sink.kwonchwi >= N:
            sp_pos = gords.index(sink)
            if sink.kwonchwi % N != 0:                 # 대형 순서 꼬리 있음
                before = {o.idx for o in gords[:sp_pos + 1]}
                spools += fill_complete(jeong, [x for x in rem if x[0] in before])
                after = [x for x in rem if x[0] not in before]
                if after:
                    spools += merge_even(jeong, after)
            else:                                      # 완벽 완스풀 → 앞/뒤 각각 균등병합
                before = {o.idx for o in gords[:sp_pos]}
                A = [x for x in rem if x[0] in before]
                B = [x for x in rem if x[0] not in before]
                if A:
                    spools += merge_even(jeong, A)
                if B:
                    spools += merge_even(jeong, B)
        else:
            spools += fill_complete(jeong, rem)
    return spools


def make_spools_crossjeong(orders, cp, teukgam):
    """교차-정길이 병합 지원 패커(신규, 2026-08 CM2최종학습 108LOT 반영).

    ① 각 주문은 먼저 자기 정길이 기준 완스풀(floor(권취/N))을 자기 스풀로 채운다(순서 안 섞임).
    ② 남은 자투리(권취%N)는 **순서(=23요청폭 내림차순 순위) 순서대로** 훑으며, 정길이가
       달라도 폭이 인접(tol 이내)하면 누적 CP중량이 상한(33.3)을 넘기 전까지 forward로
       한 스풀에 채운다. 상한을 넘거나 폭이 인접하지 않으면 스풀을 닫고 새로 시작한다.
    ③ 완성된 자투리 스풀이 CP중량 하한(11) 미만이면, 마지막으로 채운 항목의 정길이/보상을
       반복한 여유분으로 채운다(=답지에서 여유분이 그 스풀의 마지막 주문과 같은 정길이로
       나오는 패턴과 일치).
    학습 근거(108LOT 중 핵심 사례): LOT2260200347(정12000+정15000 혼합, 순5+순6) ·
    LOT2260201111(정6000+정16000 혼합, 순3+순4) · LOT2260600337(정15000+정16700 혼합) ·
    LOT2260701526 등 다수에서 반복 확인된 '폭 같으면 정길이 달라도 한 스풀' 규칙.
    같은 정길이끼리는 make_spools_perorder처럼 각자 완스풀(순서 안 섞임)을 유지하고,
    이 함수는 그 자투리에만 교차-정길이 병합을 적용한다."""
    if not orders:
        return []
    widths = [o.width for o in orders]
    avg = (max(widths) + min(widths)) / 2

    def tol(w1, w2):
        return 50 if min(w1, w2) < avg else WIDTH_TOL

    spools = []
    leftovers = []  # [(order_idx, jeong, bosang, width, count)], 순서(rank) 유지
    for o in orders:
        N = _N(o.jeong, o.width, o.bosang, cp, teukgam)
        full = o.kwonchwi // N
        for _ in range(full):
            sp = Spool(o.jeong, o.width, o.bosang, N, [], 0)
            _add(sp, o.idx, o.jeong, o.bosang, N)
            spools.append(sp)
        r = o.kwonchwi - full * N
        if r > 0:
            leftovers.append((o.idx, o.jeong, o.bosang, o.width, r))

    spools += _crossjeong_forward_merge(leftovers, cp, teukgam, tol)
    return spools


def _crossjeong_forward_merge(leftovers, cp, teukgam, tol):
    """자투리 [(order_idx,jeong,bosang,width,count), ...](순서 유지)를 폭 인접이면
    정길이가 달라도 forward로 누적해 CP중량 상한 전까지 한 스풀에 채운다. 완성된
    스풀이 CP중량 하한(11) 미만이면 마지막으로 채운 항목의 정길이/보상을 반복한
    여유분으로 채운다. make_spools_crossjeong·make_spools_faithful 공용 헬퍼."""
    spools = []

    def close(items, sp):
        if sp is None or not items:
            return
        last_j, last_b = items[-1][0], items[-1][1]
        sp.jeong, sp.bosang = last_j, last_b   # 여유분/그룹핑용 대표값 = 마지막 채운 항목
        # N을 대표값(마지막 항목) 기준으로 정해둬야 topup_yeoyu의 under(s)=filled<N 판정이
        # 정상 동작한다(2026-08 버그수정: N=0 방치 시 항상 '꽉 참'으로 보여 총생산 미달 발생).
        sp.N = _N(last_j, sp.width, last_b, cp, teukgam)
        extra = calc.extra_needed_for_min(items, last_j, last_b, cp, sp.width, teukgam)
        if extra:
            sp.yeoyu = extra
        spools.append(sp)

    cur = None            # 현재 채우는 중인 (혼합 가능) 자투리 스풀
    cur_items = []         # [(jeong,bosang,count)] — cur의 실제 구성(CP중량 계산용)
    for oi, jeong, bosang, width, cnt in leftovers:
        if cur is not None and abs(cur.width - width) > tol(cur.width, width):
            close(cur_items, cur)
            cur, cur_items = None, []
        if cur is None:
            cur = Spool(jeong, width, bosang, 0, [], 0)
            cur_items = []
        remain = cnt
        while remain > 0:
            trial = cur_items + [(jeong, bosang, 1)]
            js = calc.wonji_jeonsan_items(trial, cp, teukgam)
            w = calc.cp_weight(cp, js, max(cur.width, width))
            if w > C.CP_WEIGHT_MAX and cur_items:
                close(cur_items, cur)
                cur = Spool(jeong, width, bosang, 0, [], 0)
                cur_items = []
                continue
            cur_items.append((jeong, bosang, 1))
            cur.width = max(cur.width, width)
            _add(cur, oi, jeong, bosang, 1)
            remain -= 1
    if cur is not None:
        close(cur_items, cur)

    return spools


def make_spools_faithful(orders, cp, teukgam):
    """답지 그대로 재현 우선 모드(신규, mode="answer_v3", 2026-08 사용자 요청).

    두 학습 결과를 합친다:
      ① make_spools_streamed와 동일하게 같은 정길이를 연속충전(건너뛰기 병합)해
         완스풀 단위로 최대한 채운다 — 답지의 '같은 정길이는 순서를 건너뛰어도
         이어붙인다' 사례를 가장 정확히 재현하는 방식(108LOT에서 반복 확인).
      ② ①에서 완스풀(N)을 다 못 채운 부분스풀(자투리)은 전부 풀어서, 순서(폭)
         순서로 forward 교차-정길이 병합을 다시 시도한다 — 정길이가 달라도 폭이
         인접하면 섞는다(답지 실제 사례: LOT2260200347 순5+순6, LOT2260201111
         순3+순4 등). ①에서 이미 완스풀을 채운 스풀은 건드리지 않는다.
    답지 텍스트 100% 일치는 여전히 보장 못 한다(자투리 처리의 최종 판단은 작업자
    재량 — 108LOT 학습 결론). 이 모드는 '구조적 유사도'를 다른 모드보다 우선한다."""
    if not orders:
        return []
    spools = make_spools_streamed(orders, cp, teukgam)

    widths = [o.width for o in orders]
    avg = (max(widths) + min(widths)) / 2

    def tol(w1, w2):
        return 50 if min(w1, w2) < avg else WIDTH_TOL

    # 완스풀(N)을 못 채운 부분스풀만 '진짜 자투리'로 보고 재처리 대상으로 뽑는다
    # (기존 yeoyu는 버리고 실제 contribs만 재료로 써서 교차-정길이 병합을 새로 시도).
    weak = [s for s in spools if _count(s) + s.yeoyu < s.N]
    if not weak:
        return spools
    keep = [s for s in spools if _count(s) + s.yeoyu >= s.N]

    rank = {o.idx: i for i, o in enumerate(orders)}
    weak.sort(key=lambda s: min((rank.get(c[0], 10**9) for c in s.contribs), default=10**9))

    leftovers = []
    for s in weak:
        w = s.width
        for oi, j, b, c in s.contribs:
            leftovers.append((oi, j, b, w, c))

    keep += _crossjeong_forward_merge(leftovers, cp, teukgam, tol)
    return keep


def make_spools_answer_o1(orders, cp, teukgam):
    """'순서1 여유분' 방식: 순서1은 병합하지 않고 여유분을 더해 완스풀(N)로 만든다.
    순서1이 이미 완벽한 완스풀(권취가 N의 배수)이면 순서2로 넘어가 같은 처리를 하고,
    처음으로 완스풀이 안 떨어지는 주문까지를 '단독(완스풀)'로 두고, 그 뒤는 새 방식
    (make_spools_perorder: 순서별 완스풀+자투리병합)으로 처리한다."""
    spools = []
    split = 0
    for i, o in enumerate(orders):
        N = _N(o.jeong, o.width, o.bosang, cp, teukgam)
        k = max(1, math.ceil(o.kwonchwi / N))
        left = o.kwonchwi
        for _ in range(k):
            sp = Spool(o.jeong, o.width, o.bosang, N, [], 0)
            take = min(N, left)
            if take > 0:
                _add(sp, o.idx, o.jeong, o.bosang, take)
            left -= take
            sp.yeoyu = N - _count(sp)          # 부분스풀을 여유분으로 완스풀 완성
            spools.append(sp)
        split = i + 1
        if o.kwonchwi % N != 0:                 # 완스풀이 안 떨어짐 → 여기까지 단독, 이후 새 방식
            break
    spools += make_spools_perorder(orders[split:], cp, teukgam)
    return spools


def _schedule_per_order(orders, cp, teukgam, choji_len=0):
    """순서별 1행: 각 주문을 ceil(권취/N)개의 '동일 크기' 스풀로 나눠 **행수 = 주문수**.
    병합·건너뛰기 없음. 각 스풀은 CP중량 11~33, 합권(<10000m)은 스풀당 짝수.
    총생산길이 < 초지면 넓은 폭 순서부터 스풀당 마끼 +1(여유)로 보충."""
    rows = []
    for o in orders:
        N = _N(o.jeong, o.width, o.bosang, cp, teukgam)
        mn = calc.min_n_cp11(o.jeong, o.bosang, cp, o.width, teukgam)
        M = max(1, o.kwonchwi)
        k = max(1, math.ceil(M / N))          # 스풀 수
        s = max(math.ceil(M / k), mn)         # 스풀당 마끼(동일)
        if o.jeong < C.SHORT_LEN and s % 2 == 1:
            s += 1                            # 합권 짝수
        rows.append(Row(o.width, o.jeong, s, k, [[o.idx, o.jeong, o.bosang, M]], 0, o.bosang))

    def recompute():
        t = 0
        for r in rows:
            r.yeoyu = r.spools * r.N - sum(c for _, _, _, c in r.contribs)
            r.choji_real = calc.choji_saengsan_real(r.jeong, r.bosang, r.N, cp, teukgam)
            r.saengsan = calc.saengsan_len(r.jeong, r.bosang, r.N, r.spools, cp, teukgam)
            t += r.saengsan
        return t

    total = recompute()
    guard = 0
    while choji_len and total < choji_len + 1000 and guard < 1000:
        r = sorted(rows, key=lambda x: (-x.width, x.jeong))[0]
        r.N += 2 if r.jeong < C.SHORT_LEN else 1   # 짧은롤은 짝수 유지
        total = recompute()
        guard += 1
    rows.sort(key=lambda r: (-r.width, r.contribs[0][0] if r.contribs else 999))
    recompute()
    return rows, sum(r.saengsan for r in rows)


def schedule(orders, cp, teukgam, choji_gongjeong_len=0, mode="loss"):
    """mode='answer'(답지식: 완스풀 연속충전+건너뛰기) | 'answer_o1'(순서1 여유분 우선) |
       'answer_v2'(교차-정길이 병합, 자투리 전체를 폭 인접 기준으로 섞음) |
       'answer_v3'(답지 재현 우선, 신규 — 정길이별 완스풀 우선 + 그래도 남는 순수여유
       자투리만 교차-정길이로 재시도) | 'per_order'(순서별 1행)
       | 'loss'(손율 최소, 기본) | 'convenience'(작업 편리성).
    입력 orders는 항상 23요청지폭 내림차순으로 재정렬·재번호(rank_orders)한 뒤 패킹한다
    (2026-08 CM2최종학습 108LOT 확정 규칙 — 답지 순서라벨=폭내림차순순위)."""
    orders = rank_orders(orders)
    if mode == "per_order":
        return _schedule_per_order(orders, cp, teukgam, choji_gongjeong_len)
    if mode == "answer":
        spools = make_spools_streamed(orders, cp, teukgam)
    elif mode == "answer_o1":
        spools = make_spools_answer_o1(orders, cp, teukgam)
    elif mode == "answer_v2":
        spools = make_spools_crossjeong(orders, cp, teukgam)
    elif mode == "answer_v3":
        spools = make_spools_faithful(orders, cp, teukgam)
    else:
        spools = make_spools(orders, cp, teukgam)
    if mode == "convenience":
        spools = merge_for_convenience(spools, cp, teukgam)
    topup_yeoyu(spools, cp, teukgam, choji_gongjeong_len,
                concentrate=(mode == "answer_o1"))
    pad_long_rolls(spools)                 # 긴 롤(N<=7) 단독 부분스풀 완스풀 패딩
    enforce_even_short(spools, hapgwon=(not teukgam))  # 특감(SFH/SL)만 짝수화 제외
    # 폭 내림차순, 동폭이면 가장 앞 순서 우선(결과가 순서 흐름대로 보이도록)
    spools.sort(key=lambda s: (-s.width, min((c[0] for c in s.contribs), default=999)))
    rows = spools_to_rows(spools, cp, teukgam)
    total = sum(r.saengsan for r in rows)
    return rows, total
