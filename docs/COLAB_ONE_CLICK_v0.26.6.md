# SemanticPromptTransfer v0.26.6 Colab 운영

- v0.26.6 launcher를 열고 위에서부터 실행한다.
- 기본 검증 모드는 OFF이며 생성 LLM 결과를 semantic validator가 덮어쓰지 않는다.
- 자유대화는 검증 LLM과 분리되어 있으며 현재 심사건 질문에만 query-time RAG를 수행한다.
- Word 파일은 심사건 ID만 상단에 표시하고 본문 [근거 N] 뒤에 문맥형 캡처 부록을 붙인다.
- Drive의 demo-assets는 기존과 같이 최초 표시만 하고 생성 버튼을 누를 때 파싱/임베딩한다.
