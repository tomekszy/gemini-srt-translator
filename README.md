# SRT Subtitle Translator (Gemini API — TypeScript / Node.js)

Uniwersalne narzędzie CLI w TypeScript / Node.js do automatycznego tłumaczenia napisów filmowych w formacie .srt przy użyciu modeli Google Gemini. Program sam rozpoznaje tytuł produkcji oraz język źródłowy z nazwy pliku .srt, weryfikuje istnienie tytułu, generuje listę słów kluczowych do zachowania, obsługuje flagi CLI oraz wznawianie tłumaczenia od dowolnego numeru bloku, a na początku i końcu pliku wynikowego dopisuje informację o projekcie.

Repozytorium projektu: https://github.com/tomekszy/gemini-srt-translator

---

## Struktura projektu

- run_translate.sh — skrypt uruchomieniowy Bash ładujący zmienne z .env, instalujący zależności Node.js (w razie potrzeby), kompilujący TypeScript i uruchamiający dist/index.js.
- src/index.ts — główny kod źródłowy w TypeScript.
- dist/index.js — skompilowany kod JavaScript uruchamiany przez Node.js.
- package.json oraz tsconfig.json — konfiguracja projektu Node.js oraz kompilatora TypeScript.
- .env — plik przechowujący zmienną środowiskową GEMINI_API_KEY.

---

## Jak działa program (krok po kroku)

1. **Przygotowanie środowiska i kompilacja (run_translate.sh)**:
  - Wczytuje GEMINI_API_KEY z pliku .env.
  - Instaluje zależności z package.json, jeśli brakuje node_modules.
  - Kompiluje src/index.ts do dist/index.js (jeśli plik źródłowy jest nowszy niż wynik kompilacji).
2. **Automatyczne wykrywanie modeli (discoverUsableModels)**:
  - Na starcie program odpytuje API Gemini o wszystkie modele dostępne dla podanego klucza.
  - Odrzuca modele nieobsługujące generowania tekstu (np. embedding, imagen, veo, tts, transcribe, audio, computer-use, robotics, deep-research).
  - Sortuje pozostałe modele według przydatności do tłumaczeń wsadowych (priorytet dla modeli flash-lite, następnie flash, na końcu pro) i używa całej wykrytej listy.
3. **Wykrywanie języka źródłowego i nazewnictwo pliku wyjściowego**:
  - Program sprawdza końcówkę nazwy pliku wejściowego — jeśli przed .srt znajduje się 2- lub 3-literowy kod języka po kropce (np. film.en.srt, film.de.srt, film.ja.srt), rozpoznaje język źródłowy automatycznie (w przeciwnym razie wykrywa go z treści dialogów).
  - Przy tworzeniu pliku wyjściowego kod języka źródłowego jest zastępowany kodem języka docelowego (np. film.en.srt zostanie zapisany jako film.pl.srt zamiast film.en.pl.srt).
4. **Rozpoznawanie tytułu i słów kluczowych przez Gemini**:
  - Program wysyła nazwę pliku .srt (lub tytuł podany flagą --title / -t) do Gemini, aby ustalić pełny tytuł produkcji, ocenić, czy taki tytuł istnieje, oraz wygenerować listę słów kluczowych (nazw własnych) do zachowania.
  - W trybie interaktywnym wpisanie nowego tytułu wysyła ponowne zapytanie do Gemini z poprawionym tytułem i odświeżoną listą słów kluczowych; wciśnięcie Enter zatwierdza tytuł.
5. **Edycja słów kluczowych, język docelowy i blok startowy**:
  - Słowa kluczowe można zatwierdzić klawiszem Enter, dodać (+nazwa lub nazwa) albo usunąć (-nazwa), dopasowanie w tłumaczeniu jest niewrażliwe na wielkość liter (case-insensitive).
  - Program pobiera język docelowy z flagi --lang / -l lub pyta o niego interaktywnie (domyślnie polski).
  - Następnie pobiera numer startowy bloku SRT z flagi --start / -s lub pyta o niego (Enter oznacza start od początku pliku).
6. **Wznawianie od wskazanego numeru napisu**:
  - Jeśli podano numer początkowy (np. 422), program wczytuje istniejący plik wyjściowy, zachowuje bez zmian wszystkie bloki przed numerem 422 (1..421) i tłumaczy od bloku #422 do końca pliku.
7. **Tłumaczenie paczkami, zapis przyrostowy oraz informacja o projekcie**:
  - Linie dialogowe (z pominięciem stałych fraz napisowych) są wysyłane w paczkach po 25 sztuk z przypisanymi identyfikatorami, co gwarantuje zgodność liczby linii.
  - Zachowywane jest formatowanie kursywy, a wynik zapisywany jest na dysku po każdej paczce.
  - Na samym początku oraz na samym końcu pliku wynikowego dodawany jest blok informacyjny z nazwą narzędzia oraz adresem https://github.com/tomekszy/gemini-srt-translator (bez powielania przy kolejnych zapisach i wznawianiu).
8. **Odporność na błędy API**:
  - Przy błędach 429 (limit zapytań) lub 503 (przeciążenie) skrypt rotuje na kolejny model z wykrytej listy (do 40 prób na paczkę, a w ostateczności dzieli paczkę na mniejsze części).
  - Przy błędzie 404 model jest natychmiast usuwany z aktywnej listy bez marnowania czasu na oczekiwanie.

---

## Wymagania

- Node.js oraz npm (oraz bash).
- Klucz API Google Gemini w pliku .env:

  GEMINI_API_KEY=twoj_klucz_api_z_google_ai_studio

---

## Flagi CLI

- **--start <numer>**, **-s <numer>** — numer bloku SRT, od którego ma ruszyć tłumaczenie (poprzednie przetłumaczone bloki z pliku wyjściowego zostają zachowane).
- **--lang <język>**, **-l <język>** — język docelowy tłumaczenia (np. pl, polski, en, de; domyślnie polski).
- **--title <tytuł>**, **-t <tytuł>** — wskazówka lub wymuszony tytuł filmu/serialu przekazywany do rozpoznania słów kluczowych.
- **--yes**, **-y** — tryb nieinteraktywny (automatycznie akceptuje rozpoznany tytuł, wygenerowane słowa kluczowe oraz wartości domyślne bez zatrzymywania na pytania w konsoli).

---

## Instrukcja użytkowania

1. **Nadanie uprawnień skryptowi startowemu (jednorazowo)**:
   chmod +x run_translate.sh

2. **Tłumaczenie konkretnego pliku .srt**:
   ./run_translate.sh Nazwa.Filmu.2018.1080p.en.srt
   Program wykryje język angielski z końcówki .en.srt, a wynik zapisze jako Nazwa.Filmu.2018.1080p.pl.srt (z informacją o narzędziu i linkiem https://github.com/tomekszy/gemini-srt-translator na początku i końcu pliku).

3. **Tłumaczenie wszystkich plików .srt w bieżącym katalogu**:
   ./run_translate.sh
   Program automatycznie znajdzie wszystkie pliki *.srt (z pominięciem gotowych plików z kodem języka przed .srt oraz *.translated.srt) i przetłumaczy je po kolei.

4. **Kontynuowanie tłumaczenia przerwanego pliku**:
   Jeśli plik Film.pl.srt jest przetłumaczony np. do napisu nr 421, a od 422 zaczyna się tekst oryginalny:
   ./run_translate.sh Film.en.srt --start 422
   Bloki 1..421 pozostaną nienaruszone, a program dokończy tłumaczenie od 422 wzwyż.

5. **Uruchomienie w pełni automatyczne (bez pytań w terminalu)**:
   ./run_translate.sh Film.en.srt --lang pl --start 422 --yes
