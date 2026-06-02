"""Smoke tests for the compliance pipeline — no external dependencies required."""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from vapor_compliance.normalization.text_cleaner import clean, tokenize
from vapor_compliance.normalization.unit_normalizer import normalize_nicotine, normalize_volume, extract_all
from vapor_compliance.normalization.abbreviation_expander import expander
from vapor_compliance.normalization.normalizer import Normalizer
from vapor_compliance.normalization.llm_extractor import SKUExtractionResult
from vapor_compliance.normalization.extraction_validator import ExtractionValidator
from vapor_compliance.models.sku import RawSKU, CanonicalProduct
from vapor_compliance.models.match import MatchStage
from vapor_compliance.matching.exact_matcher import ExactMatcher
from vapor_compliance.matching.fuzzy_matcher import FuzzyMatcher
from vapor_compliance.matching.tfidf_matcher import TFIDFMatcher
from vapor_compliance.matching.bm25_matcher import BM25Matcher
from vapor_compliance.matching.semantic_ensemble import SemanticEnsemble
from vapor_compliance.registry.cpg_registry import CPGRegistry
from vapor_compliance.registry.fda_registry import FDARegistry
from vapor_compliance.registry.state_registry import StateRegistry
from vapor_compliance.pipeline.orchestrator import CompliancePipeline
from vapor_compliance.config import settings


DATA = Path(__file__).parent.parent / "data"


def _sample_products() -> list[CanonicalProduct]:
    return [
        CanonicalProduct(cpg_id="CP0001", canonical_name="JUUL Virginia Tobacco 50mg Pod",
                         brand="JUUL", manufacturer="JUUL Labs", product_type="POD",
                         flavor_canonical="Virginia Tobacco", nicotine_mg_ml=50.0,
                         form_factor="POD", source="test"),
        CanonicalProduct(cpg_id="CP0004", canonical_name="NJOY ACE Virginia Tobacco 50mg Pod",
                         brand="NJOY", manufacturer="NJOY LLC", product_type="POD",
                         flavor_canonical="Virginia Tobacco", nicotine_mg_ml=50.0,
                         form_factor="POD", source="test"),
        CanonicalProduct(cpg_id="CP0002", canonical_name="JUUL Menthol 50mg Pod",
                         brand="JUUL", manufacturer="JUUL Labs", product_type="POD",
                         flavor_canonical="Menthol", nicotine_mg_ml=50.0,
                         form_factor="POD", source="test"),
    ]


def test_text_cleaner():
    assert clean("  JOOL  VT  5%!  ") == "jool vt 5%"
    assert "jool" in tokenize("JOOL VT 5%")
    print("✓ text_cleaner")


def test_unit_normalizer():
    assert normalize_nicotine("5%") == 50.0
    assert normalize_nicotine("50mg/ml") == 50.0
    assert normalize_nicotine("50mg") == 50.0
    assert normalize_nicotine("3%") == 30.0
    assert normalize_volume("13ml") == 13.0
    units = extract_all("JUUL Virginia Tobacco Pod 5%")
    assert units["nicotine_mg_ml"] == 50.0
    assert units["product_type"] == "POD"
    print("✓ unit_normalizer")


def test_abbreviation_expander():
    expanded, exps = expander.expand_text("jool vt pod 5%")
    assert "JUUL" in expanded or "juul" in expanded.lower() or "Virginia Tobacco" in expanded
    brand = expander.resolve_brand("JUUL")
    assert brand == "JUUL"
    flavor, cat = expander.resolve_flavor("Virginia Tobacco")
    assert flavor == "Virginia Tobacco"
    assert cat == "TOBACCO"
    assert expander.is_flavor_banned("Menthol", "CA") is True
    assert expander.is_flavor_banned("Virginia Tobacco", "CA") is False
    print("✓ abbreviation_expander")


def test_normalizer():
    norm = Normalizer()
    raw = RawSKU(sku_id="TEST_001", source="POS", raw_name="JOOL VT POD 5%")
    # use_llm=False forces dictionary path so test works without API key
    result = norm.normalize(raw, use_llm=False)
    assert result.raw_sku_id == "TEST_001"
    assert result.nicotine_mg_ml == 50.0
    assert result.product_type == "POD"
    print(f"✓ normalizer (dict path) → normalized_name='{result.normalized_name}', confidence={result.normalization_confidence}")


def test_extraction_validator():
    """
    Test the dictionary validation layer against simulated LLM outputs.
    No API call needed — we construct SKUExtractionResult directly.
    """
    v = ExtractionValidator()

    # ── Case 1: LLM output is correct and confirmed by dict ───────────────────
    ext1 = SKUExtractionResult(
        raw_name="JOOL VT POD 5%",
        brand="JUUL",
        flavor_raw="Virginia Tobacco",
        flavor_canonical="Virginia Tobacco",
        nicotine_strength_raw="5%",
        nicotine_mg_ml=50.0,
        form_factor="Pod",
        normalized_sku="JUUL Virginia Tobacco 50mg Pod",
        abbreviations_expanded={"JOOL": "JUUL", "VT": "Virginia Tobacco"},
        extraction_confidence=0.95,
    )
    r1 = v.validate(ext1)
    assert r1.brand == "JUUL"
    assert r1.flavor_canonical == "Virginia Tobacco"
    assert r1.nicotine_mg_ml == 50.0
    corrections = [f for f in r1.flags if f.startswith("CORRECTION")]
    assert len(corrections) == 0, f"Unexpected corrections: {corrections}"
    print(f"\n✓ validator case1 — confirmed: conf={r1.overall_confidence:.2f} flags={r1.flags}")

    # ── Case 2: LLM got nicotine conversion wrong ─────────────────────────────
    ext2 = SKUExtractionResult(
        raw_name="NJOY ACE 5% Pod",
        brand="NJOY",
        flavor_raw="Virginia Tobacco",
        flavor_canonical="Virginia Tobacco",
        nicotine_strength_raw="5%",
        nicotine_mg_ml=5.0,          # LLM forgot ×10 conversion
        form_factor="POD",
        normalized_sku="NJOY Virginia Tobacco 5mg Pod",
        abbreviations_expanded={},
        extraction_confidence=0.88,
    )
    r2 = v.validate(ext2)
    assert r2.nicotine_mg_ml == 50.0, f"Expected 50.0 after correction, got {r2.nicotine_mg_ml}"
    assert any("CORRECTION:nicotine" in f for f in r2.flags)
    print(f"✓ validator case2 — nicotine corrected 5→50: flags={r2.flags}")

    # ── Case 3: LLM expanded abbreviation differently from dictionary ──────────
    ext3 = SKUExtractionResult(
        raw_name="JOOL VT POD 5%",
        brand="JUUL",
        flavor_raw="Vermont Tobacco",
        flavor_canonical="Vermont Tobacco",   # LLM over-expanded VT → Vermont
        nicotine_strength_raw="5%",
        nicotine_mg_ml=50.0,
        form_factor="POD",
        normalized_sku="JUUL Vermont Tobacco 50mg Pod",
        abbreviations_expanded={"VT": "Vermont Tobacco"},   # dict says Virginia
        extraction_confidence=0.82,
    )
    r3 = v.validate(ext3)
    abbr_correction = [f for f in r3.flags if "abbreviation" in f and "CORRECTION" in f]
    assert len(abbr_correction) > 0, f"Expected abbreviation correction, flags: {r3.flags}"
    assert r3.abbreviations_expanded.get("VT") == "Virginia Tobacco"
    print(f"✓ validator case3 — abbr VT→Vermont corrected to Virginia Tobacco: flags={r3.flags}")

    # ── Case 4: Novel brand not in dictionary ─────────────────────────────────
    ext4 = SKUExtractionResult(
        raw_name="VAPEX Pro Mango Ice 5%",
        brand="VAPEX",                        # new brand, not in dict
        flavor_raw="Mango Ice",
        flavor_canonical="Mango",
        nicotine_strength_raw="5%",
        nicotine_mg_ml=50.0,
        form_factor="DISPOSABLE",
        normalized_sku="VAPEX Mango 50mg Disposable",
        abbreviations_expanded={},
        extraction_confidence=0.78,
    )
    r4 = v.validate(ext4)
    assert r4.brand == "VAPEX"                # kept, not in dict
    assert any("UNVERIFIED:brand" in f for f in r4.flags)
    print(f"✓ validator case4 — novel brand kept with flag: flags={r4.flags}")

    # ── Summary table ─────────────────────────────────────────────────────────
    print("\n  Validation summary:")
    for label, r in [("confirmed", r1), ("nic_corrected", r2), ("abbr_corrected", r3), ("novel_brand", r4)]:
        print(
            f"  {label:<18} llm={r.llm_confidence:.2f}  "
            f"val={r.validation_confidence:.2f}  "
            f"overall={r.overall_confidence:.2f}  "
            f"needs_review={r.needs_review}"
        )


def test_exact_matcher():
    products = _sample_products()
    matcher = ExactMatcher()
    matcher.build_index(products)

    raw = RawSKU(sku_id="T1", source="POS", raw_name="JUUL Virginia Tobacco 50mg Pod")
    norm = Normalizer().normalize(raw)
    norm.brand = "JUUL"
    norm.flavor_canonical = "Virginia Tobacco"
    norm.nicotine_mg_ml = 50.0
    norm.form_factor = "POD"

    result = matcher.match(norm)
    assert result is not None
    assert result.matched_cpg_id == "CP0001"
    assert result.confidence == 1.0
    print("✓ exact_matcher")


def test_fuzzy_matcher():
    products = _sample_products()
    matcher = FuzzyMatcher()
    matcher.build_index(products)

    raw = RawSKU(sku_id="T2", source="POS", raw_name="NJOY ACE VA Tobacco 5%")
    norm = Normalizer().normalize(raw)

    result = matcher.match(norm, threshold=0.30)
    assert result is not None
    print(f"✓ fuzzy_matcher → {result.matched_cpg_id} confidence={result.confidence}")


def test_tfidf_matcher():
    products = _sample_products()
    matcher = TFIDFMatcher()
    matcher.build_index(products)

    raw = RawSKU(sku_id="T3", source="POS", raw_name="JUUL Menthol Pod")
    norm = Normalizer().normalize(raw)

    result = matcher.match(norm, threshold=0.10)
    assert result is not None
    print(f"✓ tfidf_matcher → {result.matched_cpg_id} confidence={result.confidence}")


def test_bm25_matcher():
    products = _sample_products()
    matcher = BM25Matcher()
    matcher.build_index(products)

    raw = RawSKU(sku_id="T4", source="POS", raw_name="NJOY Virginia Tobacco")
    norm = Normalizer().normalize(raw)

    result = matcher.match(norm, threshold=0.10)
    assert result is not None
    print(f"✓ bm25_matcher → {result.matched_cpg_id} confidence={result.confidence}")


def test_semantic_ensemble():
    """
    Stage 3 runs TF-IDF + BM25 + Embedding in parallel.
    Verify the result carries per-method predictions and an agreement label.
    """
    products = _sample_products()

    # Lower thresholds so all methods produce results in this small test corpus
    settings.TFIDF_THRESHOLD = 0.10
    settings.BM25_THRESHOLD = 0.10
    settings.EMBEDDING_THRESHOLD = 0.10

    ensemble = SemanticEnsemble()
    ensemble.build_index(products)

    raw = RawSKU(sku_id="T5", source="POS", raw_name="JUUL Virginia Tobacco 5%")
    norm = Normalizer().normalize(raw)

    result = ensemble.match(norm)
    assert result is not None, "Ensemble should return a result"

    print(f"\n✓ semantic_ensemble")
    print(f"  match_stage      : {result.match_stage}")
    print(f"  matched_cpg_id   : {result.matched_cpg_id}")
    print(f"  confidence       : {result.confidence}")
    print(f"  agreement        : {result.stage3_agreement}")
    print(f"  stage3_predictions:")
    for method, pred in result.stage3_predictions.items():
        status = "✓" if pred.predicted else "✗"
        print(f"    {status} {method:12s} → cpg={pred.cpg_id or 'miss':10s}  conf={pred.confidence:.3f}")
    print(f"  explanation: {result.match_explanation}")

    assert result.stage3_agreement in ("all_agree", "majority", "split", "single")
    assert len(result.stage3_predictions) > 0


def test_flavor_extractor():
    """
    Validate residual token method on real-world tricky SKU patterns.
    Each case has flavor at a different position and with different noise.
    """
    from vapor_compliance.normalization.flavor_extractor import flavor_extractor

    cases = [
        # (raw_name, brand, expected_canonical, description)
        ("JUUL Virginia Tobacco 5%",              "JUUL",     "Virginia Tobacco", "brand flavor nic — standard"),
        ("NJOY ACE Virginia Tobacco 50mg Pod",    "NJOY",     "Virginia Tobacco", "brand + product-line before flavor"),
        ("ELF BAR BC5000 Watermelon Ice 50mg",    "ELF BAR",  "Watermelon",       "brand + model-code before flavor"),
        ("GEEK BAR Pulse 15000 Strawberry Ice 5%","GEEK BAR", "Strawberry",       "brand + line + puff-count before flavor"),
        ("HYDE Edge Recharge Rainbow Candy 5%",   "HYDE",     None,               "novel flavor not in dict — ok to miss"),
        ("VUSE Alto Original Tobacco 50mg Pod",   "VUSE",     "Virginia Tobacco", "Original Tobacco → Virginia Tobacco"),
        ("LOST VAPE Orion Bar 10000 Mango Ice 5%","LOST VAPE","Mango",            "long model name + puff count"),
        ("FUME EXTRA Mint 5%",                    "FUME",     "Menthol",          "product-line Extra, Mint → Menthol"),
        ("BLU Menthol 24mg Cartridge",            "BLU",      "Menthol",          "flavor immediately after brand"),
        ("PUFF BAR Blue Razz 5%",                 None,       None,               "unknown brand + unknown flavor"),
    ]

    print("\n✓ flavor_extractor — residual token method")
    print(f"  {'SKU':<45} {'Expected':<20} {'Got':<20} {'Conf':>5}  {'Strip Method'}")
    print("  " + "─" * 115)

    for raw_name, brand, expected, desc in cases:
        canonical, category, span, conf = flavor_extractor.extract(raw_name, brand=brand)
        match_sym = "✓" if canonical == expected else ("~" if canonical and expected and expected in (canonical or "") else "✗")
        if expected is None:
            match_sym = "–"
        print(
            f"  {match_sym} {raw_name:<43} "
            f"exp={str(expected):<18} "
            f"got={str(canonical):<18} "
            f"conf={conf:.2f}  [{desc}]"
        )

    # Hard assertions on cases where we must get it right
    c, _, _, _ = flavor_extractor.extract("NJOY ACE Virginia Tobacco 50mg Pod", brand="NJOY")
    assert c == "Virginia Tobacco", f"Expected Virginia Tobacco, got {c}"

    c, _, _, _ = flavor_extractor.extract("ELF BAR BC5000 Watermelon Ice 50mg", brand="ELF BAR")
    assert c == "Watermelon", f"Expected Watermelon, got {c}"

    c, _, _, _ = flavor_extractor.extract("GEEK BAR Pulse 15000 Strawberry Ice 5%", brand="GEEK BAR")
    assert c == "Strawberry", f"Expected Strawberry, got {c}"

    c, _, _, _ = flavor_extractor.extract("JUUL Virginia Tobacco 5%", brand="JUUL")
    assert c == "Virginia Tobacco", f"Expected Virginia Tobacco, got {c}"

    c, _, _, _ = flavor_extractor.extract("BLU Menthol 24mg Cartridge", brand="BLU")
    assert c == "Menthol", f"Expected Menthol, got {c}"

    print("\n  Debug trace for ELF BAR BC5000 Watermelon Ice 50mg:")
    dbg = flavor_extractor.debug("ELF BAR BC5000 Watermelon Ice 50mg", brand="ELF BAR")
    print(f"    tokens    : {dbg['tokens']}")
    print(f"    stripped  : {dbg['stripped']}")
    print(f"    residual  : {dbg['residual']}")
    print(f"    matched   : '{dbg['matched_span']}' → {dbg['canonical_flavor']} (conf={dbg['confidence']:.2f})")


def test_full_pipeline():
    cpg = CPGRegistry()
    cpg.load_from_csv(DATA / "sample_cpg.csv")

    fda = FDARegistry()
    fda.load_from_csv(DATA / "sample_fda.csv")

    state = StateRegistry()
    state.load_from_csv(DATA / "sample_state.csv")

    pipeline = CompliancePipeline(cpg, fda, state)
    pipeline.startup()

    df = pipeline.process_from_csv(DATA / "sample_pos.csv", source="POS", target_states=["TX", "CA", "NY"])
    print(f"✓ full_pipeline → {len(df)} records processed")
    print(df[["sku_id", "state", "compliance_status", "confidence", "match_method"]].to_string())

    summary = pipeline.get_summary(df)
    print(f"\nSummary: {summary['by_status']}")


if __name__ == "__main__":
    test_text_cleaner()
    test_unit_normalizer()
    test_abbreviation_expander()
    test_flavor_extractor()
    test_extraction_validator()
    test_normalizer()
    test_exact_matcher()
    test_fuzzy_matcher()
    test_tfidf_matcher()
    test_bm25_matcher()
    test_semantic_ensemble()
    test_full_pipeline()
    print("\n✓ All tests passed")
