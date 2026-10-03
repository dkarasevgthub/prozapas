"""CommerceML 2 — разбор и сборка файлов обмена с 1С.

Чистый формат: ни базы, ни FastAPI. На вход байты файла, на выходе позиции и
список непринятых — поэтому разбор проверяется на настоящих выгрузках 1С без
поднятого Postgres.

Читаем широко, пишем узко. Файл приходит от чужой программы, и её редакцию
нельзя узнать по номеру версии: одна и та же 2.07 у разных поставщиков кладёт
остаток то атрибутом, то вложенным элементом. Поэтому разбор пробует все
известные формы, а `ВерсияСхемы` идёт только в журнал.

Чего в файле нет: **цен**. ProЗапас их не ведёт, а выдумать нечем — пустая цена
в обмене означала бы «товар стоит ноль» и обнулила бы прайс на приёмной
стороне. Поэтому выгрузка остатков несёт только количества, без `ТипыЦен` и
`Цены`. Строгая проверка по XSD на этом споткнётся: оба элемента там
обязательные. Живые приёмники остатков это переживают, но если конкретная 1С
файл не примет — здесь и появится заглушка типа цены.
"""
from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable, NamedTuple

#: Пространство имён одно на все редакции 2.0x. Старые выгрузки его не объявляют
#: вовсе — поэтому после разбора имена элементов зачищаются, а не ищутся с
#: префиксом: поиск по «Товар» в файле с xmlns молча находит ноль товаров.
NS = "urn:1C.ru:commerceml_2"
ROOT = "КоммерческаяИнформация"

#: Выгружаем 2.07: её отдаёт 1С:УТ 11, в ней остаток пишется атрибутом
#: «Склад/@КоличествоНаСкладе», и она проходит проверку по схеме 2.10 —
#: каталог и предложения в 2.07…2.10 не менялись.
VERSION = "2.07"

#: Наши идентификаторы в обмене — постоянные, а не случайные при каждой
#: выгрузке: 1С сверяет каталог по этому Ид, и новый GUID каждый раз означал бы
#: для неё новый каталог при каждом обмене.
CLASSIFIER_ID = "9a1d5f80-7c3e-4b21-9f5d-70726f7a6170"
CATALOG_ID = "9a1d5f80-7c3e-4b21-9f5d-70726f7a6171"
OWNER_ID = "9a1d5f80-7c3e-4b21-9f5d-70726f7a6172"
OWNER_NAME = "Сталкер Групп ООО"
OWNER_OFFICIAL = "Общество с ограниченной ответственностью «Сталкер Групп»"

#: Склад в файле обязан быть GUID’ом, а у нас код вида «128». Пятая версия от
#: постоянного пространства даёт один и тот же идентификатор для одного кода.
_WAREHOUSE_NS = uuid.UUID("9a1d5f80-7c3e-4b21-9f5d-70726f7a6173")

ET.register_namespace("", NS)


class Malformed(ValueError):
    """Файл разобрать нечем: не XML, не тот корень, не тот раздел.

    Не ошибка отдельной позиции — такой файл не применяется целиком.
    Роутер превращает это в 400: разбор не состоялся (api.md §3.3).
    """


class Issue(NamedTuple):
    """Непринятая позиция файла."""

    index: int
    ref: str
    reason: str


class Item(NamedTuple):
    """Позиция номенклатуры из import.xml."""

    code1c: str | None
    article: str | None
    name: str
    unit: str
    unit_weight: Decimal | None
    index: int


class Offer(NamedTuple):
    """Предложение из offers.xml: нас интересует только количество."""

    code1c: str | None
    article: str | None
    qty: Decimal
    index: int


class ExportItem(NamedTuple):
    """Строка выгрузки справочника."""

    code1c: str | None
    article: str
    name: str
    unit: str
    unit_weight: Decimal | float | None


class ExportStock(NamedTuple):
    """Строка выгрузки остатков."""

    code1c: str | None
    article: str
    name: str
    unit: str
    qty: Decimal | float


def base_id(code1c: str | None) -> str | None:
    """«товар-guid#характеристика-guid» → «товар-guid».

    Варианты товара (размер, цвет) 1С выгружает предложениями с составным Ид.
    В нашем справочнике одна строка на товар, поэтому сопоставлять надо по
    левой части — сам товар.
    """
    if not code1c:
        return None
    head = code1c.partition("#")[0].strip()
    return head or None


# --- Разбор ---------------------------------------------------------------

def parse_catalog(raw: bytes) -> tuple[list[Item], list[Issue]]:
    """import.xml → позиции номенклатуры."""
    root = _root(raw)
    nodes = root.findall("Каталог/Товары/Товар")
    if not nodes:
        if root.find("ПакетПредложений") is not None:
            raise Malformed("Это файл остатков (ПакетПредложений), а не "
                            "номенклатуры — загрузите его в разделе «Остатки»")
        raise Malformed("В файле нет товаров: ожидался Каталог/Товары/Товар")

    items: list[Item] = []
    issues: list[Issue] = []
    for index, node in enumerate(nodes, 1):
        code1c = _text(node, "Ид") or None
        article = _text(node, "Артикул") or None
        ref = code1c or article or f"позиция {index}"

        if _deleted(node):
            issues.append(Issue(index, ref, "помечена на удаление в 1С"))
            continue
        if not code1c and not article:
            issues.append(Issue(index, ref,
                                "нет ни кода 1С, ни артикула — сопоставить не с чем"))
            continue
        name = _text(node, "Наименование")
        if not name:
            issues.append(Issue(index, ref, "нет наименования"))
            continue
        unit = _unit(node)
        if not unit:
            issues.append(Issue(index, ref, "нет базовой единицы измерения"))
            continue

        items.append(Item(code1c=code1c, article=article, name=name[:500],
                          unit=unit, unit_weight=_weight(node), index=index))
    return items, issues


def parse_offers(raw: bytes) -> tuple[list[Offer], list[Issue]]:
    """offers.xml → количества по позициям."""
    root = _root(raw)
    nodes = (root.findall("ПакетПредложений/Предложения/Предложение")
             or root.findall("ИзмененияПакетаПредложений/Предложения/Предложение"))
    if not nodes:
        if root.find("Каталог") is not None:
            raise Malformed("Это файл номенклатуры (Каталог), а не остатков — "
                            "загрузите его в разделе «Справочник»")
        raise Malformed("В файле нет предложений: ожидался "
                        "ПакетПредложений/Предложения/Предложение")

    offers: list[Offer] = []
    issues: list[Issue] = []
    for index, node in enumerate(nodes, 1):
        code1c = _text(node, "Ид") or None
        article = _text(node, "Артикул") or None
        ref = code1c or article or f"предложение {index}"

        if not code1c and not article:
            issues.append(Issue(index, ref,
                                "нет ни кода 1С, ни артикула — сопоставить не с чем"))
            continue
        qty = _quantity(node)
        if qty is None:
            issues.append(Issue(index, ref, "нет количества"))
            continue
        if qty < 0:
            issues.append(Issue(index, ref, f"количество отрицательное: {qty}"))
            continue

        offers.append(Offer(code1c=code1c, article=article, qty=qty, index=index))
    return offers, issues


def _root(raw: bytes) -> ET.Element:
    """Разобрать файл и снять пространство имён с имён элементов.

    Байты, а не строка: кодировку объявляет сам файл — каталог 1С отдаёт в
    UTF-8, заказы в windows-1251, и решать за него нельзя. BOM снимаем руками:
    разбор споткнулся бы на нём в первой же строке.
    """
    if not raw or not raw.strip():
        raise Malformed("Файл пуст")
    try:
        root = ET.fromstring(raw.lstrip(b"\xef\xbb\xbf"))
    except ET.ParseError as exc:
        raise Malformed(f"Файл не разобран как XML: {exc}") from None
    for el in root.iter():
        if isinstance(el.tag, str):
            el.tag = el.tag.rpartition("}")[2]
    if root.tag != ROOT:
        raise Malformed(f"Ожидался корень «{ROOT}», а в файле «{root.tag}»")
    return root


def _text(el: ET.Element, tag: str) -> str:
    child = el.find(tag)
    return (child.text or "").strip() if child is not None else ""


def _decimal(raw: str) -> Decimal | None:
    """«1 250,5» → 1250.5.

    Запятая и пробелы-разделители разрядов приходят из живых выгрузок, хотя по
    схеме тип десятичный. Неразрывный и тонкий пробел — оттуда же.
    """
    cleaned = (raw.replace("\xa0", "").replace(" ", "")
               .replace(" ", "").replace(",", "."))
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _unit(node: ET.Element) -> str:
    """Единица измерения из «БазоваяЕдиница» — атрибутом или текстом.

    Код ОКЕИ не берём: у нас единица — строка («шт.», «м», «лист»), и «796»
    в справочнике никому ничего не скажет.
    """
    base = node.find("БазоваяЕдиница")
    if base is None:
        return ""
    for source in (base.get("НаименованиеКраткое"), base.text,
                   base.get("МеждународноеСокращение"),
                   base.get("НаименованиеПолное")):
        value = (source or "").strip()
        if value:
            return value[:20]
    return ""


def _weight(node: ET.Element) -> Decimal | None:
    """Вес единицы: отдельным элементом или реквизитом.

    В стандарте поля для веса нет. Выгрузки в стиле Битрикса кладут «Вес»
    рядом с наименованием, каноничная 1С — в «ЗначенияРеквизитов».
    """
    direct = _text(node, "Вес")
    if direct:
        return _decimal(direct)
    for rec in node.iterfind("ЗначенияРеквизитов/ЗначениеРеквизита"):
        name = _text(rec, "Наименование").lower().replace(" ", "").replace(",", "")
        if name in ("вес", "весединицы", "вескг", "весединицыкг"):
            return _decimal(_text(rec, "Значение"))
    return None


def _deleted(node: ET.Element) -> bool:
    """Товар, помеченный в 1С на удаление: атрибутом или реквизитом."""
    if (node.get("Статус") or "").strip().lower() == "удален":
        return True
    if _text(node, "ПометкаУдаления").lower() == "true":
        return True
    for rec in node.iterfind("ЗначенияРеквизитов/ЗначениеРеквизита"):
        if _text(rec, "Наименование").replace(" ", "").lower() == "пометкаудаления":
            return _text(rec, "Значение").lower() == "true"
    return False


def _quantity(node: ET.Element) -> Decimal | None:
    """Количество по предложению.

    Сначала итог «Количество», потом сумма по складам. Каноничный CommerceML
    пишет остаток атрибутом «Склад/@КоличествоНаСкладе», документ быстрого
    обновления — тем же атрибутом на элементе «Склады», а выгрузка в стиле
    Битрикса кладёт его во вложенные «Остатки/Остаток». Все три и читаем:
    искать только один вид значило бы молча получить нулевой остаток по всему
    складу вместо отказа.
    """
    total = _decimal(_text(node, "Количество"))
    if total is not None:
        return total

    parts: list[Decimal] = []
    for tag in ("Склад", "Склады"):
        for house in node.findall(tag):
            value = _decimal(house.get("КоличествоНаСкладе") or "")
            if value is not None:
                parts.append(value)
    for rest in node.iterfind("Остатки/Остаток"):
        value = _decimal(_text(rest, "Количество"))
        if value is None:
            value = _decimal(_text(rest, "Склад/Количество"))
        if value is not None:
            parts.append(value)
    return sum(parts) if parts else None


# --- Сборка ---------------------------------------------------------------

def warehouse_uid(code: str) -> str:
    """Идентификатор склада для файла: 1С ждёт GUID, у нас код вида «128»."""
    return str(uuid.uuid5(_WAREHOUSE_NS, code))


def build_catalog(rows: Iterable[ExportItem], *, now: datetime) -> bytes:
    """Номенклатура → import.xml."""
    root = _new_root(now)
    _classifier(root)
    catalog = ET.SubElement(root, "Каталог", {"СодержитТолькоИзменения": "false"})
    ET.SubElement(catalog, "Ид").text = CATALOG_ID
    ET.SubElement(catalog, "ИдКлассификатора").text = CLASSIFIER_ID
    ET.SubElement(catalog, "Наименование").text = "Номенклатура ProЗапас"
    _owner(catalog)
    goods = ET.SubElement(catalog, "Товары")
    for row in rows:
        node = _product(goods, row)
        if row.unit_weight is not None:
            records = ET.SubElement(node, "ЗначенияРеквизитов")
            record = ET.SubElement(records, "ЗначениеРеквизита")
            ET.SubElement(record, "Наименование").text = "Вес"
            ET.SubElement(record, "Значение").text = _number(row.unit_weight)
    return _render(root)


def build_offers(rows: Iterable[ExportStock], *, warehouse_code: str,
                 warehouse_name: str, now: datetime) -> bytes:
    """Остатки одного склада → offers.xml."""
    root = _new_root(now)
    _classifier(root, groups=False)
    pack = ET.SubElement(root, "ПакетПредложений",
                         {"СодержитТолькоИзменения": "false"})
    # Завершающая решётка — так 1С связывает пакет с каталогом.
    ET.SubElement(pack, "Ид").text = f"{CATALOG_ID}#"
    ET.SubElement(pack, "Наименование").text = f"Остатки ProЗапас · {warehouse_name}"
    ET.SubElement(pack, "ИдКаталога").text = CATALOG_ID
    ET.SubElement(pack, "ИдКлассификатора").text = CLASSIFIER_ID
    _owner(pack)
    houses = ET.SubElement(pack, "Склады")
    house = ET.SubElement(houses, "Склад")
    ET.SubElement(house, "Ид").text = warehouse_uid(warehouse_code)
    ET.SubElement(house, "Наименование").text = warehouse_name

    offers = ET.SubElement(pack, "Предложения")
    for row in rows:
        node = _product(offers, row, tag="Предложение")
        ET.SubElement(node, "Количество").text = _number(row.qty)
        ET.SubElement(node, "Склад", {
            "ИдСклада": warehouse_uid(warehouse_code),
            "КоличествоНаСкладе": _number(row.qty)})
    return _render(root)


def _new_root(now: datetime) -> ET.Element:
    return ET.Element(f"{{{NS}}}{ROOT}", {
        "ВерсияСхемы": VERSION,
        "ДатаФормирования": now.strftime("%Y-%m-%dT%H:%M:%S"),
    })


def _classifier(root: ET.Element, *, groups: bool = True) -> None:
    node = ET.SubElement(root, "Классификатор")
    ET.SubElement(node, "Ид").text = CLASSIFIER_ID
    ET.SubElement(node, "Наименование").text = "Классификатор ProЗапас"
    _owner(node)
    if groups:
        # Групп у нас нет, но товар обязан ссылаться на существующую группу,
        # иначе приёмная сторона кладёт его «в никуда».
        wrap = ET.SubElement(node, "Группы")
        group = ET.SubElement(wrap, "Группа")
        ET.SubElement(group, "Ид").text = CATALOG_ID
        ET.SubElement(group, "Наименование").text = "Номенклатура"


def _owner(parent: ET.Element) -> None:
    node = ET.SubElement(parent, "Владелец")
    ET.SubElement(node, "Ид").text = OWNER_ID
    ET.SubElement(node, "Наименование").text = OWNER_NAME
    ET.SubElement(node, "ОфициальноеНаименование").text = OWNER_OFFICIAL


def _product(parent: ET.Element, row, *, tag: str = "Товар") -> ET.Element:
    """Общая часть «Товара» и «Предложения»: порядок элементов задан схемой."""
    node = ET.SubElement(parent, tag)
    if row.code1c:
        ET.SubElement(node, "Ид").text = row.code1c
    ET.SubElement(node, "Артикул").text = row.article
    ET.SubElement(node, "Наименование").text = row.name
    unit = ET.SubElement(node, "БазоваяЕдиница",
                         {"НаименованиеКраткое": row.unit,
                          "НаименованиеПолное": row.unit})
    unit.text = row.unit
    if tag == "Товар":
        wrap = ET.SubElement(node, "Группы")
        ET.SubElement(wrap, "Ид").text = CATALOG_ID
    return node


def _number(value: Decimal | float) -> str:
    """Число для файла: точка, без экспоненты, без хвостовых нулей."""
    dec = value if isinstance(value, Decimal) else Decimal(str(value))
    text = format(dec.normalize(), "f")
    return text if text != "-0" else "0"


def _render(root: ET.Element) -> bytes:
    ET.indent(root, space="\t")
    return ET.tostring(root, encoding="UTF-8", xml_declaration=True)
