# SemanticPromptTransfer v0.22 검증 보고서

- 패키지: `semantic-prompt-transfer==0.22.0`
- 기준일: 2026-08-31
- 기본 표현: L0
- 대상: Colab 임시 런타임, 사용자 범위, 실제 PDF 업로드, FastAPI, HTML 계약, DOCX, 삭제

## 1. 결과 요약

| 검증 항목 | 결과 | 확인 내용 |
|---|---|---|
| 소스 컴파일·단위 테스트 | 통과 | 29/29 |
| L0 회귀 | 통과 | v0.21·v0.22 각 1,090개, ID·임베딩 입력·전달문 SHA-256 동일 |
| 실제 CPU E5 | 통과 | INT8 ONNX, 384차원, PDF 2개를 한 논리 검색 범위에서 모두 회수 |
| 신용조사서 | 통과 | 예시 양식의 sheet/cell 매핑 일치, 채운 검증본에서 사실 3건 추출 |
| 회원가입·로그인 | 통과 | 부서명·이름·사번 등록, 사번 ID/비밀번호 로그인, 중복 409 |
| 사용자 범위 격리 | 통과 | 다른 사용자 token의 case 접근 401 |
| 양식 다운로드 | 통과 | 무인증 401, 인증 200, XLSX ZIP signature 확인 |
| HTTP 업로드 | 통과 | 신용조사서 1개와 PDF 2개 모두 202 후 READY |
| 심사의견 생성 | 통과 | 5개 항목, job 진행률 100, DOCX 200 |
| 문서 X 삭제 | 통과 | 대상 원본·파생파일 삭제, 대상 벡터 0, 다른 PDF 유지 |
| 런타임 폐기 | 통과 | close 후 runtime root 부재, Google Drive 미사용 |
| HTML 구조·JS | 통과 | ID 중복 없음, JavaScript syntax 정상, popup/업로드 bar 없음, inline X·양식 버튼 있음 |

## 2. 실제 E5·다중 PDF 스모크

CPU `multilingual-e5-small` INT8 ONNX로 서로 다른 PDF 2개를 처리했다. 문서당
NPZ shard 1개, 전체 벡터 2개가 생성되었고 결합 질의 Top 결과에 두 document가
모두 포함됐다. 신용조사서 사실 3건과 함께 5개 심사항목 DOCX를 생성했다.

첫 PDF를 삭제한 뒤 그 document의 벡터 count는 0, 서버 원본은 부재,
파생파일은 삭제되었다. 두 번째 PDF의 shard와 검색 결과는 유지되었다. 마지막에
runtime close를 수행했고 임시 root 전체가 사라졌다.

## 3. FastAPI 실요청 스모크

선택 의존성을 설치한 격리 경로에서 FastAPI TestClient로 실제 multipart 요청을
수행했다. 최신 FastAPI/Pydantic에서 함수 내부 `UploadFile` 지연 주석을 해석하지
못하는 호환성 문제를 발견해 웹 모듈의 주석 평가를 교정했다. 교정 후 다음 전
구간이 통과했다.

`health → signup → login → template download → credit XLSX upload → PDF ×2 upload
→ list READY → review 100 → DOCX download → document delete → cross-user deny`

## 4. HTML 검증 범위

현재 실행 도구에서 브라우저 렌더링 surface를 사용할 수 없어 픽셀 기반 화면
검수는 수행하지 못했다. 대신 표준 HTML parser와 Node JavaScript syntax 검사를
실행했다. 요소 ID 26개는 모두 유일했고 다음 계약을 확인했다.

- 로그인·회원가입과 부서명·이름·사번 필드
- 신용조사서 `업로드`·`양식 다운로드`
- 신용조사서와 첨부자료의 inline 파일명·단계·`×`
- 업로드 자료현황 문구와 dialog 부재
- 업로드용 progress bar 부재
- 심사의견 progress bar 유지
- 700px 이하 단일 열 규칙

외부 POC URL을 발급하는 단계에서 Chrome 데스크톱·모바일 육안 검수는 다시
수행해야 한다.

## 5. LLM 검증 범위

원격 OpenAI 호환 어댑터는 로컬 HTTP stub으로 URL, model, message, 응답 파싱을
확인했다. 별도 LLM Colab은 아직 제공되지 않았으므로 실제 모델 품질은 평가하지
않았다. 실제 POC 스모크에서는 즉시 실행형 `EvidenceTemplateGenerator`가 현재
근거만 사용해 5개 항목을 만들고 수치·evidence 검증을 통과했다.

## 6. 판정과 제한

v0.22는 특정 시간대의 단일 Colab POC에서 회원가입, 다중 PDF RAG, 정형
신용조사서 우선, FEW SHOT 선택, 심사의견 Word, 검증형 파일 삭제를 연결하는
기준 구현으로 사용할 수 있다.

다음은 운영 합격으로 보지 않는다: 사번 비밀번호, 임시 SQLite, 로컬 NPZ 정확검색,
단일 프로세스 background task, 기본 PDF 텍스트 추출, 예시 Excel/Word 양식,
근거형 CPU 폴백. 운영 전 요구사항 문서의 잔여 과제를 별도 완료해야 한다.

## 7. 최종 배포 산출물

최종 소스에서 wheel과 sdist를 생성했다. wheel을 외부 인덱스 조회와 의존성
설치 없이 별도 디렉터리에 설치한 뒤 `0.22.0` import를 확인했다. 패키지 리소스로
HTML 화면과 빈 신용조사서 XLSX 양식이 모두 존재하고 각각 정상적으로 읽혔다.
wheel·sdist 내부에는 `poc_server.py`, HTML, XLSX, v0.22 요구사항·검증 문서가
포함되며 전체 STX 원문, MASTER, 임베딩 모델, 실제 업로드 자료는 포함하지 않는다.
