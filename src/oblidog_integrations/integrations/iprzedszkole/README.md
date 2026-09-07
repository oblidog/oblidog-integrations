# Integracja iPrzedszkole

Integracja pobiera bieżące należności jednego dziecka z portalu iPrzedszkole i
przekazuje je do jednej kategorii w Oblidog Ledger. Jest przeznaczona do
uruchamiania okresowego, np. raz dziennie.

## Konfiguracja

Utwórz plik konfiguracyjny na podstawie szablonu:

```bash
cp .env.iprzedszkole.example .env.iprzedszkole
```

Wymagane są następujące zmienne:

```text
IPRZEDSZKOLE_KINDERGARTEN=...
IPRZEDSZKOLE_LOGIN=...
IPRZEDSZKOLE_PASSWORD=...
OBLIDOG_URL=https://...
OBLIDOG_API_KEY=...
OBLIDOG_CATEGORY_CODE=IPRZ
```

`OBLIDOG_CATEGORY_CODE` musi zawierać dokładnie cztery litery. Opcjonalne
`IPRZEDSZKOLE_ACCOUNT_NAME` służy wyłącznie do identyfikacji konta w logach.

Uruchomienie lokalne:

```bash
make run-iprzedszkole
```

## Przepływ biznesowy

```text
iPrzedszkole                         Oblidog Ledger
─────────────                         ──────────────
logowanie → wybrane dziecko
raport należności ──────────────────► snapshot category-data
lista opłat ─────────────────────────► trzy komponenty bieżącego obligation
```

Integracja używa bieżącej daty w strefie `Europe/Warsaw`. Raport roczny jest
pobierany dla roku szkolnego, który zaczyna się we wrześniu. Z raportu wybierany
jest wpis dla bieżącego miesiąca kalendarzowego; gdy portal go jeszcze nie
opublikował, używany jest najnowszy dostępny wpis.

### Snapshot kategorii

Następujące salda z raportu rocznego są zapisywane jako płaski rekord
`category-data`:

| iPrzedszkole | Ledger |
| --- | --- |
| `DoZaplaty` | `summary_to_pay` |
| `Zaplacono` | `summary_paid` |
| `Zaleglosc` | `summary_overdue` |
| `Nadplata` | `summary_overpayment` |
| opłata stała | `costs_fixed` |
| wyżywienie | `costs_meal` |
| opłaty dodatkowe | `costs_additional` |

Snapshot jest tworzony tylko wtedy, gdy różni się od ostatniego rekordu danych
kategorii. Dzięki temu nie powstają puste obserwacje przy niezmienionych danych.

### Komponenty obligation

Szczegółowa lista opłat jest zapisywana w obligation bieżącego miesiąca o
kluczu:

```text
<OBLIDOG_CATEGORY_CODE>-YYYY-MM
```

| Rodzaj opłaty w iPrzedszkole | Component Ledger | Etykieta | `external_id` |
| --- | --- | --- | --- |
| `0` | `monthly_fee` | Opłata stała | `costs_fixed` |
| `2` | `monthly_fee` | Wyżywienie | `costs_meal` |
| `1` | `monthly_fee` | Opłaty dodatkowe | `costs_additional` |

Każde uruchomienie wykonuje upsert wszystkich trzech komponentów. Stałe
`external_id` sprawiają, że dana pozycja jest aktualizowana, a nie duplikowana.
Brak opłaty jest zapisywany jako `0.00`.

## Granice odpowiedzialności

Integracja nie tworzy obligation i nie zmienia jego `current_amount`, dat ani
lifecycle (`ready`, `paid`). Obligation musi istnieć w Ledger przed pierwszym
uruchomieniem integracji.

`summary_to_pay` jest saldem widocznym w portalu i może obejmować zaległości,
nadpłaty albo korekty. Nie musi więc być równy sumie trzech komponentów opłat;
komponenty opisują rozbicie bieżących pozycji, a snapshot opisuje saldo konta.
