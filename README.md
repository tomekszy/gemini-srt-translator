# SRT Subtitle Translator (Gemini API)
# SRT Subtitle Translator (Gemini API)

Uniwersalne narzędzie CLI do automatycznego tłumaczenia napisów filmowych w formacie `.srt` przy użyciu modeli Google Gemini. Automatycznie wykrywa dostępne modele tekstowe na kluczu API, obsługuje konfigurację z pliku `keywords.json` (tytuł, języki, słownik nazw własnych, pomijane frazy) oraz pozwala wznawiać tłumaczenie od dowolnego numeru napisu.

---

## Struktura projektu

- `run_translate.sh` — skrypt uruchomieniowy Bash, który tworzy lub odbudowuje środowisko wirtualne Pythona (`.venv`), instaluje bibliotekę `google-genai`, kopiuje `GEMINI_API_KEY` do `.venv/.env` i uruchamia program.
- `translate_srt_gemini.py` — główny skrypt parsujący pliki `.srt`, wykrywający dostępne modele Gemini, zarządzający paczkami tłumaczeń i zapisujący pliki wyjściowe.
- `keywords.json` *(opcjonalny)* — plik konfiguracyjny z tytułem filmu, językiem źródłowym i docelowym, listą nazw własnych oraz frazami pomijanymi przy tłumaczeniu.
- `.env` — plik przechowujący zmienną środowiskową `GEMINI_API_KEY`.

---

## Jak działa program (krok po kroku)

1. **Inicjalizacja środowiska (`run_translate.sh`)**:
   - Sprawdza, czy katalog `.venv` istnieje i ma poprawne ścieżki (w razie przeniesienia katalogu projektu automatycznie odbudowuje `.venv`).
   - Instaluje pakiet `google-genai` wewnątrz `.venv`, jeśli nie jest jeszcze dostępny.
   - Pobiera `GEMINI_API_KEY` z głównego pliku `.env`, zapisuje go w `.venv/.env` i eksportuje do środowiska procesu.
2. **Automatyczne wykrywanie modeli (`discover_usable_models`)**:
   - Na starcie program odpytuje API Gemini (`client.models.list()`) o wszystkie modele dostępne dla podanego klucza.
   - Odrzuca modele nieobsługujące generowania tekstu (np. `embedding`, `imagen`, `veo`, `tts`, `transcribe`, `audio`, `computer-use`, `robotics`, `deep-research`).
   - Sortuje pozostałe modele według przydatności do tłumaczeń wsadowych (priorytet dla modeli `flash-lite`, następnie `flash`, na końcu `pro`) i używa całej wykrytej listy jako puli roboczej i zapasowej.
3. **Wczytywanie konfiguracji (`keywords.json` lub tryb interaktywny)**:
   - Program szuka pliku `keywords.json` kolejno w: katalogu roboczym, katalogu pliku `.srt` oraz katalogu skryptu.
   - Każdy klucz z `keywords.json` (`title`, `source_lang`, `target_lang`, `keywords`, `skip_phrases`) jest wypisywany w konsoli i pomija odpowiadające mu pytanie interaktywne. Brakujące sekcje są dopytywane od użytkownika na starcie.
   - Następnie program pyta o **numer początkowego bloku SRT** (`Enter` oznacza start od początku pliku).
4. **Wznawianie od wskazanego numeru napisu**:
   - Jeśli podano numer początkowy (np. `422`), program wczytuje istniejący plik wyjściowy, zachowuje bez zmian wszystkie bloki przed numerem `422` (`1..421`) i tłumaczy od bloku `#422` do końca pliku.
5. **Tłumaczenie paczkami i zapis przyrostowy**:
   - Linie dialogowe (z pominięciem fraz z `skip_phrases`) są wysyłane w paczkach po 25 sztuk z przypisanymi identyfikatorami (`{"id": 0, "text": "..."}`), co gwarantuje zgodność liczby linii.
   - Zachowywane jest formatowanie kursywy (`<i>...</i>`), a wynik zapisywany jest na dysku po każdej paczce.
6. **Odporność na błędy API**:
   - Przy błędach `429` (limit zapytań) lub `503` (przeciążenie) skrypt rotuje na kolejny model z wykrytej listy (do 40 prób na paczkę, a w ostateczności dzieli paczkę na mniejsze części).
   - Przy błędzie `404` model jest natychmiast usuwany z aktywnej listy bez marnowania czasu na oczekiwanie.

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
