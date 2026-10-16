"""영화 데이터 도구 4개의 구현. 에이전트(agent.py)와 MCP 서버(movie_mcp/)가 같은 코드를 쓴다.

여기에는 LangChain·MCP 어느 쪽 데코레이터도 없다. 이름·설명·인자는 각 모듈의 상수로 두고,
두 쪽이 그 상수로 도구를 등록한다. 한쪽에서만 설명을 고치면 로컬과 MCP의 모델 입력이 달라져
평가 결과가 갈린다.

- imdb  : imdb_search(벡터 검색), imdb_sql(SQLite SELECT)
- kobis : kobis_search(KOBIS 오픈API 6개 모드)
- web   : web_search(Tavily)

도구 이름은 바꾸지 말 것. "검색 중" 표시(page.tsx), 출처 추출(sources.py), 골든셋 expected_tools가
이름에 묶여 있다.
"""
