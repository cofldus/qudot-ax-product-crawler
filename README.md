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
| 일반 브랜드몰 | `https://m.happylandmall.com/` | `GenericMallCrawler` |

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
ANTHROPIC_API_KEY=
PLAYWRIGHT_HEADLESS=true
MAX_PRODUCTS=100
REQUEST_DELAY_MIN=0.5
REQUEST_DELAY_MAX=1.5
```

`ANTHROPIC_API_KEY`를 설정하지 않으면 AI 필드(`brand_name`, `option1`, `option2`, `hashtags`, `usp`, `category_group`)가 비어있는 `partial` 상태로 출력된다.

`.env`는 `.gitignore`에 포함되어 있으며 절대 커밋하지 않는다.

### 환경 변수 전체 목록

```env
# AI 정규화 (필수 — 없으면 partial 상태로 출력)
ANTHROPIC_API_KEY=

# Supabase (선택 — 설정 시 크롤링 결과를 DB에 자동 저장)
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_KEY=eyJhbGci...

# 크롤링 동작
PLAYWRIGHT_HEADLESS=true
MAX_PRODUCTS=100
REQUEST_DELAY_MIN=0.5
REQUEST_DELAY_MAX=1.5
```

### Supabase 테이블 초기화

Supabase 대시보드 → SQL Editor에서 아래 파일을 실행한다.

```bash
# 또는 supabase CLI 사용 시
supabase db push
```

마이그레이션 파일: `supabase/migrations/001_init.sql`

생성 테이블:
- `crawl_runs` — 실행 이력 (스토어별 수집 요약)
- `partner_products` — 정규화된 상품 (`source_url` 기준 upsert)

### 실행 방법 A — CLI

```bash
# 네이버 브랜드스토어
python main.py --url https://brand.naver.com/kefii --max-products 5

# 네이버 스마트스토어
python main.py --url https://smartstore.naver.com/phytonutri --max-products 5

# 일반 브랜드몰
python main.py --url https://m.happylandmall.com/ --max-products 5

# 출력 경로 지정
python main.py --url https://brand.naver.com/kefii --max-products 3 --output outputs/kefii.json
```

결과는 `outputs/result_{slug}_{timestamp}.json`에 저장된다. `SUPABASE_URL`이 설정되어 있으면 동시에 DB에도 upsert된다.

### 실행 방법 B — FastAPI 서버

```bash
uvicorn api:app --reload --port 8000
```

```
POST http://localhost:8000/crawl
Content-Type: application/json

{
  "url": "https://brand.naver.com/kefii",
  "max_products": 5
}
```

Swagger UI: `http://localhost:8000/docs`

---

## AI 필드 정책

AI(`claude-sonnet-4-6`)는 아래 6개 필드에만 사용한다. 가격·상품명·이미지 같은 결정적 필드는 DOM/JSON/API 결과만 사용하며 AI가 개입하지 않는다.

| 필드 | 허용 여부 | 비고 |
|---|:---:|---|
| `brand_name` | ✅ AI 허용 | 상품명·카테고리에서 추정 |
| `option1` | ✅ AI 허용 | `option_texts` 조합·정규화 |
| `option2` | ✅ AI 허용 | `option_texts` 조합·정규화 |
| `hashtags` | ✅ AI 허용 | `detail_text` 기반 추출 |
| `usp` | ✅ AI 허용 | `detail_text` 기반 요약 (300자 이내) |
| `category_group` | ✅ AI 허용 | `category_path` + 상품명 기반 분류 → enum 검증 |
| `name` | ❌ AI 금지 | API → `__NEXT_DATA__` → DOM 결정적 추출 |
| `image_url` | ❌ AI 금지 | API `representImage` → DOM img fallback |
| `consumer_price` | ❌ AI 금지 | API `salePrice` → DOM → 본문 텍스트 fallback |
| `sales_price` | ❌ AI 금지 | API `discountedSalePrice` → DOM → 본문 텍스트 fallback |
| `lowest_price` | ❌ AI 금지 | `--lowest-price` 활성화 시 네이버 쇼핑 실조회, 실패·미활성화 시 `null` |
| `discount_rate` | ❌ AI 금지 | `consumer_price`·`sales_price` 기반 자동 계산 |
| `source_url` | ❌ AI 금지 | 크롤링 URL 그대로 |

---

## 상태 정의

각 상품 항목에는 `status` 필드가 포함된다.

| 상태 | 의미 |
|---|---|
| `normalized` | AI 필드까지 정상 채워진 상태 |
| `partial` | 결정적 필드(이름·가격·이미지)는 추출됐으나 AI 필드 없음 (API 키 미설정 또는 AI 호출 실패) |

크롤링 자체가 실패한 상품은 `products` 배열이 아닌 `errors` 배열에 기록된다.

---

## 기술 선택 이유

**Python 3.11+ / Playwright (async)**  
네이버 스토어는 Next.js 기반 SPA다. 상품 목록과 상세 데이터가 JavaScript 렌더링 이후에야 DOM에 나타나거나 API 응답으로만 존재한다. 정적 HTTP 요청으로는 수집할 수 없어 동적 렌더링 대응이 가능한 Playwright를 선택했다.

**Pydantic v2**  
`RawProduct`(크롤링 원본)와 `PartnerProductCreateInput`(최종 출력)의 스키마를 분리했다. 원본은 검증 없이 보존하고, 최종 출력 단계에서 가격 역전·enum 범위·AI 필드 허용 여부를 일괄 검증한다. Pydantic v2의 `model_validator`로 가격 역전 방지와 할인율 자동 계산을 선언적으로 처리했다.

**Anthropic SDK**  
`category_group`, `usp`, `hashtags`, `option1`/`option2`, `brand_name` 6개 필드에만 AI를 사용한다. 가격·상품명·이미지 같은 결정적 필드는 AI가 개입하면 오탐이 발생하기 때문에 DOM/JSON/API 결과만 사용한다.

**API 인터셉트 우선 전략**  
네이버 스토어 상품 상세 페이지를 로드하면 `/n/v2/channels/{id}/products/{pid}` API가 자동 발화한다. Playwright response 핸들러로 이 응답을 가로채 정가(`salePrice`)·할인가(`discountedSalePrice`)·옵션·카테고리를 직접 파싱한다. DOM selector보다 구조가 안정적이고 재시도가 필요 없다.

**Supabase (Postgres)**  
`SUPABASE_URL`이 설정되면 크롤링 완료 후 결과를 `partner_products` 테이블에 upsert한다. `source_url`을 PK로 사용해 재크롤 시 덮어쓰기(증분 업데이트)가 자연스럽게 된다. `crawl_runs` 테이블로 실행 이력도 관리한다.

**FastAPI**  
`api.py`에 `POST /crawl` 엔드포인트를 제공한다. CLI(`main.py`)와 동일한 `run_crawl()` 서비스를 공유하며, 큐닷 기존 Next.js + FastAPI 스택에 바로 병합 가능하다.

---

## 필드별 처리 설명

| 필드 | 처리 방식 | AI 사용 | 실패 시 처리 |
|---|---|:---:|---|
| `name` | product detail API → `__NEXT_DATA__` JSON → DOM h3/title fallback | X | `field_errors` 기록 |
| `image_url` | API `representImage` + `galleryImages` → DOM img fallback | X | null 처리, `field_errors` 기록 |
| `option1` / `option2` | `option_texts`를 AI가 조합·정규화 | O | null, `missing_reasons` 기록 |
| `consumer_price` | API `salePrice` (정가 역할) → DOM selector → 본문 텍스트 fallback | X | null, `field_errors` 기록 |
| `sales_price` | API `discountedSalePrice` → DOM selector → 본문 텍스트 fallback | X | 필수 실패 사유 기록 |
| `lowest_price` | `--lowest-price` 활성화 시 네이버 쇼핑 실크롤링 (유사도 0.35+ 오탐 방지) | X | null 유지, `field_errors` 또는 `missing_reasons` 기록 |
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

100개 URL 발견, 3개 수집 (`--max-products 3`, `ANTHROPIC_API_KEY` 미설정):

```json
{
  "store_url": "https://brand.naver.com/kefii",
  "crawler_type": "naver_store",
  "summary": {
    "total_discovered": 100,
    "total_attempted": 3,
    "total_crawled": 3,
    "total_normalized": 0,
    "failed_count": 0,
    "partial_count": 3
  },
  "products": [
    {
      "status": "partial",
      "partner_product": {
        "name": "케피 대용량 혼합 벌크팩 버블클렌저 200ml 7종 + 300ml 4종 + 슬라임 4종",
        "source_url": "https://brand.naver.com/kefii/products/9285518735",
        "image_url": "https://shop-phinf.pstatic.net/...",
        "consumer_price": 344000,
        "sales_price": 122500,
        "discount_rate": 64.39,
        "lowest_price": null,
        "brand_name": null,
        "hashtags": [],
        "usp": null,
        "category_group": [],
        "ai_fields": [],
        "missing_reasons": {
          "lowest_price": "최저가 미조회 — --lowest-price 플래그로 활성화 가능",
          "brand_name": "ANTHROPIC_API_KEY 미설정",
          "hashtags": "ANTHROPIC_API_KEY 미설정",
          "usp": "ANTHROPIC_API_KEY 미설정",
          "category_group": "ANTHROPIC_API_KEY 미설정"
        }
      },
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
  ],
  "errors": []
}
```

### 네이버 스마트스토어 — `https://smartstore.naver.com/phytonutri`

URL 발견 후 상품 상세 접근 불가 (서버측 인증 제한):

```json
{
  "store_url": "https://smartstore.naver.com/phytonutri",
  "crawler_type": "naver_store",
  "summary": {
    "total_discovered": 15,
    "total_attempted": 3,
    "total_crawled": 0,
    "total_normalized": 0,
    "failed_count": 3,
    "partial_count": 0
  },
  "products": [],
  "errors": [
    {
      "url": "https://smartstore.naver.com/phytonutri/products/9623766251",
      "reason": "로그인 페이지로 리디렉트됨 — 비인증 접근 차단",
      "error_type": "EXTRACT_FAILED"
    }
  ]
}
```

**원인**: phytonutri 스마트스토어는 서버측에서 비인증 상태의 상품 상세 페이지 접근에 대해 `302 → nid.naver.com/nidlogin` 리디렉트를 발행한다. 목록 페이지 접근은 가능하고 `special-products` API 인터셉트로 상품 ID를 발견했으나, 개별 상품 상세 데이터는 수집할 수 없다. 이는 해당 스토어의 Naver 인증 설정에 의한 서버측 제한이다.

각 상품의 `raw_evidence["access"]`에 리디렉트 감지 결과가 기록된다:

```json
{
  "access": {
    "final_url": "https://nid.naver.com/nidlogin.login?...",
    "page_title": "네이버 : 로그인",
    "redirect_or_login_detected": true,
    "matched_api_urls": [],
    "unmatched_api_urls_count": 0
  }
}
```

### 일반 브랜드몰 — `https://m.happylandmall.com/`

102개 URL 발견, 3개 수집 (`--max-products 3`, `ANTHROPIC_API_KEY` 미설정):

```json
{
  "store_url": "https://m.happylandmall.com/",
  "crawler_type": "generic",
  "summary": {
    "total_discovered": 102,
    "total_attempted": 3,
    "total_crawled": 3,
    "total_normalized": 0,
    "failed_count": 0,
    "partial_count": 3
  },
  "products": [
    {
      "status": "partial",
      "partner_product": {
        "name": "[압소바] 베베 반소 상하 A1313011",
        "source_url": "https://m.happylandmall.com/goods/goods_view.php?goodsNo=1000000314",
        "image_url": "https://godomall-storage.cdn-nhncommerce.com/.../detail_054.jpg",
        "consumer_price": null,
        "sales_price": 59000,
        "discount_rate": null,
        "lowest_price": null
      },
      "raw_evidence": {
        "name": "DOM selector",
        "image_url": "DOM img",
        "price_dom": {
          "method": "DOM selector",
          "selector": "goods_price",
          "text": "59,000원"
        }
      }
    }
  ],
  "errors": []
}
```

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

**부분 결과 보존**  
상품 단위 실패가 전체 수집을 중단시키지 않도록 연속 실패 임계값(기본 5회) 초과 시에만 조기 종료하고 수집된 부분 결과를 반환한다. 이 덕분에 phytonutri처럼 100% 실패하는 경우에도 발견된 URL 목록은 보존된다.

### 해결하지 못한 이유

**phytonutri 상품 상세 접근 실패**  
phytonutri 스마트스토어는 비인증 headless 브라우저에서 모든 상품 상세 페이지가 `302 → Naver 로그인`으로 리디렉트된다. 목록 페이지 쿠키로 컨텍스트 워밍업 후 재시도, `window.location.href` same-origin 이동, 목록 페이지 컨텍스트에서 product API 직접 fetch 등을 시도했으나 서버측 인증 검사를 통과하지 못했다. 로그인 세션 없이는 해결할 수 없다.

**detail_text 공백**  
네이버 스토어의 상품 상세설명은 대부분 이미지로만 구성되어 있다. contents API의 `textContent`가 비어있거나 `renderContent`가 이미지 태그만 있는 경우 텍스트를 추출할 수 없다. 이 경우 `field_errors["detail_text"]`에 "이미지 기반 상세페이지로 텍스트 추출 제한"을 기록하고 AI 정규화 단계에서는 해당 필드를 건너뛴다.

### 새로 알게 된 점

**네이버 API 필드명 역설**  
`salePrice`가 정가(소비자가)이고 `discountedSalePrice`가 실제 판매가라는 점은 처음에 반드시 잘못 이해하게 된다. 내부 API 응답을 직접 들여다보지 않으면 DOM에서 의미를 추론하다 역전 오류가 발생한다. 이를 계기로 API 응답과 표시값을 항상 독립적으로 검증하는 단위 테스트의 중요성을 재확인했다.

**스마트스토어 인증 정책 차이**  
브랜드스토어(`brand.naver.com`)와 스마트스토어(`smartstore.naver.com`)는 같은 네이버 플랫폼이지만 인증 정책이 전혀 다르다. 브랜드스토어는 비인증 headless 브라우저에서도 상품 상세 접근이 가능하지만, phytonutri 스마트스토어는 모든 상품 상세에 서버측 로그인 검사가 걸려 있다. 같은 도메인 구조라고 같은 크롤러로 처리 가능하다는 가정은 위험하다.

**Playwright response 핸들러 누적 문제**  
`page.on("response", handler)`를 per-product 루프 안에서 등록하면 이전 핸들러가 해제되지 않아 메모리 누수와 오탐이 발생한다. `page.remove_listener`로 명시적 해제 + `asyncio.create_task` + `asyncio.gather` 패턴으로 해결했다.

### 시간이 더 있었다면

- **시장 최저가 (`lowest_price`)**: `--lowest-price` 플래그로 네이버 쇼핑 실크롤링을 활성화할 수 있다. Jaccard 유사도 0.35+ 오탐 방지와 수집 근거(`raw_evidence.lowest_price`)를 포함한다. 네이버 쇼핑 UI 구조 변경 시 셀렉터 유지보수가 필요하다.
- **증분 재크롤**: 상품 URL 해시를 DB에 저장하고 변경분만 재크롤하는 구조. 현재는 매번 전체 재크롤.
- **happylandmall consumer_price**: 현재 `sales_price`만 추출됨. 정가 셀렉터 추가로 할인율 계산도 가능하다.
- **최저가 오탐 방지**: 여러 판매처에서 수집한 가격 중 이상값을 필터링하는 로직이 필요하다.

### 실제 서비스로 확장한다면

- **인증 필요 스토어 처리**: phytonutri처럼 로그인 검사를 거는 스토어는 OAuth 세션 관리 또는 운영자 계정 쿠키 주입이 필요하다. 이를 안전하게 보관하고 순환시키는 Secret Manager 연동이 전제된다.
- **스키마 버전 관리**: `PartnerProductCreateInput`이 변경되면 기존 수집 데이터와 불일치가 발생한다. Pydantic 스키마에 버전 필드를 두고 마이그레이션 스크립트를 관리해야 한다.
- **웹훅·알림**: 가격 변동이나 품절 감지 시 Slack 또는 이메일 알림을 트리거하면 운영팀 수작업을 추가로 줄일 수 있다.
- **FastAPI 서비스화**: 현재는 CLI(`main.py`). FastAPI로 래핑해 `/crawl` POST endpoint로 노출하면 큐닷 기존 Next.js + FastAPI 스택에 바로 병합 가능하다.
