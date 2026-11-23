from app.company_card import safe

def test_inn_digits_are_not_stripped():
    assert safe("7718894438") == "7718894438"
    assert safe("7706019535") == "7706019535"
    assert safe("1010101010") == "1010101010"

def test_control_characters_are_removed():
    assert safe("Компания\x00\nООО") == "Компания ООО"
