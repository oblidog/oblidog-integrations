# Integracja NJU Mobile

Integracja pobiera faktury oraz podsumowanie jednego konta NJU Mobile i
synchronizuje je z jedną kategorią Oblidog Ledger. Dla każdego konta należy
uruchamiać oddzielną instancję z własnym plikiem poświadczeń i kategorią.

## Konfiguracja

Utwórz plik konfiguracyjny na podstawie szablonu:

```bash
cp .env.nju.example .env.nju
```

Wymagane zmienne:

```text
NJU_PHONE=...
NJU_PASSWORD=...
OBLIDOG_URL=https://...
OBLIDOG_API_KEY=...
OBLIDOG_CATEGORY_CODE=...
```

`NJU_ACCOUNT_NAME` jest opcjonalny i służy wyłącznie do identyfikacji konta w
logach.

Uruchomienie lokalne:

```bash
make run-nju
```

Schemat danych podsumowania można wypisać poleceniem:

```bash
make print-nju-schema
```

## Przepływ biznesowy

```text
NJU Mobile                              Oblidog Ledger
─────────                              ──────────────
faktury bieżącego okresu ─────────────► bieżący obligation i komponenty faktur
faktury poprzedniego okresu ──────────► komponenty poprzedniego obligation
podsumowanie konta ───────────────────► snapshot category-data
```

Integracja używa czasu `Europe/Warsaw`. Okres na portalu ma format `MM.RRRR`.
Dla faktur bieżącego okresu targetem jest obligation:

```text
<OBLIDOG_CATEGORY_CODE>-YYYY-MM
```

Faktury poprzedniego okresu są również odświeżane jako komponenty. Pozwala to
zachować spójną historię, gdy faktura lub jej status zmieni się po zakończeniu
miesiąca.

## Faktury i komponenty

Każda faktura jest upsertowana jako component typu `invoice`:

| Pole Ledger | Wartość z NJU |
| --- | --- |
| `external_id` | numer dokumentu |
| etykieta | numer dokumentu |
| kwota | `kwota zapłacona + do zapłaty` |
| źródło | `nju` |
| metadane | daty, kwoty częściowe, okres, status i flaga opłacenia |

Numer dokumentu jest stabilnym identyfikatorem komponentu. Powtórne
uruchomienie aktualizuje istniejący component, zamiast tworzyć duplikat.

## Bieżący obligation

Jeżeli w bieżącym okresie są faktury, integracja oczekuje dokładnie jednego
obligation dla kategorii i miesiąca. Następnie wylicza:

| Pole Ledger | Reguła |
| --- | --- |
| `current_amount` | suma pełnych kwot wszystkich faktur bieżącego okresu |
| `issue_date` | najwcześniejsza data wystawienia faktury |
| `due_date` | najwcześniejszy termin płatności faktury |
| docelowy lifecycle | `ready`, albo `paid`, gdy wszystkie faktury mają status `zapłacona` |

W lifecycle `draft` i `collecting_data` integracja aktualizuje dane, oznacza
obligation jako `ready`, a przy pełnym opłaceniu także jako `paid`.

Gdy obligation jest już `ready` albo `paid`, niezmienione dane powodują no-op.
Zmiana kwoty, dat lub docelowego lifecycle powoduje jego ponowne otwarcie,
aktualizację i ponowne przejście do `ready` lub `paid`. W innych stanach
lifecycle integration pozostawia obligation bez zmian i zapisuje ostrzeżenie.

## Snapshot category-data

Podsumowanie konta jest dostępne niezależnie od faktur i jest eksportowane jako
snapshot `category-data`, gdy różni się od ostatniego zapisanego rekordu.

Snapshot obejmuje:

```text
overpayment
amount_due
last_payment_amount
billing_period_start
billing_period_end
liability_limit
```

Brak lub zmiana formatu podsumowania nie blokuje synchronizacji faktur:
integracja loguje ostrzeżenie i kontynuuje. Nieudane logowanie lub nieprawidłowe
dane faktury kończą uruchomienie błędem.

## Brak faktur

Jeśli portal nie zawiera faktur ani dla bieżącego, ani poprzedniego okresu,
integracja kończy pracę po ewentualnym eksporcie podsumowania konta. Nie tworzy
ani nie zmienia wtedy obligation.
