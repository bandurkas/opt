# Ruflo Agents — Справочник команд

## Синтаксис

```bash
ruflo agent spawn -t <type> --task "описание задачи"
```

> `ruflo agent run` — не существует. Используй `spawn`.

---

## Доступные типы агентов

| Тип | Назначение |
|-----|-----------|
| `coder` | Написание и рефакторинг кода |
| `researcher` | Исследование кода, анализ зависимостей |
| `tester` | Генерация unit/e2e/performance тестов |
| `reviewer` | Code review, security audit |
| `architect` | Проектирование архитектуры |
| `coordinator` | Координация нескольких агентов |
| `analyst` | Анализ данных и отчёты |
| `optimizer` | Оптимизация кода и производительности |
| `security-architect` | Проектирование безопасности |
| `security-auditor` | Аудит безопасности |
| `memory-specialist` | Работа с памятью агентов (AgentDB) |
| `swarm-specialist` | Управление роями агентов |
| `performance-engineer` | Анализ LCP, FCP, TTI, unused JS |
| `core-architect` | Ядро системы, критичная архитектура |
| `test-architect` | Проектирование тестовых стратегий |

---

## Примеры для GDI FutureWorks

```bash
# Анализ производительности (LCP, unused JS)
ruflo agent spawn -t performance-engineer --task "analyze /Users/styserg/Desktop/gdi_future_works"

# Code review ключевых файлов
ruflo agent spawn -t reviewer --task "review src/components/LanguageContext.tsx"

# Генерация unit тестов
ruflo agent spawn -t tester --task "generate unit tests for src/lib/"

# Поиск unused JS и dead code
ruflo agent spawn -t optimizer --task "find unused JS in Next.js app at /Users/styserg/Desktop/gdi_future_works"

# Security audit
ruflo agent spawn -t security-auditor --task "audit API routes in /Users/styserg/Desktop/gdi_future_works/src/app/api"
```

---

## Управление агентами

```bash
ruflo agent list              # список активных агентов
ruflo agent status <id>       # статус агента
ruflo agent logs <id>         # логи агента
ruflo agent stop <id>         # остановить агента
ruflo agent metrics           # метрики всех агентов
ruflo agent health            # здоровье агентов
```

---

## Статус системы

```bash
ruflo status                  # общий статус
ruflo swarm status            # статус роя
ruflo doctor                  # диагностика
ruflo memory stats            # статистика памяти
```
