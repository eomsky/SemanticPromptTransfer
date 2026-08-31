# SemanticPromptTransfer v0.26.5 검증 기록

- 전체 pytest 통과
- Python compileall 통과
- 신규 익명 case 최초 조회 시 ABC기업 신용조사서 1개 + 사업보고서 1개가 UPLOADED / progress 0 상태로 복제됨
- 최초 조회 시 parser/encoder 호출 0회, vector count 0 확인
- 파일명 다운로드가 case-owned 복제본 bytes를 반환
- 샘플 삭제 후 같은 case 재조회에서 재-seed 되지 않음
- Drive demo 원본은 읽기 전용이며 사용자 삭제 대상이 아님
- wheel bytes: 188834
- wheel SHA-256: `55849770da11caeea78bc8b5695c072706c44beea4f58f4b1c8db8b1fdd34425`
