# Бриф: GDI FutureWorks

## Общие параметры

- **Account ID:** act_2043250756570558
- **Валюта:** IDR
- **Общий дневной бюджет:** 180,000 IDR/день (≈ все кампании вместе)

## Активные кампании / направления

| Направление | Campaign ID | Цель CPL | Бюджет/день | Приоритет | Статус | Тип |
|-------------|-------------|----------|-------------|-----------|--------|-----|
| WA Indonesia | 120245389315770432 | 4,000 IDR | 45,000 IDR | высокий | активен | whatsapp |
| WA Malaysia | — | 6,000 IDR | 45,000 IDR | средний | активен | whatsapp |
| Data Analyst WA | 120245608538570432 | 4,000 IDR | 45,000 IDR | средний | активен | whatsapp |
| Site Indonesia | — | 600 IDR | 45,000 IDR | средний | активен | site_leads |

## Метрики успеха

- **WA кампании:** `onsite_conversion.total_messaging_connection` = WA-диалоги (лиды)
- **Site кампании:** `offsite_conversion.fb_pixel_lead` = Pixel Lead события
- **CPL WA:** spend / WA-диалогов
- **CPL Site:** spend / Pixel Lead

## Таргетинг по умолчанию (для новых adsets)

### WA Indonesia / Data Analyst
```json
{
  "age_min": 18,
  "age_max": 45,
  "genders": [1, 2],
  "geo_locations": { "countries": ["ID"] },
  "targeting_automation": { "advantage_audience": 1 },
  "device_platforms": ["mobile"]
}
```

### WA Malaysia
```json
{
  "age_min": 18,
  "age_max": 45,
  "genders": [1, 2],
  "geo_locations": { "countries": ["MY"] },
  "targeting_automation": { "advantage_audience": 1 },
  "device_platforms": ["mobile"]
}
```

## История CPL (справочно)

| Дата | WA Indonesia CPL | Site CPL (LPV) | Malaysia CPL | Data Analyst CPL |
|------|-----------------|----------------|--------------|-----------------|
| Apr 7-11 | 3,681 IDR | ~548 IDR | 6,313 IDR | — (только старт) |
