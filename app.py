# -*- coding: utf-8 -*-
"""지폭스케줄 생성기 — Streamlit UI.

흐름: 스케줄 PDF 업로드 → (Gemini 자동 파싱 또는 수동) 주문 검수/수정 →
      지폭스케줄 생성 → 결과 표 + 검증 + 엑셀 다운로드.
실행: streamlit run app.py
"""
import os
import pandas as pd
import streamlit as st

from jipok.models import Order
from jipok import pack, calc, pdf_extract, excel_io, gemini_parse

st.set_page_config(page_title="지폭스케줄 생성기", layout="wide")
st.title("📄 지폭스케줄 생성기 (PM23 생산지폭)")
st.caption("스캔 PDF를 Gemini로 자동 파싱하거나 직접 입력해 검수한 뒤 스케줄을 생성하세요.")

def _default_gem_key():
    """API 키 자동 로드: 환경변수(로컬) → Streamlit Cloud Secrets 순. 없으면 빈 값."""
    k = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if k:
        return k
    try:                       # secrets.toml/클라우드 Secrets가 없으면 접근만으로 예외 → 무시
        return st.secrets.get("GEMINI_API_KEY", "") or ""
    except Exception:
        return ""


# gem_key는 위젯(브라우저 전송)에 저장하는 '수동 입력용' 값 → 기본 빈 값.
# 저장된 키(Secrets/환경변수)는 위젯에 넣지 않고 파싱 시 서버에서만 읽는다(브라우저 노출 방지).
DEFAULTS = {"jong_k": "한솔감열지 SF", "cp_k": 48, "teukgam_k": False, "lot_k": "", "choji_k": 0,
            "gem_key": "", "gem_model": gemini_parse.DEFAULT_MODEL,
            "data_version": 0, "parse_msg": None}
for k, v in DEFAULTS.items():
    st.session_state.setdefault(k, v)
if "orders_df" not in st.session_state:
    st.session_state.orders_df = pd.DataFrame(
        [{"순서": i + 1, "정길이": 16000, "권취": 0, "지폭": 0, "보상": 120,
          "23요청폭(자동)": 0} for i in range(6)])


def do_parse():
    """Gemini 파싱 콜백 — 헤더·주문표를 채운다(위젯 생성 전 실행됨)."""
    f = st.session_state.get("pdf_up")
    if f is None:
        st.session_state.parse_msg = ("error", "먼저 PDF를 업로드하세요.")
        return
    try:
        # 수동 입력이 있으면 우선, 없으면 저장된 키(Secrets/환경변수)를 '서버에서만' 사용
        eff_key = (st.session_state.get("gem_key") or _default_gem_key()) or None
        data = gemini_parse.parse_schedule(
            f.getvalue(), eff_key,
            st.session_state.get("gem_model", gemini_parse.DEFAULT_MODEL))
        st.session_state["jong_k"] = str(data.get("jong", ""))
        st.session_state["cp_k"] = int(data.get("cp", 48))
        st.session_state["lot_k"] = str(data.get("lot", ""))
        st.session_state["choji_k"] = int(data.get("choji", 0))
        st.session_state["teukgam_k"] = bool(data.get("teukgam", False))
        cp_v = int(data.get("cp", 48) or 48)
        tg_v = bool(data.get("teukgam", False))
        olist = data.get("orders", [])
        jps = [int(o.get("jipok", 0) or 0) for o in olist]
        raw_w = [calc.request_width(jp, cp_v, tg_v) if jp > 0 else 0 for jp in jps]
        smooth_w = calc.smooth_request_widths(raw_w)   # 인접 순서 평활화
        rows_d = []
        for i, o in enumerate(olist):
            rows_d.append({
                "순서": int(o.get("sunseo", i + 1)),
                "정길이": int(o.get("jeong", 0)),
                "권취": int(o.get("kwonchwi", 0)),
                "지폭": jps[i],
                "보상": int(o.get("bosang", 120)),
                "23요청폭(자동)": smooth_w[i],
            })
        st.session_state.orders_df = pd.DataFrame(rows_d)
        st.session_state.data_version += 1
        msg = f"{len(data.get('orders', []))}개 주문 파싱 완료 — 표에서 검수하세요."
        warns = data.get("_warnings") or []
        if warns:
            msg += "  ⚠️ " + ", ".join(warns)
        st.session_state.parse_msg = ("success", msg)
    except Exception as e:
        st.session_state.parse_msg = ("error", f"Gemini 파싱 실패: {e}")


with st.sidebar:
    st.header("① 기본 정보")
    st.text_input("지종", key="jong_k")
    st.number_input("CP (평량)", min_value=30, max_value=100, step=1, key="cp_k")
    st.checkbox("특감 (SFH/SL)", key="teukgam_k")
    st.text_input("계획 LOT", key="lot_k")
    st.number_input("초지공정 길이 (m)", min_value=0, step=1, key="choji_k")

    st.divider()
    st.file_uploader("스케줄 PDF 업로드", type="pdf", key="pdf_up")

    st.subheader("🤖 Gemini 자동 파싱")
    if _default_gem_key():          # 값은 표시하지 않고 '있음/없음'만 확인(브라우저로 값 전송 X)
        st.caption("🔑 저장된 키(Secrets/환경변수) 자동 사용 중 — 아래 입력칸은 비워두세요.")
    st.text_input("Gemini API 키 (수동 입력 시에만)", type="password", key="gem_key",
                  help="여기 입력하면 이 값이 우선. 비워두면 서버에 저장된 Secrets/환경변수 키를 "
                       "'서버에서만' 읽어 씁니다(브라우저로 키를 보내지 않음).")
    st.selectbox("모델", gemini_parse.MODELS, key="gem_model",
                 help="gemini-flash-latest=항상 최신 flash(권장). 모델 미제공(404)·쿼터·혼잡 시 "
                      "다른 모델로 자동 폴백합니다.")
    st.button("PDF 자동 파싱", on_click=do_parse,
              disabled=st.session_state.get("pdf_up") is None, width="stretch")
    if st.session_state.parse_msg:
        lvl, msg = st.session_state.parse_msg
        (st.success if lvl == "success" else st.error)(msg)

jong = st.session_state["jong_k"]
cp = st.session_state["cp_k"]
teukgam = st.session_state.get("teukgam_k", False)
lot = st.session_state["lot_k"]
choji = st.session_state["choji_k"]

left, right = st.columns([5, 6])
with left:
    st.subheader("스케줄 원본")
    f = st.session_state.get("pdf_up")
    if f is not None:
        try:
            for img in pdf_extract.render_pages(f.getvalue(), dpi=200):
                st.image(img, width="stretch")
        except Exception as e:
            st.error(f"PDF 렌더 실패: {e}")
    else:
        st.info("사이드바에서 PDF를 업로드하면 여기에 표시됩니다.")

with right:
    st.subheader("② 주문 (RE공정) — 검수 / 수정")
    st.caption("Gemini 파싱 결과를 검수하거나 직접 입력. 23요청폭은 지폭으로 자동계산(직접 덮어쓰기 가능).")
    df = st.data_editor(
        st.session_state.orders_df, num_rows="dynamic", width="stretch",
        key=f"editor_{st.session_state.data_version}",
        column_config={
            "정길이": st.column_config.NumberColumn(help="RE 지폭 칸의 뒤(아래) 숫자 (예 12000)"),
            "권취": st.column_config.NumberColumn(help="마끼 수(작은 정수)"),
            "지폭": st.column_config.NumberColumn(help="RE 지폭 칸의 앞(위) 숫자(최종 재단폭, 예 4915)"),
            "보상": st.column_config.SelectboxColumn(options=[120, 170, 240]),
            "23요청폭(자동)": st.column_config.NumberColumn(
                help="지폭+코타손실+트림(CP≤48:+85, 초과:+75) 반올림. 0이면 생성 시 자동계산"),
        })
    bcol1, bcol2 = st.columns([1, 2])
    recompute = bcol1.button("🔄 23요청폭 재계산", width="stretch",
                             help="지폭으로 23호기요청지폭 다시 계산")
    go = bcol2.button("🚀 지폭스케줄 생성", type="primary", width="stretch")

    if recompute:
        cp_v = int(st.session_state["cp_k"]); tg_v = st.session_state.get("teukgam_k", False)
        df2 = df.copy()
        # 순서대로 원시 23요청폭 계산 → 인접 평활화 → 같은 순서로 되돌려 배정
        try:
            order_ix = df2.sort_values("순서").index.tolist()
        except KeyError:
            order_ix = list(df2.index)
        raw_w = [calc.request_width(int(df2.at[i, "지폭"] or 0), cp_v, tg_v)
                 if int(df2.at[i, "지폭"] or 0) > 0 else 0 for i in order_ix]
        for i, v in zip(order_ix, calc.smooth_request_widths(raw_w)):
            df2.at[i, "23요청폭(자동)"] = v
        st.session_state.orders_df = df2
        st.session_state.data_version += 1
        st.rerun()


def build_orders(df, cp, teukgam):
    orders = []
    for _, r in df.iterrows():
        try:
            kwon = int(r["권취"]); jeong = int(r["정길이"])
        except (ValueError, TypeError):
            continue
        if kwon <= 0 or jeong <= 0:
            continue
        jp = int(r.get("지폭") or 0)
        override = int(r.get("23요청폭(자동)") or 0)
        width = override if override > 0 else (
            calc.request_width(jp, int(cp), teukgam) if jp > 0 else 4880)
        # 특감(SFH/SL)만 짧은롤에서 검수표 보상값 사용; 그 외는 원래대로 짧은롤 강제 120
        bval = int(r.get("보상") or 120)
        bosang = 120 if (jeong < 10000 and not teukgam) else bval
        orders.append(Order(int(r["순서"]), jeong, kwon, width, bosang))
    return orders


if go:
    orders = build_orders(df, cp, teukgam)
    if not orders:
        st.warning("권취·정길이가 입력된 주문이 없습니다.")
    else:
        o1 = pack.schedule(orders, int(cp), teukgam, int(choji), mode="answer_o1")
        ans = pack.schedule(orders, int(cp), teukgam, int(choji), mode="answer")
        v2 = pack.schedule(orders, int(cp), teukgam, int(choji), mode="answer_v2")
        v3 = pack.schedule(orders, int(cp), teukgam, int(choji), mode="answer_v3")
        st.session_state.result = {"orders": orders, "o1": o1, "ans": ans, "v2": v2, "v3": v3}

if "result" in st.session_state:
    res = st.session_state.result
    orders = res["orders"]
    st.divider()
    st.subheader("③ 생성된 지폭스케줄")
    def _lbl(name, key):
        r0 = res[key][0]
        return f"{name} ({len(r0)}행 · {sum(x.spools for x in r0)}스플)"
    opt_v3 = _lbl("답지 재현 우선", "v3")
    opt_o1 = _lbl("여유분 우선", "o1")
    opt_ans = _lbl("완스풀 우선", "ans")
    opt_v2 = _lbl("교차정길이 병합", "v2")
    pick = st.radio("스케줄 방식", [opt_v3, opt_v2, opt_o1, opt_ans], horizontal=True,
                    help="답지 재현 우선(신규,권장)=같은 정길이 연속충전 우선 + 그래도 남는 자투리만 폭 인접이면 "
                         "정길이 달라도 병합(2026-08 CM2최종학습 108LOT 기반, 답지 구조와 가장 근접) · "
                         "교차정길이 병합=자투리 전체를 폭 인접 기준으로 우선 섞음 · "
                         "여유분 우선=순서1(또는 첫 비완스풀 주문)은 병합없이 여유분으로 완스풀 완성 후 나머지 처리 · "
                         "완스풀 우선=같은 정길이끼리 완스풀 연속충전, 다른 정길이는 건너뜀")
    rows, total = {opt_o1: res["o1"], opt_ans: res["ans"], opt_v2: res["v2"], opt_v3: res["v3"]}[pick]
    res_df = pd.DataFrame([{
        "23호기요청지폭": r.width, "초지생산실길이": f"{r.choji_real:,}", "스플수": r.spools,
        "비고": r.bigo(), "생산길이": f"{r.saengsan:,}"} for r in rows])
    st.dataframe(res_df, width="stretch", hide_index=True)

    excess = total - int(choji)
    win_ok = (excess >= 0) if choji else total > 0
    cp_bad = even_bad = 0
    for r in rows:
        # r.contribs/r.yeoyu는 그 행에 묶인 '여러 스풀'의 누적치(스플수=r.spools)라
        # 그대로 items로 넘기면 스풀 1개가 아니라 spools개 분량을 한 스풀인 것처럼
        # 계산해 CP중량이 실제보다 spools배 부풀려진다. 실제 스풀 1개의 전산길이는
        # 이미 r.saengsan(=round100(전산길이)*spools)에 들어있으므로 나눠서 구한다.
        js_per_spool = r.saengsan / r.spools
        w = calc.cp_weight(int(cp), js_per_spool, r.width)
        if not (calc.C.CP_WEIGHT_MIN <= w <= calc.C.CP_WEIGHT_MAX):
            cp_bad += 1
        hapgwon = not teukgam       # 특감(SFH/SL)만 짝수 규칙 제외, 그 외는 원래대로 적용
        if (hapgwon and not r.mixed and r.jeong < calc.C.SHORT_LEN
                and (sum(c for _, _, _, c in r.contribs) + r.yeoyu) % 2 == 1):
            even_bad += 1

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("총생산길이", f"{total:,}")
    c2.metric("초지 대비", f"{excess:+,}", delta="초지이상" if win_ok else "부족")
    c3.metric("CP중량 위반", cp_bad, delta="OK" if cp_bad == 0 else "확인", delta_color="inverse")
    c4.metric("<10000m 홀수", even_bad, delta="OK" if even_bad == 0 else "확인", delta_color="inverse")
    if not win_ok:
        st.warning("총생산길이가 초지공정길이보다 적습니다. 여유분/병합 조정 필요.")

    mode_tag = {opt_o1: "여유분우선", opt_ans: "완스풀우선", opt_v2: "교차정길이병합",
                opt_v3: "답지재현우선"}[pick]
    xlsx = excel_io.export_schedule_bytes(
        rows, total, jong=jong, lot=lot, cp=int(cp),
        jeong_lens=[o.jeong for o in orders], choji=int(choji))
    st.download_button(f"⬇️ 엑셀(.xlsx) 다운로드 — {mode_tag}", xlsx,
                       file_name=f"지폭스케줄_{lot or 'output'}_{mode_tag}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       width="stretch")
    st.caption("위 라디오로 방식을 비교해 선택하세요. 자투리·여유분 배치는 작업자 판단으로 조정될 수 있습니다(초안).")
