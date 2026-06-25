# Commodity Catalyst and Volatility Monitor

## Implementation Plan - Data Collection First

**Initial market:** NYMEX WTI crude oil futures and options  
**Context market:** Brent futures  
**Primary cadence:** Daily after settlement  
**Core principle:** No analytics or agent output is trusted unless it can be reproduced from immutable, provenance-rich source data.

## 1. Target Outcome

Build a daily workflow that:

1. collects and preserves WTI futures and option settlement data;
2. collects official fundamental releases and a structured catalyst calendar;
3. validates contract mappings, units, dates, and option price quality;
4. derives futures-curve and option-surface features;
5. identifies where forward risk is concentrated;
6. ranks upcoming catalysts by delivery-window relevance;
7. produces scenarios, confirmation/invalidation triggers, and a daily forward-risk brief.

The MVP must not claim executable mispricing, historical extremeness, or causal attribution from settlement-only data.

## 2. Delivery Priorities

| Priority | Workstream | Why it comes first |
|---|---|---|
| P0 | Source inventory and data contracts | Prevents later rework and incorrect mappings |
| P1 | Immutable raw collection | Creates a historical asset from day one |
| P2 | Normalization and quality controls | Makes calculations reproducible |
| P3 | Futures and option analytics | Produces structured market signals |
| P4 | Catalyst extraction and ranking | Adds agentic relevance analysis |
| P5 | Scenarios and report | Converts signals into forward-looking decision support |

## 3. Data Sources and Access Strategy

### 3.1 Bootstrap sources

- CME public WTI futures settlement pages.
- CME WTI option settlement pages and settlement tools.
- CME delayed quote pages for manual checks only; public quotes are delayed.
- EIA Open Data API and Weekly Petroleum Status Report.
- CFTC Commitments of Traders for weekly aggregate positioning context.
- Curated high-quality catalyst feeds or manually supplied articles for the first demo.

### 3.2 Production upgrade path

Abstract all market-data collectors behind provider interfaces. The intended production replacement is CME DataMine or another licensed end-of-day distributor. Intraday CME WebSocket or vendor feeds are a later enhancement.

### 3.3 Source registry

Create `config/sources.yaml` with:

```yaml
sources:
  cme_wti_futures_settlement:
    tier: public_bootstrap
    cadence: daily
    expected_after: "18:00 America/Chicago"
    format: html_or_export
    license_note: "prototype/manual verification; review terms before automated production use"

  cme_wti_option_settlement:
    tier: public_bootstrap
    cadence: daily
    format: html_or_export

  eia_api_v2:
    tier: official_open_api
    cadence: weekly_and_monthly
    requires_api_key: true

  cftc_cot:
    tier: official_open_file
    cadence: weekly
```

## 4. Canonical Data Contracts

### 4.1 Futures settlement

```text
trade_date
exchange
product
contract_code
delivery_month
settlement
volume
open_interest
currency
price_unit
source_id
source_record_id
retrieved_at
raw_file_sha256
```

Natural key: `(trade_date, exchange, product, contract_code, source_id)`.

### 4.2 Option settlement

```text
trade_date
option_symbol
option_expiry
underlying_contract
underlying_delivery_month
strike
call_put
settlement
volume
open_interest
exercise_style
settlement_style
contract_multiplier
source_id
retrieved_at
raw_file_sha256
```

Natural key: `(trade_date, option_expiry, underlying_contract, strike, call_put, source_id)`.

### 4.3 Fundamental observation

```text
series_id
period
release_timestamp
vintage_timestamp
value
unit
geography
source_id
retrieved_at
```

Always preserve release and revision vintages. Never overwrite a prior vintage.

### 4.4 Catalyst event

```json
{
  "event_id": "stable-hash",
  "event_type": "inventory_release|outage|opec|sanctions|refinery|weather|macro_demand|other",
  "title": "...",
  "published_at": "...",
  "effective_start": "...",
  "effective_end": null,
  "commodity": "crude_oil",
  "region": "US Gulf",
  "direction": "bullish_supply|bearish_demand|two_sided|unclear",
  "magnitude": "low|medium|high|unknown",
  "affected_horizon": "prompt_1m|prompt_3m|6m|12m|structural",
  "source_quality": "primary|high_quality_secondary|other",
  "source_id": "...",
  "evidence": ["verbatim fact snippets within copyright limits"]
}
```

## 5. Storage and Lineage

```text
data/
  raw/{source_id}/{retrieval_date}/...
  bronze/{dataset}/trade_date=YYYY-MM-DD/*.parquet
  silver/{dataset}/trade_date=YYYY-MM-DD/*.parquet
  gold/features/trade_date=YYYY-MM-DD/*.parquet
  gold/events/event_date=YYYY-MM-DD/*.parquet
  manifests/
  quality_reports/
  reports/
```

Rules:

- Raw data is append-only and immutable.
- Every raw object gets SHA-256, byte size, retrieval timestamp, source URL/identifier, and HTTP metadata when applicable.
- Bronze retains source-native identifiers.
- Silver normalizes symbology, units, dates, and mappings.
- Gold contains derived features only; every row links back to silver inputs.
- Use Parquet and DuckDB for the MVP.

## 6. Collection Pipeline

### 6.1 Collector interface

```python
from typing import Protocol

class Collector(Protocol):
    source_id: str

    def discover(self, as_of_date): ...
    def fetch(self, item): ...
    def persist_raw(self, payload, metadata): ...
    def parse_bronze(self, raw_path): ...
```

### 6.2 Idempotency

Each run should:

1. discover expected artifacts;
2. compare with the manifest;
3. skip identical files;
4. retain revised files as a new vintage;
5. write a run record with success, warning, and failure counts.

### 6.3 Scheduling

- Market settlements: once daily after expected publication, with two retries.
- EIA weekly release: scheduled collection shortly after publication, plus a later revision check.
- CFTC: weekly.
- Catalyst articles/releases: daily batch in MVP; event-time collection later.

### 6.4 Failure policy

- Missing market file: report `collection_incomplete`; do not silently reuse the previous day.
- Partial option chain: ingest valid rows, quarantine invalid rows, and mark surface coverage insufficient.
- Changed schema: fail closed and save the raw payload for investigation.

## 7. Reference Data

Maintain versioned tables for:

- futures contract codes and delivery months;
- option expiry to underlying-future mapping;
- last trade, settlement, and expiration dates;
- contract multipliers, tick sizes, currency, and units;
- exercise and settlement style;
- holiday calendar and expected publication dates.

Reference data is not an LLM task. It must be explicit, reviewed, and unit-tested.

## 8. Quality Controls

### Critical checks

- duplicate natural keys;
- contract sequence and missing delivery months;
- wrong option-to-future mapping;
- option expiry on or before observation date;
- settlement below intrinsic value beyond tolerance;
- failed or unstable implied-volatility inversion;
- unexpected unit/currency change;
- insufficient valid strikes per expiry;
- stale or unchanged chains inconsistent with source timestamps;
- abrupt source row-count change;
- late or missing expected file.

### Quality status

```text
PASS       safe for analytics
WARN       usable with disclosed limitations
FAIL       excluded from gold features
QUARANTINE requires manual review
```

Generate a daily quality report before analytics begin.

## 9. Analytics After Data Is Stable

### 9.1 Futures features

- contract-level daily and five-day returns;
- adjacent calendar spreads and changes;
- butterflies and local movement residuals;
- front-versus-back slope;
- WTI-Brent differential and curve comparison;
- volume/open-interest coverage flags.

### 9.2 Option features

- Black-76 implied volatility with correct futures mapping;
- delta and vega;
- ATM IV;
- 25-delta call and put IV;
- risk reversal and butterfly;
- term structure;
- event-expiry versus neighboring-expiry variance;
- smile-fit residual and valid-strike count.

### 9.3 Agreement states

```text
confirmed_upside_risk
confirmed_downside_or_demand_risk
non_directional_uncertainty
futures_only_repricing
options_only_repricing
cross_market_disagreement
no_material_change
insufficient_data
```

## 10. Catalyst Agent

### 10.1 Small-model extraction task

Input: article/release text plus target market and observation date.  
Output: the structured catalyst schema, affected horizon, direction, source quality, and evidence snippets.

### 10.2 Deterministic relevance score

Combine:

- temporal overlap;
- delivery-window/expiry overlap;
- commodity and geography match;
- directional consistency;
- magnitude;
- source quality;
- novelty;
- semantic relevance score from the model.

The model should not control the entire ranking.

### 10.3 Synthesis task

Produce:

- primary and secondary hypotheses;
- supporting evidence;
- contradicting evidence;
- unexplained component;
- confidence;
- additional checks requested from the quant layer.

## 11. Forward-Looking Report

Daily sections:

1. **Current market-implied risk:** delivery months and expiries where risk is concentrated.
2. **Upcoming catalysts:** ranked by exposure overlap and degree already priced.
3. **Futures-options agreement:** coherent, divergent, or insufficient.
4. **Scenarios:** bull/base/bear/event, with curve and volatility shocks.
5. **Confirmation and invalidation:** observable conditions.
6. **Data caveats:** settlement-only, sparse chain, or missing context.
7. **Next review:** event or publication time.

## 12. Repository Layout

```text
ccvm/
  config/
    markets/wti.yaml
    sources.yaml
    event_taxonomy.yaml
  data/
  src/
    collectors/
    parsers/
    reference/
    validation/
    analytics/
    agents/
    scenarios/
    reporting/
    storage/
  schemas/
  tests/
    fixtures/
    integration/
    regression/
  app/
  scripts/
  docs/
```

## 13. Suggested Build Sequence

### Milestone 0 - Data contract and fixtures

- Finalize schemas.
- Create two manual WTI futures dates and two option-chain dates.
- Add known-bad fixtures: duplicate row, wrong underlying, sparse expiry, invalid price.
- Implement schema validation.

**Exit:** fixtures produce expected PASS/WARN/FAIL results.

### Milestone 1 - Raw collection and manifests

- Implement source registry.
- Build raw persistence, checksums, manifests, and collection-run table.
- Add one market collector and EIA collector.
- Make reruns idempotent.

**Exit:** three consecutive collection runs preserve lineage without duplicates.

### Milestone 2 - Normalization and reference data

- Parse bronze tables.
- Build WTI contract calendar and option-underlying map.
- Normalize units and timestamps.
- Generate a daily quality report.

**Exit:** all sample dates reconcile to source totals and mappings.

### Milestone 3 - Daily feature store

- Implement futures features.
- Implement IV solver and option metrics.
- Store gold feature objects.
- Add hand-calculated regression tests.

**Exit:** results are reproducible and stable across reruns.

### Milestone 4 - Catalyst extraction and ranking

- Implement event schema and source deduplication.
- Add model-based structured extraction.
- Add deterministic relevance scoring.
- Review top-ranked events manually.

**Exit:** top-three events are relevant to the identified delivery window in test cases.

### Milestone 5 - Scenarios and report

- Add editable curve and volatility scenarios.
- Generate confirmation/invalidation triggers.
- Build Markdown and Streamlit views.
- Save the report and all input IDs.

**Exit:** one command reproduces the complete daily brief from stored data.

## 14. Test Strategy

- Unit tests for symbols, calendars, mappings, IV inversion, spreads, RR, and BF.
- Golden-file tests for parsed source fixtures.
- Regression tests for daily feature output.
- Contract tests for every collector.
- End-to-end test using two consecutive settlement dates.
- Report test that verifies every numerical statement links to a feature ID and every event claim links to a source ID.

## 15. Definition of Done for MVP

- At least 10 clean daily WTI futures snapshots.
- At least 10 usable WTI option settlement snapshots with explicit coverage metrics.
- Automated EIA release collection with vintage preservation.
- Immutable raw store and reproducible silver/gold tables.
- Daily quality report.
- Futures and option risk-map metrics.
- Agreement classification.
- Ranked upcoming catalyst list.
- Three scenarios with confirmation and invalidation triggers.
- Saved Markdown report and Streamlit review page.
- Clear disclaimer that settlement data does not establish executability.

## 16. First Tasks to Assign to a Coding Agent

1. Create the repository and Pydantic schemas.
2. Implement raw-file persistence, SHA-256 manifest, and DuckDB run tables.
3. Build CSV/manual fixture loaders before any web collector.
4. Implement WTI futures normalization and contract ordering.
5. Implement option-underlying mapping and validation.
6. Add EIA APIv2 collector with vintage storage.
7. Add daily quality report.
8. Only after the above passes, implement curve and volatility analytics.

This ordering is intentional: the project should accumulate a trustworthy dataset before spending effort on sophisticated agent reasoning.
