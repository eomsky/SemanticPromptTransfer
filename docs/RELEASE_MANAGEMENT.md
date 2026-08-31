# SemanticPromptTransfer 릴리스 관리 기준

이 문서는 기능 개발 중에는 소스만 갱신하고, 패키지화를 결정한 시점에 빠르게 동일 버전의 wheel, sdist, 문서, 검증 결과를 만들기 위한 기준이다.

## 1. 버전 정보

- 배포 메타데이터: `pyproject.toml`의 `project.version`
- 런타임 단일 기준: `src/semantic_prompt_transfer/version.py`의 `PACKAGE_VERSION`
- 변경 이력: `CHANGELOG.md`
- 버전 요구사항: `docs/REQUIREMENTS_v<버전>.md`

`tools/check_release_contract.py`는 위 버전과 패키지 HTML 리소스의 일치 여부를 검사한다. 새 버전에서는 검사 스크립트의 요구사항 파일 경로도 함께 갱신한다.

## 2. 소스 변경 시 항상 유지할 정보

1. 운영 범위 키: `tenant_id`, `case_id`, `document_id`
2. 기본 표현 수준: L0
3. 신용조사서 우선순위: 항목별 사실 > 공통 사실 > 첨부자료
4. FEW SHOT의 사실 근거 사용 금지
5. 삭제 성공 조건: 문서 벡터 0건 및 서버 원본 부재
6. 생성기 계약: `TextGenerator.generate(messages) -> str`
7. 다운로드 양식 계약: XLSX와 셀 매핑 JSON의 동시 버전 관리
8. POC 저장 계약: `/content` 임시 루트, Google Drive runtime 사용 금지, 종료 시 purge
9. 사용자 계약: 부서명·이름·사번, POC ID/초기 비밀번호=사번, 사용자별 case 격리
10. 외부 어댑터 경계: PDF/Excel 파서, Vector DB, 객체 저장소, 작업 큐, 운영 LLM

## 3. 릴리스 포함 범위

- `src/semantic_prompt_transfer`
- 최소 HTML 인터페이스와 API 계약
- 단위 테스트와 운영 스모크 도구
- STX 축약 예제만 포함한 비식별 예제
- 요구사항·운영 구조·검증·릴리스 관리 문서
- wheel 및 sdist

다음은 포함하지 않는다.

- 전체 STX 원문, 전체 MASTER, 실제 심사자료
- 고객 파일과 운영 인덱스
- 임베딩 모델과 CPU LLM 모델 파일
- 인증정보와 운영환경 설정값

## 4. 패키지화 체크리스트

1. 버전과 변경 이력을 갱신한다.
2. `compileall`과 전체 단위 테스트를 실행한다.
3. 전체 STX L0 회귀 해시를 직전 기준 버전과 비교한다.
4. 실제 E5 운영 스모크로 두 문서 단일 인덱스, 5개 항목 생성, 삭제를 확인한다.
5. wheel/sdist를 빌드하고 격리 설치, CLI, HTML 리소스를 확인한다.
6. 요구사항 DOCX/PDF를 전 페이지 렌더링해 시각 검수한다.
7. 검증 보고서와 SHA-256 매니페스트를 생성한다.
8. GitHub `main`에 fast-forward 커밋하고 Google Drive의 신규 버전 폴더에 같은 산출물을 올린다.
9. GitHub 커밋과 Drive 파일 목록을 다시 읽어 적재 결과를 확인한다.

검증을 마친 뒤 `tools/build_release_bundle.py --output-dir <출력경로>
--version <패키지버전>`을 실행하면 wheel·sdist·HTML·XLSX·운영 문서·소스 ZIP과
SHA-256 매니페스트를 같은 버전명으로 모은다. 실제 양식 적용 시에는 XLSX와
`credit_report_template.json`을 먼저 같은 커밋에서 교체한 뒤 이 도구를 실행한다.

## 5. 버전 폴더 규칙

- 작업 소스: `v<세자리>_package`
- 최종 산출물: `v<세자리>_output`
- Google Drive: 기존 SemanticPromptTransfer 버전 루트 아래 `v<major.minor>` 신규 폴더
- 기존 버전 파일은 덮어쓰지 않고 보존한다.

## 6. 릴리스 판정 구분

- 코드 계약 통과: 단위 테스트와 어댑터 검증 통과
- 기준 운영 스모크 통과: 포함된 E5 모델과 축약 자료로 전체 흐름 통과
- 운영 배포 준비: 인증, 악성파일 검사, 객체 저장소, 실제 Vector DB, 작업 큐, 실제 CPU/내부 LLM 성능까지 별도 통과

앞의 두 판정이 통과해도 마지막 항목이 자동으로 충족되는 것은 아니다.
