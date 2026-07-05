# docs/ 안내

이 폴더는 카카오 PlayMCP 공모전용 MCP 서버(`slug-mcp`)의 **배경 지식**을 담습니다.
"어떻게 실행·배포하는가"는 다루지 않습니다 — 그건 [../README.md](../README.md) 몫입니다.

## README.md ↔ docs/ 역할 분리

| | 다루는 것 | 갱신 빈도 |
|---|---|---|
| [`../README.md`](../README.md) | 실행법, 환경변수, 도구 목록, 테스트, CI/CD, 배포 절차 — **현재 구현 상태를 그대로 반영하는 살아있는 문서**. 구현이 바뀌면 반드시 즉시 같이 갱신한다 | 구현이 바뀔 때마다 |
| `docs/*` | 왜 이렇게 만들었는지(제품 컨셉), 무엇을 지켜야 하는지(대회 규정), 아직 안 정해졌거나 확정된 아키텍처 결정 | 드묾 (결정이 바뀔 때만) |

실행 명령, env var 표, 도구 표, 배포 절차/시크릿 표는 docs/에 복사하지 않고
README.md로 링크합니다.

## 문서 목록

| 문서 | 내용 |
|---|---|
| [product-concept.md](product-concept.md) | 이 서비스가 누구를 위해 무엇을 하는가, 현재 구현 범위와 간극 |
| [architecture-decisions.md](architecture-decisions.md) | 배포/CI-CD 경계, 도구 세분화 전략 등 결정 기록(ADR) |
| [playmcp-guidelines.md](playmcp-guidelines.md) | 카카오 PlayMCP 심사 규정 원문 + 현재 구현의 충족 여부 체크리스트 |
| [subscription-policy-spec.md](subscription-policy-spec.md) | 청약 자격판정·가점계산 룰 엔진 정책 명세(입력/필터/공공·민영 분기/가점산식/출력) + 조항별 검증상태 |

## 권장 읽는 순서

- 처음 합류할 때: product-concept → architecture-decisions → playmcp-guidelines
- PlayMCP 제출/재심사 직전: playmcp-guidelines부터 다시 확인

## 참고: CLAUDE.md는 아직 없음

"README는 실제 구현에 맞춰 즉시 갱신되어야 한다"는 규칙은 현재 이 문서에만
컨벤션으로 적혀 있습니다. 루트 `CLAUDE.md`로 미러링해 향후 Claude Code 세션이
자동으로 지키게 만드는 방법도 있지만, 이번 정리 범위에서는 만들지 않기로
했습니다. 필요해지면 별도로 요청하세요.
