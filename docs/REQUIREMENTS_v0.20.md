# SemanticPromptTransfer v0.20 운영환경 요구사항 기준선

- 기록일: 2026-08-31
- 기준 패키지: v0.20.0
- 상태: 기준 구현·패키지 검증 완료
- 목적: 신용조사서와 기타 첨부파일을 이용한 5개 심사항목 생성 및 Word 산출

## 1. 사용자 화면

HTML 화면은 다음 기능을 제공한다.

1. `신용조사서 업로드`
   - 규격화된 Excel 양식만 허용한다.
   - 한 심사건의 최우선 근거 자료로 사용한다.
2. `기타 첨부파일 업로드`
   - PDF 등 지원 형식의 복수 파일을 허용한다.
   - 신용조사서를 보완하는 부수 근거로 사용한다.
3. `업로드 파일 확인`
   - 파일명, 유형, 크기, 업로드 시각, 처리 상태를 표시한다.
   - 사용자가 파일 삭제를 요청할 수 있다.
4. `의견생성`
   - 입력자료 준비상태를 확인한 뒤 비동기 생성 작업을 시작한다.
   - 진행률과 현재 단계를 표시한다.
5. `심사의견 다운로드`
   - 생성과 검증이 완료되면 활성화한다.
   - 고정된 다섯 항목 순서의 Word 파일을 제공한다.

## 2. 고정 심사항목

| 코드 | 심사항목 |
|---|---|
| A | 재무제표 주요계정(현황 및 향후전망) |
| B | 수익성(현황 및 향후전망) |
| C | 재무안정성 및 자산의 질(현황 및 향후전망) |
| D | 현금흐름 및 채무상환능력(현황 및 향후전망) |
| E | 주요 매출처 및 매출비중 변동 추이 |

각 항목명은 기본 쿼리 역할을 한다. 검색어가 부족하면 항목별로 승인된 `query_profile`을 사용해 관련 계정, 비율, 기간, 위험요인 및 전망 표현을 확장한다. 실행 때마다 자유 생성하지 않고 버전 관리되는 정적 프로파일을 기본으로 한다.

## 3. 근거 우선순위

각 심사항목의 근거는 다음 순서로 구성한다.

1. `TIER_1`: 신용조사서 내 해당 항목 기초자료
2. `TIER_2`: 신용조사서 내 공통 항목
3. `TIER_3`: 기타 첨부파일에서 검색된 근거

이 우선순위는 단순 유사도 점수 가산이 아니라 단계적 근거 구성 규칙으로 적용한다.

- TIER_1은 해당 항목 생성 시 검색 점수와 무관하게 필수 주입한다.
- TIER_2는 항목별 공통자료 매핑에 따라 필수 또는 선택 주입한다.
- TIER_3은 Vector DB의 범위 필터와 항목별 확장 쿼리로 검색한다.
- 기타 첨부파일의 내용이 신용조사서와 충돌하면 신용조사서를 제1기준으로 유지하고 차이를 별도 표시한다.
- 서로 다른 근거의 숫자를 평균하거나 임의로 합치지 않는다.

## 4. 신용조사서 처리

신용조사서는 정형 Excel이므로 일반 문서 RAG만으로 처리하지 않는다.

1. 파일 형식과 양식 버전을 검증한다.
2. 시트·셀·명명범위를 canonical field로 매핑한다.
3. 원본 셀 주소, 수식 여부, 값, 단위, 기간 및 양식 버전을 보존한다.
4. 항목별 자료와 공통자료를 구조화 저장소에 적재한다.
5. 검색과 감사 편의를 위한 L0 텍스트 뷰를 별도로 만들 수 있으나, 원본 정형값을 최우선으로 사용한다.

필요 메타데이터 예시는 다음과 같다.

```text
tenant_id, case_id, document_id, template_version,
review_item_code, field_id, field_name, value, unit, period,
sheet_name, cell_range, formula, source_hash, extracted_at
```

## 5. 기타 첨부파일 처리

1. 파일을 문서 저장소에 보관한다.
2. 각 파일에 전역 `document_id`와 해시를 부여한다.
3. PDF 등 문서 형식은 Cells 1~3을 거쳐 MASTER로 변환한다.
4. L0 청크를 생성하고 Vector DB에 UPSERT한다.
5. 모든 청크에 tenant, case, document, page, content type, source hash를 기록한다.
6. 전역 청크 ID를 사용하여 문서 간 ID 충돌을 방지한다.

## 6. FEW SHOT 관리

각 심사항목 A~E에는 승인된 FEW SHOT 예시가 제공된다.

FEW SHOT은 근거가 아니라 다음을 안내하는 생성 자산이다.

- 문단 구조
- 분석 전개 방식
- 수치와 전망을 서술하는 문체
- 위험요인과 완화요인의 균형
- 기대 분량과 표현 수준

FEW SHOT을 TIER_4 근거로 취급하거나 현재 심사건의 사실로 인용하지 않는다.

권장 스키마:

```text
example_id, review_item_code, example_version, approval_status,
industry_tags, situation_tags, input_summary, output_example,
style_tags, effective_from, effective_to, content_hash
```

선택 규칙:

1. 현재 심사항목과 같은 `review_item_code`만 선택한다.
2. 승인 상태가 `APPROVED`인 예시만 사용한다.
3. 산업·상황 태그가 있으면 가까운 예시를 우선한다.
4. 항목별 2~3개 이내로 제한해 토큰 예산을 관리한다.
5. 실제 기업 자료를 사용한 예시는 익명화한다.
6. 최종 답변의 모든 수치·회사명·기간이 현재 심사건 근거에 존재하는지 검증한다.

프롬프트에서는 FEW SHOT과 현재 근거를 명시적으로 분리한다.

```text
[작성 지침]
[FEW SHOT: 스타일 전용, 사실 인용 금지]
[TIER_1: 신용조사서 항목자료]
[TIER_2: 신용조사서 공통자료]
[TIER_3: 기타 첨부파일 근거]
[현재 심사건 작성 요청]
```

## 7. 의견생성 처리

의견생성은 비동기 작업으로 수행한다.

처리 단계는 다음 순서다.

1. `PRECHECK → CREDIT_REPORT_LOAD`
2. `ATTACHMENT_RETRIEVAL`
3. `ITEM_A~E_GENERATION`
4. `NUMERIC_AND_SOURCE_VALIDATION → VALIDATION_RESULT_AGGREGATION`
5. `DOCX_RENDER → COMPLETE`

다섯 항목은 항목별 근거와 FEW SHOT을 분리하여 생성하고, 마지막에 항목별 검증 결과를 집계한다. 공통 수치·기간·회사 범위의 은행별 교차검증 규칙은 운영 연동 단계에서 추가한다.

진행률 예시:

| 진행률 | 단계 |
|---:|---|
| 0~10% | 입력 및 양식 검증 |
| 10~25% | 신용조사서 정형자료 로드 |
| 25~40% | 기타 첨부파일 검색 준비 |
| 40~70% | 항목별 근거 구성과 초안 생성 |
| 70~90% | 수치·단위·기간·출처 검증 |
| 90~98% | 항목별 검증 결과 집계와 Word 렌더링 |
| 100% | 다운로드 가능 |

## 8. 파일 상태와 삭제

파일 상태 예시:

```text
UPLOADED → VALIDATING → PARSING → INDEXING → READY
                                      └→ FAILED
READY → DELETING → DELETED
```

삭제 시 다음 자산을 동일 `document_id` 기준으로 제거한다.

- 원본 파일
- 추출 MASTER 및 중간 산출물
- 구조화 저장소의 신용조사서 자료
- Vector DB의 모든 청크
- 캐시 및 파생 검색자료

감사 로그와 삭제 완료 상태는 보존한다. 진행 중인 의견생성 작업이 해당 파일을 참조하면 삭제를 차단하거나 작업을 취소한 뒤 삭제한다.

## 9. 저장소 역할

| 저장소 | 역할 |
|---|---|
| 문서 저장소 | 원본 Excel, PDF 및 생성 Word |
| 관계형/구조화 저장소 | 심사건, 파일 상태, 신용조사서 canonical field, 생성 작업 |
| Vector DB | 기타 첨부파일 L0 청크와 선택적 신용조사서 검색 뷰 |
| FEW SHOT 저장소 | 승인·버전 관리된 항목별 작성 예시 |
| 감사 로그 | 업로드, 삭제, 생성, 다운로드, 모델·프롬프트 버전 |

## 10. 패키지 반영 범위

v0.19에서 다음 기반 기능을 유지했다.

- L0 청크 생성
- E5 인코더 계약
- tenant/case/document 메타데이터
- 문서 단위 UPSERT 개념
- Dense + BM25 + RRF 검색
- 근거 기반 프롬프트 패키지

v0.20에서 다음 운영 기준 기능을 추가했다.

- 신용조사서 Excel 양식 파서와 canonical schema
- 항목별 TIER_1·TIER_2 매핑
- 항목별 query profile
- FEW SHOT 저장·선택·버전 관리
- 실제 Vector DB 백엔드 및 문서 삭제
- 전역 청크·근거 ID
- 파일·작업 상태 저장소와 비동기 orchestration
- 프롬프트 내 source tier, document ID, 파일명 표시
- 숫자·단위·기간·FEW SHOT 누출 검증
- 5개 항목 Word 조립과 렌더 검증

## 11. 구현 전 필요한 입력

- 신용조사서 Excel 실제 양식과 양식 버전 규칙
- 다섯 심사항목별 셀·필드 매핑표
- 공통자료의 필드 목록과 항목별 사용 규칙
- 항목별 승인 FEW SHOT 예시와 선택 태그
- 최종 Word 양식 또는 샘플
- 기타 첨부파일 허용 확장자와 용량 제한
- 운영 인증·보관기간·삭제정책

## 12. 수학적 처리 계약

심사항목 $i \in \{A,B,C,D,E\}$의 현재 심사건 근거는 다음처럼 구성한다.

$$
E_i = E_i^{(1)} \oplus E_i^{(2)} \oplus R(q_i, D^{(3)} \mid t,c)
$$

여기서 $E_i^{(1)}$은 신용조사서 항목자료, $E_i^{(2)}$는 공통자료, $D^{(3)}$는 기타 첨부파일이고, $t,c$는 tenant와 case 필터다. $\oplus$는 점수 혼합이 아니라 순서를 보존하는 근거 결합이다.

FEW SHOT $F_i$는 근거 집합 $E_i$에 포함하지 않는다. 선택점수는 다음의 결정론적 구성으로 정의한다.

$$
S(f\mid i,l,k,z)=I_i(f)\{w_l M_l(f,l)+w_k M_k(f,k)+w_z M_z(f,z)\}
$$

- $I_i(f)$: 동일 심사항목이며 승인 상태인 경우에만 1
- $M_l$: 여신유형 정확 일치 또는 일반 fallback
- $M_k$: 산업분류 정확·접두 계층 일치
- $M_z$: 상황 태그 교집합

최종 출력 $y_i$의 수치 토큰 집합 $N(y_i)$은 현재 근거의 수치 집합을 벗어나지 않아야 한다.

$$
N(y_i) \subseteq N(E_i), \qquad N(y_i) \cap \{N(F_i)-N(E_i)\}=\varnothing
$$

전역 청크 ID는 문서별 로컬 ID 충돌을 피하도록 다음 값의 해시로 생성한다.

$$
g=\operatorname{SHA256}(tenant\Vert case\Vert document\Vert local\_chunk\Vert level)
$$

## 13. v0.20 구현 결과

이번 버전에서 다음 기능을 구현했다.

- `CreditReportParser`: 버전형 Excel 매핑과 셀 수준 출처
- `QueryProfileRegistry`: 5개 고정 심사항목 확장 쿼리
- `FewShotRegistry`, `FewShotSelector`: 항목·여신유형·산업분류·상황 태그 선택
- `EvidenceAssembler`: TIER 1·2·3 순서 보존
- `OpinionValidator`: 근거 없는 수치와 FEW SHOT 누출 차단
- `OperationalRegistry`: 파일 및 생성작업 상태·감사 이벤트
- `DocumentLifecycleService`: 원본·파생자료·Vector DB 삭제 조정
- `global_chunk_id`: 다중 문서 전역 식별자
- `ReviewGenerationOrchestrator`: 5개 항목 생성·검증·진행률
- `OpinionDocumentBuilder`: 고정 순서 Word 및 근거 추적표
- `ChromaVectorStore`: 선택적 영속 Vector DB 어댑터

## 14. 검증 기준

- 기존 7개 테스트와 신규 운영 테스트 전부 통과
- 실제 CPU multilingual E5 ONNX INT8로 첨부파일 2건 통합 적재
- 동일 case 통합검색과 문서 1건 선택삭제 확인
- 항목별 FEW SHOT 5개 항목 선택 확인
- 신용조사서 근거 TIER 1·2와 첨부근거 TIER 3 순서 확인
- 5개 심사항목 Word 생성과 0~100% 진행 이벤트 확인
- 전체 STX L0 1,090개 청크의 ID·임베딩입력·전달문이 v0.19와 동일

## 15. 운영 배포 전 잔여사항

현재 v0.20은 운영 계약과 기준 구현이다. 실제 은행 배포 전에는 다음 연결이 필요하다.

- 실제 신용조사서 양식 매핑 승인
- FEW SHOT 원본의 익명화·승인·유효기간 관리
- HTML/API 인증과 권한검증
- Object Storage 및 악성파일 검사
- 분산 작업큐와 실패 재시도
- 실제 Chroma 또는 승인 Vector DB 부하·동시성 검증
- 내부 LLM 어댑터와 생성결과 업무 검증
- 공식 심사의견 Word 템플릿 적용
- 보관기간·삭제·감사정책 확정
