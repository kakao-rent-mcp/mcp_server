# slug-mcp

한국 주택 청약 공고를 조회하고, 사용자의 소득·자산·가족구성·청약통장 정보를 바탕으로
자격·가점을 판정해 당첨 가능성이 높은 공고를 추천하는
[FastMCP](https://github.com/jlowin/fastmcp) 서버입니다.

이 서버는 자연어를 직접 파싱하지 않습니다. 자연어 이해는 MCP 클라이언트(사용자 쪽 AI)가
담당하고, 이 서버는 클라이언트가 구조화해서 넘긴 값(나이, 소득, 무주택기간 등)을 받아
세션에 누적 저장하고, 룰 엔진 판정 + 공공데이터포털 API 조회를 수행합니다.
금액 단위는 전부 원(KRW)이며 필드명에 `_krw`가 붙습니다.

카카오 PlayMCP 마켓 심사 규격을 전제로 만들었습니다 (아래 [PlayMCP 심사 규격](#playmcp-심사-규격) 참고).

프로젝트 배경(제품 컨셉, 대회 규정, 아키텍처 결정 기록)은 [docs/](docs/README.md)에 정리되어 있습니다.

## 빠른 시작

```bash
uv sync
cp .env.example .env   # 발급받은 서비스키를 .env에 채워 넣기
uv run slug-mcp
```

기본 실행 방식은 `stdio`라서 Claude Desktop 같은 로컬 MCP 클라이언트에 바로 붙일 수
있습니다. 네트워크 너머에서 접속해야 하면 `MCP_TRANSPORT=streamable-http`로 바꿔서
실행하세요 (아래 [배포](#배포) 참고).

## 환경변수

| 변수 | 설명 |
|---|---|
| `DECODING_KEY` | 공공데이터포털 디코딩 서비스키. odcloud(부동산원 청약홈) API에 사용 |
| `ENCODING_KEY` | 공공데이터포털 인코딩 서비스키. LH API에 사용 |
| `MCP_TRANSPORT` | `stdio`(기본) \| `http` \| `sse` \| `streamable-http` |
| `MCP_HOST` | http 계열 transport일 때 바인딩 호스트 (기본 `0.0.0.0`) |
| `MCP_PORT` | http 계열 transport일 때 포트 (기본 `8000`) |

## 제공하는 도구

| 도구 | 하는 일 |
|---|---|
| `search_housing_notices` | 지역·유형으로 진행 중인 분양 공고 목록 검색 |
| `get_notice_detail` | 공고 하나의 상세정보 + 주택형별 분양가·면적 |
| `get_competition_stats` | 공고의 과거 경쟁률·당첨가점·특별공급 신청현황 |
| `update_my_profile` | 대화에서 파악한 사용자 정보를 세션에 누적 저장 (부분 업데이트) |
| `get_my_profile` | 저장된 프로필과 완성도(부족 항목·다음 질문) 조회 |
| `analyze_my_subscription` | 룰 엔진 종합 판정: 자격 필터 → 가점·배점 → 컷오프 대조 → 트랙 추천 |
| `recommend_housing` | 프로필 분석 + 실시간 공고·경쟁률을 결합해 실현가능성 순 추천 (핵심 기능) |

### 사용 흐름 (대화 몇 번이면 끝)

```
사용자: "34살, 서울 살고 결혼했어요. 마포 쪽 청약 노려요"
  → update_my_profile(...)           # 부분 저장, 응답의 next_questions로 부족한 정보 안내
사용자: "무주택 6년차, 월소득 750, 통장 6년에 1,800만원요"
  → update_my_profile(session_id, ...)  # ready_for_analysis=true
  → analyze_my_subscription(session_id) # 민영 가점 45/84, 신생아·신혼·다자녀 특공 매칭 등
  → recommend_housing(session_id)       # 진행 중 공고를 실현가능성 순으로 추천
```

프로필은 **인메모리 문서 스토어**(세션ID 키, TTL 24시간, LRU 상한)에 보관합니다.
배포 환경(KC 클라우드)에 외부 DB를 붙일 수 없어 프로세스 메모리를 쓰며, 서버 재시작
시 사라집니다. MCP transport 자체는 stateless(PlayMCP 권장)를 유지하고 애플리케이션
수준의 상태만 세션ID로 이어 붙입니다 — 결정 배경은
[docs/architecture-decisions.md ADR-003](docs/architecture-decisions.md#adr-003) 참고.

### 룰 엔진

자격판정·가점계산은 [docs/subscription-policy-spec.md](docs/subscription-policy-spec.md)
명세를 구현한 것입니다 (구현: `engine.py`, `scoring.py`, 기준값: `config/eligibility_rules.yaml`).

- **Hard Filter**: 무주택(유주택자는 공공 전체·민영 특공 차단, 민영 일반은 가점 0점),
  공공 60㎡ 이하 자산 컷, 규제지역 세대주 요건(서울 전역+경기 12곳 동적 목록)
- **민영 가점 84점**: 무주택기간 32 + 부양가족 35 + 통장 가입기간 17(배우자 합산)
- **공공 특별공급 배점**: 신생아(우선70/일반20/추첨10 소득트랙), 신혼부부(LH 배점표:
  우선 9점/일반 12점), 다자녀(100점 만점)
- **컷오프 대조**: 목표지역 S/A/B/C 등급별 예상 컷 + `recommend_housing`이 과거
  당첨가점 실측값으로 보정, 미달 시 우회 전략(추첨형 특공·대형 평형·대안 지역) 제시
- 🟡(원문 재확인 필요) 규칙이 판정에 관여하면 `verification_notes`에 경고를 담아
  돌려줍니다. 🔴(미검증) 규칙은 로직에 넣지 않았습니다.

## PlayMCP 심사 규격

카카오 PlayMCP 마켓 등록 조건에 맞춰 다음을 지킵니다 ([server.py](src/slug_mcp/server.py)에 반영).

- **이름에 `kakao` 미사용** — 서버명·도구명 어디에도 대소문자 불문 `kakao`가 없어야 합니다.
- **annotations 5종 필수** — 모든 도구에 `title / readOnlyHint / destructiveHint /
  openWorldHint / idempotentHint`를 명시합니다. 현재 도구는 전부 조회·계산이라
  `readOnly=true, destructive=false, idempotent=true`이고, 외부 공공데이터 API를 부르는
  도구만 `openWorld=true`(`check_eligibility`는 순수 계산이라 `false`).
- **description에 서비스명 병기** — 모든 도구 설명 앞에 `[슬러그(Slug)]`(국문·영문 병기)를
  붙이고 1,024자 이내로 유지합니다.
- **Stateless HTTP** — http 실행 시 `stateless_http=True`(세션 미사용)로 띄웁니다.
- **Streamable HTTP 전용** — 원격 접속은 `MCP_TRANSPORT=streamable-http`만 사용합니다.

> 등록 URL이 생기면 [MCP Inspector](https://github.com/modelcontextprotocol/inspector)로
> 표준 준수 여부를 사전 점검하세요: `npx @modelcontextprotocol/inspector`.

## 알려진 미완성 부분 (같이 다듬어야 할 것)

- **⚠️ 2026-07-06 데이터 정합성 전수조사에서 오류가 다수 확인됐습니다.** 규제지역
  목록(2026-07-01 추가분 경기 3곳 누락), 특공 소득 판정에 임대주택용 소득표 혼입,
  민영 1순위 가입기간 미검사, 다자녀 가입기간 배점의 존재하지 않는 구간 등 —
  전체 목록·근거·영향은 [docs/data-integrity-audit.md](docs/data-integrity-audit.md)
  참고. 해당 감사는 기록만 하며 코드 수정은 후속 작업입니다.
- **기준값 yaml은 사람이 갱신해야 합니다.** 소득표(`urban_worker_monthly_income_krw`)는
  LH청약플러스 2025년도 적용분(2026-07-05 확인), 자산 상한은 2026-02-27 공고분,
  규제지역 목록은 2025-10-15 대책 기준입니다. 매년/고시 개정 시
  `config/eligibility_rules.yaml`과 [정책 명세](docs/subscription-policy-spec.md)의
  검증 태그를 함께 갱신하세요.
- **🔴 미검증이던 규칙 2건은 2026-07-06 웹 검증으로 공식 확인됐습니다.** 미성년자
  통장 가입기간 인정 최대 5년(규칙 제10조⑥, 2024-07-01 시행), 민영·국민주택
  신생아특공 신설(제35조의3, 2026-06-15 시행). 둘 다 아직 미구현이며, 반영 계획은
  [감사 문서](docs/data-integrity-audit.md)의 후속 조치 참고.
- **프로필 저장은 프로세스 메모리입니다.** 서버 재시작·스케일아웃 시 세션이 사라집니다.
  단일 컨테이너 배포 전제이며, 다중 인스턴스가 필요해지면 외부 스토어 검토가 필요합니다.
- **임대 도메인은 미구현입니다.** LH `lhLeaseNoticeBfhDtllInfo1`(분양임대공고별
  상세정보, 사전청약) 오퍼레이션은 아직 서버에서 HTTP 500을 반환하는 상태라
  `clients/lh.py`에만 있고 도구로는 연결하지 않았습니다.

## 테스트

```bash
uv run pytest -v
```

정부 API는 개인 서비스키가 있어야 하고 요청 제한도 있어서, 네트워크가 필요한 테스트는
`tests/fixtures/`에 저장해 둔 실제 응답 녹화본으로 동작합니다. 서비스키 없이도 항상
돌아갑니다. 룰 엔진(스토어·스코어링·파이프라인)은 순수 계산이라 목킹 없이 검증합니다.

| 테스트 | 검증 대상 |
|---|---|
| `test_store.py` | 인메모리 세션 스토어 (deep merge·TTL·LRU) |
| `test_scoring.py` | 민영 84점 가점, 특공 배점표, 소득비율 |
| `test_engine.py` | Hard Filter → 트랙 분기 → 컷오프·강제매칭 파이프라인 |
| `test_profile_tools.py` / `test_analyze_tool.py` | 대화형 프로필 설정·분석 도구 |
| `test_notices.py` / `test_competition.py` / `test_recommend.py` | 공공데이터 API 도구 (fixture 목킹) |
| `test_server.py` | PlayMCP 심사 규격 (도구 수·이름·annotations·description) |

```bash
uv run ruff check .        # 린트
uv run ruff format --check .  # 포맷 확인
uv run mypy src             # 타입체크
```

## CI/CD

- **`.github/workflows/ci.yml`**: PR마다 린트·포맷·타입체크·테스트를 돈다 (PR 게이트).
  실제 API 키는 필요 없다 (fixture 기반).
- **`.github/workflows/build.yml`**: `main`에 머지(push)되면 `test → build-push`를 실행한다.
  - `build-push`: Docker 이미지를 빌드해 `ghcr.io/<이 저장소>`에 `latest`와 커밋 sha
    태그로 올린다. GHCR 로그인은 GitHub 내장 토큰을 쓰므로 별도 등록이 필요 없다.
  - 이때 `DECODING_KEY`/`ENCODING_KEY`(공공데이터 서비스키)를 Secrets에서 꺼내
    `--build-arg`로 이미지에 구워 넣는다 (아래 [배포](#배포) 참고).

## 배포

KC 클라우드는 **미리 빌드된 이미지를 레지스트리에서 pull해 실행**합니다 (런타임 환경변수
주입 기능이 없음). 그래서 서비스키를 런타임에 넣을 수 없어, **CI 빌드 시점에** GitHub
Secrets의 키를 이미지에 구워 넣습니다. 소스에는 키가 없지만 **이미지 레이어에는 평문으로
남으므로, GHCR 패키지는 반드시 `private`으로 유지해야 합니다** (소스 레포 공개 여부와 무관).

동작 흐름:

```
main push → build.yml: --build-arg로 키 주입 → private GHCR 이미지 push
         → KC: 레지스트리 인증(사용자/PAT)으로 그 이미지 pull → 컨테이너 실행
```

빌드에 필요한 GitHub Secrets (Settings → Secrets and variables → Actions):

| 이름 | 값 |
|---|---|
| `DECODING_KEY` | 공공데이터포털 디코딩 서비스키 (odcloud/청약홈용) |
| `ENCODING_KEY` | 공공데이터포털 인코딩 서비스키 (LH용) |

KC 배포 입력값:

| 항목 | 값 |
|---|---|
| Registry 호스트 | `ghcr.io` |
| Registry 사용자 | GitHub 사용자명 |
| Registry 비밀번호 | `read:packages` 권한 PAT |
| image_name | `<owner>/<repo>` (예: `kakao-rent-mcp/mcp_server`) |
| image_tag | `latest` (또는 커밋 sha) |

로컬에서 직접 빌드·실행하려면 (키는 `.env`로 주입):

```bash
docker build -t slug-mcp .   # 로컬 실행은 build-arg 없이 빌드하고
docker run -d --name slug-mcp -p 8000:8000 --env-file .env slug-mcp
```

## 기여

기준값(yaml)이나 판정 로직을 고칠 때는 반드시 `tests/test_scoring.py`·
`tests/test_engine.py`와 [정책 명세](docs/subscription-policy-spec.md)의 검증 태그도
같이 갱신해주세요. 새 API 오퍼레이션을 도구로 추가할 때는 `tests/fixtures/`에 실제 응답
샘플을 저장해두면 다른 사람이 검증하기 쉽습니다.
