"""Regression coverage for evidence-based approximate property identity."""

import pytest

from imob.domain.similarity import SimilarityListing, build_similarity_groups, match_reasons


def _listing(identifier: int, **updates: object) -> SimilarityListing:
    return SimilarityListing.model_validate(
        {
            "id": identifier,
            "agency_slug": f"agency-{identifier}",
            "purpose": "venda",
            "property_type": "apartamento",
            "neighborhood": "centro",
            "title": "Apartamento à venda",
            "building": "Edifício Andressa",
            "area_private": 119,
            "bedrooms": 3,
            "suites": 2,
            "bathrooms": 3,
            "parking": 1,
            **updates,
        }
    )


def test_missing_address_can_match_with_independent_evidence() -> None:
    """Recover the local Andressa pair through building, same-kind area and attributes."""
    left = _listing(1, area_private=118.96)
    right = _listing(2)
    assert match_reasons(left, right)
    assert build_similarity_groups((left, right)) == build_similarity_groups((right, left))
    assert len(set(build_similarity_groups((left, right)).values())) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"street": "Rua Tocantins"},
        {"street_number": "505"},
        {"title": "Apartamento apto 102"},
        {"bedrooms": 2},
        {"suites": 1},
        {"bathrooms": 2},
        {"parking": 2},
        {"area_private": 140},
        {"building": "Edifício Outro"},
    ],
)
def test_known_conflicts_veto_probable_matches(change: dict[str, object]) -> None:
    """Independent conflicts override building names and similar descriptions."""
    title = (
        "Apartamento"
        if set(change) <= {"bedrooms", "suites", "bathrooms", "parking", "area_private"}
        else "Apartamento apto 101"
    )
    left = _listing(1, street="Rua Tamoio", street_number="503", title=title)
    right = _listing(2, street="Rua Tamoio", street_number="503", title=title)
    assert not match_reasons(left, SimilarityListing.model_validate({**right.model_dump(), **change}))


def test_reported_false_matches_remain_separate() -> None:
    """Common neighborhood, rounded area and two bedrooms do not establish identity."""
    common = {
        "purpose": "aluguel",
        "building": None,
        "area_private": None,
        "bedrooms": 2,
        "suites": None,
        "bathrooms": None,
        "parking": None,
    }
    listings = (
        _listing(
            290,
            **{
                **common,
                "agency_slug": "imobilli",
                "area_total": 113,
                "title": "Apartamento Ed. Jatir Eugenio Radaelli apto 101 Rua Tamoio 503",
            },
        ),
        _listing(
            291,
            **{
                **common,
                "agency_slug": "imobilli",
                "area_total": 110,
                "title": "Apartamento Suíte mais um Quarto no Centro",
            },
        ),
        _listing(
            1043,
            **{
                **common,
                "area_total": 110,
                "title": "Apartamento com uma suite mais dois quartos, sacada excelente",
            },
        ),
    )
    assert build_similarity_groups(listings) == {}


def test_different_area_kinds_are_not_compared() -> None:
    """Private area must not be compared with another listing's total area."""
    left = _listing(1, area_private=119, suites=None, bathrooms=None, parking=None)
    right = _listing(2, area_private=None, area_total=119, suites=None, bathrooms=None, parking=None)
    assert not match_reasons(left, right)


def test_groups_do_not_chain_incompatible_pairs() -> None:
    """A–B and B–C compatibility cannot hide an A–C area conflict."""
    listings = (_listing(1, area_private=100), _listing(2, area_private=105), _listing(3, area_private=110))
    assert match_reasons(listings[0], listings[1])
    assert match_reasons(listings[1], listings[2])
    assert not match_reasons(listings[0], listings[2])
    groups = build_similarity_groups(listings)
    assert groups[1] == groups[2]
    assert 3 not in groups


def test_address_conflict_can_be_detected_in_title() -> None:
    """A structured address and a title-supplied address can contradict each other."""
    assert not match_reasons(
        _listing(1, street="Rua Tamoio", street_number="503"),
        _listing(2, title="Apartamento Rua Tocantins 505"),
    )


def test_sparse_records_and_disagreements_are_exposed_for_review() -> None:
    """A shared building and rooms remain discoverable even without area evidence."""
    from imob.domain.similarity import assess_similarity

    left = _listing(1, area_private=None, bathrooms=1)
    right = _listing(2, area_private=None, bathrooms=3)
    actual = assess_similarity(left, right)
    assert actual.level == "review"
    assert any("banheiros" in warning for warning in actual.uncertainties)
    assert any("áreas" in warning for warning in actual.uncertainties)
    assert not build_similarity_groups((left, right))


def test_title_features_reconcile_rooms_plus_suites_and_decimal_areas() -> None:
    """Supplement explicit text without comparing unknown or different area kinds."""
    from imob.domain.similarity import prepare_listing

    listing = _listing(
        1,
        bedrooms=None,
        suites=None,
        area_private=None,
        title="Apartamento com duas suítes + um quarto",
        description="Área privativa: 118,96 m²",
    )
    actual = prepare_listing(listing)
    assert (actual.bedrooms, actual.suites, actual.area_private) == (3, 2, 118.96)
    assert listing.bedrooms is None
    assert prepare_listing(actual) == actual


def test_building_prefix_variants_do_not_hide_candidates() -> None:
    """Normalize repeated building prefixes and a trailing neighborhood."""
    from imob.domain.similarity import assess_similarity

    actual = assess_similarity(
        _listing(1, building="EDIFÍCIO RESIDENCIAL ANDRESSA CENTRO"),
        _listing(2, building="Edifício Andressa"),
    )
    assert actual.level == "probable"


def test_currency_in_titles_does_not_affect_similarity() -> None:
    """Identity classification must survive different or undisclosed monetary text."""
    from imob.domain.similarity import assess_similarity

    left = _listing(1, title="Apartamento Edifício Andressa")
    original = assess_similarity(left, _listing(2, title="Apartamento Edifício Andressa"))
    for suffix in ("R$ 700.000,00", "R$ 7.000.000,00", "Preço: 70.000", ""):
        actual = assess_similarity(left, _listing(2, title=f"Apartamento Edifício Andressa {suffix}"))
        assert actual == original


def test_three_independent_exact_features_promote_without_area_requirement() -> None:
    """Edifício, quartos and banheiros suffice; missing size must not veto this user rule."""
    from imob.domain.similarity import assess_similarity

    base = _listing(1, area_private=None, suites=None, bathrooms=None, parking=None)
    two_features = assess_similarity(base, base.model_copy(update={"id": 2, "agency_slug": "other"}))
    assert two_features.level == "review"
    assert two_features.identical_features == ("edifício/condomínio", "quartos")
    left = base.model_copy(update={"bathrooms": 2})
    right = left.model_copy(update={"id": 2, "agency_slug": "other"})
    actual = assess_similarity(left, right)
    assert actual.level == "probable"
    assert actual.identical_features == ("edifício/condomínio", "quartos", "banheiros")


def test_street_number_and_size_suffice_without_room_counts() -> None:
    """The user's example is three distinct pieces of evidence, even for same-agency ads."""
    from imob.domain.similarity import assess_similarity

    left = _listing(
        1,
        agency_slug="same",
        property_type="casa",
        building=None,
        street="Rua Tamoio",
        street_number="503",
        area_private=110,
        bedrooms=None,
        suites=None,
        bathrooms=None,
        parking=None,
    )
    right = left.model_copy(update={"id": 2})
    actual = assess_similarity(left, right)
    assert actual.level == "probable"
    assert actual.identical_features == ("rua", "número do endereço", "metragem do mesmo tipo")


def test_size_and_location_are_not_double_counted() -> None:
    """Private/built/total areas plus street/building must not turn two facts into five."""
    from imob.domain.similarity import assess_similarity

    left = _listing(
        1,
        street="Rua Tamoio",
        area_private=100,
        area_built=120,
        area_total=150,
        bedrooms=None,
        suites=None,
        bathrooms=None,
        parking=None,
    )
    right = left.model_copy(update={"id": 2, "agency_slug": "other"})
    actual = assess_similarity(left, right)
    assert actual.level == "review"
    assert actual.identical_features == ("rua", "metragem do mesmo tipo")
    assert "número do endereço" not in actual.identical_features


def test_nearby_area_does_not_count_as_an_identical_feature() -> None:
    """A tolerance match can support review but cannot satisfy the three-exact-feature rule."""
    from imob.domain.similarity import assess_similarity

    left = _listing(1, suites=None, bathrooms=None, parking=None, area_private=118.96)
    right = left.model_copy(update={"id": 2, "agency_slug": "other", "area_private": 119})
    actual = assess_similarity(left, right)
    assert actual.level == "review"
    assert actual.identical_features == ("edifício/condomínio", "quartos")


def test_description_identity_links_a_generic_card_to_an_explicit_unit_title() -> None:
    """Regression: identity in one description must connect to another agency's title."""
    from imob.domain.similarity import find_similarity_pairs, prepare_listing

    left = _listing(
        1,
        building=None,
        title="Apartamento no Centro",
        bathrooms=None,
        description=(
            "Apartamento 501 Ed. Alameda\n"
            "Localizado na Rua Brasil , 599 Centro\n"
            "2 banheiros\nAgende uma visita!\n"
            "Endereço: Rua da Imobiliária, 1350"
        ),
    )
    right = _listing(
        2,
        building="Ed. Alameda",
        title="Apartamento 501 Ed. Alameda",
        area_private=None,
        bedrooms=None,
        suites=None,
        bathrooms=None,
        parking=None,
    )
    actual = prepare_listing(left)
    assert (actual.building, actual.unit, actual.street, actual.street_number, actual.bathrooms) == (
        "alameda",
        "501",
        "brasil",
        "599",
        2,
    )
    pairs = find_similarity_pairs((left, right))
    assert len(pairs) == 1
    assert pairs[0].assessment.level == "probable"
    assert pairs[0].assessment.identical_features == ("edifício/condomínio", "unidade")


def test_description_identity_is_not_limited_to_first_300_characters() -> None:
    """Read explicit property facts late in the description while retaining source fields."""
    from imob.domain.similarity import prepare_listing

    description = (
        "Excelente localização e ambientes aconchegantes. "
        * 20
        + "\nApartamento 601 Ed. Alameda\nRua Brasil, 599\n"
        "01 suíte com closet, mais 01 dormitório\n02 banheiros\n01 vaga"
    )
    listing = _listing(
        1,
        building=None,
        title="Apartamento",
        bedrooms=None,
        suites=None,
        bathrooms=None,
        parking=None,
        description=description,
    )
    actual = prepare_listing(listing)
    assert (
        actual.building,
        actual.unit,
        actual.street,
        actual.street_number,
        actual.bedrooms,
        actual.suites,
        actual.bathrooms,
        actual.parking,
    ) == ("alameda", "601", "brasil", "599", 2, 1, 2, 1)
    assert prepare_listing(actual) == actual
    assert listing.building is None


def test_broker_office_address_does_not_become_property_location() -> None:
    """A contact signature is distinct from the advertised property's description."""
    from imob.domain.similarity import prepare_listing

    actual = prepare_listing(
        _listing(
            1,
            building=None,
            description=(
                "Apartamento espaçoso.\nPara mais informações entre em contato\n"
                "Endereço: Rua da Imobiliária, 1350\nApartamento 101 Ed. Escritório"
            ),
        )
    )
    assert (actual.street, actual.street_number, actual.building, actual.unit) == (None, None, None, None)


def test_conflicting_description_unit_forces_review() -> None:
    """Do not silently override an explicit unit when the description identifies another one."""
    from imob.domain.similarity import assess_similarity

    left = _listing(1, title="Apartamento 501", description="Apartamento 502 no edifício")
    right = _listing(2, title="Apartamento 501")
    actual = assess_similarity(left, right)
    assert actual.level == "review"
    assert any("unidades diferentes" in message for message in actual.uncertainties)


def test_building_and_unit_alone_identify_the_property() -> None:
    """No third attribute is required for the same unit in the same building."""
    from imob.domain.similarity import assess_similarity

    left = _listing(
        1,
        title="Apartamento 501 Ed. Alameda",
        building="Ed. Alameda",
        neighborhood=None,
        area_private=None,
        bedrooms=None,
        suites=None,
        bathrooms=None,
        parking=None,
    )
    right = left.model_copy(update={"id": 2, "agency_slug": "other"})
    actual = assess_similarity(left, right)
    assert actual.level == "probable"
    assert actual.identical_features == ("edifício/condomínio", "unidade")
    assert "Identificação direta: mesmo edifício e mesma unidade" in actual.reasons
    assert len(set(build_similarity_groups((left, right)).values())) == 1


def test_direct_identity_keeps_metadata_disagreements_visible() -> None:
    """Source counts or areas can differ while the explicitly identified apartment is the same."""
    from imob.domain.similarity import assess_similarity

    left = _listing(1, title="Apartamento 501", bathrooms=1, area_private=100)
    right = _listing(2, title="Apartamento 501", bathrooms=2, area_private=120)
    actual = assess_similarity(left, right)
    assert actual.level == "probable"
    assert any("banheiros" in warning for warning in actual.uncertainties)
    assert any("Área" in warning for warning in actual.uncertainties)


def test_unit_number_requires_the_same_building() -> None:
    """Apartment 501 is not a universal identity across different buildings."""
    from imob.domain.similarity import assess_similarity

    left = _listing(1, title="Apartamento 501", building="Ed. Alameda")
    right = _listing(2, title="Apartamento 501", building="Ed. Outro")
    assert assess_similarity(left, right).level == "none"
