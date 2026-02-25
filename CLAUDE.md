# MathCrew — Claude Code 설정

## 프로젝트 개요
딸의 수학 교육 + CrewAI 실험용으로 만든 적응형 수학 튜터.
Python(Starlette) 백엔드 + Vanilla JS 프론트엔드, SQLite, Gemini/Ollama LLM.

## 핵심 파일
- `web_tutor.py` — 웹 서버 + API + CrewAI 에이전트 파이프라인 + Problem Bank
- `db.py` — SQLite 스키마 + 게이미피케이션 (XP/레벨/업적)
- `templates/index.html` — 프론트엔드 SPA

## 온라인 서비스 전환 TODO

아래 항목을 순서대로 하나씩 처리할 것. 각 항목 완료 시 [x]로 체크.

### Phase 1: 데이터 안전성
- [ ] **DB → PostgreSQL 전환**: SQLite는 동시 쓰기에 취약. asyncpg 또는 SQLAlchemy async로 교체. `db.py` 전체 리팩터 필요
- [ ] **Connection pooling**: 매 쿼리마다 connect/close 대신 풀 사용
- [ ] **마이그레이션 도구 도입**: try/except ALTER TABLE → Alembic

### Phase 2: 인증/보안
- [ ] **비밀번호 해싱**: PIN 평문 비교(`db.py:129`) → bcrypt
- [ ] **세션 보안 강화**: 쿠키에 `secure`, `httponly`, `samesite` 플래그 추가
- [ ] **CSRF 토큰**: POST 엔드포인트 보호
- [ ] **회원가입/인증 플로우**: 이메일 인증 또는 OAuth 추가

### Phase 3: LLM 비용 관리
- [ ] **유저별 rate limiting**: 분당/일당 요청 제한
- [ ] **API 키 관리**: 유료 플랜 또는 사용량 기반 과금 구조 설계
- [ ] **LLM 호출 모니터링**: 사용량 로깅 및 알림

### Phase 4: 서버 아키텍처
- [ ] **Task queue 도입**: CrewAI 파이프라인을 Celery/RQ로 분리 (현재 threading.Thread)
- [ ] **인메모리 상태 → Redis**: `sse_queues`, `current_problems`, `scaffold_states` 이동
- [ ] **Docker 컨테이너화**: Dockerfile + docker-compose
- [ ] **리버스 프록시 + HTTPS**: Nginx 또는 Caddy 설정
- [ ] **Gunicorn + Uvicorn workers**: 멀티프로세스 구성

### Phase 5: 프론트엔드/배포
- [ ] **에러 핸들링 개선**: 사용자 친화적 에러 메시지
- [ ] **CDN 배포**: 정적 파일 분리
- [ ] **모니터링**: 헬스체크 엔드포인트 + 로깅 (Sentry 등)

### Phase 6: 미성년자 데이터 보호 및 컴플라이언스 (미국 서비스 기준)
- [ ] **COPPA 준수**: 13세 미만 아동 — 부모/보호자 동의 플로우 구현 (2026.04 개정안 완전 시행)
- [ ] **FERPA 준수**: K-12 학교용 — 학생 데이터는 교육 목적으로만 사용, 제3자 공유 제한
- [ ] **완전 로컬 모드**: `LLM_MODE=local` 시 모든 에이전트가 Ollama 사용, 데이터가 서버 밖으로 나가지 않음 (학교/기관용)
- [ ] **클라우드 모드 데이터 잔류**: 클라우드 LLM 사용 시 US 리전만 허용 (Gemini US 리전 / Azure OpenAI US East·West)
- [x] **Privacy Policy 페이지**: 수집 데이터 항목, 저장 위치, LLM 전송 여부 명시
- [ ] **데이터 삭제 기능**: 학생/부모 요청 시 모든 학습 데이터 완전 삭제 API
- [ ] **LLM 서비스 선택 기준**: 미국 내 데이터 잔류 보장, 학습 데이터 미사용 확인된 서비스만 사용. 중국 서비스(GLM 등) 사용 금지
- [ ] **완전 로컬 모드 최소 사양**: Qwen3 14B 기준 (16GB VRAM). Gemini Flash 동급은 Qwen3 32B (24GB VRAM). 8GB GPU는 Qwen3 8B. GPU 없으면 클라우드 권장
- [ ] **로컬 모델 후보** (수학 교육 기준): Qwen3 14B/32B (수학 추론 우수), DeepSeek R1 Distill 14B (수학 특화), Llama 4 Scout 17B (영어 자연스러움), Nemotron 30B (Math 500 91%)
