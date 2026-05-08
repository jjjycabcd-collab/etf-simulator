import streamlit as st
import fitz  # PyMuPDF
import json
import pandas as pd
from PIL import Image
import io
from streamlit_drawable_canvas import st_canvas

# 페이지 기본 설정 (넓게 쓰기) 
st.set_page_config(page_title="SI 데이터 구축 엔진 (Web)", layout="wide")

# 세션 상태 초기화
if 'annotations' not in st.session_state:
    st.session_state.annotations = []
if 'page_idx' not in st.session_state:
    st.session_state.page_idx = 0

# ----------------------------------------
# 핵심 로직 함수
# ----------------------------------------
def get_autofit_rect(page, pdf_rect, enabled=True):
    if not enabled:
        return pdf_rect
    words = page.get_text("words")
    fitted_rect = None
    for w in words:
        w_rect = fitz.Rect(w[:4])
        if pdf_rect.intersects(w_rect):
            fitted_rect = w_rect if fitted_rect is None else fitted_rect | w_rect 
    return fitted_rect if fitted_rect else pdf_rect

def get_sorted_text(page, rect):
    words = page.get_text("words", clip=rect)
    if not words: return ""
    x_coords = sorted([w[0] for w in words])
    gaps = [x_coords[i+1] - x_coords[i] for i in range(len(x_coords)-1)]
    max_gap = max(gaps) if gaps else 0
    threshold = rect.width * 0.15 
    if max_gap > threshold:
        split_idx = gaps.index(max_gap)
        split_x = (x_coords[split_idx] + x_coords[split_idx+1]) / 2
        left_col = sorted([w for w in words if w[0] < split_x], key=lambda w: (w[1], w[0]))
        right_col = sorted([w for w in words if w[0] >= split_x], key=lambda w: (w[1], w[0]))
        sorted_words = left_col + right_col
    else:
        sorted_words = sorted(words, key=lambda w: (w[1], w[0]))
    return " ".join([w[4] for w in sorted_words])

# ----------------------------------------
# UI 구성
# ----------------------------------------
st.title("📄 문서 데이터 추출 엔진 - Web Prototype")
st.markdown("고객 데모용 프로토타입입니다. PDF를 업로드하고 이미지 위에 박스를 그려 데이터를 추출해 보세요.")

# 1. 사이드바: 파일 업로드 및 옵션
with st.sidebar:
    st.header("설정 및 업로드")
    uploaded_file = st.file_uploader("PDF 파일 업로드", type=['pdf'])
    autofit_enabled = st.checkbox("✨ 정밀 오토피팅 모드", value=True)
    
    if st.button("🗑️ 전체 데이터 초기화"):
        st.session_state.annotations = []
        st.rerun()

if uploaded_file is not None:
    # PDF 메모리 로드
    doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")
    total_pages = len(doc)
    
    # 페이지 네비게이션
    col1, col2, col3 = st.columns([1, 2, 1])
    with col1:
        if st.button("◀ 이전") and st.session_state.page_idx > 0:
            st.session_state.page_idx -= 1
            st.rerun()
    with col2:
        st.markdown(f"<h4 style='text-align: center;'>Page {st.session_state.page_idx + 1} / {total_pages}</h4>", unsafe_allow_html=True)
    with col3:
        if st.button("다음 ▶") and st.session_state.page_idx < total_pages - 1:
            st.session_state.page_idx += 1
            st.rerun()

    # 현재 페이지 렌더링
    page = doc.load_page(st.session_state.page_idx)
    pdf_width, pdf_height = page.rect.width, page.rect.height
    
    # 웹 화면에 맞게 스케일링 (가로 800px 기준)
    canvas_width = 800
    scale_factor = canvas_width / pdf_width
    canvas_height = int(pdf_height * scale_factor)
    
    # PDF 페이지를 이미지로 변환 (배경용)
    pix = page.get_pixmap(matrix=fitz.Matrix(scale_factor, scale_factor))
    img_data = pix.tobytes("png")
    bg_image = Image.open(io.BytesIO(img_data))

    # 화면 분할 (좌측: 캔버스, 우측: 추출 데이터)
    main_col, data_col = st.columns([2, 1])

    with main_col:
        st.write("🖌️ **이미지 위를 드래그하여 영역을 선택하세요.** (선택 후 크기 조절 가능)")
        # Drawable Canvas 생성
        canvas_result = st_canvas(
            fill_color="rgba(0, 0, 255, 0.2)",
            stroke_width=2,
            stroke_color="#0000FF",
            background_image=bg_image,
            update_streamlit=True,
            height=canvas_height,
            width=canvas_width,
            drawing_mode="rect",
            key="canvas",
        )

        # 캔버스에 그려진 박스 데이터 처리
        if canvas_result.json_data is not None:
            objects = canvas_result.json_data["objects"]
            # 새로 그려진 박스가 있다면 추출 로직 실행
            if len(objects) > len([a for a in st.session_state.annotations if a['page'] == st.session_state.page_idx]):
                latest_obj = objects[-1]
                
                # 캔버스 좌표 -> PDF 절대 좌표 변환
                x0 = latest_obj["left"] / scale_factor
                y0 = latest_obj["top"] / scale_factor
                x1 = (latest_obj["left"] + latest_obj["width"] * latest_obj["scaleX"]) / scale_factor
                y1 = (latest_obj["top"] + latest_obj["height"] * latest_obj["scaleY"]) / scale_factor
                
                user_rect = fitz.Rect(x0, y0, x1, y1)
                fitted_rect = get_autofit_rect(page, user_rect, autofit_enabled)
                extracted_text = get_sorted_text(page, fitted_rect)
                
                # 세션에 데이터 저장
                st.session_state.annotations.append({
                    "id": len(st.session_state.annotations) + 1,
                    "page": st.session_state.page_idx,
                    "bbox": [round(fitted_rect.x0, 2), round(fitted_rect.y0, 2), round(fitted_rect.x1, 2), round(fitted_rect.y1, 2)],
                    "text": extracted_text
                })
                st.rerun()

    with data_col:
        st.write("📊 **추출 데이터 목록**")
        if not st.session_state.annotations:
            st.info("선택된 영역이 없습니다.")
        else:
            # 추출된 데이터를 보기 좋게 표시
            for idx, anno in enumerate(reversed(st.session_state.annotations)):
                with st.expander(f"데이터 #{anno['id']} (Page {anno['page']+1})", expanded=(idx==0)):
                    st.caption(f"BBox: {anno['bbox']}")
                    # 텍스트 에디터 연동 (수정 시 실시간 반영)
                    edited_text = st.text_area("텍스트 수정", value=anno['text'], key=f"text_{anno['id']}", height=100)
                    if edited_text != anno['text']:
                        # 원본 배열 찾아가서 수정 (reversed 배열이므로 id로 매칭)
                        for origin_anno in st.session_state.annotations:
                            if origin_anno['id'] == anno['id']:
                                origin_anno['text'] = edited_text
                    
            # JSON 내보내기 버튼
            st.markdown("---")
            export_json = json.dumps(st.session_state.annotations, ensure_ascii=False, indent=2)
            st.download_button(
                label="💾 전체 데이터 JSON 다운로드",
                data=export_json,
                file_name="extracted_data.json",
                mime="application/json",
                use_container_width=True
            )
else:
    st.info("👈 좌측 사이드바에서 PDF 파일을 업로드해주세요.")
