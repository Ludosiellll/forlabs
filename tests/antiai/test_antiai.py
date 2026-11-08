import pytest

from forlabs.antiai.code import scan_code
from forlabs.antiai.design import scan_design
from forlabs.antiai.similarity import scan_similarity, similarity
from forlabs.antiai.text_ru import scan_paragraph_openings, scan_text

REFERENCE = ("В среде разработки Visual Studio Code разработан файл программы task1_stairs_and_square.go, "
             "реализующий два алгоритма на базе срезов, массивов и циклов for. Согласно варианту 14 из "
             "приложения А необходимо создать двумерный массив 5 х 5 и написать функцию заполнения его "
             "случайными числами от 30 до 60.")


def rules(fs):
    return sorted({f.rule_id for f in fs})


@pytest.mark.parametrize("text", [
    "Код был сгенерирован автоматически.", "Генерация данных выполнена.", "Нейросеть помогла.",
    "Использован ChatGPT.", "Ответ дал бот.", "Это задание на оценку 9–10 баллов.",
    "Задание повышенного уровня сложности выполнено.", "Пожалуйста, выберите документ.",
    "Искусственный интеллект подсказал решение.",
])
def test_banned_words(text):
    assert "ai.banned" in rules(scan_text(text, "x"))


@pytest.mark.parametrize("text", [
    "Массив играет важную роль в программе.", "Цикл является ключевым элементом.",
    "В современном мире программирование востребовано.", "Стоит отметить, что код работает.",
    "Go поддерживает широкий спектр типов.", "Срезы не только удобны, но и быстры.",
    "В данной работе рассмотрены срезы.", "Это мощный инструмент разработчика.",
    "Тестирование — неотъемлемая часть разработки.", "Давайте рассмотрим пример.",
])
def test_cliches(text):
    assert "ai.cliche" in rules(scan_text(text, "x"))


def test_reference_text_is_clean():
    assert scan_text(REFERENCE, "x") == []


def test_model_word_only_in_ai_sense():
    assert scan_text("Создана модель данных из трёх таблиц.", "x") == []
    assert "ai.banned" in rules(scan_text("Текст написала языковая модель.", "x"))


def test_same_openings():
    paras = ["В программе создан массив.", "В программе описан цикл.", "В программе выведен результат.", "Другой текст."]
    assert rules(scan_paragraph_openings(paras)) == ["ai.same_opening"]
    assert scan_paragraph_openings(paras[:2] + paras[3:]) == []


def test_code_rules():
    code_many_comments = "\n".join(["// считаем", "x := 1", "// выводим", "fmt.Println(x)"])
    assert "code.comment_density" in rules(scan_code(code_many_comments, "go", "x"))
    assert "code.english_comment_bsl" in rules(scan_code("// calculate total\nСумма = 1;", "bsl", "x"))
    assert "code.todo" in rules(scan_code("x = 1 # TODO fix", "python", "x"))
    assert "code.separator" in rules(scan_code('fmt.Println("==========")', "go", "x"))
    assert "code.emoji" in rules(scan_code('print("Готово ✅")', "python", "x"))
    assert scan_code("package main\n\nfunc main() {\n    x := 1\n    fmt.Println(x)\n}", "go", "x") == []


def test_design_rules():
    assert "design.ai_gradient" in rules(scan_design("body{background:linear-gradient(135deg,#667eea 0%,#764ba2 100%)}", "x"))
    assert "design.glass" in rules(scan_design(".card{backdrop-filter: blur(10px)}", "x"))
    assert "design.neon" in rules(scan_design(".b{box-shadow: 0 0 30px #00f0ff}", "x"))
    assert "design.default_font" in rules(scan_design("body{font-family:'Inter', sans-serif}", "x"))
    assert "design.emoji_icons" in rules(scan_design("<span>🚀</span> Начать", "x"))
    assert "design.slogan" in rules(scan_design("<h1>Откройте для себя Бобруйск</h1>", "x"))
    assert scan_design("body{font-family: Georgia, serif; color:#222; background:#f4efe6}", "x") == []


def test_similarity():
    assert similarity(REFERENCE, REFERENCE) == 1.0
    assert similarity(REFERENCE, "Совсем другой текст про сеть и маршрутизаторы Cisco в офисе.") < 0.1
    f = scan_similarity(REFERENCE, {"petrov": REFERENCE})
    assert rules(f) == ["ai.similar_profile"]
