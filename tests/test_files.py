from app.files import safe_filename, unique_names


def test_safe_filename_keeps_title_readable():
    assert safe_filename("O segredo que ninguém te conta", "x") == "O segredo que ninguém te conta"
    assert safe_filename("Você sabia disso?", "x") == "Você sabia disso"
    assert safe_filename('Erro: "não faça" A/B <isso>*', "x") == "Erro não faça A B isso"
    assert safe_filename("Título com emoji 🔥", "x") == "Título com emoji 🔥"


def test_safe_filename_fallbacks():
    assert safe_filename("", "corte-1") == "corte-1"
    assert safe_filename("???", "corte-2") == "corte-2"
    assert safe_filename("CON", "corte-3") == "corte-3"
    assert safe_filename("terminando com ponto...", "x") == "terminando com ponto"


def test_safe_filename_limits_length_on_word_boundary():
    name = safe_filename("palavra " * 40, "x")
    assert len(name) <= 120
    assert name.endswith("palavra")


def test_unique_names():
    assert unique_names(["Gancho", "Outro", "gancho", "Gancho"]) == ["Gancho", "Outro", "gancho (2)", "Gancho (3)"]
