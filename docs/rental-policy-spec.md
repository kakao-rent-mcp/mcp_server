# 임대주택 자격 자동판정 설계 (analyze_my_rental)

작성: 2026-07-13. 상태: 기준표 확보 완료(`config/rental_rules.yaml`), 엔진 미구현.

## 배경

임대 트랙은 "찾기"(search_lease_notices → get_lease_notice_detail → extract_lease_notice_text)와
프로필 질문 유도(RENTAL_CORE_BY_TYPE)까지 갖췄지만, 분양의
analyze_my_subscription에 해당하는 자격 자동판정이 없다. `analyze.py`는
track=rental이면 `rental_not_supported`를 돌려주고 수동 공고문 대조를 안내한다.

## 결정 사항

- **범위**: 영구·국민·행복·공공임대 4유형 전부 (2026-07-13 사용자 확정).
- **도구 표면**: 새 도구 `analyze_my_rental` 추가 (분양 판정과 분리, 사용자 확정).
  `analyze.py`의 rental 분기 안내문은 "analyze_my_rental을 호출하세요"로 교체.
- **모듈**: 기존 `engine.py`(953줄)를 건드리지 않고 `rental_engine.py` 신설.
  기준값은 `config/rental_rules.yaml`로 분리 (매년 고시 갱신 지점).
- **판정 철학**: 일반 고시 기준으로 '잠정판정'을 주고, 최종 확인은 공고문 대조
  (extract_lease_notice_text)로 안내하는 하이브리드. 분양의 provisional/컷오프
  참고기준선 철학과 동일 — 공고별 기준 편차가 실재하므로 단정하지 않는다.

## 판정 흐름 (rental_engine.analyze_rental)

1. **공통 차단필터** — 무주택 세대구성원(분양의 '규제지역 세대주'보다 엄격, 세대원
   전원 무주택), 유형별 총자산/부동산 상한, 자동차가액 상한. 걸리면 사유와 함께
   `ineligible`.
2. **유형 분기** — rental_type별 판정:
   - permanent: 수급자·유공자(소득 70%)·한부모·65세+차상위 → 1순위, 소득 50% 이하 → 2순위. 통장 무관.
   - national: 면적별 소득컷(기본 70%, 50㎡ 미만 우선 50%) → 순위(50㎡ 미만은 거주지,
     이상은 통장 24회/6회) → 동순위 배점표(나이·부양가족·거주연수·자녀·납입횟수).
   - happy: 프로필로 계층 추론(청년 19~39·신혼 7년/6세 자녀·한부모·고령자 65+·주거급여
     수급자) → 계층별 소득(100%, 신혼 맞벌이 120%)·자산 대조. 대학생·산단근로자 등
     추론 불가 계층은 verification_notes로 안내.
   - public: 통장 요건(수도권 12개월/12회, 외 6개월/6회) → 60㎡ 이하 소득 100% → 부동산·차량 자산.
   - **rental_type 미지정이면 4유형 전부 스크리닝**해 신청 가능 유형 목록을 돌려준다
     (유형 미정 사용자에게 유형 추천 역할).
3. **결과 조립** — 분양과 같은 스키마 철학: status / confidence(core만 차면
   provisional) / headline / rank·tiebreak_score 또는 eligible_types /
   action_items / verification_notes.

## 소득 판정 방식 (분양과 다름 — 혼용 금지)

- 분양 소득표는 1·2·3인을 "3인 이하" 통합 행으로 쓰지만, **임대는 1·2·3인 개별 행 +
  1인 +20%p·2인 +10%p 가산**을 쓴다. `rental_rules.yaml`의
  `rental_income_100pct_krw` + `household_income_bonus_pct`가 이 체계다.
- 1·2인 기본값은 마이홈 공표 컬럼(가산 포함 금액)에서 역산했고, "공표값 = 기본값 ×
  (비율+가산)"이 원 단위까지 일치함을 검산했다. 4인 이상은 분양표와 동일.
- 기존 `scoring.income_ratio_pct()`는 분양표를 읽으므로 임대용 환산 함수를 따로 둔다.

## 기준표에 넣은 것 / 뺀 것

**넣음**: 프로필 필드와 대응되는 기준 전부 — 소득표·가산, 유형별 자산 상한(행복주택은
계층별), 영구 1·2순위 규칙, 국민 면적별 소득컷·순위·배점표, 행복 계층별
나이·소득·자산·최대거주기간, 공공 통장 요건·면적별 소득.

**뺌 (사유와 대응)**:
- 영구임대 1순위 중 북한이탈주민·일본군위안부 피해자·아동복지시설 퇴소자 — 프로필
  필드가 없는 희귀 카테고리. 해당자는 공고문 대조 안내.
- 행복주택 대학생·취업준비생·사회초년생·산업단지근로자 계층 — 재학·재직 필드가 없어
  추론 불가. verification_notes로 가능성 안내.
- 우선공급 물량 비율(25%)·분양전환가격 산정식 — 자격판정이 아님.
- 최대 거주기간 — 자격이 아니라 입주 후 조건. 판정 결과에 참고로만 첨부.

## 요검증 항목 (구현 시 실제 공고문으로 대조)

`rental_rules.yaml` 주석에도 표기. extract_lease_notice_text로 2026년 실공고 4종을
뽑아 대조한다 (RENTAL_CORE_BY_TYPE 때 쓴 방법과 동일).

1. 영구임대 2순위 소득 가산 — 마이홈 표기 "50%(1인 90%, 2인 80%)"가 공통 가산
   (+20/+10%p → 70%/60%)과 어긋남. 오독 가능성.
2. 영구임대 자산 상한 — "자산요건 충족"으로만 표기, 국민임대와 동일한지.
3. 행복주택 고령자·주거급여수급자 자산 상한 — 페이지 미표기.
4. 8인 이상 가구 소득 기준 — 고시 미확인.

## 모델 확장 (최소)

- `WelfareStatus.is_housing_benefit_recipient` (주거급여 수급 — 행복주택 계층).
- 국민임대 배점의 '고령부양(65세+ 1년 이상)'은 필드를 추가하지 않고 0점 처리 +
  action_items 안내 (배점 1개 항목 때문에 질문을 늘리지 않는다).

## 테스트 계획

`tests/test_rental_engine.py` — 수급자 영구 1순위 / 국민 소득컷 초과 탈락 /
국민 배점 계산 / 행복 청년 나이 초과 / 자산·차량 차단 / 유형 미정 전체 스크리닝 /
core 부족 needs_more_info / provisional 판정. 기존 test_rental_profile.py의
질문 유도 흐름과 판정의 접점 1케이스.

## 구현 순서

1. rental_engine.py (rules 로더 + 차단필터 + 유형별 판정) + 테스트
2. tools/rental_analyze.py + server.py 등록(_READ_LOCAL) + instructions 갱신
3. analyze.py rental 분기 안내문 교체, models.py 필드 추가
4. 요검증 항목 4건 공고문 대조 후 YAML 확정
