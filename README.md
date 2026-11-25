# SRT Subtitle Translator (Gemini API)

Uniwersalne narzędzie CLI do automatycznego tłumaczenia napisów filmowych w formacie .srt przy użyciu modeli Google Gemini. Program sam rozpoznaje tytuł produkcji z nazwy pliku .srt, weryfikuje jego istnienie, generuje listę słów kluczowych do zachowania, pozwala wznawiać tłumaczenie od dowolnego numeru bloku oraz dopisuje informację o projekcie na początku i końcu pliku wynikowego.

Repozytorium projektu: https://github.com/tomekszy/gemini-srt-translator

---

## Struktura projektu

- run_translate.sh — skrypt uruchomieniowy Bash, który tworzy lub odbudowuje środowisko wirtualne Pythona (.venv), instaluje bibliotekę google-genai, kopiuje GEMINI_API_KEY do .venv/.env i uruchamia program.
- translate_srt_gemini.py — główny skrypt wykrywający modele Gemini, identyfikujący tytuł i słowa kluczowe, tłumaczący napisy paczkami i zapisujący pliki wyjściowe.
- .env — plik przechowujący zmienną środowiskową GEMINI_API_KEY.

---

## Jak działa program (krok po kroku)

1. **Inicjalizacja środowiska (run_translate.sh)**:
  - Sprawdza, czy katalog .venv istnieje i ma poprawne ścieżki (w razie przeniesienia katalogu projektu automatycznie odbudowuje .venv).
  - Instaluje pakiet google-genai wewnątrz .venv, jeśli nie jest jeszcze dostępny.
  - Pobiera GEMINI_API_KEY z głównego pliku .env, zapisuje go w .venv/.env i eksportuje do środowiska procesu.
2. **Automatyczne wykrywanie modeli (discover_usable_models)**:
  - Na starcie program odpytuje API Gemini o wszystkie modele dostępne dla podanego klucza.
  - Odrzuca modele nieobsługujące generowania tekstu (np. embedding, imagen, veo, tts, transcribe, audio, computer-use, robotics, deep-research).
  - Sortuje pozostałe modele według przydatności do tłumaczeń wsadowych (priorytet dla modeli flash-lite, następnie flash, na końcu pro) i używa całej wykrytej listy.
3. **Rozpoznawanie tytułu i słów kluczowych przez Gemini**:
  - Program wysyła nazwę pliku .srt do Gemini, aby odgadnąć tytuł produkcji, ocenić, czy taki tytuł istnieje, oraz wygenerować listę słów kluczowych (nazw własnych) do zachowania.
  - Użytkownik widzi rozpoznany tytuł, informację o istnieniu tytułu, wygenerowane słowa kluczowe oraz listę pomijanych fraz napisowych.
  - Wpisanie nowego tytułu wysyła ponowne zapytanie do Gemini z poprawionym tytułem i odświeżoną listą słów kluczowych; wciśnięcie Enter zatwierdza tytuł.
4. **Edycja słów kluczowych, język docelowy i blok startowy**:
  - Słowa kluczowe można zatwierdzić klawiszem Enter, dodać (+nazwa lub nazwa) albo usunąć (-nazwa), dopasowanie w tłumaczeniu jest niewrażliwe na wielkość liter (case-insensitive).
  - Program pyta wyłącznie o język docelowy (domyślnie polski); język źródłowy jest rozpoznawany automatycznie przez model.
  - Następnie program pyta, od którego numeru bloku SRT rozpocząć tłumaczenie (Enter oznacza start od początku pliku).
5. **Wznawianie od wskazanego numeru napisu**:
  - Jeśli podano numer początkowy (np. 422), program wczytuje istniejący plik wyjściowy, zachowuje bez zmian wszystkie bloki przed numerem 422 (1..421) i tłumaczy od bloku #422 do końca pliku.
6. **Tłumaczenie paczkami, zapis przyrostowy oraz informacja o projekcie**:
  - Linie dialogowe (z pominięciem stałych fraz napisowych) są wysyłane w paczkach po 25 sztuk z przypisanymi identyfikatorami, co gwarantuje zgodność liczby linii.
  - Zachowywane jest formatowanie kursywy, a wynik zapisywany jest na dysku po każdej paczce.
  - Na samym początku oraz na samym końcu pliku wynikowego dodawany jest blok informacyjny z nazwą narzędzia oraz adresem https://github.com/tomekszy/gemini-srt-translator (bez powielania przy kolejnych zapisach i wznawianiu).
7. **Odporność na błędy API**:
  - Przy błędach 429 (limit zapytań) lub 503 (przeciążenie) skrypt rotuje na kolejny model z wykrytej listy (do 40 prób na paczkę, a w ostateczności dzieli paczkę na mniejsze części).
  - Przy błędzie 404 model jest natychmiast usuwany z aktywnej listy bez marnowania czasu na oczekiwanie.

---

## Wymagania

- System Linux / macOS z zainstalowanym python3 (z modułem venv) oraz bash.
- Klucz API Google Gemini w pliku .env:

  GEMINI_API_KEY=twoj_klucz_api_z_google_ai_studio

---

## Instrukcja użytkowania

1. **Nadanie uprawnień skryptowi startowemu (jednorazowo)**:
   chmod +x run_translate.sh

2. **Tłumaczenie konkretnego pliku .srt**:
   ./run_translate.sh Nazwa.Filmu.2018.1080p.srt
   Wynik zostanie zapisany jako Nazwa.Filmu.2018.1080p.pl.srt (z informacją o narzędziu i linkiem https://github.com/tomekszy/gemini-srt-translator na początku i końcu pliku).

3. **Tłumaczenie wszystkich plików .srt w bieżącym katalogu**:
   ./run_translate.sh
   Program automatycznie znajdzie wszystkie pliki *.srt (z pominięciem gotowych *.pl.srt) i przetłumaczy je po kolei.

4. **Kontynuowanie tłumaczenia przerwanego pliku**:
   Jeśli plik Film.pl.srt jest przetłumaczony np. do napisu nr 421, a od 422 zaczyna się tekst oryginalny:
   Uruchom ./run_translate.sh Film.srt.
   Na pytanie Od którego numeru napisu (bloku SRT) tłumaczymy plik? wpisz 422 i zatwierdź klawiszem Enter.
   Bloki 1..421 pozostaną nienaruszone, a program dokończy tłumaczenie od 422 wzwyż.
