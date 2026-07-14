# tool result 정제 전수 조사 (신규 심사 규칙 대응)

- 조사일: 2026-07-14
- 배경: PlayMCP 가이드에 **result 관련 규칙 2개**가 신규 추가됨.
  1. tool call **result 크기는 최소한**으로 구성할 것.
  2. **error인 경우 / widget json이 아닌 경우** → text content에 **정제된 텍스트(마크다운 등)** 를
     권장하며, **API 응답을 그대로 사용하는 것을 지양**.
- 목적: 등록된 11개 도구 각각이 원본 API 응답을 노출하는지 / result가 과대한지 판별해
  리팩터링 범위를 확정한다. (이 문서는 조사만 — 수정은 후속 작업)

## 판정 요약

| # | 도구 | 외부 API | 현재 반환 형태 | 원본 노출 | 크기 과대 | 조치 |
|---|---|---|---|---|---|---|
| 1 | `search_housing_notices` | odcloud | `odcloud.get()` **원본 그대로** | ❌ 심각 | ❌ | **필수** |
| 2 | `get_notice_detail` | odcloud | `{notice, unit_types}` = 원본 `data` 행 배열 | ❌ | ⚠️ | **필수** |
| 3 | `search_lease_notices` | LH | wrapper는 정규화, `data`=원본 `dsList` 행 | ❌ | ⚠️ | **필수** |
| 4 | `get_lease_notice_detail` | LH | `{datasets(원본), attachments(정제)}` | ⚠️ 부분 | ⚠️ | **필수** |
| 5 | `extract_lease_notice_text` | LH+PDF | `{attachments, selected, byte_size, text}` | ✅ | ❌ text 거대 | 검토 |
| 6 | `get_competition_stats` | odcloud | `{competition, winning_scores, special_supply}` = 원본 행 | ❌ | ⚠️ | **필수** |
| 7 | `update_my_profile` | 없음 | 자체 스키마 | ✅ | ✅ | 없음 |
| 8 | `get_my_profile` | 없음 | 자체 스키마 | ✅ | ✅ | 없음 |
| 9 | `analyze_my_subscription` | 없음 | 엔진 자체 스키마 | ✅ | ✅ | 없음 |
| 10 | `analyze_my_rental` | 없음 | 엔진 자체 스키마 | ✅ | ✅ | 없음 |
| 11 | `recommend_housing` | odcloud(내부) | 자체 조립 + 각 추천에 **원본 notice 통째 삽입** | ❌ | ❌ | **필수** |

- 원본 노출 ❌: 5개 (1, 2, 3, 6, 11) + 부분 1개 (4)
- 크기 과대: 1, 2, 3, 4, 5, 6, 11
- 무해(자체 스키마): 7, 8, 9, 10

## 도구별 상세

### 1. search_housing_notices — 가장 심각
- [notices.py:35](../src/slug_mcp/tools/notices.py#L35) `return await odcloud.get(...)`.
- odcloud 원본 wrapper(`page/perPage/totalCount/currentCount/matchCount/data`)와
  각 행의 모든 대문자 원본 컬럼(`HOUSE_MANAGE_NO`, `HSSPLY_ADRES`,
  `HOUSE_DTL_SECD_NM`, `RCEPT_BGNDE/ENDDE`, `SUBSCRPT_AREA_CODE_NM`,
  `TOT_SUPLY_HSHLDCO` … 30+개)를 그대로 전달.
- 조치: 필요한 필드만 뽑아 의미어 키로 매핑 + wrapper 축소.

### 2. get_notice_detail
- [notices.py:51-54](../src/slug_mcp/tools/notices.py#L51-L54) wrapper는 벗겼으나 `notice`/`unit_types`가
  원본 `data` 행 배열(전 컬럼)이다.

### 3. search_lease_notices
- [lh_lease.py:72-81](../src/slug_mcp/tools/lh_lease.py#L72-L81) `_parse_list_response`가 wrapper는
  청약홈 반환형에 맞춰 정규화했지만 `data`=`ds_list` 원본 LH 행(원본 컬럼)이다.

### 4. get_lease_notice_detail
- [lh_lease.py:261-264](../src/slug_mcp/tools/lh_lease.py#L261-L264) `attachments`는 `{type,name,url}`로 잘
  정제됨. 그러나 `datasets`는 원본 데이터셋 블록을 이름별로 묶기만 함(원본 컬럼).

### 5. extract_lease_notice_text
- [lh_lease.py:308-313](../src/slug_mcp/tools/lh_lease.py#L308-L313) 반환은 정제형이나 `text`가
  공고문 PDF 전문이라 매우 큼. **크기 최소화** 규칙 관점에서 검토 필요
  (요약/절단/필요분만 등).

### 6. get_competition_stats
- [competition.py:24-28](../src/slug_mcp/tools/competition.py#L24-L28) wrapper는 벗겼으나 세 배열 모두
  원본 `data` 행(전 컬럼). 참고: `get_competition_rates`/`get_winning_scores`는
  MCP 미등록 헬퍼지만 recommend 내부에서 원본 행을 반환해 흘려보낸다.

### 11. recommend_housing
- [recommend.py:449-450](../src/slug_mcp/tools/recommend.py#L449-L450) 각 추천 항목에 `"notice": notice`로
  **원본 공고 dict를 통째로** 삽입 → 핵심 도구가 원본 컬럼을 그대로 노출.
- 조치: 추천 카드에 필요한 최소 필드만(공고명·지역·주택형·접수일·공급세대 등)
  의미어 키로 재구성.

## 에러 처리 (신규 규칙 2 — error case)
- 외부 API 도구 전부 예외를 그대로 전파한다:
  - odcloud/LH: `raise_for_status()` → httpx 예외 전파 ([odcloud.py:56-66](../src/slug_mcp/clients/odcloud.py#L56-L66))
  - `extract_lease_notice_text`: PyMuPDF 미설치 시 `RuntimeError`, 다운로드 실패 시 httpx 예외
- 현재는 에러가 원본 예외 문자열로 노출됨 → 규칙은 **정제된 텍스트(마크다운) 안내**를 권장.
- 조치: 조회 도구에 공통 에러 정제 래퍼(사용자용 안내 메시지 + 원인 요약) 도입 검토.

## 다음 단계 (제안)
1. (본 조사 완료) 범위 확정.
2. `playmcp-guidelines.md`에 신규 규칙 2개 + 미충족 상태 반영.
3. 반환 정제 리팩터링:
   - 공통: odcloud/LH 원본 행 → 의미어 키 매핑 유틸 1개 도입.
   - 우선순위: 1 → 11 → 2/3/6 → 4 → (5 크기) → 에러 정제.
</content>
</invoke>
