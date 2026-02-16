from inspectelement.models import ElementSummary
from inspectelement.name_suggester import suggest_element_name, to_upper_snake


def _summary(**overrides) -> ElementSummary:
    base = ElementSummary(
        tag="input",
        id=None,
        classes=[],
        name=None,
        role="textbox",
        text=None,
        placeholder=None,
        aria_label=None,
        label_text=None,
        attributes={},
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def test_turkish_text_normalized_to_ascii_upper_snake() -> None:
    summary = _summary(text="Şube İletişim Adresi")
    assert suggest_element_name(summary) == "SUBE_ILETISIM_ADRESI_TXT"


def test_long_name_trimmed() -> None:
    summary = _summary(text="Bu alan oldukca uzun bir isim uretmelidir ve limitlenmelidir")
    result = suggest_element_name(summary)
    assert len(result) <= 44
    assert result.endswith("_TXT")


def test_invalid_chars_removed_and_priority_prefers_data_testid() -> None:
    summary = _summary(
        text="Genel Metin",
        attributes={"data-testid": "@@adres#alani!!"},
        tag="button",
        role="button",
    )
    assert suggest_element_name(summary) == "ADRES_ALANI_BTN"


def test_clickable_prefers_visible_text_over_noisy_testid() -> None:
    summary = _summary(
        tag="a",
        role="link",
        text="Yemek",
        attributes={"data-testid": "topslider-none-1-yemek"},
    )
    assert suggest_element_name(summary) == "YEMEK_LNK"


def test_fallback_locator_text_is_extracted() -> None:
    assert (
        suggest_element_name(
            summary=None,
            fallback="//a[normalize-space()='Yemek']",
        )
        == "YEMEK_TXT"
    )


def test_to_upper_snake_handles_digit_prefix() -> None:
    assert to_upper_snake("123 deneme") == "E_123_DENEME"
