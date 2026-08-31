# SemanticPromptTransfer v0.24 검증 기록

검증일: 2026-08-31

## 결과

- 자동화 테스트: 33/33 통과
- Python 소스 compileall: 통과
- 운영 HTML JavaScript `node --check`: 통과
- v0.24 노트북 5개 코드 셀 compile: 통과
- 단일 LLM 확인: v0.24 노트북에 `TwoPassReviewGenerator` 없음
- 생성 한도 확인: 항목당 `max_new_tokens=1200`
- 순차 생성 확인: A→B→C→D→E, section 완료 이벤트 5건
- 실시간 이벤트 확인: stage, file_progress, token, section_complete, complete
- 원문 캡처 확인: Excel 셀 범위 PNG 및 PDF bbox PNG 생성
- 추가 산출물 UI/API 옵션 제거 확인

## 실제 첨부 신용조사서 검증

`양식파일_ABC기업_v1.0.xlsx`를 외부 검증 입력으로 사용했다. 파일 자체는
저장소나 wheel에 포함하지 않았다.

- 7개 운영 시트에서 871개 근거 행 추출
- `7. 종합의견`의 가~마 구간을 A~E 심사항목에 개별 라우팅
- 신용조사서 근거가 첨부자료보다 먼저 배치되는지 확인
- 5개 심사항목 생성과 DOCX 렌더링 완료
- 항목별 근거 ID 3건 연결 확인
- 클릭 대상 Excel 원문 캡처 예: `7. 종합의견 · A4:J4`

## 패키지

- 파일: `semantic_prompt_transfer-0.24.0-py3-none-any.whl`
- 크기: 166,503 bytes
- SHA-256: `6a8dc3a02b81848a0ad2b1faf0672537fad0db9abb4b9b662f10a3672430d289`
- wheel 내부 버전, 운영 HTML, 빈 신용조사서 양식, 근거 캡처 모듈 확인

## Google Drive

- `versions/v0.24`: `1FO5Rq3xzt_odobAQK4p4rFAOi_7q4t5x`
- `runtime-assets/v0.24`: `1IjWXNIGqPF9TYeuKJZqrq46V6gfqQiS2`
- wheel Drive ID: `1T-K2IMU2NQ7t7HVpdZClFBvUA4JO6_rE`
- 매니페스트 Drive ID: `1E7crjxHHFEkqMtXoVboJkD99DJ11zX2X`
- 운영 노트북 Drive ID: `1KRvFuied-KubFFg5MuPEP4oKr3IUSOK2`

Drive 재조회 결과 wheel 166,503 bytes, 매니페스트 1,291 bytes, 노트북
22,476 bytes가 각각 새 v0.24 폴더에 존재함을 확인했다.
