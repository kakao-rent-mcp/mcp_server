# slug-mcp

한국 주택 청약(분양·임대) 공고를 조회하고, 사용자의 소득·자산·가족구성·청약통장 정보를
바탕으로 자격에 맞는 공고를 추천하는 [FastMCP](https://github.com/jlowin/fastmcp) 서버입니다.

이 서버는 자연어를 직접 파싱하지 않습니다. 자연어 이해는 MCP 클라이언트(사용자 쪽 AI)가
담당하고, 이 서버는 클라이언트가 구조화해서 넘긴 값(연봉, 가구원수, 결혼상태 등)을 받아
공공데이터포털 API 조회 + 자격판정 로직을 수행합니다.

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
| `check_eligibility` | 사용자 프로필로 특정 공급유형 자격 여부 판정 |
| `recommend_housing` | 위 도구들을 조합해 자격·경쟁력 순으로 공고 추천 (핵심 기능) |

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

- **소득기준표가 비어 있습니다.** `src/slug_mcp/config/eligibility_rules.yaml`의
  `median_monthly_income_by_household_size`가 빈 값입니다. 통계청 KOSIS 또는
  청약홈이 매년 발표하는 확정표로 채워야 소득 조건이 정상 판정됩니다. 채우기 전까지
  `check_eligibility`는 소득 조건을 `needs_manual_review=True`로 표시하고 넘어갑니다.
- **자산 상한·예치금표는 2026-07-01 웹 검색으로 확인한 값입니다.** 실서비스 반영 전
  yaml 파일 상단 출처(국가법령정보센터, 청약홈)로 재확인해주세요. 법령 개정 시 갱신 필요.
- **공공주택 청약통장 1순위 판정이 수도권/비수도권 2단계만 구분합니다.** 투기과열지구 등
  세분화하려면 지역코드 매핑이 더 필요합니다.
- **LH `lhLeaseNoticeBfhDtllInfo1`(분양임대공고별 상세정보, 사전청약) 오퍼레이션은
  아직 서버에서 HTTP 500을 반환하는 상태**라 `clients/lh.py`에만 있고 도구로는
  연결하지 않았습니다. LH 쪽 이슈가 풀리면 `tools/`에 상세조회 도구를 추가하면 됩니다.

## 테스트

```bash
uv run pytest -v
```

정부 API는 개인 서비스키가 있어야 하고 요청 제한도 있어서, 테스트는 `tests/fixtures/`에
저장해 둔 실제 응답 녹화본으로 동작합니다. 서비스키 없이도 항상 돌아갑니다.

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

기준값(yaml)이나 판정 로직을 고칠 때는 반드시 `tests/test_eligibility.py`도 같이
갱신해주세요. 새 API 오퍼레이션을 도구로 추가할 때는 `tests/fixtures/`에 실제 응답
샘플을 저장해두면 다른 사람이 검증하기 쉽습니다.
