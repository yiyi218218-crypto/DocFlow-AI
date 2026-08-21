"""Model bench: extraction quality vs. cost, on real documents.

Answers the question that actually decides the model choice for this product:
**does the model reproduce a verbatim quote?** A model that paraphrases its
`source_quote` fails span alignment, which demotes the fact, which removes the
highlight — so quote fidelity *is* citation accuracy here, and it is not what
generic benchmarks measure.

Reports per model:
  * citation verification rate (share of facts aligning at >= 95)
  * facts found
  * deadline correctness against a small hand-written key
  * real cost from the usage ledger

Run:  uv run python -m docflow.eval.model_bench
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field

import pymupdf

from docflow.ai.extraction.extractor import extract_document
from docflow.ai.llm import client as llm_client
from docflow.core.config import settings
from docflow.documents.pdf import parse_pdf
from docflow.models.enums import CitationStatus, FactKind

# Small hand-written key. Honest about its size: this is a model-selection
# smoke bench, not the labelled evaluation set described in docs/20-ai.md §19.
CASES: list[tuple[str, str, str]] = [
    (
        "du-circular",
        """University of Dhaka
Office of the Registrar

ADMISSION CIRCULAR 2026-2027
Published on 1 August 2026

Applications for the 2026-2027 undergraduate session must be submitted by
15 September 2026 at 5:00 PM.

Required documents
Applicants must submit an attested copy of the academic transcript.
Applicants must also provide a photocopy of the National ID card.
Two passport-size photographs are required with the application form.

Late submissions will not be considered under any circumstances.""",
        "2026-09-15",
    ),
    (
        "scholarship",
        """Ministry of Education

PRIME MINISTER'S SCHOLARSHIP PROGRAMME 2026
Published on 12 August 2026

The online application portal closes on 30 September 2026.

Applicants must submit a certified copy of the most recent academic transcript.
Applicants must provide an income certificate issued by the Union Parishad.
A recommendation letter from the head of the institution is required.""",
        "2026-09-30",
    ),
    (
        "bangla-notice",
        """ঢাকা বিশ্ববিদ্যালয়
ভর্তি বিজ্ঞপ্তি ২০২৬

আবেদনের শেষ তারিখ ১৫ সেপ্টেম্বর ২০২৬।

প্রয়োজনীয় কাগজপত্র
শিক্ষার্থীদের অবশ্যই একাডেমিক ট্রান্সক্রিপ্ট জমা দিতে হবে।
জাতীয় পরিচয়পত্রের ফটোকপি প্রয়োজন।""",
        "2026-09-15",
    ),
]


@dataclass
class ModelResult:
    model: str
    facts: int = 0
    verified: int = 0
    partial: int = 0
    unverified: int = 0
    deadlines_found: int = 0
    deadlines_correct: int = 0
    cost_usd: float = 0.0
    errors: list[str] = field(default_factory=list)

    @property
    def verification_rate(self) -> float:
        return self.verified / self.facts if self.facts else 0.0


def build_pdf(body: str) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_textbox(
        pymupdf.Rect(50, 55, 545, 790), body, fontsize=10.5, lineheight=1.4, fontname="helv"
    )
    data = doc.tobytes()
    doc.close()
    return data


async def bench_model(model: str) -> ModelResult:
    result = ModelResult(model=model)

    # Point every tier at the model under test, then reset the provider cache so
    # the change takes effect.
    for attr in ("llm_tier_fast", "llm_tier_balanced", "llm_tier_deep"):
        object.__setattr__(settings, attr, f"openai:{model}")
    llm_client.reset_providers()

    for name, body, expected_due in CASES:
        try:
            parsed = parse_pdf(build_pdf(body))
            extraction = await extract_document(parsed)
        except Exception as exc:
            result.errors.append(f"{name}: {type(exc).__name__}")
            continue

        result.cost_usd += extraction.total_cost_usd
        for fact in extraction.facts:
            result.facts += 1
            if fact.citation_status is CitationStatus.VERIFIED:
                result.verified += 1
            elif fact.citation_status is CitationStatus.PARTIAL:
                result.partial += 1
            else:
                result.unverified += 1

            if fact.kind is FactKind.DEADLINE:
                result.deadlines_found += 1
                if fact.due_at and fact.due_at.date().isoformat() == expected_due:
                    result.deadlines_correct += 1

    return result


async def main() -> None:
    from docflow.core.logging import configure_logging

    configure_logging(level="WARNING", json_output=False, service="bench")

    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY is required for the model bench.")

    models = os.environ.get(
        "BENCH_MODELS", "gpt-4o-mini,gpt-4.1-nano,gpt-5-nano,gpt-4.1-mini"
    ).split(",")

    print(f"\n  {len(CASES)} documents · {len(models)} models\n")
    header = f"  {'model':<14} {'facts':>5} {'verified':>9} {'partial':>8} {'unver':>6} {'deadline✓':>10} {'cost $':>9}"
    print(header)
    print("  " + "-" * (len(header) - 2))

    results: list[ModelResult] = []
    for model in models:
        r = await bench_model(model.strip())
        results.append(r)
        rate = f"{r.verification_rate:.0%}"
        deadline = f"{r.deadlines_correct}/{len(CASES)}"
        print(
            f"  {r.model:<14} {r.facts:>5} {rate:>9} {r.partial:>8} "
            f"{r.unverified:>6} {deadline:>10} {r.cost_usd:>9.5f}"
        )
        if r.errors:
            print(f"      errors: {'; '.join(r.errors)}")

    print()
    best = max(results, key=lambda r: (r.verification_rate, r.deadlines_correct, -r.cost_usd))
    print(f"  Best quote fidelity + deadline accuracy per dollar: {best.model}\n")


if __name__ == "__main__":
    asyncio.run(main())
