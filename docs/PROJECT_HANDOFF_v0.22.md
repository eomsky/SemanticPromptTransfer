# SemanticPromptTransfer 프로젝트 인수인계 — v0.22

- 문서 목적: 동일 프로젝트의 다른 대화창에서 작업을 안전하게 이어가기 위한 상세 이력과 현재 상태 기록
- 작성 기준일: 2026-08-31
- 현재 패키지: `semantic-prompt-transfer==0.22.0`
- 현재 기본 표현: `L0 (semantic_linearized)`
- 현재 GitHub 기능 구현 기준 커밋: `846e59e8c64af4e6eb567df892e2010e84960d9e`
- 현재 판정: 제한된 시간대에 실행하는 단일 Colab POC 기준 구현 및 패키지 검증 완료

> 이 문서는 요구사항 문서나 README를 대체하지 않는다. 대화에서 결정된 배경,
> 버전 간 변화, 실제 적재 상태, 아직 완료되지 않은 항목을 한곳에 모은 작업
> 인수인계 문서다. 다음 대화에서는 이 파일과 GitHub `main`, Google Drive의
> `versions/v0.22` 및 `runtime-assets/v0.22`를 먼저 확인해야 한다.

## 1. 가장 먼저 알아야 할 현재 상태

현재 구현은 다음 범위까지 연결되어 있다.

1. 사용자가 HTML에서 부서명·이름·사번으로 임시 회원가입한다.
2. POC에서는 사번을 아이디와 초기 비밀번호로 사용한다.
3. 사용자는 신용조사서 XLSX 1개와 기타 첨부자료 여러 개를 업로드한다.
4. 신용조사서는 정형 사실로 읽어 최우선 근거로 사용한다.
5. 첨부자료는 L0 청크와 CPU E5 임베딩으로 한 심사건의 논리 Vector DB에 적재한다.
6. 파일명과 처리단계가 화면에 직접 표시되며, `×`로 파일·파생자료·벡터를 함께 삭제한다.
7. 다섯 개의 고정 심사항목별 근거를 조립하고 심사의견 초안을 생성한다.
8. 완료 시 다섯 항목을 포함한 DOCX를 다운로드한다.
9. 단일 코드 셀 Colab 노트북이 Drive의 승인된 wheel·모델을 검증해 `/content`로 복사하고 HTML/API/ngrok URL을 기동한다.
10. 회원정보, 업로드 원본, 벡터, 파생파일, 심사의견은 Colab 임시 디스크에만 저장되고 런타임 종료 시 제거된다.

다만 이것은 정식 은행 운영환경이 아니다. 실제 양식, 실제 FEW SHOT, 공식 Word
양식, 기업 인증, 악성파일 검사, 영속 저장소, 관리형 Vector DB, 분산 작업 큐,
내부 LLM 품질평가는 아직 연결되지 않았다.

### 현재 완료·미완료 요약

| 영역 | 상태 | 비고 |
|---|---|---|
| L0 기본 RAG | 완료 | v0.18부터 운영 기본값으로 확정 |
| 다중 PDF 논리 단일 DB | 완료 | POC에서는 문서별 NPZ shard를 하나의 검색 DB처럼 사용 |
| tenant/case/document 범위 | 완료 | 검색은 tenant/case 필수, 교체·삭제는 document까지 사용 |
| 신용조사서 우선순위 | 완료 | 실제 양식 대신 빈 예시 XLSX와 예시 매핑 사용 중 |
| 5개 심사항목 | 완료 | 고정 항목 및 결정적 확장 query profile |
| FEW SHOT 구조 | 완료 | 실제 승인 예시 데이터는 아직 미제공 |
| HTML/API 연결 | 완료 | 로그인, 업로드, inline 상태, 삭제, 생성, DOCX 다운로드 |
| Colab 단일 셀 기동 | 완료 | Drive 자산 검증, E5 로드, FastAPI, ngrok 공통 게이트 |
| CPU 임시 생성 | 완료 | 근거형 템플릿 폴백, 품질용 최종 LLM 아님 |
| 별도 LLM Colab 교체 경계 | 완료 | OpenAI 호환 `chat/completions` 어댑터 |
| 실제 사용자 Colab 실기동 | 미수행 | 개발환경의 Drive 모사·실 E5·API 스모크까지 완료 |
| 실제 신용조사서 양식 | 대기 | 사용자가 추후 제공 예정 |
| 실제 FEW SHOT | 대기 | 여신유형·산업분류 등에 따라 제공 예정 |
| 공식 심사의견 Word 양식 | 대기 | 현재는 기준 DOCX 생성기 사용 |
| PyPI 게시 | 미완료 | Trusted Publishing 준비 이력은 있으나 실제 게시 완료 아님 |

## 2. 프로젝트 목적과 고정 업무 요구사항

프로젝트의 목적은 다양한 신용심사 자료를 근거로 다섯 개 심사항목의 의견 초안을
생성하는 것이다. 최종 품질보다 먼저 다음 운영 계약을 안정적으로 만드는 데 초점을
두었다.

- 자료 업로드와 처리상태 확인
- 여러 문서의 범위가 섞이지 않는 RAG
- 신용조사서 우선의 근거 조립
- FEW SHOT과 현재 심사건 사실의 격리
- 수치·기간·단위·출처 검증
- 파일 삭제 시 벡터까지 함께 제거
- 생성 진행률과 Word 다운로드
- LLM·Vector DB·저장소·인증의 향후 교체 가능성

고정 심사항목은 다음과 같다.

| 코드 | 심사항목 |
|---|---|
| A | 재무제표 주요계정(현황 및 향후전망) |
| B | 수익성(현황 및 향후전망) |
| C | 재무안정성 및 자산의 질(현황 및 향후전망) |
| D | 현금흐름 및 채무상환능력(현황 및 향후전망) |
| E | 주요 매출처 및 매출비중 변동 추이 |

표시 제목 자체가 기본 검색어 역할을 하지만, 문자열이 짧으므로 실행 시 자유롭게
LLM이 확장하지 않는다. 버전 관리되는 `query_profile`이 관련 계정, 비율, 기간,
위험요인, 전망 표현을 결정적으로 추가한다.

## 3. 절대 유지해야 할 핵심 결정

다음 결정은 후속 개발에서 명시적 사용자 변경 요청이 없는 한 유지한다.

1. 기본 검색 표현은 L0다.
2. 현재 L0는 완전한 평문이 아니라 표의 `구분=값`, 제목, 단위, 열 경로를 포함한 `semantic_linearized`다.
3. L1은 LLM 가독성을 위한 Markdown 표현이고 L2는 선택된 표의 세부 관계 표현이다.
4. 전체 코퍼스를 L2 JSON 그대로 임베딩하지 않는다.
5. 신용조사서 근거 우선순위는 `TIER_1 > TIER_2 > TIER_3`이다.
6. FEW SHOT은 스타일·구조 자산이며 현재 심사건의 사실 근거가 아니다.
7. 검색·파일·벡터는 최소 `tenant_id`, `case_id`, `document_id` 범위를 가진다.
8. 검색에는 tenant와 case가 필수다.
9. 문서 교체·삭제에는 document 범위까지 포함한다.
10. 삭제 성공은 대상 벡터 0건과 서버 원본 부재를 모두 확인해야 한다.
11. LLM은 `TextGenerator.generate(messages) -> str` 계약 뒤에서 교체한다.
12. 실제 신용조사서 양식 적용 시 XLSX와 셀 매핑 JSON을 같은 버전에서 함께 바꾼다.
13. 전체 STX 원문, 전체 MASTER, 실제 고객자료, 운영 인덱스, 인증정보, 모델은 공개 GitHub·wheel에 포함하지 않는다.
14. 개발 버전마다 반드시 패키지를 만들 필요는 없지만 변경 이력·스키마·의존성·회귀·패키지 준비정보는 누적한다.
15. Google Drive의 기존 버전 폴더는 덮어쓰지 않는다.

## 4. 버전별 작업 이력

### 4.1 v0.17 이전 기반

이 대화의 패키지화 작업은 기존 PDF 분석 결과를 입력으로 시작했다. v0.17은 PDF
구조 분석과 MASTER 생성의 기준선이었다. v0.18 이후에는 Cells 1~3을 프리징하고
기존 MASTER와 저장된 모델을 외부 입력으로 재사용했다.

중요한 경계는 다음과 같다.

- 기존 고품질 PDF→MASTER 파이프라인: v0.17 계열
- RAG 청크·임베딩·검색·프롬프트 패키지: v0.18 이후 Cells 4~7
- v0.22 POC의 임의 PDF 업로드: `pypdf` 기반 경량 텍스트 추출

따라서 v0.22에서 임의 PDF를 업로드할 수 있다는 사실이 v0.17의 계층·표·단위·연속표
전처리 전체가 온라인 업로드 경로에 통합됐다는 의미는 아니다. 스캔 PDF와 복잡표,
정교한 좌표·계층 복원은 잔여 과제다.

### 4.2 v0.18 — 기본 RAG 기준선 확정

v0.18에서는 Cells 1~3을 다시 실행하거나 재학습하지 않고, 기존 v0.17 MASTER와
모델을 사용해 Cells 4~7의 RAG 표현을 비교했다.

비교 표현은 다음과 같다.

- L0: `semantic_linearized` 텍스트
- L1: LLM 전달용 Markdown
- L2: 표의 계층 관계를 상세히 표현한 JSON

검색은 다음 구성으로 수행됐다.

- 인코더: `intfloat/multilingual-e5-small`
- 실행: ONNX Runtime CPU, INT8 AVX512 VNNI
- 검색: dense cosine + BM25 + RRF
- 결과: 쿼리별 Top-5
- 기준 쿼리: 유동성, 특수관계자, 소송, 공급자금융, 현금흐름

핵심 관찰은 L2가 구조를 많이 담지만 길이가 너무 길다는 점이었다. Top-5 검색 결과는
각 쿼리에서 존재했지만 Cell 7의 24,000자 프롬프트 예산을 적용하면 L2는 전체 25개
근거 중 19개만 실제 LLM 프롬프트에 포함됐다. L0와 L1은 각 쿼리에서 Top-5가 모두
전달됐다. 가장 구조적인 표현이 오히려 최종 근거 보존율을 떨어뜨린 사례다.

이 결과로 다음 운영 원칙을 정했다.

- 기본 검색: L0
- 선택된 표를 LLM에 전달: 필요 시 L1
- 선택된 표의 셀 관계가 중요할 때만 압축 L2
- L2 전체 JSON의 전면 임베딩: 비권장

현재 L0 표 청크도 제목, 단위, 논리표 ID, `구분=값`, 회사 또는 열 경로를 이미
포함한다. 이것이 L1·L2 대비 검색 차이가 작았던 직접적인 이유다.

본문 청크 범위도 확인했다. 같은 `scope_heading_id`의 본문을 최대 1,800자까지
결합하고, 초과 시 약 180자를 중첩한다. 계층 변경은 경계지만 표와 페이지 경계는
본문 절단점이 아니다. 표 앞뒤 본문이 같은 계층이면 표를 건너 하나의 본문 청크로
합쳐질 수 있다. 이는 기본 RAG 성립을 막지는 않지만 향후 정교화 항목이다.

최종 판정은 다음과 같았다.

- 기본 RAG·개발 기준선: 충족
- 사람 검토를 전제로 한 여신심사 지원: 사용 가능
- 무검토 자동 여신판단: 미충족
- 전체 STX L0 기준: 1,090개 청크

### 4.3 v0.19 — 최초 운영 RAG 패키지

v0.19에서 Cells 4~7을 설치 가능한 Python 패키지로 분리했다. 운영 기본값은 L0로
고정하고 L1·L2는 명시적으로 선택하는 실험 옵션으로 남겼다.

주요 구성은 다음과 같다.

- `PackageChunkBuilder`
- `E5OnnxEncoder`
- `RAGIndex`
- `RetrievalEngine`
- `PromptPackageBuilder`
- `RAGPipeline`
- `OfflineIndexBuilder`
- `OnlineRAGService`
- CLI: `spt-rag index/retrieve/prompt/inspect`

운영 경로는 오프라인과 온라인으로 분리했다.

```text
오프라인 WRITE/UPSERT
  → 인덱스 영속 저장
  → 온라인 LOAD
  → tenant_id/case_id 필터 검색
```

한 심사건에 여러 문서를 추가하고 문서 단위로 교체할 수 있도록 원자적 UPSERT와
중복 제거를 추가했다. 온라인 서비스는 MASTER 재로딩·재임베딩 없이 저장된 인덱스를
LOAD한다. NPZ exact search는 소규모 기준 백엔드이며 대규모 Vector DB는 교체
대상으로 명시했다.

STX엔진은 전체 원문이나 MASTER를 wheel에 포함하지 않고 5개 주제의 축약 예제,
기본 질의, 기대 결과만 포함했다. 당시 검증 결과는 다음과 같다.

- 소스 단위 테스트 7/7
- 전체 STX L0 1,090개 청크의 ID·임베딩 입력·LLM 전달문이 v0.18과 동일
- 축약 예제 5개 질의에서 의도한 주제 청크 Top-1 5/5
- 요구사항 PDF·DOCX 시각 검수
- wheel·sdist 격리 설치 및 배포 묶음 검증

GitHub 최초 패키지 커밋은 `2cbf25032f78ca8c3cde7509e551e4786453bdeb`이다.
이후 PyPI Trusted Publishing 준비 커밋과 배포물 격리 커밋이 추가됐지만, 이
대화에서 실제 PyPI 게시 완료는 확인되지 않았다. `LicenseRef-Proprietary`도
유지 중이므로 공개 PyPI 게시 전 라이선스 결정을 다시 확인해야 한다.

### 4.4 v0.20 — 신용조사서·FEW SHOT·5개 심사항목 운영 계약

사용자가 실제 운영 화면과 업무 우선순위를 설명한 뒤 패키지를 전면 확장했다.

주요 요구는 다음과 같았다.

- 신용조사서: 규격화된 Excel, 최우수 신뢰도
- 기타 첨부파일: 부수 의견과 보완 근거
- 고정 심사항목 5개
- 항목별 기초자료와 공통자료 분리
- 근거 우선순위 세분화
- 각 항목의 FEW SHOT 제공 예정
- 여신유형·산업분류·상황 태그에 따른 FEW SHOT 선택
- 다중 PDF를 단일 Vector DB에 적재
- 파일 삭제 시 파일과 임베딩 벡터 동시 삭제
- 진행률과 Word 다운로드

이 요구를 다음 계층으로 구현했다.

1. `TIER_1`: 신용조사서 내 해당 심사항목 기초자료
2. `TIER_2`: 신용조사서 내 공통 기초자료
3. `TIER_3`: 기타 첨부파일 검색 근거

FEW SHOT은 별도 스타일 섹션으로 두고 현재 사건의 근거 집합에는 넣지 않았다.
현재 출력의 모든 숫자·회사명·기간이 현재 사건 근거에 있는지 검증하도록 했다.

v0.20 주요 구현은 다음과 같다.

- `CreditReportParser`: 버전형 Excel 매핑과 셀 수준 provenance
- `QueryProfileRegistry`: 다섯 심사항목의 결정적 검색어 확장
- `FewShotRegistry`, `FewShotSelector`: 항목·여신유형·산업·상황 선택
- `EvidenceAssembler`: TIER 1→2→3 순서 보존
- `OpinionValidator`: 근거 없는 수치와 FEW SHOT 누출 차단
- `OperationalRegistry`: 파일·생성작업 상태와 감사 이벤트
- `DocumentLifecycleService`: 원본·파생자료·벡터 삭제 조정
- `global_chunk_id`: 문서 간 로컬 청크 ID 충돌 방지
- `ReviewGenerationOrchestrator`: 5개 항목 생성·검증·진행률
- `OpinionDocumentBuilder`: 고정 순서 Word와 근거 추적표
- `ChromaVectorStore`: 선택형 영속 Vector DB 어댑터

검증은 15/15 단위 테스트, 실제 CPU E5 384차원, 문서 2건 단일 인덱스,
문서 1건 삭제 후 다른 문서 유지, 5개 FEW SHOT 선택, Word 생성, 전체 STX
L0 1,090개 회귀 동일성까지 통과했다. Chroma 서버 연결·부하 시험은 수행하지
않았고 NPZ와 인메모리 백엔드 계약을 검증했다.

GitHub 기준 커밋은 `b0e5f7715a090f84c40c574bcefe34880bab486d`다.

### 4.5 v0.21 — 최소 HTML 및 삭제 원자성

초기 HTML 스케치는 신용조사서·기타 첨부자료 업로드, 업로드 자료현황 팝업,
심사의견 다운로드와 각 진행률 막대로 구성됐다. 사용자의 수정 요청에 따라 최종
방향은 다음처럼 바뀌었다.

- 업로드 진행률 막대 제거
- 우측에 업로드 파일명과 처리단계 직접 표시
- 각 파일명 옆에 `×` 삭제 버튼
- 업로드 자료현황 조회 버튼과 팝업 제거
- 심사의견 생성·다운로드 진행률만 유지
- 신용조사서 행에 `양식 다운로드` 버튼 신설

v0.21은 이 화면과 운영 코드를 연결하기 위한 애플리케이션 경계를 추가했다.

- 파일별 상태와 진행률
- `OperationalApplicationService`
- 선택형 FastAPI 업로드·목록·삭제·작업·다운로드 경로
- 원본·파생자료를 보관하는 안전한 로컬 artifact store
- 벡터 삭제 후 count 0 검증
- 서버 원본 삭제 후 부재 검증
- 실패 시 파일을 성공으로 숨기지 않는 `FAILED` 상태
- 교체 가능한 CPU 생성기 계약
- 승인된 standalone HTML 패키지 포함

삭제는 서로 다른 저장소를 하나의 DB 트랜잭션으로 묶을 수 없기 때문에 다음의
검증 가능한 단계로 구현됐다.

```text
DELETING 잠금
  → 대상 벡터 삭제
  → 대상 벡터 count == 0
  → 파생자료 삭제
  → 서버 원본 삭제
  → 원본 부재 확인
  → DELETED
```

중간 실패 시 `FAILED`로 남기고 화면에서 제거하지 않는다. 사용자의 PC에 보관된
업로드 전 원본은 삭제 대상이 아니다.

LLM은 `TransformersCpuGenerator`와 `EvidenceTemplateGenerator` 폴백 구조로
분리했다. 소형 CPU 후보로 Qwen 0.5B를 고려했으나 실제 모델 성능시험은 하지
않았다. v0.21 검증은 20/20 테스트, L0 1,090개 회귀, 실제 E5 다중 문서,
삭제, DOCX, wheel/sdist까지 통과했다. 당시 FastAPI 실제 서버 기동은 의존성
미설치로 소스·계약 수준만 검증했고, 이 부분은 v0.22에서 실요청으로 보완했다.

GitHub 기준 커밋은 `132892b368606775a0be140c7a6f9a14847df29e`다.

### 4.6 v0.22 — 제한 시간형 Colab POC

사용자가 타 부서가 URL로 POC를 테스트하고, 소스·모델은 본인 Google Drive에
두며, 애플리케이션과 향후 LLM을 Colab에서 일정 시간만 기동하는 구성을 선택했다.
S3나 사용자 PC 설치는 이번 POC에서 제외했다.

v0.22의 주요 변화는 다음과 같다.

- 부서명·이름·사번 회원가입
- POC 아이디와 초기 비밀번호를 사번으로 고정
- PBKDF2-HMAC 해시를 임시 SQLite에 저장
- 만료시간이 있고 사용자 case 범위에 묶인 세션 토큰
- 사용자별 결정적 해시형 `case_id`
- 다른 사용자 case 접근 차단
- PDF, DOCX, XLSX, TXT, MD의 경량 업로드 처리
- 문서별 원자적 NPZ shard 기반 `ShardedNpzVectorStore`
- HTML과 API를 같은 FastAPI 포트에서 제공
- OpenAI 호환 원격 LLM 어댑터
- 외부 LLM 부재·실패 시 즉시 CPU 근거 템플릿 폴백
- 신용조사서 양식 다운로드 API와 빈 예시 XLSX 포함
- Colab `/content` 전용 임시 런타임과 종료 purge

원래 애플리케이션은 Google Drive를 사용하지 않는다는 원칙을 세웠다. 이후 단일
노트북 실행 편의를 위해 다음과 같이 경계를 정교화했다.

- Colab **런처만** Google Drive를 마운트한다.
- 런처는 승인 wheel·E5 모델·토크나이저를 해시 검증해 `/content`로 복사한다.
- 애플리케이션의 runtime root는 Drive 경로를 거부한다.
- 회원·업로드·벡터·심사의견은 Drive에 쓰지 않는다.
- 런처 재실행 시 이전 서버·터널·임시 심사자료를 정리한다.
- Colab 종료 시 `/content`와 사용자 자료가 폐기된다.

단일 코드 셀 노트북은 다음을 자동 수행한다.

1. `MyDrive/SemanticPromptTransfer` 마운트
2. 자산 manifest 로드
3. wheel·압축 모델·tokenizer의 크기와 SHA-256 검증
4. `/content/spt_bootstrap_v022`로 복사
5. ONNX 압축 해제와 원본 SHA-256 재검증
6. 패키지 POC 의존성과 `pyngrok==8.1.2` 설치
7. E5·FastAPI·HTML 기동
8. ngrok Basic Auth가 적용된 외부 URL 출력
9. 종료·재실행 정리 함수 등록

필수 Colab Secrets는 다음과 같다.

- `NGROK_AUTHTOKEN`
- `SPT_GATE_PASSWORD`: 8자 이상
- `SPT_GATE_USER`: 선택, 기본값 `spt-poc`

별도 LLM Colab을 연결할 때만 다음 Secrets를 추가한다.

- `SPT_LLM_BASE_URL`
- `SPT_LLM_MODEL`
- `SPT_LLM_API_KEY`

v0.22 정식 릴리스 보고서는 29/29 단위 테스트를 기록했다. 단일 실행 런처와 관련
테스트를 추가한 뒤 최신 소스 기준 전체 테스트는 30/30 통과했다. 실제 CPU E5,
FastAPI health, 패키지 HTML root, ngrok token·Basic Auth 옵션, 종료 purge를 다시
검증했다.

GitHub 릴리스 커밋은 `1ff2db587cc145402fd3bf5702f95ea007197314`, 단일 실행
런처 보완 커밋이 현재 HEAD인 `846e59e8c64af4e6eb567df892e2010e84960d9e`다.

## 5. 현재 운영 아키텍처

### 5.1 구성요소

```text
사용자 브라우저
  → ngrok 공통 Basic Auth
  → Colab FastAPI: HTML + API
      ├─ 임시 사용자·세션 SQLite
      ├─ 신용조사서 정형 사실
      ├─ 첨부파일·파생자료
      ├─ 문서별 NPZ vector shard
      ├─ 5개 심사항목 orchestration
      └─ DOCX 생성

선택: 별도 LLM Colab
  ← HTTPS OpenAI 호환 chat/completions

Google Drive
  → 런처·wheel·E5 모델 공급에만 사용
  → 사용자 업로드와 운영 DB 저장에는 사용하지 않음
```

### 5.2 단일 논리 Vector DB

운영 가정은 여러 PDF를 한 Vector DB에 넣는 것이다. v0.22 POC에서는 이를 다음과
같이 구현했다.

- 논리적 관점: 한 사용자의 한 심사건에 속한 모든 첨부문서를 함께 검색
- 물리적 관점: 문서당 NPZ shard 1개
- 검색 범위: `(tenant_id, case_id)` 필수
- 교체·삭제 범위: `(tenant_id, case_id, document_id)`
- 장점: 한 문서 추가·교체·삭제가 다른 문서 shard를 재작성하지 않음
- 한계: 단일 프로세스·소규모 정확검색 기준이며 대규모 동시성용 DB가 아님

향후 Chroma나 승인된 관리형 Vector DB로 바꾸더라도 이 범위 계약과 문서 삭제
검증은 그대로 유지해야 한다.

### 5.3 근거 조립

심사항목 `i`의 근거는 다음 순서를 유지한다.

```text
E_i = 신용조사서 항목별 사실
    + 신용조사서 공통 사실
    + tenant/case 범위의 첨부자료 검색 결과
```

신용조사서와 첨부자료가 충돌하면 신용조사서를 제1기준으로 유지하고 차이를 별도
표시해야 한다. 서로 다른 근거의 숫자를 평균하거나 임의 합산하지 않는다.

FEW SHOT은 심사항목, 여신유형, 산업분류, 상황 태그로 선택한다. 현재 사건 근거와
별도 블록에 두고, FEW SHOT에만 있는 숫자·회사명·기간이 최종 출력에 유입되면
검증에서 차단한다.

### 5.4 LLM 교체 경계

현재 생성기 계약은 다음 하나다.

```text
TextGenerator.generate(messages) -> str
```

우선순위는 다음과 같다.

1. 설정된 원격 OpenAI 호환 LLM
2. 출력 근거검증
3. 미설정·호출실패·검증실패 시 `EvidenceTemplateGenerator`

CPU 폴백은 현재 사건의 evidence ID와 수치만 사용해 빠르게 초안을 만드는 연결
검증용이다. 문장 품질을 보장하는 최종 LLM이 아니다. 나중에 LLM을 교체할 때 검색,
진행률, 검증, DOCX 코드는 변경하지 않는 것이 설계 목표다.

## 6. 최종 HTML·사용자 흐름

### 6.1 로그인 전

- 회원가입 입력: 부서명, 이름, 사번
- POC 아이디: 사번
- POC 초기 비밀번호: 사번
- 로그인 후 발급된 세션 토큰은 사용자 case에 묶임

사번 비밀번호 규칙은 제한된 POC에서만 허용한 임시 규칙이다. 정식 운영에서는
SSO/RBAC와 계정 정책으로 반드시 교체한다.

### 6.2 로그인 후 메인화면

1. 신용조사서
   - `양식 다운로드`
   - `업로드`
   - 활성 파일 1개
   - 파일명, 처리단계, `×`
2. 기타 첨부자료
   - 복수 파일 업로드
   - 각 파일명, 처리단계, `×`
3. 심사의견
   - 의견생성
   - 0~100% 진행률
   - 완료 후 DOCX 다운로드

업로드 진행률 막대, 업로드 자료현황 버튼, 자료현황 팝업은 최종 화면에서 제거됐다.
처리상태는 각 파일명 옆에 `파일적재/파일검증/파일해석/벡터임베딩/완료/실패`로
표시한다.

## 7. 현재 API 계약

```text
GET    /api/v1/runtime/health
POST   /api/v1/poc/users
POST   /api/v1/poc/login
GET    /api/v1/poc/me
DELETE /api/v1/poc/sessions/current
GET    /api/v1/templates/credit-report.xlsx
POST   /api/v1/cases/{case_id}/credit-report
POST   /api/v1/cases/{case_id}/attachments
GET    /api/v1/cases/{case_id}/documents
DELETE /api/v1/cases/{case_id}/documents/{document_id}
POST   /api/v1/cases/{case_id}/review-jobs
GET    /api/v1/review-jobs/{job_id}
GET    /api/v1/review-jobs/{job_id}/opinion.docx
```

health, 회원가입, 로그인 외 경로는 `X-POC-Token`을 요구한다.

## 8. 주요 소스 파일 지도

| 파일·모듈 | 역할 |
|---|---|
| `chunking.py` | L0/L1/L2 청크 생성 진입점 |
| `_chunk_builder_base.py` | 기존 개발용 청크 빌더 기반 로직 |
| `encoding.py` | E5 ONNX CPU 인코더 |
| `indexing.py`, `retrieval.py`, `pipeline.py` | 인덱스, 검색, RAG 파이프라인 |
| `credit_report.py` | 신용조사서 XLSX 매핑·canonical fact |
| `query_profiles.py` | 고정 5개 항목 확장 검색 프로파일 |
| `fewshot.py` | 승인 FEW SHOT 등록·선택 |
| `prompting.py`, `validation.py` | TIER 프롬프트와 수치·근거 검증 |
| `orchestration.py`, `review.py` | 5개 항목 생성 흐름과 진행률 |
| `review_docx.py` | 심사의견 Word 조립 |
| `registry.py`, `storage.py` | 문서·작업 상태와 원본·파생자료 저장 |
| `vector_store.py` | 인메모리·Chroma 등 Vector DB 계약 |
| `application.py`, `web.py` | 운영 서비스와 선택형 웹 API |
| `llm.py` | CPU·원격 LLM·근거형 폴백 경계 |
| `poc_identity.py`, `poc_session.py` | POC 사용자·세션·범위 격리 |
| `poc_processing.py` | POC 업로드 형식별 경량 추출·인덱싱 |
| `poc_review.py` | POC 심사의견 작업 |
| `poc_bootstrap.py`, `colab_runtime.py` | Colab 임시 런타임 구성·폐기 |
| `poc_server.py` | Uvicorn/FastAPI 실행 진입점 |
| `notebooks/colab_poc_launcher_v022.py` | 단일 셀 Colab 실행 소스 |
| `notebooks/SemanticPromptTransfer_v0.22_COLAB_LAUNCHER.ipynb` | 사용자가 실행할 노트북 |
| `notebooks/colab_runtime_assets_v022.json` | Drive 런타임 자산 manifest 소스 |
| `tools/build_colab_notebook_v022.py` | Python 런처에서 단일 셀 노트북 생성 |
| `tools/run_colab_launcher_local_smoke.py` | Drive 모사·실 E5·API·ngrok stub 스모크 |

## 9. 실행과 검증 결과

### 9.1 v0.22 정식 릴리스 검증

- 소스 컴파일 및 당시 단위 테스트 29/29
- v0.21·v0.22 L0 1,090개 청크의 ID·임베딩 입력·전달문 해시 동일
- CPU multilingual E5 small INT8 ONNX, 384차원
- PDF 2개를 한 논리 검색 범위에서 함께 회수
- 신용조사서 예시 양식에서 canonical fact 3건 추출
- 회원가입·로그인·중복가입 409
- 다른 사용자 case 접근 401
- 인증된 양식 다운로드 200
- 신용조사서 1개와 PDF 2개 업로드 후 READY
- 다섯 항목 생성, 진행률 100, DOCX 다운로드 200
- 문서 1건 삭제 후 대상 벡터 0·원본 부재·다른 PDF 유지
- runtime close 후 `/content/spt_poc_runtime` 부재
- HTML ID·JavaScript·최종 UI 계약 검사 통과

### 9.2 단일 실행 런처 추가 검증

- 최신 전체 단위 테스트 30/30
- wheel 99,359 bytes와 SHA-256 검증
- 압축 E5 모델 83,513,087 bytes와 SHA-256 검증
- tokenizer 17,082,730 bytes와 SHA-256 검증
- 압축 해제 ONNX 118,346,824 bytes와 SHA-256 재검증
- 실제 E5 로드
- FastAPI health와 패키지 HTML root 등록
- ngrok token·Basic Auth·port 8000 옵션 전달
- cleanup 후 runtime root 제거

검증 도구의 ngrok 연결은 실제 외부 터널 생성이 아니라 stub으로 옵션 전달을
검증했다. 사용자의 실제 Google 계정·Colab·ngrok 환경에서 URL을 발급하는 최초
실기동은 아직 수행해야 한다.

## 10. Google Drive 실제 구조와 관리 방법

### 10.1 확인된 루트

- 루트: `MyDrive/SemanticPromptTransfer`
- Drive 폴더 ID: `1O-u50pN0o7bAz5bOK9hctEpwDOtE2NTf`
- 하위 폴더: `input`, `datasets`, `versions`, `runtime-assets`
- 버전 폴더 ID: `1YLarZKCIPIDpc66V02sq4H_HYi5QktrT`

현재 확인된 개념 구조는 다음과 같다.

```text
MyDrive/SemanticPromptTransfer/
├─ input/                      # 비공개 입력 원본
├─ datasets/                   # 평가·예제·정답 데이터
├─ versions/
│  ├─ v0.2 ... v0.17/
│  ├─ v0.20/
│  ├─ v0.21/
│  └─ v0.22/                   # 릴리스 산출물
├─ runtime-assets/
│  └─ v0.22/                   # Colab 기동에 직접 필요한 자산
├─ SemanticPromptTransfer_VERSIONING_RELEASE_POLICY.md
└─ SemanticPromptTransfer_RELEASE_READINESS.json
```

현재 Drive 목록에는 `versions/v0.18`과 `versions/v0.19` 폴더가 보이지 않았고,
파일명 검색에서도 해당 산출물을 찾지 못했다. v0.18은 실험 기준선, v0.19는
GitHub와 당시 작업공간에 패키지 산출물이 존재했으나 Drive 버전 보존은 누락된
것으로 보인다. 다음 대화에서 원본 산출물의 복구 가능성을 확인하되, 없는 파일을
추정해 새로 만들고 과거 원본이라고 표시해서는 안 된다.

### 10.2 `versions/v0.22`

이 폴더에는 다음이 실제 적재되어 있다.

- wheel·sdist
- 소스 ZIP
- 요구사항 MD·PDF·DOCX
- 검증 보고서
- 운영 아키텍처
- 릴리스 관리 문서
- HTML
- 예시 신용조사서 XLSX
- L0 회귀 결과
- FastAPI·Colab POC·HTML 계약 검증 JSON
- SHA-256 manifest

Drive 폴더 ID는 `1akcJPr8uPjZN1WfLfys9caDYCBuDNAfw`다.

### 10.3 `runtime-assets/v0.22`

이 폴더는 사용자가 노트북 하나만 실행할 수 있도록 만든 런타임 공급 경로다.

```text
runtime-assets/v0.22/
├─ SemanticPromptTransfer_v0.22_COLAB_LAUNCHER.ipynb
├─ SemanticPromptTransfer_v0.22_COLAB_ASSETS.json
├─ SemanticPromptTransfer_v0.22_COLAB_ONE_CLICK.md
├─ SemanticPromptTransfer_v0.22_COLAB_LAUNCHER_SMOKE.json
└─ model/onnx/
   ├─ model_qint8_avx512_vnni.onnx.gz
   └─ tokenizer.json
```

폴더 ID는 `1GGA2Iq_FQicvk8nk2Tmvm__aYotEToZ6`다.

중요 자산은 다음과 같다.

| 자산 | 크기 | SHA-256 |
|---|---:|---|
| v0.22 wheel | 99,359 | `5fdf8f8943eaa5e3c6cedec9897332e44944f995a01f7165721fb84dca722dcf` |
| 압축 ONNX | 83,513,087 | `c71dc479552b0324801cfadd53fd3c630955367123bfb58da60824ecd4640936` |
| tokenizer | 17,082,730 | `0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39` |
| 압축 해제 ONNX | 118,346,824 | `dd476dd0c2514e9b9be83aeb3853fac0763e0bdf4a71645407587d77c48a2d88` |

### 10.4 앞으로의 Drive 버전 관리 규칙

기능 변경이 있으면 기본적으로 새 개발 버전을 만든다. 예를 들어 v0.22 이후 의미
있는 변경은 v0.23으로 기록한다.

```text
versions/v0.23/
├─ SemanticPromptTransfer_v0.23_REQUIREMENTS.md
├─ SemanticPromptTransfer_v0.23_VALIDATION.md
├─ SemanticPromptTransfer_v0.23_MANIFEST.json
├─ 변경된 실행·검증 산출물
└─ 패키지화를 결정한 경우 wheel·sdist·source bundle
```

다음 규칙을 지킨다.

1. 이전 버전 폴더의 파일을 덮어쓰지 않는다.
2. 개발 중에는 패키지를 매번 만들지 않아도 된다.
3. 패키지를 만들지 않는 버전도 변경 목적, API·CLI·의존성·설정·스키마 변화, 테스트, 회귀, 알려진 제한을 남긴다.
4. 패키지화를 결정하면 wheel·sdist·요구사항·검증·manifest의 버전을 모두 일치시킨다.
5. GitHub 반영, GitHub Release, PyPI 게시는 각각 별도의 명시적 결정으로 처리한다.
6. `runtime-assets/vX.Y`는 실제 Colab 런처가 필요할 때만 만든다.
7. 런처의 자산 manifest에는 Drive 상대경로, 크기, SHA-256을 기록한다.
8. 실제 고객자료·전체 원문·MASTER·인덱스·비밀키는 공개 GitHub와 패키지에 넣지 않는다.
9. 업로드 후 Drive 파일 목록과 크기를 다시 읽어 적재를 검증한다.
10. 버전 루트의 manifest는 변경 후 다시 생성한다. 기존 manifest에 없는 파일을 추가했다면 manifest도 갱신하거나 별도 post-release artifact임을 명시한다.

현재 루트의 `SemanticPromptTransfer_VERSIONING_RELEASE_POLICY.md`와
`SemanticPromptTransfer_RELEASE_READINESS.json`은 v0.19~v0.20 시점 내용으로
남아 있어 v0.22 최신 상태와 맞지 않는다. 다음 버전 작업 초기에 이 두 루트
문서를 v0.22 기준으로 갱신하되, 과거 버전 폴더의 스냅샷은 변경하지 않는 것이 좋다.

## 11. GitHub와 PyPI 상태

### 11.1 GitHub

- 저장소: `https://github.com/eomsky/SemanticPromptTransfer`
- 브랜치: `main`
- v0.22 기능 구현 기준 커밋: `846e59e8c64af4e6eb567df892e2010e84960d9e`

이 문서와 같은 관리 문서를 추가하면 `main`의 최신 커밋은 위 기능 기준 커밋보다
앞설 수 있다. 다음 대화에서는 브랜치 HEAD를 다시 읽되, v0.22 기능 기준선과
관리 문서 커밋을 구분한다.

주요 커밋은 다음과 같다.

| 버전·의미 | 커밋 |
|---|---|
| v0.19 운영 RAG 패키지 | `2cbf25032f78ca8c3cde7509e551e4786453bdeb` |
| v0.19 PyPI Trusted Publishing 준비 | `a47921293460aeafc668934e23a24fc7c6de3cda` |
| 배포물 격리 | `e62879482ca41f7981818213341abf5a831c6056` |
| v0.20 운영 여신 RAG | `b0e5f7715a090f84c40c574bcefe34880bab486d` |
| v0.21 HTML·CPU 생성 어댑터 | `132892b368606775a0be140c7a6f9a14847df29e` |
| v0.22 Colab POC 릴리스 | `1ff2db587cc145402fd3bf5702f95ea007197314` |
| v0.22 Drive 기반 단일 실행 런처 | `846e59e8c64af4e6eb567df892e2010e84960d9e` |

모델 파일과 실제 입력자료는 GitHub에 올리지 않는다. GitHub는 소스·테스트·축약
예제·문서·배포물만 보관한다.

### 11.2 PyPI

이 대화에서 PyPI 게시 준비를 점검했고 Trusted Publishing 방식을 선호하는 것으로
정리했다. 그러나 실제 PyPI 업로드 완료는 확인되지 않았다. 따라서 다음 대화에서
패키지가 PyPI에 존재한다고 가정하면 안 된다.

현재 라이선스 표현은 `LicenseRef-Proprietary`다. 공개 PyPI 게시 전 다음을 다시
결정해야 한다.

- 독점 라이선스를 유지한 공개 배포인지
- 공개 라이선스로 전환할지
- 실제 프로젝트명 사용 가능 여부
- PyPI Trusted Publisher와 GitHub Actions 설정
- 공개 배포물에서 민감·대용량 파일이 제외됐는지

## 12. Colab 실행 절차

### 12.1 실행 파일

- Drive 파일명: `SemanticPromptTransfer_v0.22_COLAB_LAUNCHER.ipynb`
- Drive 파일 ID: `1S_NOMbwpTlx9iduZ61AhZ__-1ZQi3U00`
- 노트북은 Markdown 안내 셀과 실행 코드 셀 1개로 구성된다.

### 12.2 사용자 실행 순서

1. Drive에서 노트북을 Google Colaboratory로 연다.
2. Colab Secrets에 `NGROK_AUTHTOKEN`과 `SPT_GATE_PASSWORD`를 저장한다.
3. 필요하면 `SPT_GATE_USER`를 저장한다.
4. 별도 LLM Colab이 있으면 LLM 관련 Secrets를 추가한다.
5. 코드 셀 하나를 실행한다.
6. Drive 마운트 권한을 승인한다.
7. 출력된 `심사 화면 열기` 링크를 연다.
8. ngrok 공통 게이트 사용자와 비밀번호로 접속한다.
9. HTML에서 회원가입·로그인 후 자료를 업로드하고 심사의견을 생성한다.
10. 필요한 DOCX를 PC에 다운로드한 뒤 테스트 종료 시 Colab 런타임을 종료한다.

우리 소스와 모델은 Drive에서 가져오지만 Python 제3자 의존성 설치와 ngrok 연결에는
인터넷이 필요하다. 완전 오프라인 실행이 필요하면 의존성 wheelhouse까지 Drive에
보관하는 별도 버전이 필요하다.

## 13. 공개·비공개 자산 경계

### 공개 가능

- 패키지 소스
- 테스트 코드
- 축약·비식별 예제
- HTML 샘플
- 빈 예시 신용조사서
- 요구사항·검증·운영 문서
- wheel·sdist

### 공개 금지 또는 별도 승인 필요

- 전체 STX 사업보고서 원문
- 전체 MASTER
- 실제 고객·심사자료
- 실제 FEW SHOT 원문이 고객정보를 포함하는 경우
- 운영 사용자·사번 데이터
- Vector DB와 업로드 파생자료
- API key, ngrok token, 비밀번호
- 내부 LLM endpoint
- 비공개 학습 모델과 승인되지 않은 평가 데이터

Secrets는 소스, 노트북 출력, HTML, GitHub, manifest에 직접 기록하지 않는다.

## 14. 알려진 제한과 미완료 항목

### 최우선 입력 대기

1. 실제 신용조사서 Excel 양식
2. 다섯 항목별 셀·필드 매핑
3. 공통자료 필드와 각 항목의 사용 규칙
4. 여신유형·산업분류·상황별 승인 FEW SHOT
5. 공식 심사의견 Word 양식

### 기술 보완

1. v0.17 고품질 PDF→MASTER 파이프라인을 온라인 업로드 처리기에 연결
2. 스캔 PDF OCR과 복잡표·연속표 처리
3. 실제 Colab 계정에서 단일 노트북 최초 기동
4. 외부 URL의 Chrome 데스크톱·모바일 육안 검수
5. 실제 LLM Colab 연결과 품질·지연·메모리 평가
6. 실패 재시도와 작업 취소
7. 다중 사용자 동시성·부하 시험
8. 운영 Vector DB, 객체 저장소, 백업·복구
9. 악성파일 검사와 업로드 용량·확장자 정책
10. 공식 인증·권한·감사·보관·삭제 정책

### 문서·버전 관리 보완

1. Drive의 누락된 v0.18·v0.19 보존 여부 확인
2. 루트 `VERSIONING_RELEASE_POLICY`를 v0.22 기준으로 갱신
3. 루트 `RELEASE_READINESS`를 v0.22 기준으로 갱신
4. 단일 실행 런처가 정식 v0.22 manifest 이후 추가됐으므로 post-release 산출물 관계를 명시하거나 manifest를 재생성
5. 다음 기능 변경은 v0.23으로 시작하고 v0.22 파일을 덮어쓰지 않기

## 15. 다음 대화에서 권장하는 시작 순서

다른 대화창에서는 다음 순서로 시작하는 것이 안전하다.

1. 이 `PROJECT_HANDOFF_v0.22.md`를 읽는다.
2. GitHub `main`의 HEAD가 이 문서에 적힌 커밋 이상인지 확인한다.
3. Drive의 `versions/v0.22`와 `runtime-assets/v0.22` 파일 목록을 확인한다.
4. 실제 사용자가 제공한 신규 Excel·FEW SHOT·Word 파일이 있는지 확인한다.
5. 변경 요구가 기능 변경이면 v0.23 작업 폴더와 Drive 폴더를 새로 만든다.
6. L0 1,090개 회귀 기준을 유지해야 하는 변경인지 먼저 판단한다.
7. 실제 양식 변경이면 XLSX와 매핑 JSON을 함께 수정한다.
8. 전체 단위 테스트, 실제 E5 다중 문서, 삭제, API, DOCX를 다시 실행한다.
9. 패키지화는 사용자가 결정한 경우에만 wheel·sdist까지 수행한다.
10. 종료 시 GitHub와 Drive를 각각 재조회해 적재를 검증한다.

### 다음 대화에 붙여넣을 수 있는 시작 요청

```text
Google Drive의 SemanticPromptTransfer/versions/v0.22에 있는
SemanticPromptTransfer_v0.22_PROJECT_HANDOFF.md와 GitHub main을 기준으로
작업을 이어가라. 현재 기준은 v0.22, 기본 검색은 L0이며 기존 v0.22 파일을
덮어쓰지 않는다. 먼저 handoff의 완료·미완료 항목, Drive/GitHub 실제 상태,
신규 제공 파일을 대조한 뒤 필요한 경우 v0.23으로 작업 계획을 제시하라.
신용조사서 우선순위 TIER 1>2>3, FEW SHOT 사실 격리, tenant/case/document
범위, 검증형 파일·벡터 삭제 계약을 유지하라.
```

## 16. 참고 파일

저장소에서 우선 읽을 문서는 다음과 같다.

- `README.md`
- `CHANGELOG.md`
- `docs/REQUIREMENTS_v0.22.md`
- `docs/VALIDATION_v0.22.md`
- `docs/OPERATIONAL_ARCHITECTURE.md`
- `docs/RELEASE_MANAGEMENT.md`
- `docs/COLAB_ONE_CLICK_v0.22.md`
- `docs/COLAB_POC_RUNBOOK.md`
- `notebooks/SemanticPromptTransfer_v0.22_COLAB_LAUNCHER.ipynb`
- `notebooks/colab_runtime_assets_v022.json`

## 17. 인수인계 기준 결론

v0.22는 기본 L0 RAG, 다중 첨부문서, 신용조사서 우선 근거, FEW SHOT 격리,
검증형 삭제, 최소 HTML, 임시 사용자 로그인, 5개 항목 DOCX, 단일 실행 Colab
런처를 연결한 시간 제한형 POC 기준선이다.

다음 개발의 가장 큰 품질 향상 지점은 코드 구조를 다시 만드는 것이 아니라 실제
업무 자산을 연결하는 것이다. 우선순위는 실제 신용조사서와 셀 매핑, 승인 FEW
SHOT, 공식 Word 양식, 실제 LLM 품질평가, 고품질 PDF 전처리 순이다. 그 전까지
현재 CPU 폴백 결과를 실제 심사의견 품질로 해석하면 안 된다.
