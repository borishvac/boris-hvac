# Калькулятор сметы «вентиляция и кондиционирование под ключ»

Публичный калькулятор для сайта на Tilda. Подбирает приточку Breezart по актуальному прайс-листу, считает 3 варианта комплектации (Базово / Комфорт / Люкс) и формирует PDF КП для скачивания клиентом.

## Что внутри

```
backend/
  parser.py           — парсер PDF прайса Breezart → catalog.json
  requirements.txt
.github/workflows/
  update-catalog.yml  — GitHub Actions: парсинг раз в месяц
frontend/
  catalog.json        — текущий снимок каталога (обновляется автоматически)
  calculator.html     — единый файл калькулятора для вставки в Tilda
```

## Архитектура

```
                                                  раз в месяц
   ┌──────────────────┐         GitHub Actions       ┌────────────────┐
   │ breezart.ru/     │ ──────  скачивает PDF, ────► │ catalog.json   │
   │  /tech/...pdf    │         парсит, коммитит     │ в репозитории  │
   └──────────────────┘                               └───────┬────────┘
                                                              │
                                                       GitHub Pages
                                                              │
                                                              ▼
       ┌─────────────────────────────────────────────────────────────┐
       │  Tilda (блок T123) — calculator.html                        │
       │  1. Загружает catalog.json                                  │
       │  2. Опрос пользователя                                      │
       │  3. Расчёт 3 комплектаций                                   │
       │  4. Форма «Имя+Телефон» → Tilda webhook                     │
       │  5. PDF КП через jsPDF                                      │
       └─────────────────────────────────────────────────────────────┘
```

## Развёртывание шаг за шагом

### 1. Создать репозиторий на GitHub

```bash
# Клонировать этот проект
git init breezart-calc && cd breezart-calc
# (или загрузить файлы вручную)

# Закоммитить и запушить
git add . && git commit -m "init"
git remote add origin git@github.com:YOUR_USER/breezart-calc.git
git push -u origin main
```

### 2. Включить GitHub Pages

`Settings → Pages → Source: Deploy from branch → Branch: main /(root)`

После сохранения через 1–2 минуты будет доступно:

```
https://YOUR_USER.github.io/breezart-calc/catalog.json
https://YOUR_USER.github.io/breezart-calc/calculator.html  (для теста)
```

### 3. Дать Actions права на коммит

`Settings → Actions → General → Workflow permissions → Read and write permissions → Save`

### 4. Запустить парсер вручную (первый раз)

`Actions → Update Breezart catalog → Run workflow`

Через ~30 секунд в репозитории должен появиться обновлённый `frontend/catalog.json`. Дальше — раз в месяц автоматически (1-е число, 03:00 UTC).

### 5. Настроить calculator.html

Открыть `frontend/calculator.html` и заменить три константы в начале блока `<script>`:

```js
const CATALOG_URL = "https://YOUR_USER.github.io/breezart-calc/catalog.json";
const TILDA_WEBHOOK_URL = "https://forms.tildacdn.com/procces/";  // получите в Tilda
const COMPANY = {
  name: "Ваша компания",
  phone: "+7 (___) ___-__-__",
  email: "info@yourcompany.ru",
  site: "yourcompany.ru",
};
```

**Как получить TILDA_WEBHOOK_URL.** В Tilda создайте форму (любой блок с формой), в её настройках укажите вашу почту/CRM для получения заявок. Подсмотрите URL отправки в DevTools (Network → отправьте тест → найдите POST на `forms.tildacdn.com`) либо используйте Zapier / Make для маршрутизации в Bitrix24 / amoCRM.

### 6. Вставить в Tilda

В нужной странице добавить блок **T123 «HTML-код»** (Другое → HTML-код) и вставить **всё содержимое** `calculator.html` целиком, включая `<style>` и `<script>`. Опубликовать.

## Коммерческие правила

Все правила вынесены в объект `RULES` в начале `<script>`. Менять там же:

| Что | Где |
|---|---|
| Запас по производительности | `RULES.RESERVE` |
| Цены за 100 м² (утепл., крепёж, транспорт) | `RULES.HARDWARE_PER_100M2` и пр. |
| Категория кондиционера | `RULES.AC_THRESHOLD_AREA`, `AC_THRESHOLD_ROOMS` |
| Цены кондиционеров | `RULES.AC_INDUSTRIAL_TABLE`, `AC_RESI_LUX_TABLE` и пр. |
| Стоимость на комнату | `RULES.PER_ROOM_AC` |
| Увлажнение | `RULES.HUMIDIFIER` |
| ПНР | `RULES.COMMISSIONING_PCT` |
| Озонация | `RULES.OZONATION_LUX_HOUSE` |

## Что делать, если парсер сломался

GitHub Actions присылает уведомление о падении на email владельца репозитория. В этом случае:

1. Зайти в `Actions → последний неудачный запуск → логи` — посмотреть ошибку.
2. Если структура PDF поменялась (Breezart перенёс/переименовал серию), править регулярки или маркеры в `backend/parser.py`.
3. Пока парсер не починен, фронт продолжает работать на последнем удачно собранном `catalog.json` (он не перезаписывается при ошибке) и на встроенном `FALLBACK_CATALOG`.

## Локальное тестирование

```bash
# Python 3.10+, pip
cd backend
pip install -r requirements.txt
python parser.py ../frontend/catalog.json

# Открыть фронт локально (любой http-сервер)
cd ../frontend
python -m http.server 8000
# → http://localhost:8000/calculator.html
```

## Что не покрывает калькулятор

- Объекты с бассейном — показывается дисклеймер, расчёт не учитывает бассейн.
- Объекты <50 или >800 м² — выводится сообщение «свяжитесь индивидуально».
- ПВУ с рекуператорами (серии Lux RP, RR, Aqua RP) — пока не входит в логику подбора. Если понадобятся — добавлять в `pickIntakeUnit`.
- Точные аэродинамические характеристики (расход vs давление) — Breezart использует БД с кривыми, у нас её нет. На практике для типовых сетей не критично.

## Поддержка прайса Breezart

Парсер ищет серии по заголовкам PDF: «Lux - приточные установки», «Aqua - приточные установки» и т.д. Если Breezart переименует заголовок — поправить список `SERIES_MARKERS` в `backend/parser.py`.

Источник прайса: <https://breezart.ru/tech/price_breezart.pdf>
