# Integracja e-Kartoteka

Integracja uruchamiana przez `make run-ekartoteka` pobiera dane z e-Kartoteki
i synchronizuje je z jedną kategorią oraz jej obligation w Oblidog.

## Konfiguracja

W pliku `.env.ekartoteka` wymagane są:

```text
EKARTOTEKA_USERNAME=...
EKARTOTEKA_PASSWORD=...
OBLIDOG_URL=https://...
OBLIDOG_API_KEY=...
```

Opcjonalnie `OBLIDOG_LOG_FORMAT=json` zmienia logi na JSON.

## Co robi `run()`

Jeden run wykonuje kolejno:

1. Eksport rocznego snapshotu rozliczeń jako `category-data`.
2. Upsert komponentów opłat dla poprzedniego i bieżącego miesiąca
   obligation.
3. Uzupełnienie danych i przejście do `ready` dla tych samych dwóch
   obligation, o ile e-Kartoteka opublikowała naliczenie, a lifecycle jest
   `draft` lub `collecting_data`.
4. Kontrolę bieżącego obligation: gdy nie ma naliczenia, obligation w stanie
   innym niż `draft` / `collecting_data` zostaje oznaczone jako `error`.

Klucz obligation ma format:

```text
<kod kategorii z kontekstu integracji>-YYYY-MM
```

## Mapowanie okresów

E-Kartoteka publikuje naliczenie z datą `DataOd` w miesiącu poprzedzającym
miesiąc płatności. Dlatego:

| `DataOd` w e-Kartotece | obligation w Oblidog |
| --- | --- |
| `2026-07-01` | `YYYY-08` |
| `2026-08-01` | `YYYY-09` |

Run obsługuje poprzedni miesiąc właśnie po to, aby nadrobić naliczenie
opublikowane przed spóźnionym uruchomieniem integracji.

## Komponenty obligation

Z endpointów opłat miesięcznych pobierane są lokale, okresy oraz pozycje
`Nalicz`. Każda pozycja staje się komponentem typu `monthly_fee`.

Komponent ma stabilne `external_id`:

```text
<id-lokalu>:<id-naliczenia>:<indeks-pozycji>
```

W metadanych komponentu pozostają surowe dane lokalu, okresu oraz pozycji.
Komponenty służą do rozbicia kwoty; nie są źródłem `current_amount`, ponieważ
nie obejmują m.in. odsetek.

## Kwota, daty i lifecycle obligation

Publikacja okresu opłat jest warunkiem uruchamiającym zasilenie obligation.
Następnie integracja wylicza:

| Pole Oblidog | Wartość |
| --- | --- |
| `current_amount` | suma `DoZaplaty` z rocznych kartotek kont 204, 206 i 210 dla miesiąca obligation |
| `issue_date` | `DataOd` opublikowanego okresu opłat |
| `due_date` | 15. dzień miesiąca obligation |
| lifecycle | `ready`, po zapisaniu powyższych pól |

W rocznej kartotece e-Kartoteki `Mc` jest indeksem zero-based: `0` oznacza
styczeń, a `7` sierpień. Dla obligation `YYYY-08` wybierany jest zatem wpis
z `Mc = 7`.

Do `current_amount` używane jest wyłącznie `DoZaplaty`. Nie używamy:

- `Zaplacono`, bo wpłata może zostać zaksięgowana w kolejnym miesiącu;
- `Zaleglosc` i `s`, bo są saldem narastającym, zależnym od płatności;
- bieżących sald kont, z tego samego powodu.

Przykład z `reverse/rozliczenia.txt`: wpis 204 dla `Mc = 7` ma
`DoZaplaty = 522.31`, a odpowiadająca mu spóźniona wpłata jest widoczna przy
`Mc = 8`. Właściwa kwota obligation za sierpień nadal pochodzi z `Mc = 7`.

Jeśli lifecycle nie jest `draft` ani `collecting_data`, dane nie są
nadpisywane.

## Snapshot category-data

Snapshot zawiera płaskie pola dla kont 204, 206 i 210:

```text
account_<symbol>_credit
account_<symbol>_debit
account_<symbol>_balance
```

oraz daty aktualizacji kategorii `DK`, `DKL`, `LI`, `NRB`, `NL` jako pola
`update_<category>_at`. Daty bez strefy czasowej z e-Kartoteki są
interpretowane jako `Europe/Warsaw` i eksportowane jako `date-time` RFC 3339.

Przed utworzeniem rekordu integracja porównuje cały nowy snapshot z ostatnim
rekordem category-data tej kategorii. Identyczny snapshot nie jest ponownie
zapisywany.

Schemat JSON można wypisać poleceniem:

```bash
make print-ekartoteka-schema
```
