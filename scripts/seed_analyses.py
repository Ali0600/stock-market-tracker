"""Seed/refresh the per-stock AI analyses.

Written 2026-06-10, grounded in live yfinance data pulled the same day
(prices, valuation, analyst consensus, headlines). Informational research
notes only — deliberately no buy/sell/hold recommendations. Re-run with:

    venv/bin/python scripts/seed_analyses.py
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402

ANALYSES: dict[str, str] = {

# ---------------------------------------------------------------- Semiconductors
"NVDA": """NVIDIA designs the GPUs, networking silicon and rack-scale systems that run most of the world's AI training and inference. It is the largest company in this portfolio (~$4.9T market cap) and still its profit engine: ~63% net margin with revenue up ~85% year over year at last report.

## Performance
Up ~40% over the past year to ~$200, though ~15% below the 52-week high after a choppy spring; +7.7% over 3 months. Trailing P/E ~31 compresses to ~16 on forward estimates — earnings are still growing into the price.

## Strengths
- Dominant AI-accelerator position with deep CUDA software lock-in
- Margins and growth rarely seen at this scale; 59 analysts at strong-buy, mean target ~$298

## Risks
- Spending is concentrated in a handful of hyperscalers — any AI-capex digestion phase hits hard
- Beta ~2.2: roughly twice the market's moves in both directions
- Export controls and China policy remain structural overhangs

## What to Watch
Earnings Aug 26, 2026. Hyperscaler capex commentary and the next platform ramp's cadence matter more than the quarter itself.""",

"TSM": """Taiwan Semiconductor manufactures the leading-edge chips nearly everyone else in this portfolio designs — the closest thing the AI build-out has to a toll booth. ~$2.1T market cap, ~47% net margin.

## Performance
Up ~91% over the past year to ~$409 and only ~9% off its 52-week high — unusually steady for a stock this size. May 2026 revenue rose ~30% year over year per company sales data.

## Strengths
- Effective monopoly at the most advanced process nodes; pricing power to match
- P/E ~35 trailing / ~21 forward is modest relative to its growth; 18 analysts at strong-buy, target ~$468
- Capacity diversification under way (Arizona, Japan, Germany)

## Risks
- Taiwan-strait geopolitics is the defining tail risk and can't be diversified away within the stock
- Capex supercycles cut both ways if AI demand pauses
- US fab economics dilute margins at the edges

## What to Watch
Earnings Jul 16, 2026; monthly revenue prints; any change in advanced-node pricing or geopolitical temperature.""",

"INTC": """Intel is the portfolio's turnaround story — and one of the market's most dramatic large-cap repricings of the past year. The stock is up ~418% over twelve months to ~$107 (~$538B market cap), even though trailing earnings are still negative.

## Performance
+123% in three months alone, ~19% below its 52-week high. The valuation now leans entirely on the recovery thesis: forward P/E ~70 while margins (-5.9%) remain underwater.

## Strengths
- Foundry ambitions position it as the strategic US-soil alternative in an AI-driven, geopolitically tense chip market
- Revenue growth has turned positive (+7%) after years of decline

## Risks
- The street hasn't endorsed the rally: consensus is hold and the mean target (~$92) sits *below* the price — the market is paying for execution that hasn't fully shown up in earnings yet
- Manufacturing missteps were the original wound; another node slip would be punished severely
- Beta ~2.2 with a crowd of momentum holders

## What to Watch
Earnings Jul 23, 2026: gross-margin trajectory and foundry customer announcements are the proof points the multiple depends on.""",

"MRVL": """Marvell supplies custom AI silicon (XPUs for hyperscalers) and the electro-optics that connect AI clusters — a picks-and-shovels play one layer below NVIDIA. ~$221B market cap.

## Performance
Up ~270% over the past year and +179% in three months to ~$253, ~22% off the 52-week high. Net margin ~29% with revenue growth ~28%.

## Strengths
- Custom-silicon pipeline gives hyperscalers their NVIDIA alternative, with Marvell embedded either way via optics and interconnect
- 41 analysts at strong-buy

## Risks
- Valuation is full: P/E ~87 trailing / ~41 forward, and the mean target (~$233) is below the price after the 3-month surge
- Custom-chip wins are lumpy and customer-concentrated; a single program loss moves the stock
- Beta ~2.3 — expect violent drawdowns in any AI-sentiment wobble

## What to Watch
Earnings Aug 27, 2026; design-win announcements and 800G/1.6T optics demand commentary.""",

"AMAT": """Applied Materials sells the equipment that makes chipmaking possible — deposition, etch, inspection — so it earns from everyone's fab expansion regardless of which chip designer wins. ~$395B market cap.

## Performance
Up ~188% over the past year to ~$497, sitting ~7% from its 52-week high (chip-equipment names are at records per current coverage). Net margin ~29%.

## Strengths
- Broadest equipment portfolio in the industry across every leading-edge inflection (gate-all-around, advanced packaging, HBM)
- 36 analysts at strong-buy, mean target ~$511; P/E ~47 trailing / ~31 forward is supported by a multi-year fab build-out pipeline

## Risks
- Wafer-fab equipment is famously cyclical — orders evaporate fast when utilization dips
- China export restrictions cap a historically large revenue pool
- At record highs, the stock is priced for the cycle to keep extending

## What to Watch
Earnings Aug 13, 2026; WFE spending forecasts and China revenue mix.""",

# ---------------------------------------------------- Networking & Optical
"NOK": """Nokia has quietly become an AI infrastructure story: its optical networks and IP routing businesses (bolstered by the Infinera acquisition) now ride data-center interconnect demand, not just telco capex. Still pays a ~1.2% dividend.

## Performance
Up ~154% over the past year and +70% in three months to ~$13.40 — a re-rating, not just a rally: trailing P/E ~84 but ~28 forward as AI-driven revenue flows through. Current coverage highlights "strong growth in AI revenues."

## Strengths
- Optical + routing portfolio aligned with data-center interconnect spend; Western-vendor status helps in a security-conscious market
- Low beta (~0.8) relative to the rest of this portfolio

## Risks
- Legacy mobile-networks business still shrinks and can mask the growth story in headline numbers
- Margin is thin (~4% net); the re-rating assumes mix shift continues
- European telco capex remains structurally weak

## What to Watch
Earnings Jul 23, 2026; Network Infrastructure segment growth and AI/DCI order commentary.""",

"AAOI": """Applied Optoelectronics makes the optical transceivers and lasers that connect AI data centers. It is this portfolio's single biggest winner — and now one of its most demanding valuations (~$14B market cap, up from under $1.5B a year ago).

## Performance
Up ~973% over the past year and +342% YTD to ~$175, though ~25% below the 52-week high. Trailing EPS is still negative; forward P/E ~37 assumes the ramp lands. Revenue +51% year over year.

## Strengths
- Direct exposure to 800G/1.6T optics demand from AI clusters; recent partnership expansion (QuantumLink deployment with a major cable operator)
- Forward estimates finally show profitability after years of losses

## Risks
- Mean analyst target (~$151) is below the price — even bulls' models have been outrun
- Beta ~3.7, the highest in this portfolio; drawdowns will be brutal
- Optics is competitive and historically low-margin; customer concentration is significant

## What to Watch
Earnings Aug 6, 2026: gross-margin progression and whether hyperscaler orders broaden beyond the current anchor customers.""",

"POET": """POET Technologies develops optical interposers and photonic engines for AI interconnect — earlier-stage and more speculative than AAOI, with most of the value resting on design wins converting to volume revenue. ~$1.9B market cap.

## Performance
Up ~164% over the past year and +48% in three months to ~$11, but ~47% below the 52-week high — the chart whipsaws (+73% one month, sharp give-backs the next, per current coverage). Revenue is growing fast (+200%) off a very small base; EPS remains negative.

## Strengths
- Differentiated packaging/photonics approach aimed straight at AI cluster bottlenecks
- Recent contract and funding news de-risks the near-term balance sheet

## Risks
- Execution risk is the stock: current coverage explicitly frames contracts-to-revenue conversion as the open question, and only one analyst formally covers it
- Likely future capital raises; pre-profit microcaps dilute
- Competing against far larger optics incumbents

## What to Watch
Earnings Aug 11, 2026; announced design wins moving to production orders, and cash runway.""",

# ---------------------------------------------------------- AI Infrastructure
"SMCI": """Super Micro builds AI servers and full racks, sitting between chip vendors and data-center operators. After its accounting scare era, it now trades like a show-me stock despite extraordinary growth. ~$17.6B market cap.

## Performance
Down ~32% over the past year to ~$29 and ~53% below the 52-week high, even with revenue growth of ~123%. That tension defines it: P/E ~15 trailing / ~9 forward — the cheapest multiples in this portfolio's AI group.

## Strengths
- Speed-to-market on new GPU platforms and liquid cooling keeps it on hyperscaler shortlists
- If trust normalizes, the multiple has unusual room relative to growth

## Risks
- Governance overhang lingers (auditor turmoil, delayed filings in 2024-25); consensus sits at hold
- Net margin ~3.7% — server assembly is structurally thin, and pricing competition from Dell/HPE is intensifying
- Revenue is hyperscaler-concentrated and lumpy

## What to Watch
Earnings Aug 4, 2026; gross-margin direction and any further clean filing history rebuilding credibility.""",

"HPE": """Hewlett Packard Enterprise is the establishment way to own AI servers: enterprise distribution, the Juniper networking acquisition, and a dividend (~1.2%) attached to a suddenly fast growth story.

## Performance
The portfolio's hottest large cap over the past quarter: +112% in three months and ~+150% over the year to ~$45.49 (helped by a well-received June 1 report), ~29% off its 52-week high. Revenue +40%.

## Strengths
- AI-systems backlog plus Juniper networking turn a value stock into a growth one; forward P/E ~11 is still undemanding
- 19 analysts at buy, mean target ~$63

## Risks
- Current coverage openly asks whether the move is exhausted ("too late after its 100% yearly surge?")
- Net margin ~4%: AI servers carry low margins, so mix shift can disappoint even with strong revenue
- Integration risk on the largest acquisition in its history

## What to Watch
Next report (Sep 2026 cycle); AI backlog-to-revenue conversion and networking segment margins.""",

"NBIS": """Nebius (the ex-Yandex international business) operates AI-focused GPU cloud infrastructure — one of the purest "neocloud" plays available. ~$54B market cap, up from obscurity in under two years.

## Performance
Up ~319% over the past year and +89% in three months to ~$212, ~24% off the 52-week high. Revenue growth ~684% off a small base. Reported margins (93%) are flattered by one-time items — the underlying business is still in heavy investment mode, which the ~586x forward P/E reflects honestly.

## Strengths
- Scarce pure-play exposure to AI compute demand outpacing hyperscaler supply
- Credible engineering pedigree and rapid capacity build-out

## Risks
- Priced for years of flawless growth; any GPU-supply glut or AI-capex pause compresses neocloud economics first
- Competes against AWS/Azure/Google and fellow neoclouds (CoreWeave et al.) simultaneously
- Capital intensity means recurring financing needs

## What to Watch
Earnings Aug 6, 2026; contracted-capacity announcements and gross-margin trajectory as clusters fill.""",

"IREN": """IREN pivoted from bitcoin mining to AI data centers — renewable-powered sites in Texas and Australia now host GPU compute. The market has rewarded the pivot with a ~$18B valuation and the portfolio's wildest ride (beta ~4.2).

## Performance
Up ~400% over the past year to ~$51.52, +23% in three months, ~33% below the 52-week high. Trailing P/E ~67; forward estimates are negative as the AI build-out front-loads costs — mining cash flows fund compute capex.

## Strengths
- Owns power and land — the scarcest inputs in AI infrastructure; the 800MW Australian campus targets APAC demand
- Energized-capacity announcements have repeatedly re-rated the stock

## Risks
- Highest beta in the portfolio; it trades with both crypto and AI sentiment
- Bitcoin-economics deterioration (halvings, hashprice) erodes the funding leg
- Neocloud competition for the same GPU contracts

## What to Watch
Earnings Aug 27, 2026; AI-contract wins (size, duration, counterparties) versus mining revenue mix.""",

# ----------------------------------------------------------- Software & Cloud
"MSFT": """Microsoft is the portfolio's out-of-favor megacap: cloud + Office + Copilot at a ~$3.0T cap, now trading at multiples (P/E ~24 trailing, ~20 forward) it hasn't seen in years, with a 0.9% dividend.

## Performance
Down ~16% over the past year and ~28% below its 52-week high (~$397) — the market rotated toward pure-play AI names (the financial press's new "MANGOS" acronym pointedly excludes Microsoft). Fundamentals didn't follow the stock down: revenue +18%, net margin ~39%.

## Strengths
- The widest analyst gap in this portfolio: 55 analysts at strong-buy with a ~$561 mean target, ~41% above the price
- Azure + enterprise AI distribution remains arguably the deepest moat in software

## Risks
- OpenAI relationship economics and competition are genuinely uncertain; AI capex is compressing free cash flow
- Geopolitical friction is real (Azure China job cuts in current headlines)
- "Cheap megacap" can stay cheap while momentum money lives elsewhere

## What to Watch
Earnings Jul 29, 2026; Azure growth rate and AI-revenue disclosure quality.""",

"NOW": """ServiceNow is enterprise workflow software caught in the market's "AI disrupts SaaS" repricing — cut roughly in half over a year while still growing 22% with ~13% net margins.

## Performance
Down ~47% over the past year to ~$106 (split-adjusted), ~50% below the 52-week high; -8% over three months. The multiple compressed from a famous premium to ~21x forward earnings — cheaper than Microsoft on some measures, which would have been unthinkable in 2024.

## Strengths
- 44 analysts still at strong-buy with a ~$142 mean target (+34%); seat-based-pricing fears haven't shown up in reported growth yet
- Workflow position is sticky and agentic-AI could plausibly run *through* it rather than around it

## Risks
- The bear case — AI agents replacing per-seat enterprise software — is structural, not cyclical, and won't resolve in one quarter
- Sector-wide: peers trade near 52-week lows, so sentiment recovery may need the whole group
- Trailing P/E ~63 still embeds meaningful growth

## What to Watch
Earnings Jul 22, 2026; cRPO growth and AI (Pro Plus) attach rates — the direct evidence in the disruption debate.""",

"IBM": """IBM is the portfolio's ballast: consulting + mainframe + Red Hat software, a 2.4% dividend, beta 0.67, and credible quantum-computing optionality. ~$256B market cap.

## Performance
Roughly flat (-3%) over the past year at ~$272, ~18% off its high, +9% over three months — exactly the low-drama profile the rest of this portfolio lacks. P/E ~24 trailing / ~20 forward, net margin ~16%, revenue +9.5%.

## Strengths
- Steady free cash flow funds the dividend with room to spare; software mix keeps improving
- Quantum roadmap is among the field's most credible (current commentary favors it over pure-play quantum names), offering upside without venture-style risk

## Risks
- Growth is modest; in a momentum market it lags badly (and has)
- Consulting is macro-sensitive; AI could deflate billable hours faster than it adds bookings
- Decades of "transformation" skepticism keeps the multiple capped

## What to Watch
Earnings Jul 22, 2026; software growth rate and any quantum commercialization milestones.""",

# ------------------------------------------------------- Consumer & Fintech
"TTWO": """Take-Two owns Rockstar (Grand Theft Auto) and 2K — and the investment case is overwhelmingly one catalyst: GTA VI, slated for late 2026. ~$39B market cap.

## Performance
Down ~10% over the past year to ~$210, -0.3% over three months, ~21% off its high — coiled rather than trending while losses continue (trailing EPS -$1.61). Forward P/E ~21 assumes the release-year earnings surge; current coverage notes the company may be "less than a year from profitability."

## Strengths
- GTA VI is plausibly the largest entertainment launch in history, with a decade of GTA Online-style recurrent revenue behind it
- 29 analysts at strong-buy, mean target ~$279 (+33%)

## Risks
- Single-title concentration: another delay would hit hard (the franchise has slipped before)
- Recurrent spending across the rest of the catalog is soft (-4.5% net margin currently)
- Post-launch "sell the news" dynamics are common in games

## What to Watch
Earnings Aug 10, 2026, and any date confirmation/marketing beats for GTA VI.""",

"HOOD": """Robinhood has evolved from meme-trading app to diversified retail-finance platform — options, crypto, retirement accounts, and now prediction markets, which current coverage calls a "hot, lucrative market" entry. ~$78B market cap.

## Performance
Up ~15% over the past year but down ~25% YTD to ~$86, ~44% below the 52-week high — a sharp reversal after 2025's run. Profitability is real: ~41% net margin, P/E ~42 trailing / ~31 forward. A $35M insider purchase made headlines this week (with caveats, per the coverage).

## Strengths
- Monetization per user keeps broadening (Gold subscriptions, prediction markets, crypto)
- 24 analysts at buy, mean target ~$100

## Risks
- Revenue remains transaction-driven and sentiment-cyclical — engagement falls with risk appetite
- Crypto and payment-for-order-flow regulation are recurring overhangs
- Beta ~2.3 on top of customer-behavior cyclicality

## What to Watch
Earnings Jul 29, 2026; prediction-market traction and net deposit trends.""",

"BYND": """Beyond Meat is the portfolio's distressed name: the plant-based meat category shrank instead of growing, and the stock trades at $0.68 with a ~$352M market cap and an attempted pivot into protein drinks.

## Performance
Down ~80% over the past year and ~91% below the 52-week high; revenue declining (-15%). Consensus is underperform with a $0.70 target — the rare case where analysts target the current price. Current coverage asks whether the protein-drink pivot "changes the thesis."

## Strengths
- Brand recognition in its category remains real, and the new-product pivot is a genuine strategy change
- Prior debt restructuring bought time

## Risks
- Sub-$1 share price introduces exchange-compliance and reverse-split scenarios
- Cash burn against shrinking revenue is the core problem a pivot must outrun
- Category headwinds look structural (price, taste perception, GLP-1 era protein preferences)

## What to Watch
Earnings Aug 5, 2026; protein-drink distribution wins and cash runway disclosure.""",

# ----------------------------------------------------------- Frontier Tech
"RGTI": """Rigetti builds superconducting quantum computers — a real lab business with a venture-style stock. ~$6.5B market cap on revenue that remains tiny (growth +199% off a very small base).

## Performance
Up ~55% over the past year to ~$19.44 but ~67% below its 52-week high after the quantum-hype cycle deflated; +15% over three months as the group stabilized. EPS -$0.89; no formal consensus rating, mean target ~$29.

## Strengths
- Credible technical roadmap (chiplet-scaling approach) and government/research contracts
- Sector momentum returns in waves; current coverage flags dip-buying interest in quantum names

## Risks
- Commercial quantum advantage is likely years away; until then revenue is grants and prototypes
- Dilution funds the science — share count grows every year
- Competing against IBM/Google-scale balance sheets and trapped-ion rivals

## What to Watch
Qubit-fidelity/logical-qubit milestones and contract awards; next report (Aug 2026 cycle) for cash runway.""",

"ASTS": """AST SpaceMobile is building a satellite constellation that connects ordinary smartphones directly from orbit, partnered with major carriers. ~$34B market cap — remarkable for a pre-profit company, which is the whole debate.

## Performance
Up ~139% over the past year to ~$87 but ~35% below the 52-week high and flat over three months. Revenue is finally appearing (+1,952% off a near-zero base); losses continue (EPS -$1.80). Consensus sits at hold with a ~$81 target — below the price. SpaceX-IPO chatter currently dominates the space-sector narrative.

## Strengths
- Carrier partnerships (AT&T, Verizon, Vodafone lineage) validate the direct-to-device thesis
- First-mover scale in a market with genuine physics and spectrum moats

## Risks
- Launch cadence, satellite economics and capital needs are all still being proven; dilution is recurring
- SpaceX/Starlink direct-to-cell is a formidable competitor with its own launch capacity
- Beta ~2.6 with retail-heavy ownership

## What to Watch
Earnings Aug 10, 2026; satellites launched vs. plan and commercial-service revenue ramp.""",

"OKLO": """Oklo develops compact fast-fission reactors and aims to sell power (not reactors) to data centers and defense sites — the portfolio's nuclear-renaissance bet, now in its hangover phase.

## Performance
Down ~31% YTD and ~72% below its 52-week high at ~$54 (~$9.4B cap) — current coverage notes the stock is down ~42% over six months. Pre-revenue: forward EPS remains negative. 19 analysts still skew positive (mean target ~$89), but targets set during the hype look stale.

## Strengths
- Real catalysts: the ARMEC manufacturing acquisition (announced this week) and a new fuel program address the two hardest SMR bottlenecks — build capacity and fuel supply
- Data-center power demand gives SMRs their first credible commercial anchor

## Risks
- NRC licensing timelines are measured in years; first revenue is late-decade in most scenarios
- Pre-revenue valuations re-rate violently with rate and sentiment shifts (already demonstrated)
- Fuel (HALEU) supply chain remains a genuine constraint

## What to Watch
Earnings Aug 10, 2026 (cash runway); licensing milestones and any signed power-purchase agreements.""",

# ---------------------------------------------------------------- Biotech
"ARTV": """Artiva Biotherapeutics develops off-the-shelf NK-cell therapies, and has pivoted its lead program toward autoimmune disease — rheumatoid arthritis — with efficacy data expected in H1 per recent conference commentary. ~$360M market cap.

## Performance
Up ~279% over the past year but ~49% below its 52-week high at ~$7.42 — classic biotech: the move happens around data, not quarters. Cash burn is real (EPS -$3.55). Five analysts at strong-buy with a ~$36 mean target, typical of microcap-biotech optimism (treat wide targets skeptically).

## Strengths
- NK-cells-for-autoimmune is one of biotech's hotter mechanisms, and the RA pivot targets a vast market
- Clear, dated catalyst (H1 2026 efficacy update) creates a definable event path

## Risks
- Binary data risk — a miss likely retraces most of the year's gain
- Will need financing; dilution around data events is standard
- Earlier-stage than CAR-T autoimmune competitors

## What to Watch
The H1 2026 RA efficacy readout and any partnership announcements around it.""",

"IKT": """Inhibikase Therapeutics is a ~$219M clinical-stage biotech focused on kinase inhibitors — historically Parkinson's (risvodetinib), with a pulmonary arterial hypertension program advancing. Seven analysts at strong-buy with a $6 mean target against a $1.66 price — a gap that says more about microcap coverage than certainty.

## Performance
Down ~15% over the past year and -18% over three months; ~27% below the 52-week high. Pre-revenue with modest burn (EPS -$0.44); last reported May 12, 2026.

## Strengths
- Multiple shots on goal from one chemistry platform; PAH program addresses a concrete, well-defined market
- Institutional ownership (~29%) is meaningful for a microcap

## Risks
- Clinical-stage binary risk with limited cash generation — financing/dilution cycles are the operating model
- Thin liquidity exaggerates both directions
- Long timelines: neuro and PAH trials read out slowly

## What to Watch
Trial enrollment/readout guidance on the PAH program and cash-runway updates at the next quarterly report.""",

"PRQR": """ProQR Therapeutics is an RNA-editing platform company (Axiomer) with an Eli Lilly partnership — a technology bet where the platform's validation matters more than any single asset. ~$149M market cap at $1.41.

## Performance
Down ~23% over the past year, -22% over three months, ~55% below the 52-week high. Analysts just cut this year's revenue estimates by ~22% per current coverage (milestone-timing driven). Eight analysts still at strong-buy with a ~$8.9 mean target — again, wide microcap targets warrant skepticism. Beta ~0.06: it trades on its own news, not the market.

## Strengths
- RNA editing is a frontier modality and the Lilly collaboration provides validation plus non-dilutive funding
- Platform deals can re-rate the whole company on a single announcement

## Risks
- Pre-revenue with estimate cuts already this year; cash runway is the perpetual question
- Editing-platform competition (Wave, Korro, beam-adjacent players) is intense
- Partner-dependent: Lilly's priorities dictate the pace

## What to Watch
First-in-human data timing for lead Axiomer programs and any expansion of the Lilly collaboration.""",

# ---------------------------------------------------------------- Cannabis
"CGC": """Canopy Growth was once cannabis' flagship; at $1.01 (~$454M cap) it now trades on survival mechanics and US-reform optionality. A just-announced acquisition has coverage split — "game-changer or another misstep."

## Performance
Down ~36% over the past year and ~58% below the 52-week high; revenue is flat (-0.3%) with deeply negative margins (-117%). Only one analyst still formally covers it (hold, ~$1.76 target).

## Strengths
- Constellation Brands lineage left it with recognizable brands and a US-entry structure (Canopy USA) ready if federal reform lands
- Cannabis sentiment turns violently on any rescheduling headline — optionality cuts both ways

## Risks
- $1 share price puts exchange-compliance and reverse-split scenarios on the table
- Cash burn with shrinking coverage and financing options
- Canadian market remains oversupplied and price-compressed

## What to Watch
Earnings Jun 15, 2026 (days away); integration detail on the acquisition and any US scheduling movement.""",

"TLRY": """Tilray has diversified from Canadian cannabis into beverages (US craft beer, now BrewDog distribution in the UK) and European medical cannabis — funding deals with new shares along the way. ~$600M cap at $5.02 (post reverse split).

## Performance
Down ~48% YTD and ~78% below the 52-week high, though +17% over the trailing year reflects the reverse-split-era volatility. Trailing EPS (-$14.63) is distorted by impairments; forward P/E ~19 implies analysts expect a swing toward profitability — treat that with caution.

## Strengths
- Beverage segment provides real, legal, US revenue that pure cannabis peers lack; German/European medical momentum is genuine
- Recent deals (Lyphe buyout) extend the international footprint

## Risks
- Persistent share issuance funds M&A — dilution is explicit in current headlines (1.6M new shares for the latest deal)
- Core cannabis margins remain negative (-157% net)
- US federal reform timing is unknowable and priced in repeatedly

## What to Watch
Next report (Jul 2026 cycle); beverage-segment margins and whether German medical growth offsets Canadian price compression.""",

# ---------------------------------------------------------------- Materials
"MP": """MP Materials operates Mountain Pass, the only scaled US rare-earth mine, and is integrating downstream into magnets — a strategic-asset story (DoD and Apple agreements in 2025) wrapped in a commodity stock. ~$9.5B market cap.

## Performance
Up ~108% over the past year to ~$53.45 but ~47% below the 52-week high and -14% over three months — the strategic-premium spike has been digesting. Revenue +119% as the magnetics segment ramps (current coverage calls Q1'26 magnetics revenue a potential "turning point"); net margin still negative (-20%) during the build-out.

## Strengths
- Genuine scarcity: Western rare-earth supply chains start here, and policy tailwinds are bipartisan
- Magnet offtake agreements convert commodity exposure into contracted manufacturing revenue
- 16 analysts at strong-buy, mean target ~$80

## Risks
- Rare-earth pricing (set largely by China) still drives the upstream P&L; China can flood or starve the market strategically
- Forward P/E ~46 already prices substantial magnet-segment success
- Single-asset operational risk at Mountain Pass

## What to Watch
Earnings Aug 6, 2026; magnetics utilization/margins and any new government or OEM offtake announcements.""",
}


def main() -> None:
    timestamp = date.today().isoformat()
    db.init()
    missing, done = [], 0
    for ticker, md in ANALYSES.items():
        if db.set_analysis(ticker, md.strip(), timestamp):
            done += 1
        else:
            missing.append(ticker)
    print(f"Seeded {done} analyses ({timestamp}).")
    if missing:
        print("Not in DB (skipped):", ", ".join(missing))
    tracked = set(db.all_tickers())
    unwritten = tracked - set(ANALYSES)
    if unwritten:
        print("Tracked but without analysis:", ", ".join(sorted(unwritten)))


if __name__ == "__main__":
    main()
