# PlayMCP 서버 개발가이드 (팀 정리본)

- 원문 Update Date: 2026.06.12
- 팀 정리본 갱신: 2026.07.14 (원문에 tool result 관련 규칙 2개 추가 반영)
- 이 문서는 원문을 팀 컨텍스트용으로 옮겨 적은 것입니다. 원문이 개정되면 이
  날짜와 아래 체크리스트도 함께 갱신하세요.

> ⚠️ MCP 서버가 아래 조건을 충족하지 않을 경우 심사 단계에서 반려될 수 있습니다.

## 1. PlayMCP 서버 생성 조건

1. MCP 서버는 **최소 지원버전: 2025-03-26, 최대 지원버전: 2025-11-25**을
   만족해야 한다.
   - **Streamable HTTP** 방식만 지원한다.
   - **Remote MCP 서버만** 지원한다 — 공개된 URL로 접근 가능한 도메인이어야
     한다.
   - **Stateless MCP 서버를 권장**한다 (no session).
   - 사용자 인증이 필요한 경우, **OAuth 인증 혹은 커스텀 헤더 방식**을
     지원해야 한다.
2. **MCP Inspector**로 MCP 표준 스펙 준수 여부를 사전 점검해야 한다.
3. MCP 서버 생성 시에는 **활발하게 운영되는 SDK**를 사용하거나 참조해야
   한다.
4. MCP Server Name 또는 Tool Name에 **"kakao"를 prefix 또는 suffix로 사용할
   수 없다**.
   - 대소문자 구분 없이 prefix, suffix, 중간 포함 모두 불가.

## 2. PlayMCP Tool 구성

1. **툴 이름**: 최소 1자 ~ 최대 128자. 영어 대소문자(A-Z,a-z), 숫자(0-9),
   `_`, `-`만 허용. 중복 불가. **대소문자 구분**(`getInfo` ≠ `GetInfo`).
2. **툴 개수**: 서버당 **20개 초과 금지**, **3~10개 권장**. (과도하게
   많으면 LLM의 툴콜 발생 확률이 낮아짐.)
3. **반드시 포함할 property**: `name`, `description`, `inputSchema`,
   `annotations`. `annotations`는 `title`, `readOnlyHint`,
   `destructiveHint`, `openWorldHint`, `idempotentHint` **모두** 값을
   지정해야 한다.
4. **description 작성 유의사항**:
   - 가능한 **영문 작성 권장**.
   - description에 **MCP 서비스 이름**을 포함해야 하며, 국문·영문을
     병기한 고유명사로 표기한다. 예: *"Retrieves a list of the current
     most popular or trending songs from Melon(멜론)"*.
   - **1,024자 이내**로 작성한다 (너무 길면 툴 호출 자체에도, 다른 툴
     호출에도 불리).
5. **권장 규칙**: Kakao Tools에 반영될 때 카카오가 PlayMCP 지정 prefix를
   tool name에 자동으로 붙이므로, tool name 자체에 MCP 서비스명을
   포함시킬 필요는 없다. (원문에는 이 항목 뒤에 PlayMCP 콘솔에서 prefix가
   자동 부여되는 예시 스크린샷이 있었으나, 이미지 데이터는 텍스트 문서로
   옮기지 않았다.)
6. **tool result 구성 (2026.07 추가)**:
   - **result의 크기는 최소한**으로 구성한다.
   - tool call result가 **error인 경우**와 **widget json이 아닌 경우**에는
     text content에 **정제된 텍스트 형식(예: 마크다운)** 을 권장하며,
     **API 응답을 그대로 사용하는 것을 지양**한다. API 응답을 그대로 쓰면
     불필요한 데이터가 많아 답변 품질이 떨어진다.

## 3. 현재 구현 충족 여부 체크리스트

| 규칙 | 상태 | 근거 |
|---|---|---|
| 1-1 지원 버전 범위 | ✅ 충족 | MCP SDK 지원범위 `2025-03-26 ~ 2025-11-25`로 규격 요구와 정확히 일치 (2026.07.14 확인) |
| 1-1 Streamable HTTP만 | ✅ 충족 | `Dockerfile`의 `MCP_TRANSPORT=streamable-http`, `server.py` |
| 1-1 Remote(공개 URL) | ⬜ 배포 후 확인 | [ADR-001](architecture-decisions.md#adr-001) |
| 1-1 Stateless 권장 | ✅ 충족 | `server.py`의 `stateless_http=True`. 프로필 세션은 애플리케이션 수준 상태(인메모리, [ADR-003](architecture-decisions.md#adr-003))로, MCP 프로토콜 세션과 무관 |
| 1-1 사용자 인증(해당 시) | ⬜ 판단 필요 | 소득·자산 등 개인 정보를 인증 없이 파라미터로 받음 — 인증이 "필요한 경우"에 해당하는지 자체가 미검토 |
| 1-2 Inspector 사전 점검 | ⬜ 미실시 | README에 계획만 명시됨 |
| 1-3 활발한 SDK | ✅ 충족 | FastMCP 3.4.2 |
| 1-4 "kakao" 네이밍 금지 | ✅ 충족 | 서버명 `slug-mcp`, 도구명 확인됨 |
| 2-1 툴 이름 규칙 | ✅ 충족 | 11개 도구 모두 조건 만족 — `tests/test_server.py`가 자동 검증 |
| 2-2 툴 개수(3~10 권장) | ⚠️ 권장 초과 | **11개** — 하드 상한(20) 위반은 아니나 권장 범위(3~10) 초과. 임대 도구 추가로 늘어남, [ADR-002](architecture-decisions.md#adr-002) |
| 2-3 annotations 5종 | ✅ 충족 | `server.py`의 `_READ_EXTERNAL` / `_READ_LOCAL` / `_WRITE_LOCAL` — `tests/test_server.py`가 자동 검증 |
| 2-4 서비스명 병기 | ✅ 충족 | `[슬러그(Slug)]` 접두 |
| 2-4 영문 작성 권장 | ⚠️ 부분 충족 | 서비스명은 국·영문 병기지만 description 본문은 한글 |
| 2-4 1,024자 이내 | ✅ 충족 | 본문 길이 상 문제 없음 |
| 2-5 prefix 자동 부여 | ℹ️ 참고사항 | 별도 대응 불필요 |
| 2-6 result 최소화 / API 원문 지양 | ⚠️ 대부분 충족 | 조회 도구 정제 완료(`_projection.py`). 단 특별공급(특공)·LH datasets는 실 샘플 확보 후 정제 예정 — [result-refinement-audit.md](result-refinement-audit.md) |
| 2-6 error 정제 텍스트 | ✅ 충족 | 외부 API 도구 전부 `refine_errors`로 예외를 `{status:"error", message}` 안내로 정제 — `tests/test_errors.py` |

## 4. 확인·재검토 필요 항목 (펀치리스트)

- [x] MCP 프로토콜 버전이 2025-03-26 ~ 2025-11-25 범위 안인지 확인
      (SDK 지원범위가 규격과 일치, 2026.07.14)
- [ ] 배포 후 공개 URL로 실제 접근 가능한지 확인
- [ ] 개인 소득/자산 정보를 다루는 도구에 OAuth/커스텀 헤더 인증이
      필요한지 판단
- [ ] MCP Inspector(`npx @modelcontextprotocol/inspector`)로 사전 점검
      실행
- [ ] tool description 본문을 영문으로 옮길지 여부 결정
- [ ] **툴 개수 11개 → 권장 범위(3~10) 초과** — 통합/축소 여부 검토
      (예: 헬퍼성 도구 정리, [ADR-002](architecture-decisions.md#adr-002))
- [ ] **특별공급(특공) result 정제** — `getAPTSpsplyReqstStus` 실 응답
      샘플 확보 후 34필드 코드조합({지역}_{유형}_CNT) 전용 변환 구현
- [ ] **LH get_lease_notice_detail의 datasets 정제** — 공고유형별 데이터셋
      변형 샘플 확보 후 매핑
- [ ] 원문 가이드 개정 여부를 주기적으로 확인하고 이 문서에 반영
