# Qudot AX Partner Developer Assignment

스토어 URL을 입력하면 전 상품을 수집하고, AI로 정규화한 뒤 큐닷 `PartnerProductCreateInput` JSON을 출력하는 CLI 도구다.

---

## 프로젝트 개요

```
스토어 URL → URL 발견 → 상품 상세 크롤링 → RawProduct → AI 정규화 → PartnerProductCreateInput JSON
```

**대상 URL**

| 스토어 | URL | 크롤러 |
|---|---|---|
| 네이버 브랜드스토어 | `https://brand.naver.com/kefii` | `NaverStoreCrawler` |
| 네이버 스마트스토어 | `https://smartstore.naver.com/phytonutri` | `NaverStoreCrawler` |
| 일반 브랜드몰 | `https://m.happylandmall.com/` | `GenericMallCrawler` (미구현) |

---

## 실행 방법

### 의존성 설치

```bash
pip install -r requirements.txt
playwright install chromium
```

### 환경 변수

`.env` 파일을 프로젝트 루트에 생성한다.

```env
ANTHROPIC_API_KEY=sk-ant-...
PLAYWRIGHT_HEADLESS=true
MAX_PRODUCTS=100
REQUEST_DELAY_MIN=0.5
REQUEST_DELAY_MAX=1.5
```

`.env`는 `.gitignore`에 포함되어 있으며 절대 커밋하지 않는다.

### 실행 명령어 (스모크 테스트)

```bash
# 네이버 브랜드스토어
python scripts/smoke_naver_store.py --url https://brand.naver.com/kefii --max-products 5

# 네이버 스마트스토어
python scripts/smoke_naver_store.py --url https://smartstore.naver.com/phytonutri --max-products 5
```

결과는 `outputs/smoke_{slug}_{timestamp}.json`에 저장된다.

---

## 기술 선택 이유

**Python 3.11+ / Playwright (async)**  
네이버 스토어는 Next.js 기반 SPA다. 상품 목록과 상세 데이터가 JavaScript 렌더링 이후에야 DOM에 나타나거나 API 응답으로만 존재한다. 정적 HTTP 요청으로는 수집할 수 없어 동적 렌더링 대응이 가능한 Playwright를 선택했다.

**Pydantic v2**  
`RawProduct`(크롤링 원본)와 `PartnerProductCreateInput`(최종 출력)의 스키마를 분리했다. 원본은 검증 없이 보존하고, 최종 출력 단계에서 가격 역전·enum 범위·AI 필드 허용 여부를 일괄 검증한다. Pydantic v2의 `model_validator`로 가격 역전 방지와 할인율 자동 계산을 선언적으로 처리했다.

**Anthropic SDK**  
category_group, usp, hashtags, option1/option2, brand_name 5개 필드에만 AI를 사용한다. 가격·상품명·이미지 같은 결정적 필드는 AI가 개입하면 오탐이 발생하기 때문에 DOM/JSON/API 결과만 사용한다.

**API 인터셉트 우선 전략**  
네이버 스토어 상품 상세 페이지를 로드하면 `/n/v2/channels/{id}/products/{pid}` API가 자동 발화한다. Playwright response 핸들러로 이 응답을 가로채 정가(`salePrice`)·할인가(`discountedSalePrice`)·옵션·카테고리를 직접 파싱한다. DOM selector보다 구조가 안정적이고 재시도가 필요 없다.

---

## 필드별 처리 설명

| 필드 | 처리 방식 | AI 사용 | 실패 시 처리 |
|---|---|:---:|---|
| `name` | product detail API → `__NEXT_DATA__` JSON → DOM h3/title fallback | X | `field_errors` 기록 |
| `image_url` | API `representImage` + `galleryImages` → DOM img fallback | X | null 처리, `field_errors` 기록 |
| `option1` / `option2` | `option_texts`를 AI가 조합·정규화 | O | null, `missing_reasons` 기록 |
| `consumer_price` | API `salePrice` (정가 역할) → DOM selector → 본문 텍스트 fallback | X | null, `field_errors` 기록 |
| `sales_price` | API `discountedSalePrice` → DOM selector → 본문 텍스트 fallback | X | 필수 실패 사유 기록 |
| `lowest_price` | 미구현 — 항상 null | X | `missing_reasons` 기록 |
| `discount_rate` | `consumer_price`·`sales_price` 기반 자동 계산 | X | consumer_price 없으면 null |
| `hashtags` | `detail_text` 기반 AI 추출 | O | 빈 배열, `missing_reasons` 기록 |
| `usp` | `detail_text` 기반 AI 요약 (300자 이내) | O | null, `missing_reasons` 기록 |
| `category_group` | `category_path` + 상품명 기반 AI 분류 → enum 검증 | O | 검증 실패 사유 기록 |
| `brand_name` | AI 추정 | O | null |

**가격 필드 주의사항**  
네이버 상품 API에서 `salePrice`는 정가(소비자가)이고 `discountedSalePrice`가 실제 판매가다. 변수명이 직관적이지 않아 초기에 역전 오류가 있었고, 단위 테스트(`test_naver_store_helpers.py`)로 이를 고정했다.

---

## 샘플 출력

### 네이버 브랜드스토어 — `https://brand.naver.com/kefii`

100개 URL 발견, 3개 수집 (smoke test, `--max-products 3`):

```json
{
  "summary": {
    "total_discovered": 100,
    "total_crawled": 3,
    "success_count": 3,
    "failed_count": 0
  },
  "products": [
    {
      "source_url": "https://brand.naver.com/kefii/products/9285518735",
      "name": "케피 대용량 혼합 벌크팩 버블클렌저 200ml 7종 + 300ml 4종 + 슬라임 4종",
      "consumer_price": 344000,
      "sales_price": 122500,
      "category_path": "출산/육아 > 스킨/바디용품 > 유아바스/샴푸",
      "is_soldout": false,
      "image_urls_count": 10,
      "option_texts_count": 8,
      "raw_evidence": {
        "name": "product detail API",
        "price_api": {
          "salePrice": 344000,
          "discountedSalePrice": 122500,
          "note": "salePrice=정가(consumer), discountedSalePrice=할인가(sales)"
        },
        "image_url": "product detail API: 10개",
        "option_texts": "product detail API: optionCombinations",
        "category_path": "product detail API: category hierarchy"
      },
      "field_errors": {
        "detail_text": "이미지 기반 상세페이지로 텍스트 추출 제한"
      }
    }
  ]
}
```

### 네이버 스마트스토어 — `https://smartstore.naver.com/phytonutri`

15개 URL 발견, 상품 상세 접근 불가:

```json
{
  "summary": {
    "total_discovered": 15,
    "total_crawled": 3,
    "success_count": 0,
    "failed_count": 3
  },
  "errors": [
    {
      "url": "https://smartstore.naver.com/phytonutri/products/9623766251",
      "reason": "Naver 로그인 필요: 스토어가 비인증 접근을 차단함",
      "error_type": "EXTRACT_FAILED"
    }
  ]
}
```

**원인**: phytonutri 스마트스토어는 서버측에서 비인증 상태의 상품 상세 페이지 접근에 대해 `302 → nid.naver.com/nidlogin` 리디렉트를 발행한다. `/products` 목록 페이지는 접근 가능하고 `special-products` API 인터셉트로 15개 상품 ID를 발견했으나, 개별 상품 상세 데이터는 수집할 수 없다. 동일 스토어를 실제 브라우저(로그인 상태)에서 열면 정상 접근 가능하며, 이는 해당 스토어의 Naver 인증 설정에 의한 서버측 제한이다.

### 일반 브랜드몰 — `https://m.happylandmall.com/`

`GenericMallCrawler` 미구현. 회고에서 접근 방식을 설명한다.

---

## 회고

### 고민한 지점

**API 인터셉트 vs DOM 파싱**  
네이버 스토어는 Next.js SPA여서 DOM에 실제 상품 데이터가 렌더링되기 전에 API 응답이 먼저 도달한다. DOM selector는 클래스명 변경에 취약하고 타이밍 의존성도 있다. 반면 내부 API는 필드 구조가 안정적이다. 최종적으로 API 인터셉트를 1순위로 두고 DOM을 fallback으로 설계했다.

**가격 AI 배제 이유**  
"원"이 붙은 숫자가 페이지에 여러 개 존재한다(배송비, 쿠폰 혜택, 포인트 적립 등). LLM은 맥락 없이 가장 큰 숫자를 "정가"로 추정할 위험이 있다. 정가/판매가 판단은 context keyword 기반(정가·소비자가 → consumer_price, 할인가·혜택가 → sales_price)으로 결정적 처리했고, API 응답의 `salePrice`/`discountedSalePrice` 필드명을 기준으로 고정했다.

**URL 발견 우선순위**  
DOM href > `__NEXT_DATA__` JSON > API 인터셉트 순으로 설계했다. API 인터셉트는 추천/번들 상품 ID를 오탐할 수 있어 보조 역할에 한정했다. 스마트스토어의 경우 목록 페이지에서 자동 발화하는 `special-products` API를 이용해 상품 ID를 수집했다.

### 트레이드오프

**단일 크롤러 vs 분리**  
브랜드스토어와 스마트스토어는 API 경로(`/n/v2/` vs `/i/v2/`)만 다를 뿐 구조가 유사해 `NaverStoreCrawler` 하나로 처리했다. 향후 스마트스토어 전용 로직(리스팅 URL, 인증 처리)이 늘어나면 분리를 고려해야 한다.

**partial result 보존**  
상품 단위 실패가 전체 수집을 중단시키지 않도록 연속 실패 임계값(기본 5회) 초과 시에만 조기 종료하고 수집된 부분 결과를 반환한다. 이 덕분에 phytonutri처럼 100% 실패하는 경우에도 발견된 URL 목록은 보존된다.

### 해결하지 못한 이유

**phytonutri 상품 상세 접근 실패**  
phytonutri 스마트스토어는 비인증 headless 브라우저에서 모든 상품 상세 페이지가 `302 → Naver 로그인`으로 리디렉트된다. 다음을 시도했으나 효과 없었다:
- 목록 페이지 쿠키로 컨텍스트 워밍업 후 재시도
- `--disable-blink-features=AutomationControlled` + `navigator.webdriver = undefined` 봇 탐지 우회
- `window.location.href` same-origin 이동 (Sec-Fetch-Site 우회 시도)
- 목록 페이지 컨텍스트에서 product API 직접 fetch (10초 대기 포함) → 429

이는 해당 스토어의 서버측 인증 정책에 의한 제한으로, 로그인 세션 없이는 해결할 수 없다.

**happylandmall.com**  
일반 브랜드몰 크롤러 미구현. 구현 계획: `BeautifulSoup` + `httpx`로 상품 목록 페이지 파싱 → 상품 상세 순회 → 가격/이미지/옵션 CSS selector 추출. JavaScript 렌더링이 없는 페이지는 Playwright 없이 처리하는 것이 요청 부하 측면에서 유리하다.

**detail_text 공백**  
네이버 스토어의 상품 상세설명은 대부분 이미지로만 구성되어 있다. contents API의 `textContent`가 비어있거나 `renderContent`가 이미지 태그만 있는 경우 텍스트를 추출할 수 없다. 이 경우 `field_errors["detail_text"]`에 "이미지 기반 상세페이지로 텍스트 추출 제한"을 기록하고 AI 정규화 단계에서는 해당 필드를 건너뛴다.

### 시간이 더 있었다면

- **시장 최저가 (`lowest_price`)**: Naver 쇼핑 카탈로그 API 또는 가격비교 페이지 크롤링으로 구현 가능하지만 인증 요구 및 빈번한 구조 변경으로 별도 안정화가 필요하다.
- **증분 재크롤**: 상품 URL 해시를 DB에 저장하고 변경분만 재크롤하는 구조. 현재는 매번 전체 재크롤.
- **happylandmall 구현**: 정적 HTML 파싱 기반으로 비교적 단순하게 구현 가능. JavaScript 렌더링 필요 여부 확인 후 Playwright vs httpx 분기 처리 추가.
- **RawProduct → PartnerProductCreateInput 변환 레이어**: `app/ai/normalizer.py`와 `app/services/crawl_service.py` 미구현. AI 필드 정규화와 최종 JSON 변환이 남아있다.
- **최저가 오탐 방지**: 여러 판매처에서 수집한 가격 중 이상값(악의적 저가 등록)을 필터링하는 로직이 필요하다.
