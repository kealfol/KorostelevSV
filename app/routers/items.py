import logging
from threading import Lock
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Response
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(prefix="/items", tags=["items"])
fake_db = {1: {"name": "Foo", "price": 50.2}}
db_lock = Lock()
logger = logging.getLogger("lab6.items")


class Item(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(
        min_length=1, title="Название", description="Уникальное название товара",
        examples=["Телефон"],
    )
    price: float = Field(
        gt=0, allow_inf_nan=False, title="Цена", description="Цена больше нуля",
        examples=[199.99],
    )
    is_offer: bool | None = Field(
        None, title="Акционный товар", description="Участвует ли товар в акции",
    )


ItemId = Annotated[int, Path(title="ID товара", description="Положительный ID товара", ge=1)]


def check_unique_name(name: str, exclude_id: int | None = None):
    for item_id, item in fake_db.items():
        if item_id != exclude_id and item["name"].strip().casefold() == name.casefold():
            logger.warning("Duplicate item name, conflicting id=%s", item_id)
            raise HTTPException(status_code=409, detail="Item name already exists")


@router.get(
    "/{item_id}",
    summary="Получить товар",
    description="Поиск товара в словаре по ID. Неизвестный ID возвращает ошибку 404.",
    responses={404: {"description": "Товар не найден"}},
)
def read_item(
    item_id: ItemId,
    q: Annotated[str | None, Query(
        title="Поисковый запрос", description="Необязательная строка до 50 символов",
        max_length=50,
    )] = None,
):
    with db_lock:
        if item_id not in fake_db:
            logger.warning("Item not found: id=%s", item_id)
            raise HTTPException(status_code=404, detail="Item not found")
        return {"item_id": item_id, "item": fake_db[item_id].copy(), "q": q}


@router.post(
    "/", response_model=Item, summary="Создать товар",
    description="Сохраняет товар в памяти. Адрес нового товара указан в заголовке Location.",
    responses={409: {"description": "Название уже занято"}},
)
def create_item(item: Item, response: Response):
    # Проверка имени и запись выполняются вместе, в том числе при одновременных запросах.
    with db_lock:
        check_unique_name(item.name)
        item_id = max(fake_db, default=0) + 1
        fake_db[item_id] = item.model_dump()
    response.headers["Location"] = f"/items/{item_id}"
    logger.info("Item created: id=%s", item_id)
    return item


@router.put(
    "/{item_id}", response_model=Item, summary="Обновить товар",
    description="Полностью заменяет существующий товар и проверяет уникальность названия.",
    responses={
        404: {"description": "Товар не найден"},
        409: {"description": "Название уже занято"},
    },
)
def update_item(item_id: ItemId, item: Item):
    with db_lock:
        if item_id not in fake_db:
            logger.warning("Item not found for update: id=%s", item_id)
            raise HTTPException(status_code=404, detail="Item not found")
        check_unique_name(item.name, exclude_id=item_id)
        fake_db[item_id] = item.model_dump()
    logger.info("Item updated: id=%s", item_id)
    return item
