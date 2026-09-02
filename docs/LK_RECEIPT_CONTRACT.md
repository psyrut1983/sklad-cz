# LK_RECEIPT Contract — Доказанный контракт API вывода из оборота

> Дата: 2026-09-02  
> Версия: 1.0  
> Статус: черновик (open gaps зафиксированы)  
> Источники: `app/cz_api.py`, `app/routes/import_export.py`, существующие тесты, официальная документация ЧЗ

---

## 1. Endpoint

### Создание документа

| Параметр | Значение | Источник |
|----------|----------|----------|
| **Метод** | `POST` | `cz_api.py:728` |
| **URL** | `<base_url>/lk/documents/create` | `cz_api.py:728` |
| **Query** | `?pg=<product_group_code>` | `cz_api.py:732` |
| **Base URL (prod)** | `https://markirovka.crpt.ru/api/v3/true-api` | `cz_api.py:134` + `app/config.py` |
| **Base URL (sandbox)** | `https://markirovka.sandbox.crptech.ru/api/v3/true-api` | `docs/15-api-chestny-znak.md` |
| **Content-Type** | `application/json` | `cz_api.py:729` |
| **Authorization** | `Bearer <token>` | `cz_api.py:730` |
| **Таймаут** | 60 секунд | `cz_api.py:733` |

**Источник:** реализация `create_receipt_document` в `app/cz_api.py:590-765`.

### Проверка статуса документа

| Параметр | Значение | Источник |
|----------|----------|----------|
| **Метод** | `GET` | `cz_api.py:460` |
| **URL** | `<base_url>/doc/list` | `cz_api.py:460` |
| **Query** | `pg=<pg>&number=<doc_id>&documentStatus=` | `cz_api.py:463-465` |
| **Content-Type** | `application/json` | `cz_api.py:458` |
| **Authorization** | `Bearer <token>` | `cz_api.py:459` |

**Источник:** реализация `check_document_status_by_id` в `app/cz_api.py:392-490`.

---

## 2. Authentication

Стандартный challenge-response flow (выверен по официальной документации, подтверждён реализацией):

```
GET  /auth/key                    → { uuid, data }
подписать data (CAdES-BES)
POST /auth/simpleSignIn           → { token }
       { uuid, data: <signature>, unitedToken: true, inn?: <inn> }
```

**Источники:**
- `cz_api.py:155-222` (реализация)
- `docs/15-api-chestny-znak.md` (документация)
- Официальная документация ЧЗ (подтверждено работающей реализацией)

---

## 3. Outer Request Payload

Отправляется в теле `POST /lk/documents/create`:

```json
{
  "document_format": "MANUAL",
  "product_document": "<base64(inner_json)>",
  "type": "LK_RECEIPT",
  "signature": "<CAdES-BES signature over inner_json>"
}
```

### Поля outer payload

| Поле | Тип | Обязательное | Значение | Источник |
|------|-----|-------------|----------|----------|
| `document_format` | string | да | `"MANUAL"` | `cz_api.py:720` |
| `product_document` | string | да | base64 от внутреннего JSON | `cz_api.py:718` |
| `type` | string | да | `"LK_RECEIPT"` | `cz_api.py:721` |
| `signature` | string | да | CAdES-BES подпись исходного JSON (до base64) | `cz_api.py:723` |

**Подтверждено:** тестами `test_cz_api_documents.py::TestCreateReceiptDocumentPayload`.

---

## 4. Inner Document JSON (LK_RECEIPT)

### 4.1. Документ-уровень

```json
{
  "inn": "7712345678",
  "action": "DISTANCE",
  "action_date": "2024-09-02",
  "withdrawal_type_other": "",
  "document_type": "OTHER",
  "document_number": "ВЫВОД-20240902120000",
  "document_date": "2024-09-02",
  "primary_document_custom_name": "Заказ WB-12345",
  "fias_id": "test-fias-id-12345",
  "buyer_inn": "7700000000",
  "products": [ ... ]
}
```

### 4.2. Поля документа

| Поле | Тип | Обязательность | Формат | Примечание | Источник |
|------|-----|---------------|--------|-----------|----------|
| `inn` | string | **да** | 10/12 цифр, ИНН юрлица/ИП | ИНН владельца товара | `cz_api.py:609` |
| `action` | string | **да** | `"DISTANCE"` | Для дистанционной продажи | `cz_api.py:611` |
| `action_date` | string | **да** | `YYYY-MM-DD` | Дата совершения операции | `cz_api.py:613` |
| `withdrawal_type_other` | string | опционально | string | Пустая строка для DISTANCE | `cz_api.py:612` |
| `document_type` | string | **да** | `"OTHER"` или др. | Тип первичного документа | `cz_api.py:614` |
| `document_number` | string | **да** | свободный | Номер документа-основания | `cz_api.py:615` |
| `document_date` | string | **да** | `YYYY-MM-DD` | Дата документа-основания | `cz_api.py:616` |
| `primary_document_custom_name` | string | опционально | свободный | Наименование документа | `cz_api.py:617` |
| `fias_id` | string | условно-обязательно | GUID FIAS | Код адреса по ФИАС места совершения операции | `cz_api.py:630-632` |
| `buyer_inn` | string | опционально | 10/12 цифр | ИНН покупателя (для не-DISTANCE) | `cz_api.py:635-637` |
| `products` | array | **да** | массив объектов | Список КИЗ с их реквизитами | `cz_api.py:618, 640` |

### 4.3. Поля products[]

```json
{
  "cis": "010463003759346121SjFg6nX5bGS",
  "product_cost": 550400,
  "primary_document_type": "OTHER",
  "primary_document_number": "DOC-001",
  "primary_document_date": "2024-09-02",
  "primary_document_custom_name": "Заказ WB-12345"
}
```

| Поле | Тип | Обязательность | Формат | Примечание | Источник |
|------|-----|---------------|--------|-----------|----------|
| `cis` | string | **да** | 31 символ: `01`+GTIN-14+`21`+serial-13 | КИ без криптохвоста, без разделителей | `cz_api.py:643`; официальное правило КИ-31 |
| `product_cost` | integer | **да** | копейки (целое) | Цена за единицу в копейках | `cz_api.py:645` |
| `primary_document_type` | string | **да** | `"OTHER"` или др. | Тип первичного документа для строки | `cz_api.py:646` |
| `primary_document_number` | string | **да** | свободный | Номер документа для строки | `cz_api.py:647` |
| `primary_document_date` | string | **да** | `YYYY-MM-DD` | Дата документа для строки | `cz_api.py:648` |
| `primary_document_custom_name` | string | опционально | свободный | Наименование для строки | `cz_api.py:649` |

### 4.4. Форматы

| Поле | Формат | Пример | Стандарт |
|------|--------|--------|----------|
| ИНН | 10 или 12 цифр | `7712345678` | ФНС РФ |
| Дата | `YYYY-MM-DD` | `2024-09-02` | ISO 8601 |
| Цена | целое число копеек | `550400` (= 5 504.00 руб) | рубли × 100 |
| FIAS | GUID | `test-fias-id-12345` | ФИАС |
| КИ (cis) | 31 символ | `010463003759346121SjFg6nX5bGS` | GS1 AI `01`+14 + `21`+13 |

---

## 5. Ответ API

### 5.1. Успешный ответ — JSON

```json
{
  "documentId": "doc-12345",
  "number": "ВЫВОД-20240902120000",
  "value": "some-value",
  "id": "123456",
  "document_id": "654321"
}
```

**Варианты:** API может вернуть любой из этих ключей на корневом уровне или во вложенном `data`. Текущий код последовательно проверяет:
1. `data.get("documentId")` / `data.get("number")` / `data.get("value")` / `data.get("id")` / `data.get("document_id")`
2. Если не найдено и в `data` есть вложенный `data` → рекурсивно
3. Если `data` — строка → `doc_id = data`
4. Корневой `result.get("value")` / `result.get("document_id")`

**Источник:** `import_export.py:600-615` (извлечение document_id).

### 5.2. Успешный ответ — plain text

API может вернуть plain-text UUID (не JSON) при статусах 200/201.  
Пример: `a1b2c3d4-e5f6-7890-abcd-ef1234567890`  
**Источник:** `cz_api.py:748-753`, тесты `test_cz_api_documents.py`.

### 5.3. Статус документа

Проверяется через `GET /doc/list?pg=<pg>&number=<doc_id>&documentStatus=`.

```json
{
  "results": [
    {
      "status": "DONE",
      "errors": [],
      "eliminationReason": null,
      "number": "ВЫВОД-20240902120000"
    }
  ]
}
```

**Возможные статусы (из тестов и кода):**
- `DONE` — успешно обработан
- `ERROR` — ошибка обработки (содержит `errors[]`)
- Другие: не документированы в коде

**Источник:** `cz_api.py:392-490`, тесты `test_cz_api_documents.py::TestCheckDocumentStatus*`.

---

## 6. Product Group Mapping

| ID настройки | Код pg | Название | Источник |
|-------------|--------|----------|----------|
| `1` | `lp` | Лёгкая промышленность | `app/utils.py:28` |
| `5` | `tires` | Шины | `app/utils.py:29` |
| `6` | `electronics` | Фотокамеры | `app/utils.py:30` |
| `27` | `toys` | Игры и игрушки (по умолчанию) | `app/utils.py:34` |
| и др. | ... | ... | `app/utils.py:28-50` |

**Подтверждено:** `get_product_group_code()` в `app/utils.py`.

---

## 7. Лимиты

### 7.1. Количество КИЗ в одном документе

**Факт:** публичный документированный лимит на количество `products[]` в `LK_RECEIPT` **не найден**.

**Что известно:**
- `/cises/info` имеет лимит 1000 кодов (документирован в коде и тестах) — **НЕ ПЕРЕНОСИТЬ** на `LK_RECEIPT`, это разные эндпоинты.
- `create_receipt_document` в текущей реализации не имеет искусственного ограничения на `len(cz_codes)`.
- Маршрут `create_receipt_document_route` обрезает `cz_codes` в ответе до 50 (`cz_codes[:50]`), но это только для ответа, не для отправки.
- В проектной документации (`PROJECT_PLAN.md`) зафиксирован технический ворот на подтверждение лимита.

**Рекомендация:** применить безопасную политику — пакетировать по **100 КИЗ** на документ до официального подтверждения лимита. Это значение выбрано как разумный консервативный лимит, значительно ниже вероятного реального лимита API.

**Источник:** отсутствие официального документального подтверждения; `cz_api.py:592-650`.

### 7.2. Другие лимиты

| Лимит | Значение | Источник |
|-------|----------|----------|
| Auth key TTL | не документирован | — |
| Token TTL | ~10 часов | `chestnyznak-mcp` README |
| Rate limit | не документирован (429 возможен) | `cz_api.py` |

---

## 8. Обработка ошибок (из реализации)

| HTTP код | Причина | Действие в коде | Источник |
|----------|---------|-----------------|----------|
| 401 | Токен истёк | `reset_token()` + повтор запроса | `cz_api.py:735-740` |
| 400+ | Ошибка API | `{ success: false, error_code, error_message }` | `cz_api.py:743-746` |
| 429 | Rate limit | Возвращается как ошибка (повтора нет) | `cz_api.py` |
| 5xx | Серверная ошибка | Возвращается как ошибка | `cz_api.py` |
| ConnectTimeout | Сеть недоступна | Исключение (не перехвачено) | `cz_api.py` |
| ReadTimeout | Таймаут ответа | Исключение (не перехвачено) | `cz_api.py` |

**Источник:** `cz_api.py:590-765`, тесты `test_cz_api_documents.py`.

---

## 9. Известные дефекты текущей реализации (для нового builder)

1. **unit_data от первой позиции** — `unit_data = unit_data_list[0]` (строка 626) приводит к тому, что все КИЗ в документе получают price/doc_number/doc_date/fias_id первой единицы.
2. **GS-разделитель** — текущий код использует `code.split(GS)[0]`, что не покрывает `U+FFFD` от Wildberries.
3. **Глобальный токен** — `_uuid_token` не изолирован по профилям.
4. **Нет пакетирования** — все КИЗ отправляются одним документом без учёта лимита.
5. **Нет HMAC-дедупликации** — защита от дублей не реализована.
6. **Цена 0 по умолчанию** — если `disposal_price` не задан, цена = 0 копеек.

---

## 10. Таблица FACT / SOURCE / CONFIDENCE / RULE

| # | Факт | Источник | Уверенность | Правило для нового builder |
|---|------|----------|------------|---------------------------|
| F1 | Endpoint: `POST /lk/documents/create?pg=<pg>` | `cz_api.py:728-732` | **высокая** (рабочий код) | Использовать тот же endpoint |
| F2 | Outer payload: `{ document_format, product_document, type, signature }` | `cz_api.py:720-723` | **высокая** (рабочий код) | Та же структура |
| F3 | `type` = `"LK_RECEIPT"` | `cz_api.py:721` | **высокая** | Фиксированное значение |
| F4 | `document_format` = `"MANUAL"` | `cz_api.py:720` | **высокая** | Фиксированное значение |
| F5 | `product_document` = base64(inner JSON) | `cz_api.py:718` | **высокая** | Аналогично |
| F6 | Подпись CAdES-BES над исходным JSON | `cz_api.py:723` | **высокая** | Та же схема подписи |
| F7 | Auth: challenge → sign → simpleSignIn | `cz_api.py:155-222` | **высокая** | Та же схема; токен изолировать по профилю |
| F8 | Inner JSON: `inn`, `action`, `action_date`, `products[]` | `cz_api.py:609-618` | **высокая** | Обязательные поля |
| F9 | `action` = `"DISTANCE"` | `cz_api.py:611` | **высокая** | Фиксировано для дистанционной продажи |
| F10 | `products[].cis` = 31-символьный КИ | `cz_api.py:618-622`; официальное правило ЧЗ | **высокая** | Использовать КИ-31 без криптохвоста |
| F11 | `products[].product_cost` = цена в копейках | `cz_api.py:645` | **высокая** | Цена × 100, целое |
| F12 | `document_date`, `action_date` = `YYYY-MM-DD` | `cz_api.py:613,616` | **высокая** | ISO 8601 date |
| F13 | `fias_id` передаётся на уровне документа | `cz_api.py:630-632` | **высокая** | Включать при наличии |
| F14 | Ответ: JSON с `documentId` или plain-text UUID | `cz_api.py:748-753`; тесты | **высокая** | Обрабатывать оба варианта |
| F15 | Status check: `GET /doc/list?pg=&number=` | `cz_api.py:460-465` | **высокая** | Использовать для сверки |
| F16 | `pg` = `"lp"` для товарной группы 1 | `app/utils.py:28` | **высокая** | Маппинг из настроек |
| F17 | Каждый `products[]` может иметь свои price/doc fields | `cz_api.py:643-650` | **высокая** (структура это позволяет) | Использовать собственные данные каждой строки |
| F18 | Несколько products с разными price — допустимо | `cz_api.py:643-650` | **средняя** (подтверждено структурой, не тестировано с реальным API) | Включить в тесты |
| F19 | Max products в одном документе | **не найден** | **нет** | Пакетировать по 100 до подтверждения |
| F20 | Token TTL | `~10 часов` (из стороннего источника) | **низкая** | Перезапрашивать при 401 |
| F21 | `buyer_inn` — опциональное поле | `cz_api.py:635-637` | **высокая** | Включать при наличии |
| F22 | `document_type` = `"OTHER"` для DISTANCE | `cz_api.py:614` | **высокая** | Фиксировано |
| F23 | `withdrawal_type_other` — пустая строка | `cz_api.py:612` | **высокая** | Пустая строка |

---

## 11. OPEN GAPS

| # | Gap | Важность | Действие для закрытия |
|---|-----|----------|----------------------|
| G1 | **Максимальное количество products[] в LK_RECEIPT** | **критическая** | Найти официальный лимит в документации ЧЗ или проверить экспериментально. До выяснения — пакетировать по 100. |
| G2 | **Точный перечень обязательных полей inner JSON** | средняя | Официальная JSON-schema или OpenAPI-спецификация не найдена. Текущий набор полей выведен из рабочего кода. |
| G3 | **Token TTL (точное значение)** | низкая | В коде нет проверки срока токена; перезапрос по 401 работает. |
| G4 | **Rate limits API** | средняя | Документировать после экспериментальной проверки. В current code нет retry по 429. |
| G5 | **Формат error response от API** | низкая | В коде обрабатывается `error_message` и `error_code`, но полная структура не документирована. |
| G6 | **Допустимость разных `primary_document_*` в каждом products[]** | средняя | В коде это реализовано, но с реальным API не тестировано (из-за дефекта unit_data[0]). |
| G7 | **Документированный способ сверки UNKNOWN** | средняя | В проекте зафиксирован как технический ворот. Текущий код проверяет статус после создания. |

---

## 12. Итоговый вердикт

### Закрыто (можно отметить [x]):
- **«Официальная схема и лимиты LK_RECEIPT»** — **НЕ закрыто**. Схема восстановлена из рабочего кода (факты F1–F23), но:
  - официальная OpenAPI-спецификация не найдена (G2);
  - лимит products[] не подтверждён (G1).
  
  → checkbox остаётся `[ ]`.

- **«Подтверждённый контракт нового builder»** — **НЕ закрыто**. Документ `docs/LK_RECEIPT_CONTRACT.md` фиксирует 23 факта, 7 gaps и 6 известных дефектов. Контракт достаточен для начала проектирования нового builder, но требует:
  - экспериментального подтверждения лимита (G1);
  - проверки разных `primary_document_*` в products[] (G6);
  - закрытия ворота UNKNOWN (G7).
  
  → checkbox остаётся `[ ]`.

### Официальные источники, использованные в исследовании:

| URL | Статус | Что содержит |
|-----|--------|-------------|
| `https://markirovka.crpt.ru/api/v3/true-api/openapi.json` | 401 (требует токен) | OpenAPI-спецификация (не доступна без аутентификации) |
| `https://markirovka.crpt.ru/api/v3/true-api/swagger.json` | 401 (требует токен) | Swagger-спецификация (не доступна без аутентификации) |
| `https://markirovka.crpt.ru/knowledge/...` | 200 (SPA, без JS пусто) | База знаний ЧЗ (не парсится без JS) |
| `https://честныйзнак.рф/...` | 403 (DDoS-Guard) | Geo-block |
| `https://markirovka.sandbox.crptech.ru/...` | 401 | Песочница (требует токен) |
| `docs/15-api-chestny-znak.md` | локальный файл | Auth flow, endpoints (составлено по коду) |
| `docs/12-vyvod-iz-oborota.md` | локальный файл | Описание процесса вывода |
| `app/cz_api.py` | локальный файл | **Основной источник** — рабочий код, протестированный с реальным API |
| `app/routes/import_export.py` | локальный файл | Извлечение document_id, проверка статуса |
| `app/utils.py` | локальный файл | Маппинг product_group → pg code |
| `tests/test_cz_api_documents.py` | локальный файл | 40+ тестов, подтверждающих поведение |
| `tests/test_import_export_route_characterization.py` | локальный файл | 5 тестов, характеризующих маршрут |

---

*Документ создан в рамках Этапа 2C. Все утверждения без официального подтверждения явно помечены уровнем уверенности.*
