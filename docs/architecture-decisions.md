# 아키텍처 결정 기록 (ADR)

append-only 로그입니다. 새 결정은 아래에 이어서 추가하세요. 항목이 5~6개를
넘으면 `docs/adr/00N-*.md`처럼 파일을 분리하는 것도 고려합니다.

각 항목 템플릿: **상태 / 배경 / 결정(또는 현재 입장) / 재검토 트리거 / 관련 문서**

---

## ADR-001
**배포 플랫폼과 CI/CD 경계 (KC Cloud)**

**상태**: 확정 — GH Actions 자동화와 PlayMCP/KC Cloud 실배포는 서로 무관함.

**배경**
- 배포 대상은 카카오클라우드(KC Cloud)이며, Docker 이미지 방식으로 배포한다.
- KC Cloud는 미리 빌드된 이미지를 레지스트리에서 **pull만** 하고, 런타임
  환경변수 주입 기능이 없다 (상세: [../README.md `배포`](../README.md#배포)).
- 저장소에는 `.github/workflows/ci.yml`(PR 게이트: 린트/포맷/타입체크/테스트)과
  `.github/workflows/build.yml`(main 머지 시 테스트 → 이미지 빌드 → GHCR push)
  자동화가 있다. `build.yml` 상단 주석: "런타임 배포(컨테이너 기동)는 KC
  클라우드 쪽 방식이 정해지면 별도로 붙인다." git 이력상 한때 build
  파이프라인에 EC2 자동 배포 job이 있었으나 이후 제거되었다(커밋: "deploy
  job의 DEPLOY_ENABLED 스위치 제거").

**결정 / 현재 입장**
- GH Actions 기반 자동화(`ci.yml`, `build.yml`)는 **PlayMCP/KC Cloud로의 실제
  배포와 무관**하다. "코드 push → 자동으로 실서비스에 반영"되는 구조적 CI/CD는
  존재하지 않고, KC Cloud의 배포 모델(이미지 pull 방식) 특성상 앞으로도 이
  파이프라인만으로는 만들 수 없다.
- PlayMCP 제출/KC Cloud 반영용 이미지 준비와 실제 배포 트리거는 이 GH Actions
  파이프라인과 **별도로, 수동으로** 진행한다.

**⚠️ README.md와 조율 필요**: 현재 [`../README.md` `배포`](../README.md#배포)
섹션은 `main push → build.yml → GHCR push → KC pull` 흐름을 실제 배포
경로처럼 서술하고 있어, 이 ADR의 결론("자동화는 실배포와 무관")과 표현이
엇갈린다. docs/README.md의 "README는 즉시 갱신" 원칙에 따라, 실제 운영
방식이 확정되면 README의 배포 절차 문구를 이 ADR과 일치하도록 재검토·갱신해야
한다. (이번 docs/ 정리 작업 범위에서는 README 본문 서술을 고치지 않고 이
항목으로만 남겨둔다.)

**재검토 트리거**: 실제 배포 절차가 확정되어 README를 갱신할 때 이 ADR도
함께 갱신.

**관련 문서**: [../README.md `CI/CD`](../README.md#cicd), [../README.md `배포`](../README.md#배포)

---

## ADR-002
**도구(Tool) 세분화 전략**

**상태**: 미결정(open) — 실제 Kakao LLM의 tool-orchestration 성능을 관측한
뒤 재검토.

**배경**
- **Plan A (현재 구현)**: 도메인을 여러 개의 잘게 나눈 도구로 제공하고
  (`search_housing_notices`, `get_notice_detail`, `get_competition_stats`,
  `check_eligibility`, `recommend_housing`), 클라이언트 AI가 각 도구의
  description을 읽고 스스로 오케스트레이션해서 사용자에게 답을 준다.
  이상적인 형태지만 AI의 tool-calling 능력에 크게 의존한다.
- **Plan B (대안)**: 도메인마다 하나의 큰 flow-제어형 도구로 묶는다
  (개념적으로 `청약.py` / `임대.py` 한 개씩). MCP 서버가 흐름을 직접
  통제하므로, 사용자는 서버가 정의한 플로우를 따라갈 수밖에 없다. Kakao
  LLM의 tool 선택·오케스트레이션 능력이 Plan A를 감당하기에 부족하다고
  판단되면 이쪽으로 전환한다.
- PlayMCP 가이드 자체도 "툴 3~10개 권장, 과도하면 LLM 툴콜 확률 저하"라고
  명시한다 ([playmcp-guidelines.md](playmcp-guidelines.md)) — Plan A를
  무한정 세분화할 수 없다는 외부 제약이기도 하다.

**결정 / 현재 입장**: 지금은 Plan A 유지(이미 구현됨, 5개 도구는 권장 범위
3~10개 안). 임대 도메인을 새로 만들 때 이 결정을 다시 확인한다.

**재검토 트리거**: PlayMCP 환경에서 실제 Kakao LLM의 tool-call 동작을
관측할 수 있게 되는 시점, 또는 임대 도메인 설계 착수 시점.

**관련 문서**: [product-concept.md](product-concept.md), [playmcp-guidelines.md](playmcp-guidelines.md)
