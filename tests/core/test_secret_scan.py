from core.secret_scan import detected_secret_types


def test_detects_secret_classes_without_returning_values() -> None:
    provider_key = "sk-sp-" + "a" * 32
    telegram_key = "123456789:" + "A" * 30

    detected = detected_secret_types(f"key={provider_key}\nbot={telegram_key}")

    assert detected == ("telegram_bot", "provider_key")
    assert provider_key not in str(detected)


def test_regular_memory_text_is_allowed() -> None:
    assert detected_secret_types("TURN нужен только для закрытых сетей") == ()
