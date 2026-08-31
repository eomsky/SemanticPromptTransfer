# SemanticPromptTransfer v0.19 검증 보고서

## 판정

**PASS — L0 기본 Cells 4~7 패키지의 재현 가능한 설치, 오프라인 인덱싱, 범위가 격리된 온라인 검색, STX 축약 예제 실행을 확인했다.**

v0.19는 Cell 1~3을 실행하거나 재학습하지 않았다. 기존 v0.17 MASTER와 저장 모델을 읽기 전용 기준으로 사용한다.

## 자동 검증

| 검증 항목 | 결과 |
|---|---:|
| 패키지 단위 테스트 | 7/7 PASS |
| Python 소스 컴파일 | PASS |
| 기본 표현 수준 | L0 |
| MASTER 모드 | LOAD 고정 |
| 인덱스 기본 모드 | MEMORY |
| WRITE/LOAD 왕복 | PASS |
| 문서 단위 UPSERT | PASS |
| 온라인 tenant/case 필수 | PASS |
| CLI tenant/case 필수 | PASS |
| wheel 격리 설치·import | PASS |
| wheel 내부 손상 검사 | PASS |
| wheel/sdist 독점 LICENSE 포함 | PASS |
| PyPI Metadata 2.4·프로젝트 URL | PASS |
| GitHub OIDC 게시 워크플로 | PASS |
| 게시 실행 조건 | GitHub release published |
| wheel 내 기본 질의 | 5개 |
| wheel 내 STX 예제 청크 | 5개 |

## PyPI 게시 준비

패키지명은 `semantic-prompt-transfer`, 버전은 `0.19.0`으로 고정했다. wheel과 sdist에는 `LicenseRef-Proprietary` 메타데이터와 독점 `LICENSE` 원문이 함께 들어간다. 홈페이지·저장소·문서·이슈 URL도 PyPI 메타데이터에 포함했다.

`.github/workflows/publish.yml`은 GitHub Release가 실제로 게시될 때만 실행된다. 워크플로는 Python 3.12에서 표준 빌드, `twine check`, wheel 설치, 7개 단위 테스트를 차례로 통과한 산출물만 PyPI Trusted Publishing(OIDC)으로 전송한다. API 토큰이나 저장소 secret은 사용하지 않는다.

현재 검증 시점에는 PyPI 게시를 실행하지 않았다. 최초 게시 전에 PyPI 계정의 pending publisher에 소유자 `eomsky`, 저장소 `SemanticPromptTransfer`, 워크플로 `publish.yml`, 환경 `pypi`를 연결해야 한다.

## Cell 1~3 프리징

| 기준 산출물 | SHA-256 | 결과 |
|---|---|---:|
| v0.17 MASTER | `7eb16a348b79b7b7cb5a9103a74ece2f3ab35e5a290fef7d900236303541765c` | 불변 |
| v0.17 표 역할 모델 | `76dd6405b6376a69ea6dcc3904271dd692b3752f29fd25806a8dde5b62d6f654` | 불변 |
| E5 INT8 ONNX | `dd476dd0c2514e9b9be83aeb3853fac0763e0bdf4a71645407587d77c48a2d88` | 불변·로드 전용 |

재학습, MASTER 재생성, 대용량 청크 JSON의 기본 생성은 수행하지 않았다.

## 전체 STX L0 회귀

전체 v0.17 MASTER를 동일한 `max_chars=1800`, `text_overlap_chars=180` 조건으로 입력하여 v0.18 `DevelopmentChunkBuilder`와 v0.19 `PackageChunkBuilder`를 비교했다.

| 항목 | 결과 |
|---|---:|
| v0.18 L0 청크 | 1,090 |
| v0.19 L0 청크 | 1,090 |
| 청크 ID 불일치 | 0 |
| 임베딩 입력문 불일치 | 0 |
| LLM 전달 문서 불일치 | 0 |

따라서 패키지화 과정에서 L0 검색 입력의 의미나 경계가 바뀌지 않았다.

## STX 축약 예제 검색

패키지에 포함된 STX 자료는 전체 사업보고서가 아니라 설치와 검색 계약 확인용 사실형 샘플이다. CPU E5 INT8로 5개 예제 청크를 인코딩하고, 기본 여신심사 질의 5개를 `tenant_id=example`, `case_id=stx-2025` 범위에서 각각 Top-5로 검색했다.

| 질의 | 기대 주제 | 실제 Top-1 | 판정 |
|---|---|---|---:|
| Q01 유동성 | 현금·차입금 | `STX_SAMPLE_LIQUIDITY` | PASS |
| Q02 특수관계자 | 채권·채무 | `STX_SAMPLE_RELATED_PARTY` | PASS |
| Q03 우발위험 | 소송 | `STX_SAMPLE_LITIGATION` | PASS |
| Q04 조달구조 | 공급자금융약정 | `STX_SAMPLE_SUPPLIER_FINANCE` | PASS |
| Q05 현금흐름 | 이익·비현금·운전자본 | `STX_SAMPLE_CASH_FLOW` | PASS |

질적 판정은 다음과 같다.

- **관련성:** 5개 모두 기대 주제 청크가 Top-1이다.
- **단위:** Q01·Q02·Q04·Q05의 Top-1에 `천원`이 명시되어 있고, 단위가 필요 없는 Q03은 소송 설명이 선택됐다.
- **범위:** 다른 tenant/case의 청크는 dense·BM25 후보 산정 전에 제외된다.
- **추적성:** 결과에 질의, 확장 질의, 점수, 순위, chunk/variant ID, 문서, 메타데이터가 남는다.
- **해석 한계:** 후보가 5개뿐인 축약 스모크 테스트의 Top-1 5/5를 전체 문서 운영 정확도로 해석하면 안 된다. 전체 성능 기준은 v0.18의 전체 MASTER 검색 결과다.

## 운영 RAG 관점

### v0.19에서 보장하는 부분

- `DocumentScope`를 모든 청크에 결합하고 온라인 검색에서 tenant/case를 필수화한다.
- 오프라인 `WRITE/UPSERT`와 온라인 `LOAD` 수명주기를 분리한다.
- NPZ 저장은 `allow_pickle=False`, 임시 파일 후 원자적 교체, 프로세스 잠금을 사용한다.
- UPSERT는 같은 tenant/case/document 청크를 교체하고 다른 문서는 보존한다.
- E5 모델과 인덱스를 서비스 시작 시 한 번 로드해 반복 질의에 재사용한다.
- L0가 기본이며 L1·L2는 명시적 실험 옵션이다.
- STX 전체 PDF·MASTER·모델·실제 심사건 인덱스를 wheel에 넣지 않는다.

### 운영 배포 전에 필요한 부분

- API 인증·권한과 tenant 위변조 방지
- 저장·전송 암호화, 보존기간과 문서 삭제 API
- 분산 트랜잭션 또는 외부 Vector DB 백엔드
- ANN 검색과 대규모 성능·동시성 시험
- 중앙 로그·지표·알람과 감사 추적
- 답변 LLM의 수치·단위·근거 충실도 평가

현재 NPZ 백엔드는 단일 또는 소규모 심사건의 정확검색 기준 구현이다. 동일한 인덱스 계약을 유지하면서 Chroma 또는 별도 Vector DB 어댑터로 확장한다.

## 문서 품질

| 산출물 | 페이지 | 검증 |
|---|---:|---:|
| 요구사항 PDF | 7 | 전체 페이지 렌더 PASS |
| 요구사항 DOCX | 9 | 전체 페이지 렌더 PASS |

PDF에는 NanumGothic, NanumGothicBold, Latin Modern Math가 임베딩되었다. PDF와 DOCX 모두 한글·영문·수식, 표 경계, 머리말·꼬리말, 페이지 번호의 잘림이나 겹침이 없음을 확인했다.

## 결론

v0.19는 **기본 L0 RAG 패키지**로 사용할 수 있다. STX엔진 자료는 패키지 기능을 설명하는 축약 예제이며, 실제 운영에서는 접근 통제된 외부 MASTER와 모델, 심사건별 인덱스를 사용해야 한다. 대규모 다중 사용자 운영 승인은 Vector DB, 인증·암호화·삭제, 관측성 보완 이후로 제한한다.
