"""Обмен с 1С — CommerceML 2, раздел 6.8 api.md.

Файлы в фикстурах короткие, но воспроизводят то, чем настоящие выгрузки 1С
отличаются от учебных: BOM перед объявлением, windows-1251, отсутствующее
пространство имён, запятая в числе, составной Ид у вариантов товара,
пометка удаления и три разные формы записи остатка.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from api import commerceml
from tests import flows
from tests.flows import BOLT, PIPE, WASHER, problem

PIPE_CODE = "ТМЦ-00512"        # код 1С трубы из seed.py
NOW = datetime(2026, 9, 29, 10, 0, 0)


def catalog_xml(*goods: str, ns: bool = True) -> bytes:
    """import.xml с готовыми элементами «Товар»."""
    xmlns = ' xmlns="urn:1C.ru:commerceml_2"' if ns else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<КоммерческаяИнформация{xmlns} ВерсияСхемы="2.07"'
        ' ДатаФормирования="2026-09-29T10:00:00">'
        "<Каталог><Ид>c-1</Ид><Наименование>Каталог</Наименование><Товары>"
        + "".join(goods) +
        "</Товары></Каталог></КоммерческаяИнформация>"
    ).encode("utf-8")


def good(*, code1c=None, article=None, name="Позиция", unit="шт.",
         weight=None, deleted=False, status=None) -> str:
    attrs = f' Статус="{status}"' if status else ""
    body = f"<Товар{attrs}>"
    if code1c:
        body += f"<Ид>{code1c}</Ид>"
    if article:
        body += f"<Артикул>{article}</Артикул>"
    body += f"<Наименование>{name}</Наименование>"
    body += f'<БазоваяЕдиница Код="796" НаименованиеКраткое="{unit}">{unit}</БазоваяЕдиница>'
    records = []
    if weight is not None:
        records.append(f"<ЗначениеРеквизита><Наименование>Вес</Наименование>"
                       f"<Значение>{weight}</Значение></ЗначениеРеквизита>")
    if deleted:
        records.append("<ЗначениеРеквизита><Наименование>ПометкаУдаления"
                       "</Наименование><Значение>true</Значение></ЗначениеРеквизита>")
    if records:
        body += "<ЗначенияРеквизитов>" + "".join(records) + "</ЗначенияРеквизитов>"
    return body + "</Товар>"


def offers_xml(*items: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<КоммерческаяИнформация xmlns="urn:1C.ru:commerceml_2" ВерсияСхемы="2.07"'
        ' ДатаФормирования="2026-09-29T10:00:00">'
        "<ПакетПредложений><Ид>c-1#</Ид><Наименование>Пакет</Наименование>"
        "<ИдКаталога>c-1</ИдКаталога><Предложения>"
        + "".join(items) +
        "</Предложения></ПакетПредложений></КоммерческаяИнформация>"
    ).encode("utf-8")


def offer(*, code1c=None, article=None, qty=None, houses=None,
          nested=None, changes=False) -> str:
    body = "<Предложение>"
    if code1c:
        body += f"<Ид>{code1c}</Ид>"
    if article:
        body += f"<Артикул>{article}</Артикул>"
    body += ("<Наименование>Предложение</Наименование>"
             '<БазоваяЕдиница Код="796" НаименованиеКраткое="шт.">шт.</БазоваяЕдиница>')
    if qty is not None:
        body += f"<Количество>{qty}</Количество>"
    for house_id, amount in (houses or []):
        tag = "Склады" if changes else "Склад"
        body += f'<{tag} ИдСклада="{house_id}" КоличествоНаСкладе="{amount}"/>'
    if nested:                                   # форма выгрузки в стиле Битрикса
        body += "<Остатки>" + "".join(
            f"<Остаток><Склад><Ид>{h}</Ид><Количество>{a}</Количество></Склад></Остаток>"
            for h, a in nested) + "</Остатки>"
    return body + "</Предложение>"


# --- Разбор: без базы ------------------------------------------------------

def test_parse_reads_product_fields():
    items, issues = commerceml.parse_catalog(catalog_xml(
        good(code1c="1c-1", article="A-1", name="Труба", unit="м", weight="1.6")))
    assert issues == []
    assert items[0] == commerceml.Item(
        code1c="1c-1", article="A-1", name="Труба", unit="м",
        unit_weight=Decimal("1.6"), index=1)


def test_parse_survives_bom_and_windows_1251():
    body = catalog_xml(good(code1c="1c-1", article="A-1", name="Ёлка"))
    assert commerceml.parse_catalog(b"\xef\xbb\xbf" + body)[0][0].name == "Ёлка"

    cp1251 = (body.decode("utf-8")
              .replace('encoding="UTF-8"', 'encoding="windows-1251"')
              .encode("cp1251"))
    assert commerceml.parse_catalog(cp1251)[0][0].name == "Ёлка"


def test_parse_without_namespace():
    """Старые выгрузки xmlns не объявляют — поиск по имени с префиксом
    вернул бы ноль товаров и «успешную» загрузку пустого файла."""
    items, _ = commerceml.parse_catalog(
        catalog_xml(good(code1c="1c-1", article="A-1"), ns=False))
    assert len(items) == 1


def test_parse_accepts_decimal_comma_and_spaces():
    items, _ = commerceml.parse_catalog(
        catalog_xml(good(code1c="1c-1", article="A-1", weight="1,6")))
    assert items[0].unit_weight == Decimal("1.6")

    offers, _ = commerceml.parse_offers(offers_xml(offer(code1c="1c-1", qty="1 250,5")))
    assert offers[0].qty == Decimal("1250.5")


@pytest.mark.parametrize("marker", [{"deleted": True}, {"status": "Удален"}])
def test_parse_skips_items_deleted_in_1c(marker):
    items, issues = commerceml.parse_catalog(
        catalog_xml(good(code1c="1c-1", article="A-1", **marker)))
    assert items == []
    assert issues[0].reason == "помечена на удаление в 1С"


def test_parse_reads_all_three_stock_forms():
    """Атрибут склада, тот же атрибут в документе изменений и вложенные
    остатки Битрикса. Ищи только одну форму — получишь нулевой остаток."""
    def qty_of(item):
        return commerceml.parse_offers(offers_xml(item))[0][0].qty

    assert qty_of(offer(code1c="1c-1", qty="12")) == 12
    assert qty_of(offer(code1c="1c-1", houses=[("w1", "9"), ("w2", "3")])) == 12
    assert qty_of(offer(code1c="1c-1", houses=[("w1", "9"), ("w2", "3")],
                        changes=True)) == 12
    assert qty_of(offer(code1c="1c-1", nested=[("w1", "9"), ("w2", "3")])) == 12


def test_parse_reports_unusable_rows_without_failing_the_file():
    items, issues = commerceml.parse_catalog(catalog_xml(
        good(code1c="1c-1", article="A-1"),
        "<Товар><Ид>1c-2</Ид><Наименование>Без единицы</Наименование></Товар>",
        good(name="Без опознавательных знаков"),
        "<Товар><Артикул>A-4</Артикул>"
        '<БазоваяЕдиница НаименованиеКраткое="шт."/></Товар>',
    ))
    assert [i.article for i in items] == ["A-1"]
    assert [(i.index, i.reason) for i in issues] == [
        (2, "нет базовой единицы измерения"),
        (3, "нет ни кода 1С, ни артикула — сопоставить не с чем"),
        (4, "нет наименования"),
    ]


@pytest.mark.parametrize("raw, message", [
    (b"", "Файл пуст"),
    (b"<not xml", "не разобран"),
    ("<Прайс/>".encode("utf-8"), "Ожидался корень"),
])
def test_parse_refuses_unreadable_file(raw, message):
    with pytest.raises(commerceml.Malformed, match=message):
        commerceml.parse_catalog(raw)


def test_parse_points_at_the_right_section():
    with pytest.raises(commerceml.Malformed, match="Остатки"):
        commerceml.parse_catalog(offers_xml(offer(code1c="1c-1", qty="1")))
    with pytest.raises(commerceml.Malformed, match="Справочник"):
        commerceml.parse_offers(catalog_xml(good(code1c="1c-1", article="A-1")))


def test_base_id_splits_variant_identifier():
    assert commerceml.base_id("товар#вариант") == "товар"
    assert commerceml.base_id("товар") == "товар"
    assert commerceml.base_id(None) is None


def test_build_round_trips():
    items = [commerceml.ExportItem("1c-1", "A-1", "Труба", "м", Decimal("1.600")),
             commerceml.ExportItem(None, "A-2", "Болт", "шт.", None)]
    back, issues = commerceml.parse_catalog(
        commerceml.build_catalog(items, now=NOW))
    assert issues == []
    assert [(i.code1c, i.article, i.name, i.unit) for i in back] == [
        ("1c-1", "A-1", "Труба", "м"), (None, "A-2", "Болт", "шт.")]
    assert back[0].unit_weight == Decimal("1.6")

    stock = [commerceml.ExportStock("1c-1", "A-1", "Труба", "м", Decimal("120.500"))]
    offers, _ = commerceml.parse_offers(commerceml.build_offers(
        stock, warehouse_code="129", warehouse_name="Склад №3", now=NOW))
    assert offers[0].qty == Decimal("120.5")


def test_build_declares_namespace_and_version():
    raw = commerceml.build_catalog([], now=NOW).decode("utf-8")
    assert 'xmlns="urn:1C.ru:commerceml_2"' in raw
    assert 'ВерсияСхемы="2.07"' in raw
    assert "ДатаФормирования=" in raw


# --- Загрузка справочника --------------------------------------------------

def test_catalog_import_creates_and_updates(sender, sql):
    body = catalog_xml(
        good(code1c="НОВЫЙ-1", article="IMP-001", name="Новая позиция",
             unit="кг", weight="2.5"),
        good(code1c=PIPE_CODE, article=PIPE, name="Труба стальная 32×2 (1С)",
             unit="м", weight="1.6"),
    )
    result = sender.post("/catalog/import", content=body,
                         headers={"Content-Type": "application/xml"})
    assert result.status_code == 200, result.text
    assert result.json() == {"created": 1, "updated": 1, "unchanged": 0,
                             "skipped": 0, "issues": []}

    rows = sql("SELECT article, code1c, name, unit, unit_weight FROM catalog_item "
               "WHERE article IN ('IMP-001', :pipe) ORDER BY article", pipe=PIPE)
    assert rows[0] == ("100512", PIPE_CODE, "Труба стальная 32×2 (1С)", "м",
                       Decimal("1.600"))
    assert rows[1] == ("IMP-001", "НОВЫЙ-1", "Новая позиция", "кг", Decimal("2.500"))


def test_catalog_import_is_idempotent(sender):
    body = catalog_xml(good(code1c="ИДЕМ-1", article="IMP-002", name="Позиция"))
    first = sender.post("/catalog/import", content=body,
                        headers={"Content-Type": "application/xml"}).json()
    second = sender.post("/catalog/import", content=body,
                         headers={"Content-Type": "application/xml"}).json()
    assert (first["created"], first["unchanged"]) == (1, 0)
    assert (second["created"], second["unchanged"]) == (0, 1)


def test_catalog_import_matches_by_article_when_code_is_absent(sender, sql):
    body = catalog_xml(good(article=WASHER, name="Шайба из 1С", unit="шт."))
    assert sender.post("/catalog/import", content=body,
                       headers={"Content-Type": "application/xml"}
                       ).json()["updated"] == 1
    assert sql("SELECT name FROM catalog_item WHERE article = :a",
               a=WASHER)[0][0] == "Шайба из 1С"


def test_catalog_import_keeps_article_and_reports_the_difference(sender, sql):
    """Артикул — ключ для человека и ссылка из заказов: чужой файл его не меняет."""
    body = catalog_xml(good(code1c=PIPE_CODE, article="ДРУГОЙ-АРТИКУЛ",
                            name="Труба", unit="м"))
    result = sender.post("/catalog/import", content=body,
                         headers={"Content-Type": "application/xml"}).json()
    assert result["updated"] == 1
    assert "ДРУГОЙ-АРТИКУЛ" in result["issues"][0]["reason"]
    assert sql("SELECT article FROM catalog_item WHERE code1c = :c",
               c=PIPE_CODE)[0][0] == PIPE


def test_catalog_import_skips_new_item_whose_article_is_taken(sender):
    """Без кода 1С опереться не на что: артикул занят — позиция не заводится."""
    body = catalog_xml(good(article=BOLT, name="Двойник"))
    result = sender.post("/catalog/import", content=body,
                         headers={"Content-Type": "application/xml"}).json()
    assert (result["created"], result["updated"], result["skipped"]) == (0, 1, 0)

    body = catalog_xml(good(code1c="СВОЙ-КОД", article="СВОБОДНЫЙ", name="Новая"))
    assert sender.post("/catalog/import", content=body,
                       headers={"Content-Type": "application/xml"}
                       ).json()["created"] == 1


def test_catalog_import_refuses_to_merge_two_products_sharing_an_article(sender, sql):
    """В демо-выгрузке 1С один артикул носят два разных товара. Сопоставить их
    по артикулу значило бы слить две позиции в одну."""
    body = catalog_xml(good(code1c="ДРУГОЙ-ТОВАР-1С", article=BOLT,
                            name="Совсем другой товар"))
    result = sender.post("/catalog/import", content=body,
                         headers={"Content-Type": "application/xml"}).json()
    assert (result["created"], result["updated"], result["skipped"]) == (0, 0, 1)
    assert "разные товары" in result["issues"][0]["reason"]
    assert sql("SELECT name, code1c FROM catalog_item WHERE article = :a",
               a=BOLT)[0] == ("Болт М8×40 ГОСТ 7798", "ТМЦ-00421")


def test_catalog_import_updates_by_code_and_notes_the_article_difference(sender, sql):
    """Код 1С главнее: позиция найдена по нему, а расхождение по артикулу —
    замечание, а не пропуск."""
    body = catalog_xml(good(code1c=PIPE_CODE, article=BOLT, name="Труба из 1С"))
    result = sender.post("/catalog/import", content=body,
                         headers={"Content-Type": "application/xml"}).json()
    assert (result["updated"], result["skipped"]) == (1, 0)
    assert len(result["issues"]) == 1
    assert sql("SELECT name, article FROM catalog_item WHERE code1c = :c",
               c=PIPE_CODE)[0] == ("Труба из 1С", PIPE)


def test_catalog_import_applies_nothing_when_the_file_is_unreadable(sender, sql):
    before = sql("SELECT count(*) FROM catalog_item")[0][0]
    resp = sender.post("/catalog/import", content=b"<not xml",
                       headers={"Content-Type": "application/xml"})
    problem(resp, 400, "bad-request")
    assert sql("SELECT count(*) FROM catalog_item")[0][0] == before


def test_catalog_import_rejects_a_stock_file(sender):
    resp = sender.post("/catalog/import",
                       content=offers_xml(offer(code1c="1c-1", qty="1")),
                       headers={"Content-Type": "application/xml"})
    assert "Остатки" in problem(resp, 400, "bad-request")["detail"]


def test_catalog_import_needs_edit_rights(receiver, anon):
    """Кладовщик справочник только смотрит — правит менеджер (api.md §5)."""
    body = catalog_xml(good(code1c="1c-1", article="IMP-003"))
    resp = receiver.post("/catalog/import", content=body,
                         headers={"Content-Type": "application/xml"})
    problem(resp, 403, "forbidden")
    problem(anon.post("/catalog/import", content=body,
                      headers={"Content-Type": "application/xml"}),
            401, "unauthorized")


# --- Выгрузка справочника --------------------------------------------------

def test_catalog_export_round_trips_through_the_parser(sender):
    resp = sender.get("/catalog/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/xml")
    assert "import.xml" in resp.headers["content-disposition"]

    items, issues = commerceml.parse_catalog(resp.content)
    assert issues == []
    pipe = next(i for i in items if i.article == PIPE)
    assert (pipe.code1c, pipe.name, pipe.unit) == (PIPE_CODE, "Труба стальная 32×2", "м")
    assert pipe.unit_weight == Decimal("1.6")


def test_catalog_export_hides_archived_unless_asked(sender, sql):
    sql("UPDATE catalog_item SET is_archived = true WHERE article = :a", a=BOLT)
    plain, _ = commerceml.parse_catalog(sender.get("/catalog/export").content)
    whole, _ = commerceml.parse_catalog(
        sender.get("/catalog/export", params={"archived": True}).content)
    assert BOLT not in {i.article for i in plain}
    assert BOLT in {i.article for i in whole}


def test_catalog_export_is_open_to_viewers(receiver):
    assert receiver.get("/catalog/export").status_code == 200


# --- Загрузка остатков -----------------------------------------------------

def test_stock_import_levels_quantity_and_writes_a_movement(packer, wh, sql):
    """Инвентаризация: количество из файла становится остатком, разница —
    движением. Сумма движений обязана сходиться с балансом."""
    warehouse = wh[flows.WH3]
    before, _ = flows.balance(sql, PIPE, warehouse)
    target = before - Decimal("20.5")

    result = packer.post("/stock/import",
                         content=offers_xml(offer(code1c=PIPE_CODE, qty=target)),
                         headers={"Content-Type": "application/xml"})
    assert result.status_code == 200, result.text
    assert result.json()["updated"] == 1

    after, _ = flows.balance(sql, PIPE, warehouse)
    assert after == target
    kind, delta, balance_after, doc_type, _, comment, user_id = \
        flows.movements(sql, PIPE, warehouse)[-1]
    assert (kind, delta, balance_after) == ("recount", target - before, target)
    assert (doc_type, comment, user_id) == (None, "Импорт из 1С", packer.id)


def test_stock_import_leaves_untouched_quantities_alone(packer, wh, sql):
    warehouse = wh[flows.WH3]
    qty, _ = flows.balance(sql, PIPE, warehouse)
    moves = len(flows.movements(sql, PIPE, warehouse))

    result = packer.post("/stock/import",
                         content=offers_xml(offer(code1c=PIPE_CODE, qty=qty)),
                         headers={"Content-Type": "application/xml"}).json()
    assert (result["unchanged"], result["updated"]) == (1, 0)
    assert len(flows.movements(sql, PIPE, warehouse)) == moves


def test_stock_import_refuses_to_go_below_reserve(accepted_order, packer, wh, sql):
    """Резерв принадлежит живым заказам, а файл из 1С о них не знает."""
    warehouse = wh[flows.WH3]
    qty, reserved = flows.balance(sql, PIPE, warehouse)
    assert reserved > 0, "фикстура должна была зарезервировать трубу"

    result = packer.post(
        "/stock/import",
        content=offers_xml(offer(code1c=PIPE_CODE, qty=reserved - 1)),
        headers={"Content-Type": "application/xml"}).json()
    assert result["skipped"] == 1
    assert "резерв" in result["issues"][0]["reason"]
    assert flows.balance(sql, PIPE, warehouse) == (qty, reserved)


def test_stock_import_skips_items_missing_from_the_catalog(packer):
    result = packer.post("/stock/import",
                         content=offers_xml(offer(code1c="НЕТ-ТАКОГО", qty="5")),
                         headers={"Content-Type": "application/xml"}).json()
    assert result["skipped"] == 1
    assert "справочник" in result["issues"][0]["reason"]


def test_stock_import_sums_variants_of_one_product(packer, wh, sql):
    """Варианты товара приходят отдельными предложениями с составным Ид,
    а в справочнике им соответствует одна позиция."""
    result = packer.post("/stock/import", content=offers_xml(
        offer(code1c=f"{PIPE_CODE}#размер-1", qty="30"),
        offer(code1c=f"{PIPE_CODE}#размер-2", qty="12.5"),
    ), headers={"Content-Type": "application/xml"}).json()
    assert result["updated"] == 1
    assert flows.balance(sql, PIPE, wh[flows.WH3])[0] == Decimal("42.5")


def test_stock_import_creates_a_row_for_an_item_without_one(packer, wh, sql):
    """У склада-отправителя нет насоса — после загрузки появится строка."""
    assert flows.balance(sql, flows.PUMP, wh[flows.WH3]) == (Decimal("0"), Decimal("0"))
    result = packer.post("/stock/import",
                         content=offers_xml(offer(article=flows.PUMP, qty="7")),
                         headers={"Content-Type": "application/xml"}).json()
    assert result["created"] == 1
    assert flows.balance(sql, flows.PUMP, wh[flows.WH3])[0] == Decimal("7")


def test_stock_import_touches_only_the_callers_warehouse(packer, wh, sql):
    """Склад берётся из учётной записи: подменить его файлом нельзя."""
    other = wh[flows.WH1]
    before, _ = flows.balance(sql, BOLT, other)
    packer.post("/stock/import", content=offers_xml(
        offer(article=BOLT, qty="1", houses=[("любой-склад", "1")])),
        headers={"Content-Type": "application/xml"})
    assert flows.balance(sql, BOLT, other)[0] == before
    assert flows.balance(sql, BOLT, wh[flows.WH3])[0] == Decimal("1")


def test_stock_import_needs_edit_rights(sender, anon):
    """Менеджер остатки только смотрит — правит кладовщик (api.md §5)."""
    body = offers_xml(offer(code1c=PIPE_CODE, qty="1"))
    problem(sender.post("/stock/import", content=body,
                        headers={"Content-Type": "application/xml"}),
            403, "forbidden")
    problem(anon.post("/stock/import", content=body,
                      headers={"Content-Type": "application/xml"}),
            401, "unauthorized")


def test_stock_import_rejects_a_catalog_file(packer):
    resp = packer.post("/stock/import",
                       content=catalog_xml(good(code1c="1c-1", article="A-1")),
                       headers={"Content-Type": "application/xml"})
    assert "Справочник" in problem(resp, 400, "bad-request")["detail"]


# --- Выгрузка остатков -----------------------------------------------------

def test_stock_export_carries_own_warehouse_quantities(packer, wh, sql):
    resp = packer.get("/stock/export")
    assert resp.status_code == 200
    assert "offers.xml" in resp.headers["content-disposition"]

    offers, issues = commerceml.parse_offers(resp.content)
    assert issues == []
    pipe = next(o for o in offers if o.article == PIPE)
    assert pipe.qty == flows.balance(sql, PIPE, wh[flows.WH3])[0]
    assert pipe.code1c == PIPE_CODE


def test_stock_export_names_the_warehouse(packer):
    raw = packer.get("/stock/export").content.decode("utf-8")
    assert commerceml.warehouse_uid(flows.WH3) in raw


def test_stock_export_accepts_another_warehouse(packer, wh, sql):
    offers, _ = commerceml.parse_offers(
        packer.get("/stock/export", params={"warehouse_id": wh[flows.WH1]}).content)
    bolt = next(o for o in offers if o.article == BOLT)
    assert bolt.qty == flows.balance(sql, BOLT, wh[flows.WH1])[0]


def test_stock_export_round_trips_into_import(packer, wh, sql):
    """Выгрузили и тут же загрузили обратно — база не должна шелохнуться."""
    raw = packer.get("/stock/export").content
    moves = len(flows.movements(sql, PIPE, wh[flows.WH3]))
    result = packer.post("/stock/import", content=raw,
                         headers={"Content-Type": "application/xml"}).json()
    assert (result["created"], result["updated"], result["skipped"]) == (0, 0, 0)
    assert result["unchanged"] > 0
    assert len(flows.movements(sql, PIPE, wh[flows.WH3])) == moves


def test_stock_export_is_open_to_viewers(sender):
    assert sender.get("/stock/export").status_code == 200
