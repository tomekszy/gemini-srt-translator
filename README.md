# SRT Subtitle Translator (Gemini API)

Narzędzie CLI do automatycznego tłumaczenia napisów filmowych w formacie `.srt` z języka angielskiego na język polski przy użyciu modeli Google Gemini. Zaprojektowane z myślą o zachowaniu spójności nazewnictwa własnego, bezpiecznym zapisie przyrostowym oraz łatwym wznawianiu przerwanej pracy.

---

## Struktura projektu

- `run_translate.sh` — skrypt uruchomieniowy Bash, który automatycznie przygotowuje środowisko wirtualne Pythona (`.venv`), instaluje zależności, kopiuje klucz `GEMINI_API_KEY` i uruchamia właściwy program.
- `translate_srt_gemini.py` — główny skrypt parsujący pliki `.srt`, komunikujący się z API Gemini i zapisujący wynik do plików `*.pl.srt`.
- `keywords.json` *(opcjonalny)* — plik konfiguracyjny z tytułem produkcji oraz listą nazw własnych, które mają pozostać nieprzetłumaczone.
- `.env` — plik ze zmienną środowiskową `GEMINI_API_KEY`.

---

## Jak działa program (krok po kroku)

1. **Inicjalizacja środowiska (`run_translate.sh`)**:
   - Sprawdza, czy istnieje katalog `.venv`; jeśli nie, tworzy wirtualne środowisko Pythona.
   - Aktywuje `.venv` i sprawdza obecność biblioteki `google-genai` (w razie braku instaluje ją automatycznie).
   - Odczytuje `GEMINI_API_KEY` z głównego pliku `.env`, kopiuje go do `.venv/.env` i eksportuje do środowiska procesu.
2. **Konfiguracja tłumaczenia**:
   - Program sprawdza, czy w katalogu roboczym, katalogu pliku źródłowego lub katalogu skryptu znajduje się plik `keywords.json`.
   - Jeśli `keywords.json` istnieje: wczytuje `title` oraz listę `keywords`, wypisuje je w konsoli i **pomija** ręczne pytania o tytuł i nazwy własne.
   - Jeśli `keywords.json` nie istnieje: pyta interaktywnie o tytuł filmu oraz listę nazw własnych (oddzielonych przecinkami, dopasowanie *case-insensitive*).
   - Następnie pyta, **od którego numeru bloku SRT** rozpocząć tłumaczenie (`Enter` oznacza start od początku, czyli `0`).
3. **Wznawianie od wskazanego bloku**:
   - Jeśli podano numer początkowy (np. `422`), program otwiera istniejący plik `*.pl.srt`, zachowuje w niezmienionej formie wszystkie przetłumaczone bloki o numerach mniejszych niż `422` (`1..421`), a tłumaczenie uruchamia od bloku `#422` do końca pliku.
4. **Tłumaczenie paczkami (Batching) i zapis przyrostowy**:
   - Linie dialogowe są grupowane w paczki po 25 sztuk z przypisanymi identyfikatorami (`{"id": 0, "text": "..."}`), co zapobiega gubieniu lub sklejaniu linii przez model.
   - Zachowywane jest formatowanie kursywy (`<i>...</i>`) oraz pomijane są stopki grup napisowych (np. `YTS.MX`, `YIFY`).
   - Po każdej przetłumaczonej paczce wynik jest natychmiast zapisywany do pliku `<nazwa_pliku>.pl.srt`.
5. **Odporność na błędy i rotacja modeli**:
   - Program korzysta z listy modeli (`gemini-3.5-flash-lite`, `gemini-3.8-flash`, `gemini-3-flash-preview`, `gemini-3.6-flash`, `gemini-2.5-flash-lite`).
   - W przypadku błędu `429` (limit zapytań) lub `503` (przeciążenie modelu) automatycznie przełącza się na kolejny model z listy i ponawia próbę (do 40 prób na paczkę, a w ostateczności dzieli paczkę na mniejsze części).
   - W przypadku błędu `404` (model niedostępny lub wycofany) natychmiast usuwa dany model z listy aktywnych w bieżącej sesji bez marnowania czasu na oczekiwanie.

---

## Wymagania

- System Linux / macOS z zainstalowanym `python3` (z modułem `venv`) oraz `bash`.
- Klucz API Google Gemini w pliku `.env`:

```env
GEMINI_API_KEY=twoj_klucz_api_z_google_ai_studio
```

## Instrukcja użytkowania

1. **Nadanie uprawnień skryptowi startowemu (jednorazowo)**:
   `chmod +x run_translate.sh`

2. **Tłumaczenie konkretnego pliku .srt**:
   `./run_translate.sh Nazwa.Filmu.2018.1080p.srt`
   Wynik zostanie zapisany jako Nazwa.Filmu.2018.1080p.pl.srt.

3. **Tłumaczenie wszystkich plików .srt w bieżącym katalogu**:
   `./run_translate.sh`
   Program automatycznie znajdzie wszystkie pliki *.srt (z pominięciem gotowych *.pl.srt) i przetłumaczy je po kolei.

4. **Kontynuowanie tłumaczenia przerwanego pliku**:
   Jeśli plik Film.pl.srt jest przetłumaczony np. do napisu nr 421, a od 422 zaczyna się tekst oryginalny:
   Uruchom `./run_translate.sh Film.srt.`
   Na pytanie Od którego numeru napisu (bloku SRT) tłumaczymy plik? wpisz 422 i zatwierdź klawiszem Enter.
   Bloki 1..421 pozostaną nienaruszone, a program dokończy tłumaczenie od 422 wzwyż.
