"""Smoke tests for the compliance pipeline — no external dependencies required."""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from vapor_compliance.normalization.text_cleaner import clean, tokenize
from vapor_compliance.normalization.unit_normalizer import normalize_nicotine, normalize_volume, extract_all
from vapor_compliance.normalization.abbreviation_expander import expander
from vapor_compliance.normalization.normalizer import Normalizer
from vapor_compliance.models.sku import RawSKU, CanonicalProduct
from vapor_compliance.matching.exact_matcher import ExactMatcher
from vapor_compliance.matching.fuzzy_matcher import FuzzyMatcher
from vapor_compliance.matching.tfidf_matcher import TFIDFMatcher
from vapor_compliance.matching.bm25_matcher import BM25Matcher
from vapor_compliance.registry.cpg_registry import CPGRegistry
from vapor_compliance.registry.fda_registry import FDARegistry
from vapor_compliance.registry.state_registry import StateRegistry
from vapor_compliance.pipeline.orchestrator import CompliancePipeline


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
    result = norm.normalize(raw)
    assert result.raw_sku_id == "TEST_001"
    assert result.nicotine_mg_ml == 50.0
    assert result.product_type == "POD"
    print(f"✓ normalizer → normalized_name='{result.normalized_name}', confidence={result.normalization_confidence}")


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
    test_normalizer()
    test_exact_matcher()
    test_fuzzy_matcher()
    test_tfidf_matcher()
    test_bm25_matcher()
    test_full_pipeline()
    print("\n✓ All tests passed")
